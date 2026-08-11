# mlperf-sysinfo

A tool that automatically captures the system description of a benchmarking
machine, built on top of the
[mlc-scripts](https://github.com/mlcommons/mlperf-automations) and
[mlcflow](https://github.com/mlcommons/mlcflow) automation.

**Documentation: <https://anandhu-eng.github.io/mlperf-sysinfo/>**

```bash
pip install mlperf-sysinfo
```

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

  Written template sysinfo.yaml to path: /home/user/sysinfo.yaml

  profile: endpoints

  Edit the template sysinfo.yaml before running the actual capture command.
```

</details>

**Step 2 -- edit `sysinfo.yaml`**

Fill in a system name, your organisation, a contact email, and the model
being served. `nodes.include_local` is already `true` with an empty `ssh`
list, so leave that section alone. For a CPU-only capture, set
`accelerator: none` and drop the `serving:` block entirely -- both fields
are optional here, and skipping them is a warning, not an error.

```yaml
profile: endpoints

output:
  dir: results/sysinfo

system:
  name: devbox1
  category: datacenter
  availability: available
  accelerator: none

nodes:
  include_local: true
  ssh: []

submission:
  submitter: MyOrg
  contact: mlperf@myorg.example
  division: standardized
  model:
    name: Llama-3.1-8B-Instruct
```

**Step 3 -- check the config**

```bash
mlperf-sysinfo check -c sysinfo.yaml
```

<details>
<summary>Sample output</summary>

```console
$ mlperf-sysinfo check -c sysinfo.yaml

  profile    endpoints (v6.0 rules)
  output     results/sysinfo/system_desc.json

NODES
  ✓ this machine   included

REQUIRED BY PROFILE 'ENDPOINTS'
  ✓ 7 fields set

WORTH FILLING IN
  ! serving.url                  empty   Enables framework and version detection from the live endpoint
  ! serving.node                 empty   Enables parallelism and batch settings to be read from the startup log
  ! submission.model.precision   empty   Reviewers ask for this almost every round
  ! submission.dataset.name      empty   Identifies what the system was serving

  Ready to capture.
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

  ✓ pre-flight check     passed -- 1 node(s), profile endpoints (v6.0 rules)
  ✓ collection           1 of 1 node(s) returned hardware

  1 node(s) - profile endpoints

  Written  results/sysinfo/system_desc.json

  Next: mlperf-sysinfo show results/sysinfo/system_desc.json
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
  profile    endpoints (v6.0 rules)

  Valid. 7 required field(s) present.
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
  category: datacenter
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
  submitter: MyOrg
  contact: mlperf@myorg.example
  division: standardized
  model:
    name: Llama-3.1-8B-Instruct
    precision: fp8
```

Three rules worth knowing:

- **Anything detectable is never a config field.** CPU, memory, accelerators,
  node counts and framework version are probed. If they appear here at all,
  they are overrides.
- **`${VAR}` reads from the environment.** BMC and API credentials are
  referenced, never written down.
- **The same file works embedded.** Drop it under a `system_info:` key in a
  benchmark config and it validates identically.

Use `extends: ~/.mlperf/org.yaml` to share submitter details across configs.

## Profiles

A profile says which fields are required, which extra collection steps to run,
and what the output looks like.

| Profile | Output | Notes |
| --- | --- | --- |
| `endpoints` | grouped, keeps `node_types` | Probes the endpoint and parses the serving log |
| `inference` | flat, matches the submission checker | Hardware lifted to the top level |

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
automation produces -- the raw intermediate, per-node files, and its log --
goes into `output.dir/.mlperf-sysinfo/`. Pass `--verbose` to see the automation
log on the terminal instead of in that directory.

Two caveats worth knowing:

- **Credentials can be echoed by the collection layer.** Keeping them in
  `${VAR}` keeps them out of your config file and out of git, but the
  underlying automation prints its own command lines, so a Redfish password may
  appear in `automation.log`. Treat that directory as sensitive.
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
