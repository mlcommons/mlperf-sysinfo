# mlperf-sysinfo

A tool that automatically captures the system description of a benchmarking
machine, built on top of the
[mlc-scripts](https://github.com/mlcommons/mlperf-automations) and
[mlcflow](https://github.com/mlcommons/mlcflow) automation.

**Documentation: <https://anandhu-eng.github.io/mlperf-sysinfo/>**

```bash
pip install mlperf-sysinfo
```

`mlperf-sysinfo --help` lists the commands; `mlperf-sysinfo <command> --help` shows what one takes.

## Quick Run

The steps below capture a system description for a CPU-only system, using
only the machine you run them on. No other node is contacted.

**Step 1 -- create a starter config**

```bash
mlperf-sysinfo init endpoints
```

<details>
<summary>Sample output</summary>

```console
$ mlperf-sysinfo init endpoints
[2026-08-19 21:37:29] INFO     init: wrote template sysinfo.yaml (profile endpoints) to /home/user/sysinfo.yaml
[2026-08-19 21:37:29] INFO     init: edit it before running 'mlperf-sysinfo capture -c sysinfo.yaml'
```

</details>

**Step 2 -- edit `sysinfo.yaml`**

The sample below is a complete config for a CPU-only capture of the machine
you're on.

See [The config file](https://anandhu-eng.github.io/mlperf-sysinfo/configuration/)
for every option, and for what each working group requires.

```yaml
profile: endpoints

output:
  dir: results/sysinfo

system:
  name: devbox1
  shortened_name: devbox1
  availability: available
  accelerator: none

nodes:
  include_local: true
  ssh: []

serving:
  url: http://127.0.0.1:8000

submission:
  division: standardized
```

**Step 3 -- check the config**

```bash
mlperf-sysinfo check -c sysinfo.yaml
```

<details>
<summary>Sample output</summary>

```console
$ mlperf-sysinfo check -c sysinfo.yaml

  profile    endpoints
  output     results/sysinfo/system_desc.json

NODES
  ✓ this machine            included

SERVING
  ! http://127.0.0.1:8000   no answer   no serving framework answered

REQUIRED BY PROFILE 'ENDPOINTS'
  ✓ 5 fields set

WORTH FILLING IN
  ! system.cooling              empty   Reviewers ask how the nodes are cooled
  ! serving.node                empty   Enables parallelism and batch settings to be read from the startup log
  ! submission.notes.hardware   empty   Hardware detail no probe can report, such as interconnect topology or firmware
  ! submission.notes.software   empty   Software detail no probe can report, such as versions, flags or patches
  ! run.link_config             empty   Reviewers use it to reproduce the run

[2026-08-19 21:39:59] INFO     check: ready to capture
```

</details>

The warnings above are expected for a config with no live endpoint -- they
don't block anything. `capture` always runs this check first, with no flag
to skip it; a missing *required* field or an unreachable node is what would
stop the run.

**Step 4 -- capture**

```bash
mlperf-sysinfo capture -c sysinfo.yaml
```

<details>
<summary>Sample output</summary>

```console
$ mlperf-sysinfo capture -c sysinfo.yaml
[2026-08-19 21:42:12] INFO     collector: pre-flight check passed -- 1 node(s), profile endpoints
[2026-08-19 21:42:12] INFO     collector: collecting with tags: get-mlperf-multi-node-system-info,_cuda,_endpoints
[2026-08-19 21:42:18] INFO     collector: 1 of 1 node(s) returned hardware
[2026-08-19 21:42:18] INFO     collector: wrote results/sysinfo/system_desc.json
[2026-08-19 21:42:18] INFO     capture: 1 node(s) - 1 accelerators - profile endpoints
[2026-08-19 21:42:18] INFO     capture: run log results/sysinfo/.mlperf-sysinfo/capture_20260819_214212.log
[2026-08-19 21:42:18] INFO     capture: next: mlperf-sysinfo show results/sysinfo/system_desc.json
```

</details>

**Step 5 -- validate**

```bash
mlperf-sysinfo validate results/sysinfo/system_desc.json
```

<details>
<summary>Sample output</summary>

```console
$ mlperf-sysinfo validate results/sysinfo/system_desc.json

  file       results/sysinfo/system_desc.json
  profile    endpoints

WARNINGS
  ! cooling on every node type is empty -- Reviewers ask how the nodes are cooled
  ! hw_notes on every node type is empty -- Hardware detail no probe can report, such as interconnect topology or firmware
  ! sw_notes on every node type is empty -- Software detail no probe can report, such as versions, flags or patches
  ! link_config is empty -- Reviewers use it to reproduce the run

[2026-08-19 21:42:30] INFO     report: valid -- 5 required field(s) present, 4 warning(s)
```

</details>

## The config

Grouped by who owns the answer, not by which environment variable it sets.

```yaml
profile: endpoints

output:
  dir: results/h100_run1

system:
  name: H100x8_vLLM
  shortened_name: H100x8
  availability: available
  accelerator: cuda

nodes:
  include_local: false
  ssh:
    - root@node1
    - root@node2:2222

serving:
  url: http://node1:8000
  node: root@node1
  log: /tmp/serving.log

submission:
  division: standardized
  notes:
    hardware: ""
    software: ""

run:
  link_config: https://github.com/myorg/submission/tree/main/configs
```

Three rules worth knowing:

- **Anything detectable is never a config field.** CPU, memory, accelerators,
  node counts and framework version are probed. If they appear here at all,
  they are overrides.
- **`${VAR}` reads from the environment.** BMC and API credentials are
  referenced, never written down.
- **The same file works embedded.** Drop it under a `system_info:` key in a
  benchmark config and it validates identically.

Use `extends: ~/.mlperf/org.yaml` to share defaults across configs.

Model, dataset and concurrency details are deliberately absent: for Endpoints
they are measurement point metadata (endpoints rules 8.3) and belong in each
point's `points/<point>/config.yml`, which this tool does not write.

## Profiles

A profile says which fields are required, which extra collection steps to run,
and what the output looks like.

| Profile | Output | Notes |
| --- | --- | --- |
| `endpoints` | grouped, keeps `node_types` | Probes the endpoint and parses the serving log |
| `inference` | flat, matches the Inference submission checker | Hardware lifted to the top level |
| `training` | flat, matches `mlperf_logging/system_desc_checker` | A different checker from `inference`, with a different field set. Named `<system_name>.json` |

`mlperf-sysinfo profiles` lists them. All profiles live in this repo and ship
with the package. A config may also point at a file --
`profile: ./my-profile.yaml` -- for building one before it is upstreamed.

Profiles always track the current MLPerf round; there is no version to pin. The
round that applied is stamped into every output file, so a captured file
records the rules that produced it.

## Embedding

```python
from mlperf_sysinfo import load_config, capture

config = load_config("sysinfo.yaml")
result = capture(config)

result.output_path   # where the file landed
result.nodes         # per-node status
result.complete      # False if any node did not answer
```

`capture()` raises `CheckFailed` when pre-flight does not pass. Call
`mlperf_sysinfo.check(config)` on its own to run the same validation early --
at the start of a benchmark rather than at the end of one.

## Exit codes

`check` is meant to be scriptable, so the codes are a contract:

| Code | Meaning |
| --- | --- |
| 0 | All good |
| 1 | The run found problems (missing fields, unreachable nodes, invalid file) |
| 2 | The command or the config was wrong |

## Where files land

The deliverable is the only thing written to `output.dir`. Everything the
automation produces -- the raw intermediate, per-node files, and the run log
-- goes into `output.dir/.mlperf-sysinfo/`. The run log is named after the time
the capture started (`capture_20260819_202120.log`), carries a header saying
which profile, config and nodes produced it, and closes with the outcome.

Between those it holds both the automation's output and this tool's own actions,
each line with a date, a level and the module that acted:

```text
[2026-08-19 20:21:20] INFO     preflight: 5 of 5 required field(s) set for profile endpoints
[2026-08-19 20:21:20] INFO     collector: collecting with tags: ...,_cuda,_endpoints
[2026-08-19 20:21:27] INFO     collector: 1 of 1 node(s) returned hardware
```

The file keeps every level. On the terminal, output splits by shape: sentence-
shaped output (`init`, `capture`'s status lines, every command's verdict) *is*
log records, while the aligned tables (`check`, `show`, `validate`, `profiles`)
keep their columns. Log lines go to stderr, so `2>trace.log` separates the two.
`--log-level debug|info|warning|error` sets what shows; `--verbose` means
`debug`.

Two caveats worth knowing:

- **Credentials can be echoed by the collection layer.** Keeping them in
  `${VAR}` keeps them out of your config file and out of git, but the
  underlying automation prints its own command lines, so a Redfish password may
  appear in the run log. Treat that directory as sensitive.
- **Remote scratch files are not confined.** When collecting over SSH, the
  automation writes its own temporary files on each remote node (under `$HOME`
  and `/tmp` there). This package cannot redirect those.

## Partial captures

An unreachable node stops the run. `--allow-partial` proceeds anyway, and the
output records `complete: false` along with how many nodes answered.
`validate` then refuses the file, so a partial capture cannot be mistaken for a
whole one.

## Development

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest
uv run ruff check .
```

## License

Apache-2.0
