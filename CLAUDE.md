# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv venv && uv pip install -e ".[dev]"   # set up a dev environment
uv run pytest -q                        # full test suite (network and mlc-scripts are always stubbed)
uv run pytest tests/test_config.py::TestLoad::test_loads_a_good_file  # a single test
uv run ruff check .                     # lint (line-length 100, py310 target)
uv run --extra docs mkdocs serve        # docs at http://127.0.0.1:8000, source in docs/
uv run --extra docs mkdocs build --strict  # what CI runs; fails on a broken link or a page missing from nav
```

CI (`.github/workflows/ci.yml`) runs ruff, pytest, a CLI smoke test (`init` →
`check --offline`), and a `uv build` + `twine check` across Python 3.10-3.13.
Releasing is tag-driven and gated behind a manually-approved GitHub
Environment (`git tag v<version> && git push origin v<version>`, matching
`pyproject.toml`'s version) — tagging alone publishes nothing.

## Architecture

**The one idea: `mlc-scripts` collects, this package composes.** Probing a
machine (SSH, CPU/memory/accelerator detection, Redfish, serving-log parsing)
is shared, unchanged automation, pinned to an exact pre-release
(`mlc-scripts==1.2.0a1`) that this package is tested against. Every decision
about what a working group must supply and what its output file looks like is
policy, and lives here instead. No benchmark variation is ever passed to the
automation — it always returns the same grouped intermediate, and a profile
decides how to shape it. Adding a working group should mean writing one YAML
profile, not touching the collection code.

### Module map

| Module | Responsibility |
| --- | --- |
| `config.py` | The config schema (pydantic), `extends` merging, `${VAR}` env interpolation, `CHANGEME`-style placeholder detection |
| `profiles/` | What each working group requires, collects, and writes — YAML files, no Python |
| `preflight.py` | Validation and reachability; the engine behind the `check` command |
| `collector.py` | Invokes the automation, verifies what actually came back, orchestrates a `capture` |
| `output.py` | Shapes the collected intermediate into the profile's output shape; stamps provenance |
| `report.py` | Reads a captured file back on its own — `show` and `validate` |
| `cli.py` / `ui.py` | The `mlperf-sysinfo` command line (cyclopts) and its terminal rendering |
| `errors.py` | Every deliberate failure is one of `SysinfoError`'s subclasses |

### Config and profiles

- `SysinfoConfig` (`config.py`) is the whole config file: `system`, `nodes`,
  `serving`, `power`, `submission`, plus `extends` for shared defaults and
  `${VAR}` for secrets that must never be written to disk.
- `SysinfoConfig.all_targets` is `nodes.ssh` plus `serving.node` (deduplicated)
  if it names a machine not already in that list — a node only mentioned as
  where the server runs is still part of the system and gets reached during
  `check` and collected during `capture`. Use this, not `nodes.ssh` or
  `nodes.targets` directly, anywhere the effective node list matters.
- A `Profile` (`profiles/`, one YAML file per working group) declares
  `requires` (dotted path → why; absence is a hard stop), `recommends`
  (absence is a warning), `collect` flags (which optional steps run), and
  `shape` (`nested` keeps `node_types` for heterogeneous/disaggregated
  systems; `flat` lifts hardware to the top level for the MLPerf Inference
  submission checker). Profiles always track the *current* MLPerf round —
  there is no pinning, so a profile change applies to everyone on the next
  release, which is why profile changes get careful review.

### Two kinds of problem, handled differently

This distinction is the core of `check`/`capture`'s design:

| Kind | Examples | Behaviour |
| --- | --- | --- |
| Config problem | Missing required field, `CHANGEME` left in, unset `${VAR}`, node-group counts exceeding configured nodes | Hard stop, no override |
| Reachability problem | A node that won't answer, fewer nodes returning hardware than asked for | Stops the run, but `--allow-partial` proceeds and marks the output partial |

`capture` always runs the check first (no flag skips it). Node counts are
taken from what the collected data actually contains, never from what was
asked for — a reachable node can still return nothing (unsupported OS, a
probe needing `sudo`), and trusting the request over the result is how a
capture would silently ship half a system. Zero nodes collected is always an
error, even with `--allow-partial`; no file gets written in that case.

### Output layout

Only the deliverable (`system_desc.json` by default) is written to
`output.dir`. Everything the automation itself produces — the raw grouped
intermediate, per-node files, its own log — goes into
`output.dir/.mlperf-sysinfo/` (`--verbose` prints that log to the terminal
instead). Redfish captures are deliverables in their own right and get lifted
back out into `output.dir`.

### Known limitations

- Credentials kept in `${VAR}` stay out of the config file and git, but the
  underlying automation prints its own command lines, so a Redfish password
  can still end up in `.mlperf-sysinfo/automation.log`.
- Remote scratch files from SSH collection are not confined — the automation
  writes its own temp files under `$HOME`/`/tmp` on each remote node, and this
  package's environment doesn't propagate over that SSH leg to redirect them.
