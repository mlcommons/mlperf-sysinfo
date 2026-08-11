# Commands

Six commands. The important one is `check`.

| Command | What it does |
| --- | --- |
| [`init`](#init) | Write a starter config containing only the fields your profile needs |
| [`check`](#check) | Validate the config and reach every node it names. Nothing is collected |
| [`capture`](#capture) | Run the check, then collect and write the system description |
| [`show`](#show) | Print a readable summary of a captured file |
| [`validate`](#validate) | Check a captured file against a profile before submitting it |
| [`profiles`](#profiles) | List the built-in profiles |

## Exit codes

`check` is meant to be scriptable, so the codes are a contract. A mistyped flag
must never look like a failed check.

| Code | Meaning |
| --- | --- |
| `0` | All good |
| `1` | The run found problems — missing fields, unreachable nodes, an invalid file |
| `2` | The command or the config was wrong |

---

## `init`

```bash
mlperf-sysinfo init [PROFILE] [--path PATH] [--force]
```

`PROFILE` defaults to `endpoints`. `--path` defaults to `sysinfo.yaml` in the
current directory. Refuses to overwrite an existing file unless `--force` is
passed.

```console
$ mlperf-sysinfo init endpoints

  Written template sysinfo.yaml to path: /home/user/sysinfo.yaml

  profile: endpoints

  Edit the template sysinfo.yaml before running the actual capture command.
```

---

## `check`

```bash
mlperf-sysinfo check -c CONFIG [--offline] [--verbose]
```

Validates the config against its profile, then reaches every node, endpoint and
serving log it names — in parallel, with a 15 second timeout each. Nothing is
collected and nothing is written.

`--offline` validates the config without touching the network.

### A config that is ready

```console
$ mlperf-sysinfo check -c sysinfo.yaml

  profile    endpoints (v6.0 rules)
  output     results/h100_run1/system_desc.json

NODES
  ✓ this machine   included

REQUIRED BY PROFILE 'ENDPOINTS'
  ✓ 7 fields set

  Ready to capture.
```

### A config that is not

This is the starter config with placeholders left in. Note that `system.cooling`
and `submission.container_link` are not fields the profile requires — every
string is scanned, because a placeholder in *any* field still reaches the
submission file.

```console
$ mlperf-sysinfo check -c ph.yaml

  profile    endpoints (v6.0 rules)
  output     results/h100_run1/system_desc.json

NODES
  ✓ this machine   included

REQUIRED BY PROFILE 'ENDPOINTS'
  ✓ 7 fields set

STILL STARTER TEXT
  ✗ system.cooling               placeholder   'CHANGEME'
  ✗ system.type_detail           placeholder   'Insert system type detail here'
  ✗ submission.model.notes       placeholder   'Insert your organization name here'
  ✗ submission.notes.hardware    placeholder   'Insert your hardware notes here'
  ✗ submission.container_link    placeholder   '<your registry url>'

WORTH FILLING IN
  ! serving.url                  empty         Enables framework and version detection from the live endpoint
  ! serving.node                 empty         Enables parallelism and batch settings to be read from the startup log
  ! submission.model.precision   empty         Reviewers ask for this almost every round
  ! submission.dataset.name      empty         Identifies what the system was serving

  5 problems.
  Fill in the missing fields and run check again.
```

Exit code `1`. Anything under **worth filling in** is a warning and does not
block.

### An unreachable node

```console
NODES
  ✓ root@node1:22    reachable     NVIDIA H100 80GB HBM3 x 8
  ✗ root@node3:22    unreachable   ssh: connect to host node3 port 22: Connection timed out
  - this machine     excluded      nodes.include_local is false

  1 problem.
  Fix the unreachable nodes, or run capture --allow-partial to proceed without them.
```

---

## `capture`

```bash
mlperf-sysinfo capture -c CONFIG [--allow-partial] [--run-metadata PATH] [--verbose]
```

Runs the check first — always, with no way to skip it — then collects and
writes the output file.

```console
$ mlperf-sysinfo capture -c sysinfo.yaml

  ✓ pre-flight check     passed -- 1 node(s), profile endpoints (v6.0 rules)
  ✓ collection           1 of 1 node(s) returned hardware

  1 node(s) - 8 accelerators - profile endpoints

  Written  results/h100_run1/system_desc.json

  Next: mlperf-sysinfo show results/h100_run1/system_desc.json
```

### Options

| Option | Effect |
| --- | --- |
| `--allow-partial` | Proceed when a node is unreachable or does not report back. The output is marked partial. Never forgives a config problem, and never forgives zero nodes |
| `--run-metadata PATH` | A `run_metadata.json` to patch with serving-config values extracted from the server's startup log |
| `--verbose` | Show the automation's own log on the terminal instead of writing it to `.mlperf-sysinfo/automation.log` |

### When the check fails

The full check report is printed and nothing is collected:

```console
$ mlperf-sysinfo capture -c ph.yaml
  ...
  5 problems.
  Fill in the missing fields and run check again.

error  nothing was collected
```

### When collection comes back short

```console
error  only 1 of 2 node(s) returned hardware. See .../automation.log for which
       probe failed, or pass --allow-partial to write what was collected.
```

With `--allow-partial`:

```console
  ✓ pre-flight check     passed with warnings -- 1 node(s), profile endpoints (v6.0 rules)
  ✗ root@node3:22        skipped -- ssh: connection timed out
  ! collection           only 1 of 2 node(s) returned hardware

  Written (partial)  results/my_run/system_desc.json
  One or more nodes did not answer. The file records this.
```

Exit code `1`, and the file carries `"complete": false`.

---

## `show`

```bash
mlperf-sysinfo show PATH
```

Reads a captured file on its own — it carries the profile it was made under, so
no config is needed. The split between **detected** and **from your config** is
deliberate: it shows at a glance what the tool found versus what a person
asserted.

```console
$ mlperf-sysinfo show results/h100_run1/system_desc.json

  system       H100x8
  profile      inference (v6.0 rules)
  captured     2026-08-10T14:08:36+00:00
  size         8x NVIDIA H100 80GB HBM3

DETECTED
  cpu            Intel(R) Xeon(R) Platinum 8480+
  cores          112
  memory         2.2T
  accelerator    NVIDIA H100 80GB HBM3
  per node       8
  os             ubuntu 24.04
  software       CUDA 12.9, Driver 575.57.08

FROM YOUR CONFIG
  submitter      MyOrg
  contact        mlperf@myorg.example
  division       closed
  system type    datacenter
  status         available
```

A partial file is called out with a `state` line.

---

## `validate`

```bash
mlperf-sysinfo validate PATH [--profile NAME]
```

The last gate before submission. Checks required fields are present and
non-empty, sweeps the **whole document** for placeholder text — including
inside `node_types` — and refuses a partial capture.

```console
$ mlperf-sysinfo validate results/h100_run1/system_desc.json

  file       results/h100_run1/system_desc.json
  profile    inference (v6.0 rules)

  Valid. 6 required field(s) present.
```

```console
$ mlperf-sysinfo validate broken.json

  file       broken.json
  profile    endpoints (v6.0 rules)

PROBLEMS
  ✗ partial capture: 1 of 2 nodes answered. This file does not describe the whole system.
  ✗ submitter_org_names still holds placeholder text: 'CHANGEME'
  ✗ node_types[0].cooling still holds placeholder text: 'CHANGEME'

  3 problem(s). This file is not ready to submit.
```

`--profile` validates against a different profile than the one stamped in the
file — useful when checking whether a capture would satisfy another group's
rules.

---

## `profiles`

```console
$ mlperf-sysinfo profiles

  endpoints    MLPerf Endpoints  round 6.0
    Inference-serving endpoints. Keeps the multi-node structure in the output so
    heterogeneous and disaggregated systems stay legible.
    7 required field(s), writes nested system_desc.json

  inference    MLPerf Inference  round 6.0
    MLPerf Inference submissions. Writes the flat field set the submission checker
    expects, with node hardware lifted to the top level.
    6 required field(s), writes flat system_desc.json
```

## Colour

Output is coloured on a terminal and plain when piped. `NO_COLOR=1` and
`TERM=dumb` both suppress it.
