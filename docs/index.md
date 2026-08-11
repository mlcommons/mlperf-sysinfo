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

  Written template sysinfo.yaml to path: /home/user/sysinfo.yaml

  profile: endpoints

  Edit the template sysinfo.yaml before running the actual capture command.
```

</details>

### Step 2 -- edit `sysinfo.yaml`

Fill in the fields the profile requires: a name for the system, your
organisation, a contact email, and the model being served. `nodes.include_local`
is already `true` with an empty `ssh` list, so leave that section alone -- this
points at the machine you're on. For a CPU-only capture, set
`accelerator: none` and drop the `serving:` block entirely; both fields are
optional here, and skipping them is a warning, not an error.

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

### Step 3 -- check the config

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

  ✓ pre-flight check     passed -- 1 node(s), profile endpoints (v6.0 rules)
  ✓ collection           1 of 1 node(s) returned hardware

  1 node(s) - profile endpoints

  Written  results/sysinfo/system_desc.json

  Next: mlperf-sysinfo show results/sysinfo/system_desc.json
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
  profile    endpoints (v6.0 rules)

  Valid. 7 required field(s) present.
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
