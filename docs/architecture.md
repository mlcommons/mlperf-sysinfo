# Architecture

## The one idea

**mlc-scripts collects. mlperf-sysinfo composes.**

Probing a machine is hard, platform-specific work that should stay shared and
unchanged across MLCommons. Deciding what a working group must supply, and what
the resulting file should look like, is policy — and policy differs per group,
so it belongs in a package groups can configure.

```mermaid
flowchart LR
    A["<b>mlperf-sysinfo</b><br/>composition"] -->|one call| B["<b>mlc-scripts</b><br/>collection"]
    B --> C["SSH to each node<br/>CPU / memory / accelerators<br/>network / OS<br/>Redfish, serving log"]
    C -->|probed field set| A
    A --> D["system_desc.json<br/>metadata and order<br/>from the profile"]
```

The split runs along one line: **the automation owns what the hardware is, this
package owns everything a person had to type.** The profile's `benchmark` names
which field set to gather — the Endpoints and Inference field sets are not the
same shape and are not interchangeable — and what comes back is treated as the
probed truth. What each submitter-supplied field *says* is decided here.

!!! warning "Why the metadata is not left to the automation"
    The automation's defaults for submitter-supplied fields are placeholder
    strings: `"Insert system category here"`. A placeholder in a submission
    file is worse than an empty one, because it reads as filled in. So every
    such field is written from the config or left genuinely empty, and
    `check` is what refuses to run when one that matters is empty.

!!! note "A profile still costs no automation change"
    Requirements, recommendations, field order, which optional probes run and
    what the file is called are all profile data. A new group that can use an
    existing field set is one YAML file.

## Module map

| Module | Responsibility |
| --- | --- |
| `config.py` | The config schema, `extends` merging, `${VAR}` interpolation, placeholder detection |
| `profiles/` | What each working group requires, collects, and writes. YAML, no Python |
| `preflight.py` | Validation and reachability — the `check` command's engine |
| `collector.py` | Invokes the automation, verifies what came back, orchestrates the run |
| `output.py` | Orders the collected field set, overlays config metadata, stamps provenance |
| `report.py` | Reads a captured file back — `show` and `validate` |
| `cli.py` / `ui.py` | The command line and its rendering |
| `errors.py` | Every deliberate failure is one of these |

The `mlc-scripts` dependency is pinned to an exact pre-release, `1.2.0a2`, which
is the version this package is tested against. `pip` installs it without any
flag because the specifier names the pre-release explicitly; `uv` needs
`prerelease = "allow"`, which `pyproject.toml` already sets.

## What a capture actually does

```mermaid
flowchart TD
    A[load config] --> B[apply extends, interpolate VAR]
    B --> C[load profile]
    C --> D{pre-flight}
    D -->|config problem| E[stop, no override]
    D -->|node unreachable| F{--allow-partial?}
    F -->|no| E
    F -->|yes| G
    D -->|all good| G[call mlc-scripts]
    G --> H[read the collected field set]
    H --> I{how many nodes<br/>came back?}
    I -->|zero| J[error, nothing written]
    I -->|fewer than asked| F
    I -->|all of them| K[shape by profile]
    K --> L[stamp provenance]
    L --> M[write system_desc.json]
```

### Two kinds of problem, handled differently

This distinction is the core of the design.

| Kind | Examples | Behaviour |
| --- | --- | --- |
| **Config problem** | Missing required field, `CHANGEME` left in, unset `${VAR}`, node group counts that exceed the nodes configured | Hard stop. **No override.** Cheap to fix, and they produce bad submissions |
| **Reachability problem** | A node that will not answer, fewer nodes returning hardware than were asked for | Stops the run, but `--allow-partial` proceeds and marks the output partial |

### Verifying what came back

A node can be perfectly reachable and still return nothing — an unsupported OS,
a probe that needs `sudo`. So the node count is taken from the collected data,
never from the config:

- **zero nodes** → always an error, even with `--allow-partial`. No file written.
- **fewer than asked for** → partial. Error unless `--allow-partial`.
- **all of them** → complete.

!!! warning "This was a real bug"
    The first cut trusted the request over the result and wrote
    `complete: true` for a capture that collected nothing. Believing the
    request is exactly how a capture silently ships half a system.

## Where files land

The deliverable is the only thing written to `output.dir`. Everything the
automation produces goes into a scratch directory beside it:

```text
results/my_run/
├── system_desc.json            ← the deliverable
└── .mlperf-sysinfo/
    ├── raw-system-info.json    ← the field set mlc-scripts returned
    ├── automation.log          ← the automation's own output
    └── mlperf-system-info-single-node-0.json
```

Redfish captures (`redfish_nameplate_power.yaml`, `redfish_capture.yaml`) are
deliverables in their own right and are lifted back out into `output.dir`.

Pass `--verbose` to see the automation log on the terminal instead of in the
file.

## Known limitations

!!! danger "Credentials can reach `automation.log`"
    Keeping secrets in `${VAR}` keeps them out of your config file and out of
    git, but the underlying automation prints its own command lines. A Redfish
    password may appear in the log. Treat `.mlperf-sysinfo/` as sensitive.

!!! danger "Remote scratch files are not confined"
    When collecting over SSH, the automation writes temporary files on each
    remote node, under `$HOME` and `/tmp` there. This package cannot redirect
    those, because its environment does not propagate over the automation's own
    SSH leg.
