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

Every command documents its own options, which is the quickest way to check one
without leaving the terminal:

```bash
mlperf-sysinfo --help            # the commands
mlperf-sysinfo capture --help    # what capture takes
```

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
mlperf-sysinfo check -c CONFIG [--offline] [--verbose] [--log-level LEVEL]
```

Validates the config against its profile, then reaches every node, endpoint and
serving log it names — in parallel, with a 15 second timeout each. Nothing is
collected and nothing is written.

`--offline` validates the config without touching the network.

### A config that is ready

```console
$ mlperf-sysinfo check -c sysinfo.yaml

  profile    endpoints
  output     results/h100_run1/system_desc.json

NODES
  ✓ this machine   included

REQUIRED BY PROFILE 'ENDPOINTS'
  ✓ 5 fields set

  Ready to capture.
```

### A config that is not

This is the starter config with placeholders left in. Note that
`submission.container_link` is not a field the profile requires — every string
is scanned, because a placeholder in *any* field still reaches the submission
file.

```console
$ mlperf-sysinfo check -c ph.yaml

  profile    endpoints
  output     results/h100_run1/system_desc.json

NODES
  ✓ this machine   included

REQUIRED BY PROFILE 'ENDPOINTS'
  ✓ 4 fields set
  ✗ system.name                  placeholder   still the starter value -- Identifier for the system under test

STILL STARTER TEXT
  ✗ system.cooling               placeholder   'CHANGEME'
  ✗ submission.notes.hardware    placeholder   'Insert your hardware notes here'
  ✗ submission.container_link    placeholder   '<your registry url>'

WORTH FILLING IN
  ! serving.node                 empty         Enables parallelism and batch settings to be read from the startup log
  ! submission.notes.software    empty         Becomes sw_notes on every node type
  ! run.link_config              empty         Reviewers use it to reproduce the run

  4 problems.
  Fill in the missing fields and run check again.
```

Exit code `1`. **Worth filling in** lists fields that are *empty* and only
warns. Starter text is never a warning, wherever it turns up: `system.cooling`
is only a recommendation, but `CHANGEME` in it would still be written to the
file looking like an answer.

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
                       [--log-level LEVEL]
```

Runs the check first — always, with no way to skip it — then collects and
writes the output file.

```console
$ mlperf-sysinfo capture -c sysinfo.yaml

  ✓ pre-flight check     passed -- 1 node(s), profile endpoints
  ✓ collection           1 of 1 node(s) returned hardware

  1 node(s) - 8 accelerators - profile endpoints

  Written  results/h100_run1/system_desc.json
  log      results/h100_run1/.mlperf-sysinfo/capture_20260819_202120.log

  Next: mlperf-sysinfo show results/h100_run1/system_desc.json
```

Every run leaves a log beside the deliverable, timestamped so a retry does not
overwrite the log of the failure that prompted it. It records which profile,
config and nodes produced the run, stamps each line the automation emits, and
closes with the outcome -- see
[the run log](architecture.md#the-run-log).

### Options

| Option | Effect |
| --- | --- |
| `--allow-partial` | Proceed when a node is unreachable or does not report back. The output is marked partial. Never forgives a config problem, and never forgives zero nodes |
| `--run-metadata PATH` | A `run_metadata.json` to patch with serving-config values extracted from the server's startup log |
| `--verbose` | Watch the automation's own output on the terminal as well, and show this package's own log down to `debug`. The run log is written either way |
| `--log-level` | `debug`, `info`, `warning` or `error` — what of this package's own log reaches the terminal. The run log file always keeps every level |

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
error  only 1 of 2 node(s) returned hardware. See .../capture_20260819_202120.log
       for which probe failed, or pass --allow-partial to write what was collected.
```

With `--allow-partial`:

```console
  ✓ pre-flight check     passed with warnings -- 1 node(s), profile endpoints
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
  profile      endpoints
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
  division       standardized
  category       datacenter
  availability   available
  endpoint       http://node1:8000
  run config     TP 8
```

For a flat (`inference`) capture the same block shows `submitter`, `contact`,
`division`, `system type` and `status`.

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
  profile    inference

  Valid. 6 required field(s) present.
```

```console
$ mlperf-sysinfo validate broken.json

  file       broken.json
  profile    endpoints

PROBLEMS
  ✗ partial capture: 1 of 2 nodes answered. This file does not describe the whole system.
  ✗ endpoint_url is empty -- The endpoint under test, written to the submission as endpoint_url
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

  endpoints    MLPerf Endpoints
    Inference-serving endpoints. Writes the rules 8.2 field set, keeping
    node_types so heterogeneous and disaggregated systems stay legible.
    5 required field(s), writes nested system_desc.json

  inference    MLPerf Inference
    MLPerf Inference submissions. Writes the flat field set the submission checker
    expects, with node hardware lifted to the top level.
    6 required field(s), writes flat system_desc.json
```

## Misspelled commands and flags

A command line that does not parse points at the help page that would have
answered the question:

```console
$ mlperf-sysinfo show
error  Command "show" parameter --path requires an argument.
  Run 'mlperf-sysinfo show --help' for the arguments it takes.
```

It also names the closest real command, flag or option rather than only listing
what exists:

```console
$ mlperf-sysinfo capure
error  Unknown command "capure". Did you mean "capture"? Available commands: init, check, capture, show, validate, profiles.

$ mlperf-sysinfo check --ofline -c sysinfo.yaml
error  Unknown option: --ofline. Did you mean --offline?
```

A leading token that looks like a flag is matched against the flags rather than
the commands, since `-v` is a stab at `--version`, not at `validate`:

```console
$ mlperf-sysinfo -v
error  "-v" is not a command or a top-level flag. Did you mean "--version"?
  "--verbose" exists, but only on a command -- e.g. mlperf-sysinfo check -v
  Run 'mlperf-sysinfo --help' to see the commands.
```

`-v` is deliberately not an alias for `--version`: it is already `--verbose` on
`check` and `capture`, and one letter meaning two things is worse than being
asked which you wanted.

Misspelled config options get the same treatment — see
[Configuration](configuration.md#misspelled-options).

## Logging

Alongside the styled blocks, the tool logs its own actions — config load,
`extends` merging, each probe, what came back, what was written — with a date, a
level and the module that did it:

```console
$ mlperf-sysinfo check -c sysinfo.yaml --log-level info
[2026-08-19 20:47:33] INFO     config: loaded config sysinfo.yaml (profile endpoints)
[2026-08-19 20:47:33] INFO     preflight: 5 of 5 required field(s) set for profile endpoints
[2026-08-19 20:47:33] INFO     preflight: endpoint http://127.0.0.1:8000: no answer

  profile    endpoints
  ...
```

**The run log file always keeps every level**, whatever the terminal is set to —
see [the run log](architecture.md#the-run-log). The terminal default is `error`,
which in practice means nothing extra: every warning a check produces is
already a styled row, and printing it as a log line as well says the same thing
twice in two formats a few lines apart. Ask for `info` or `debug` when you want
the trace live.

These lines go to **stderr**, so redirecting the styled report keeps the trace
out of it:

```bash
mlperf-sysinfo capture -c sysinfo.yaml --log-level info 2>trace.log
```

## Colour

Output is coloured on a terminal and plain when piped. `NO_COLOR=1` and
`TERM=dumb` both suppress it. Log lines are coloured by level — the level word
only, so it does not compete with the styled blocks.
