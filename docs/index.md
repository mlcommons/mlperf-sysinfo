# mlperf-sysinfo

A tool that automatically captures the system description of a benchmarking
machine, built on top of the
[mlc-scripts](https://github.com/mlcommons/mlperf-automations) and
[mlcflow](https://github.com/mlcommons/mlcflow) automation.

---

## Install

```bash
pip install mlperf-sysinfo
```

`mlperf-sysinfo --help` lists the commands; `mlperf-sysinfo <command> --help` shows what one takes.

## Quick Run

The steps below capture a system description for a CPU-only system, using
only the machine you run them on. No other node is contacted.

### Step 1 -- create a starter config

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

### Step 2 -- edit `sysinfo.yaml`

Fill in the fields the profile requires: a name for the system, its category
and availability, the division, and the endpoint you served against.
`nodes.include_local` is already `true` with an empty `ssh` list, so leave that
section alone -- this points at the machine you're on. For a CPU-only capture,
set `accelerator: none` and drop `serving.node`; without it the parallelism and
batch settings are simply not read, which is a warning rather than an error.

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

serving:
  url: http://127.0.0.1:8000

submission:
  division: standardized
```

`serving.url` is required because it is written to the submission as
`endpoint_url`. `check` probes it, but nothing answering is only a warning --
the value is submission metadata, not a liveness test. Rules 8.2 also allow a
description in place of a URL, for a hosted endpoint with no public address:
`url: "Managed endpoint, us-east-1, no public URL"` is accepted and not probed.

!!! note "The model and dataset are not in this file"
    They are measurement point metadata (endpoints rules 8.3) and belong in
    each point's `points/<point>/config.yml`, which this tool does not write.

### Step 3 -- check the config

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
  ! submission.notes.hardware   empty   Becomes hw_notes on every node type
  ! submission.notes.software   empty   Becomes sw_notes on every node type
  ! run.link_config             empty   Reviewers use it to reproduce the run

[2026-08-19 21:39:59] INFO     check: ready to capture
```

</details>

The warnings above are expected for a config with no live endpoint -- they
don't block anything. `capture` always runs this check first, and there is no
flag to skip it; a missing *required* field or an unreachable node is what
would stop the run.

### Step 4 -- capture

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

### Step 5 -- validate

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
  ! hw_notes on every node type is empty -- Becomes hw_notes on every node type
  ! sw_notes on every node type is empty -- Becomes sw_notes on every node type
  ! link_config is empty -- Reviewers use it to reproduce the run

[2026-08-19 21:42:30] INFO     report: valid -- 5 required field(s) present, 4 warning(s)
```

</details>

## Three rules worth knowing

<div class="grid cards" markdown>

-   **Anything detectable is never a config field**

    ---

    CPU, memory, accelerators, node counts and framework version are probed. If
    they appear in your config at all, they are overrides.

-   **Required means required**

    ---

    A field a profile needs and cannot detect must be filled in. A config still
    saying `CHANGEME` is not filled in, and the checker says so.

-   **`${VAR}` reads from the environment**

    ---

    BMC and API credentials are referenced, never written into the file, never
    committed.

</div>

## Where to go next

| If you want to… | Read |
| --- | --- |
| Understand how the pieces fit together | [Architecture](architecture.md) |
| Write or fix a config | [The config file](configuration.md) |
| Look up a command or an exit code | [Commands](commands.md) |
| See what a real capture produces | [Sample outputs](outputs.md) |
| Onboard your working group | [Profiles](profiles.md) |
| Call it from your own tool | [Embedding it](embedding.md) |
