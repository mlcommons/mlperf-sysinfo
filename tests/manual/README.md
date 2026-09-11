# Testing mlperf-sysinfo on a real machine

A manual pass over the scenarios the automated suite cannot reach: real SSH,
real hardware detection, real serving endpoints. Copy this file to the machine
under test and work down it.

Everything below was run against **mlperf-sysinfo 1.0.0a8** (mlc-scripts
1.2.0a5, mlcflow 1.4.0a4) on a single node with **8x NVIDIA H100 80GB HBM3**
and a Xeon Platinum 8480+, Ubuntu 24.04. Those are the versions the numbers
were taken on and are deliberately not bumped with the pin; the current pin is
mlc-scripts 1.2.0a6, which differs only in rejecting a missing `system_name`
earlier — a path this package cannot reach, since `system.name` is a required
config field. Expected values marked *measured* are what that machine actually
produced — adapt the accelerator counts to yours.
Values not marked are derived from the code and have not been observed.

Multi-node scenarios reach the same box under three different names. That is a
real test of the multi-node path: each "node" is a separate SSH session, a
separate remote virtualenv and a separate per-node collection. What it cannot
test is heterogeneity — see [T5/T6](#t5-and-t6-different-gpus-across-or-within-nodes).

---

## Before you start

```bash
python3 -m venv ~/sysinfo-test/v
~/sysinfo-test/v/bin/pip install --pre 'mlperf-sysinfo==1.0.0a8'
mkdir -p ~/sysinfo-test && cd ~/sysinfo-test
```

**Passwordless SSH to yourself must work**, under every name you plan to use:

```bash
for h in localhost 127.0.0.1 "$(hostname)"; do
  printf '%-16s ' "$h"
  ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 \
      "$USER@$h" hostname 2>&1 | tail -1
done
```

All three must print the hostname. If not:

```bash
ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519      # if you have no key
cat ~/.ssh/id_ed25519.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

### Hygiene between every run — do not skip this

Each node writes its result to a **fixed path under a fixed name**:
`/tmp/mlperf-system-info-single-node/mlperf-system-info-single-node-<index>.json`.
Files from earlier runs sit there under exactly the names this run will use,
and nothing distinguishes them. A run where one node fails can pick up a
previous run's answer for that index and report it as current.

```bash
rm -rf /tmp/mlperf-system-info-single-node     # before every capture
```

---

## Generate every config at once

The scenarios below show only the block that distinguishes each one. Rather
than editing YAML by hand, write them all with this — it fills in your
username and hostname, so nothing needs substituting afterwards.

```bash
cd ~/sysinfo-test && cat > make-configs.sh <<'SCRIPT'
#!/bin/bash
# Writes t1.yaml .. t4.yaml and t7.yaml into the current directory.
set -eu
U="${U:-$USER}"; H="$(hostname)"; D="$PWD"

common() {   # $1 = output dir
cat <<YAML
profile: endpoints
output: { dir: $1 }
system:
  name: ${SYSTEM_NAME:-my_system}
  shortened_name: ${SYSTEM_NAME:-my_system}
  availability: available
  accelerator: ${ACCEL:-cuda}
  cooling: air
submission:
  division: standardized
  notes: { hardware: "test run", software: "test run" }
run:
  link_config: "https://example.invalid/test"
YAML
}

{ common out-t1; cat <<YAML
nodes:
  include_local: true
serving:
  url: "Test run, no endpoint under test"
YAML
} > t1.yaml

{ common out-t2; cat <<YAML
nodes:
  include_local: false
  ssh: [ $U@localhost ]
  ssh_key_preconfigured: true
remote:
  isolated: true
serving:
  url: "Test run, no endpoint under test"
YAML
} > t2.yaml

three="  ssh:\n    - $U@localhost\n    - $U@127.0.0.1\n    - $U@$H"

{ common out-t3; printf "nodes:\n  include_local: false\n%b\n  ssh_key_preconfigured: true\n" "$three"; cat <<YAML
remote:
  isolated: true
serving:
  url: "Test run, no endpoint under test"
YAML
} > t3.yaml

{ common out-t4; printf "nodes:\n  include_local: false\n%b\n  ssh_key_preconfigured: true\n" "$three"; cat <<YAML
  groups:
    prefill:
      - { match: ${MATCH:-NVIDIA H100}, count: 2 }
    decode:
      - { match: ${MATCH:-NVIDIA H100}, count: 1 }
remote:
  isolated: true
serving:
  url: "Test run, no endpoint under test"
YAML
} > t4.yaml

{ common out-t7; cat <<YAML
nodes:
  include_local: false
  ssh: [ $U@localhost ]
  ssh_key_preconfigured: true
remote:
  isolated: true
serving:
  url: http://127.0.0.1:8111
  node: $U@localhost
  log: $D/logs/${FW:-vllm}.log
  framework: auto
YAML
} > t7.yaml

echo "wrote t1.yaml t2.yaml t3.yaml t4.yaml t7.yaml"
SCRIPT
chmod +x make-configs.sh && ./make-configs.sh
```

Three environment variables adjust it without editing:
`SYSTEM_NAME` (default `my_system`), `ACCEL` (`cuda`/`rocm`/`xpu`/`none`), and
`MATCH` — the accelerator substring T4 groups on, which **must** match what T3
reports. Run T3 first, read `accelerator_model_name` out of its output, and set
`MATCH` from that. `FW` picks which log T7 points at.

Check one before trusting the rest — `--offline` validates the config without
touching the network:

```console
$ ~/sysinfo-test/v/bin/mlperf-sysinfo check -c t3.yaml --offline
INFO  check: config is valid
INFO  check: nothing was reached -- run without --offline before capturing
```

The only warning you should see is `serving.node empty`, which T1-T4 do not
set. Anything else is a typo in your environment variables.

---

## The scenarios

| | Scenario | Proves | Needs |
| --- | --- | --- | --- |
| [T1](#t1) | Local only, no SSH | The no-SSH path | nothing |
| [T2](#t2) | One SSH node, local excluded | The remote path, isolation | SSH to self |
| [T3](#t3) | Three SSH nodes | Multi-node collapse into one type | SSH to self |
| [T4](#t4) | Three nodes, prefill(2) + decode(1) | Function grouping | SSH to self |
| [T5](#t5-and-t6-different-gpus-across-or-within-nodes) | Different GPUs across nodes | Heterogeneous aggregation | see below |
| [T6](#t5-and-t6-different-gpus-across-or-within-nodes) | Different GPUs within a node | Per-model accelerator list | see below |
| [T7](#t7) | vLLM / SGLang / TRT-LLM | Framework detection, log parsing | fake server (Appendix A/B) |

<a name="t1"></a>
## T1 — local only, no SSH nodes

The machine running the command *is* the system. Nothing is reached over SSH,
so `remote:` is irrelevant here.

```yaml
# t1.yaml
profile: endpoints
output: { dir: out-t1 }
system:
  name: my_system
  shortened_name: my_system
  availability: available
  accelerator: cuda
  cooling: air
nodes:
  include_local: true
serving:
  url: "Test run, no endpoint under test"
submission:
  division: standardized
  notes: { hardware: "test run", software: "test run" }
run:
  link_config: "https://example.invalid/test"
```

```bash
rm -rf /tmp/mlperf-system-info-single-node
~/sysinfo-test/v/bin/mlperf-sysinfo capture -c t1.yaml
```

**Expect** (*measured*, 23s):

```
system_size                8 accelerators
node_types                 1
node_types[0].number_of_nodes   1
nodes_expected / collected      1 / 1,  complete=true
```

Check the tag string in the log has **no** `_exclude_current_node`:

```bash
grep 'collecting with tags' out-t1/.mlperf-sysinfo/*.log
```

---

<a name="t2"></a>
## T2 — one SSH node, this machine excluded

The single-remote path, and the one that proves isolation.

```yaml
# t2.yaml  — as t1.yaml, but:
output: { dir: out-t2 }
nodes:
  include_local: false
  ssh:
    - you@localhost
  ssh_key_preconfigured: true
remote:
  isolated: true
```

```bash
rm -rf /tmp/mlperf-system-info-single-node
ls -ld ~/mlcflow 2>/dev/null                      # note the mtime
~/sysinfo-test/v/bin/mlperf-sysinfo capture -c t2.yaml
```

**Expect** (*measured*, 39s): same numbers as T1 — one node, 8 accelerators —
but reached over SSH. The tag string now **does** contain
`_exclude_current_node`.

**Then verify isolation actually happened:**

```bash
ls -ld ~/mlcflow                       # mtime unchanged: the home venv was not used
ls -d /tmp/mlcflow-isolated-* 2>/dev/null | wc -l    # 0: the trap fired
grep -o 'mlcflow-isolated-[0-9a-f]*' out-t2/.mlperf-sysinfo/*.log | sort -u
grep -o 'export MLC_\(REPOS\|CACHE\)="[^"]*"' out-t2/.mlperf-sysinfo/*.log | sort -u
```

The last two should show a `/tmp/mlcflow-isolated-<id>` and both MLC roots
pointed into it. Run again with `remote: { isolated: false }` and `~/mlcflow`
and `~/MLC` will be created and kept instead — that is the contrast worth
seeing once.

---

<a name="t3"></a>
## T3 — three nodes, all identical

Three SSH sessions to the same box under three names. They are distinct
targets to the tool, and it must collapse three identical answers into one
node type with a count of three.

```yaml
# t3.yaml  — as t2.yaml, but:
output: { dir: out-t3 }
nodes:
  include_local: false
  ssh:
    - you@localhost
    - you@127.0.0.1
    - you@HOSTNAME
  ssh_key_preconfigured: true
```

```bash
rm -rf /tmp/mlperf-system-info-single-node
~/sysinfo-test/v/bin/mlperf-sysinfo capture -c t3.yaml
```

**Expect** (*measured*, 112s):

```
system_size                  24 accelerators
system_node_ensemble_total   3
node_types                   1          <- collapsed, not three entries
node_types[0].number_of_nodes    3
nodes_expected / collected       3 / 3,  complete=true
```

`node_types` being **1** is the assertion that matters. Three entries would
mean the homogeneity check failed.

---

<a name="t4"></a>
## T4 — three nodes, prefill(2) + decode(1)

Disaggregated serving: the same hardware split by function. `match` is compared
case-insensitively against the **detected accelerator model name**, so it must
be a substring of what T3 reported — `NVIDIA H100`, not `H100x8`.

```yaml
# t4.yaml  — as t3.yaml, but:
output: { dir: out-t4 }
nodes:
  include_local: false
  ssh:
    - you@localhost
    - you@127.0.0.1
    - you@HOSTNAME
  ssh_key_preconfigured: true
  groups:
    prefill:
      - { match: NVIDIA H100, count: 2 }
    decode:
      - { match: NVIDIA H100, count: 1 }
```

```bash
rm -rf /tmp/mlperf-system-info-single-node
~/sysinfo-test/v/bin/mlperf-sysinfo capture -c t4.yaml
```

**Expect** (*measured*, 109s):

```
system_size                  8 accelerators + 16 accelerators
system_node_ensemble_total   3
node_types                   2
  ensemble_id 1:  1 node   <- decode
  ensemble_id 2:  2 nodes  <- prefill
node_config    'decode: 1x NVIDIA H100; prefill: 2x NVIDIA H100'
```

Two things are easy to get wrong when reading this output:

- **The groups come out alphabetically, not in config order.** `decode` is
  ensemble 1 even though `prefill` is written first. The node-config YAML is
  serialised with sorted keys.
- **`node_types` entries carry no function label.** `node_config` is the only
  field in the document that says which group is which; you tell the entries
  apart by `number_of_nodes`.

Worth also checking the failure mode — set `count: 3` under `prefill` while
leaving `decode` at 1, and the run should refuse rather than invent a node:

```
node_config declares 4 'NVIDIA H100' node(s) across all function groups
but only 3 'NVIDIA H100' node(s) were probed.
```

---

<a name="t5-and-t6-different-gpus-across-or-within-nodes"></a>
## T5 and T6 — different GPUs across, or within, nodes

**Neither can be run end to end on a homogeneous box, and faking the hardware
does not work.** Be clear about why before spending time on it:

GPU identity comes from real CUDA device enumeration, not from parsing
`nvidia-smi` output, so a stub binary on `PATH` does not change what is
reported. Containers do not help either — every container on one host sees the
same physical GPUs. **T5 needs a second machine with a different accelerator.**

T6 is worse than untestable: **the per-node file has nowhere to put a second
accelerator model.** A real per-node result looks like this — flat, one model:

```json
{ "accelerator_model_name": "NVIDIA H100 80GB HBM3", "accelerators_per_node": 8, ... }
```

The aggregator does understand a richer form — an `accelerators` list, one
entry per model — and falls back to the flat fields when it is absent, logging:

```
Node '?' reported no accelerators list — it was probed by an older checkout.
Only one accelerator model is described; re-probe the node if it hosts more
than one.
```

That warning fires on **every** node of a current, fully up-to-date install,
because the single-node probe only emits the list when the CUDA detection
script publishes per-device state, and on the tested machine it does not. So
the per-model path is dead code in practice today. Confirm on yours:

```bash
grep -c 'older checkout' out-t3/.mlperf-sysinfo/*.log     # 0 is good news; 1+ means the flat fallback
python3 -c "import json;print('accelerators' in json.load(open('out-t3/.mlperf-sysinfo/mlperf-system-info-single-node-0.json')))"
```

### What to do instead

Test the aggregation directly. The per-node JSON files are the aggregator's
only input, so heterogeneity is a fixture, not a hardware problem — and the
fixtures ship inside the wheel:

```bash
~/sysinfo-test/v/bin/pip install pytest
cd "$(~/sysinfo-test/v/bin/python -c 'import mlc_scripts,pathlib;print(pathlib.Path(mlc_scripts.__file__).parent)')"/script/get-mlperf-multi-node-system-info/tests
~/sysinfo-test/v/bin/python -m pytest -q            # 16 passed
```

`test_aggregation.py` has a `_node(commit=, model=, accel=, per_node=)` helper
and a `_write_node(index, payload)` seam. T5 is two lines:

```python
self._write_node(0, _node(accel="H100"))
self._write_node(1, _node(accel="A100"))
self._postprocess(remote_count=2)
# expect two node_types, not one
```

T6 is the same shape with a payload carrying an `accelerators` list of two
entries — which also documents the format the probe ought to be producing.

---

<a name="t7"></a>
## T7 — serving frameworks

Two independent things are being tested, and they can fail separately:

| | Where it comes from | Lands in |
| --- | --- | --- |
| Framework name and version | HTTP probe of `serving.url` | `serving_framework` |
| Parallelism and batch | Parsing `serving.log` on `serving.node` | `tensor_parallel`, `config_summary`, ... |

Real servers work, but a 70B model to read one startup line is a poor trade.
[Appendix A](#appendix-a) is a fake endpoint and [Appendix B](#appendix-b) is a
set of startup logs; both were checked against the real probe and the real
parser.

```yaml
# t7.yaml  — as t2.yaml, but:
output: { dir: out-t7 }
serving:
  url: http://127.0.0.1:8111
  node: you@localhost
  log: /home/you/sysinfo-test/logs/vllm.log     # absolute; must exist on serving.node
  framework: auto
```

```bash
python3 fake_server.py vllm 8111 &
rm -rf /tmp/mlperf-system-info-single-node
~/sysinfo-test/v/bin/mlperf-sysinfo capture -c t7.yaml
kill %1
```

**Expect** (*measured*, vLLM):

```
serving_framework   'vLLM 0.11.0'
endpoint_url        'http://127.0.0.1:8111'
tensor_parallel     8      pipeline_parallel  1
expert_parallel     1      data_parallel      1
batch               256    disaggregated      0
config_summary      'TP 8'
```

`config_summary` showing only `TP 8` is correct, and the disagreement with the
standalone parser is worth knowing about. Rules 8.2 define the field as a
concatenation of the parallelism degrees "**where these fields are > 1**", so
dropping `EP 1`, `PP 1` and `DP 1` is the specified behaviour. Run
`get-mlperf-serving-config/parse.py` on the same log directly and it reports
`EP 1, PP 1, TP 8, DP 1` — including the degrees equal to 1, and in a
different order from the one the rules list (disaggregated, TP, PP, EP, DP).
The deliverable is right; the standalone summary is not. Assert on the
deliverable.

Repeat with `sglang` and `trtllm` — start the fake server in that mode and
point `serving.log` at the matching log. **Framework detection alone**, without
a full capture, is much faster to iterate on:

```bash
for fw in vllm sglang trtllm; do
  python3 fake_server.py $fw 8111 >/dev/null 2>&1 & sleep 1
  printf '%-8s -> ' $fw
  ~/sysinfo-test/v/bin/python -c "
from mlperf_sysinfo.preflight import probe_endpoint
r = probe_endpoint('http://127.0.0.1:8111'); print(r.ok, repr(r.detail))"
  kill %1 2>/dev/null
done
```

**Expect** (*measured*):

```
vllm     -> True 'vLLM 0.11.0'
sglang   -> True 'SGLang 0.4.6.post5'
trtllm   -> True 'TRT-LLM 1.2.0rc1'
```

Also check the negative: with no server running at all, `check` must report
`no serving framework answered` and still let the capture proceed — an
unreachable endpoint is a warning, not a config problem.

---

<a name="appendix-a"></a>
## Appendix A — fake serving endpoint

The probe tells the three frameworks apart by **which paths answer**, in this
order. `/perf_metrics` is the discriminator between TRT-LLM and vLLM, so a vLLM
stand-in must 404 on it.

```python
#!/usr/bin/env python3
"""Usage: fake_server.py <vllm|sglang|trtllm> [port]"""
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

FRAMEWORK = sys.argv[1] if len(sys.argv) > 1 else "vllm"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
ROUTES = {
    "vllm":   {"/version": {"version": "0.11.0"}},
    "sglang": {"/get_server_info": {"version": "0.4.6.post5"}},
    "trtllm": {"/perf_metrics": [], "/version": {"version": "1.2.0rc1"}},
}[FRAMEWORK]

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ROUTES:
            body = json.dumps(ROUTES[self.path]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)
    def log_message(self, *a):
        pass

print(f"fake {FRAMEWORK} on :{PORT}", flush=True)
HTTPServer(("127.0.0.1", PORT), H).serve_forever()
```

---

<a name="appendix-b"></a>
## Appendix B — fake startup logs

Put these under `~/sysinfo-test/logs/` (`mkdir -p ~/sysinfo-test/logs`
first — the config generator does not create them). Only the head of the file
is read
(2 MiB), which is where a real server prints its config. Each was run through
the real parser; the values in the comment are what came back.

```text
# logs/vllm.log  ->  vLLM 0.11.0, TP 8 PP 1 EP 1 DP 1, batch 256
INFO 09-11 08:00:00 [__init__.py:216] Automatically detected platform cuda.
INFO 09-11 08:00:01 [api_server.py:1805] vLLM API server version 0.11.0
INFO 09-11 08:00:04 [core.py:77] Initializing a V1 LLM engine (v0.11.0) with config: model='meta-llama/Llama-3.1-70B-Instruct', tensor_parallel_size=8, pipeline_parallel_size=1, data_parallel_size=1, expert_parallel_size=1, max_num_seqs=256, max_model_len=8192, enable_disagg_prefill=False,
INFO 09-11 08:01:12 [api_server.py:1935] Starting vLLM API server 0 on http://0.0.0.0:8000
```

```text
# logs/sglang.log  ->  SGLang 0.4.6, TP 8 PP 1 EP 1 DP 1, batch 256
[2026-09-11 08:00:00] server_args=ServerArgs(model_path=meta-llama/Llama-3.1-70B-Instruct, tp_size=8, pp_size=1, ep_size=1, dp_size=1, max_running_requests=256, disaggregation_mode=null, port=30000)
[2026-09-11 08:00:02] SGLang version 0.4.6.post5
[2026-09-11 08:01:40] The server is fired up and ready to roll!
```

```text
# logs/trtllm.log  ->  TRT-LLM 1.2.0, TP 8 PP 1, batch 256, max_num_tokens 8192
[TensorRT-LLM] TensorRT LLM version: 1.2.0
[09/11/2026-08:00:01] [TRT-LLM] [I] Set dtype to bfloat16.
[09/11/2026-08:00:02] [TRT-LLM] [I] LLM Args: tensor_parallel_size=8, pipeline_parallel_size=1, moe_expert_parallel_size=1, max_batch_size=256, max_num_tokens=8192, orchestrator_type=None
[09/11/2026-08:01:20] [TRT-LLM] [I] Server started at http://0.0.0.0:8000
```

The parser reads `.post5` as version `0.4.6` — the version regex takes three
numeric components. Expected, not a fault in the log.

Variations worth one run each: `enable_disagg_prefill=True` (vLLM) or
`disaggregation_mode='prefill'` (SGLang) should set `disaggregated` and put
`Disaggregated` at the front of `config_summary`; SGLang's
`max_running_requests=None` should leave `batch` null rather than zero.

Check a log without a capture:

```bash
P=$(~/sysinfo-test/v/bin/python -c "import mlc_scripts,pathlib;print(pathlib.Path(mlc_scripts.__file__).parent)")
~/sysinfo-test/v/bin/python "$P/script/get-mlperf-serving-config/parse.py" \
    --log-path logs/vllm.log --out-file /tmp/sc.json --serving-framework auto
cat /tmp/sc.json
```

---

<a name="appendix-c"></a>
## Appendix C — what a run leaves on each node

Measured after an isolated capture. Isolation redirects where MLC keeps its
state; it does **not** change the directory the remote commands run in, which
is the SSH login directory. Anything written relative to the current directory
still lands in `$HOME`.

| Path | Isolated run | Default run |
| --- | --- | --- |
| `~/mlcflow` (virtualenv) | untouched | created, kept |
| `~/MLC` (cache) | untouched | created, kept |
| `/tmp/mlcflow-isolated-<id>` | created, **removed** | — |
| `~/system-info.json` | **~180 KB, kept** | kept |
| `/tmp/mlperf-system-info-single-node/` | **kept**, fixed names | kept |
| `~/.cache/pip` | written | written |

The last three survive isolation. `~/.cache/pip` is arguably wanted — it is why
a second isolated run takes ~40s rather than minutes. The per-node directory is
the one that can corrupt a result; see
[Hygiene](#hygiene-between-every-run--do-not-skip-this).

Teardown:

```bash
rm -rf ~/sysinfo-test /tmp/mlperf-system-info-single-node ~/system-info.json
ls -d /tmp/mlcflow-isolated-* 2>/dev/null      # should already be empty
```

---

## Reporting a result

For anything that fails, the run log under `<output.dir>/.mlperf-sysinfo/` has
the whole story — including the exact SSH command line. Attach it, with:

```bash
~/sysinfo-test/v/bin/mlperf-sysinfo --version
~/sysinfo-test/v/bin/pip freeze | grep -E 'mlperf-sysinfo|mlc-scripts|mlcflow'
nvidia-smi --query-gpu=name --format=csv,noheader | sort | uniq -c
```
