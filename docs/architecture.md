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
    C -->|grouped intermediate| A
    A --> D["system_desc.json<br/>shaped by profile"]
```

Before this package existed, the shaping lived at the end of a single shared
automation script and was selected by a tag. Every new working group meant
another branch in code everyone else depended on. Now the automation always
returns the same grouped intermediate, and the profile decides the rest.

!!! note "The automation is called unmodified"
    No benchmark variation is passed on purpose, so the automation returns its
    grouped intermediate rather than pre-shaping anything. `mlc-scripts` needs
    no changes to support a new working group.

## Module map

| Module | Responsibility |
| --- | --- |
| `config.py` | The config schema, `extends` merging, `${VAR}` interpolation, placeholder detection |
| `profiles/` | What each working group requires, collects, and writes. YAML, no Python |
| `preflight.py` | Validation and reachability — the `check` command's engine |
| `collector.py` | Invokes the automation, verifies what came back, orchestrates the run |
| `output.py` | Shapes the intermediate into the profile's output; stamps provenance |
| `report.py` | Reads a captured file back — `show` and `validate` |
| `cli.py` / `ui.py` | The command line and its rendering |
| `errors.py` | Every deliberate failure is one of these |

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
    G --> H[read grouped intermediate]
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
    ├── raw-system-info.json    ← grouped intermediate from mlc-scripts
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
