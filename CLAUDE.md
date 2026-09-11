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
Releasing (`.github/workflows/release.yml`) is driven by *published* GitHub
Releases: publishing a release on a `v<version>` tag that matches
`pyproject.toml` runs the tests, then uploads to PyPI once the `pypi`
Environment is manually approved. A draft release or a bare tag publishes
nothing.

## Architecture

**The one idea: `mlc-scripts` collects, this package composes.** Probing a
machine (SSH, CPU/memory/accelerator detection, Redfish, serving-log parsing)
is shared, unchanged automation, pinned to an exact pre-release
(`mlc-scripts==1.2.0a5`) that this package is tested against. Every decision
about what a working group must supply is policy, and lives here instead.

The automation assembles a *different field set per benchmark*, so the
profile's `benchmark` names which one to ask for — `_endpoints` keeps only the
fields in endpoints rules 8.2, `_inference` keeps what the Inference
submission checker wants, `_training` keeps what
`mlperf_logging/system_desc_checker` wants, and none of them are
interchangeable. Sending no variation silently gets the endpoints field set,
which is how a `profile: inference` capture once shipped with no accelerator
in it. `build_training` refuses a document that still has `node_types` for
exactly this reason: an mlc-scripts without `_training` returns the endpoints
set, and shaping it would write every hardware field empty. What comes back is
the probed truth about the hardware; `output.py` puts it in the published
template's order and writes every config-supplied value over whatever the
automation defaulted, because those defaults are placeholder strings
(`"Insert system category here"`) that must never reach a deliverable.

Adding a working group that can use an existing field set is still one YAML
profile and no collection change.

### Module map

| Module | Responsibility |
| --- | --- |
| `config.py` | The config schema (pydantic), `extends` merging, `${VAR}` env interpolation, `CHANGEME`-style placeholder detection |
| `profiles/` | What each working group requires, collects, and writes — YAML files, no Python |
| `preflight.py` | Validation and reachability; the engine behind the `check` command |
| `collector.py` | Invokes the automation, verifies what actually came back, orchestrates a `capture` |
| `output.py` | Orders the collected field set, overlays config metadata, stamps provenance |
| `report.py` | Reads a captured file back on its own — `show` and `validate` |
| `cli.py` / `ui.py` | The `mlperf-sysinfo` command line (cyclopts) and its terminal rendering |
| `errors.py` | Every deliberate failure is one of `SysinfoError`'s subclasses |
| `runlog.py` | The per-run log file: header, one stamp per line, footer |
| `logs.py` | Leveled logging of this package's own actions, to the terminal and into the run log |
| `suggest.py` | "Did you mean ...?" for config options, profile names and flags |

### Config and profiles

- `SysinfoConfig` (`config.py`) is the whole config file: `system`, `nodes`,
  `remote`, `serving`, `power`, `submission`, `run`, plus `extends` for shared defaults
  and `${VAR}` for secrets that must never be written to disk. A section or list
  whose entries are all commented out parses as `None`; `config.drop_empty_sections`
  turns it into an empty container, driven by the schema so that an unset
  *scalar* (`cooling:`) and an optional model (`power.redfish:`) stay `None` —
  those mean "unset" and "not configured", not "empty".
- Model, dataset and concurrency details are **not** config fields. For
  Endpoints they are measurement point metadata (rules 8.3,
  `points/<point>/config.yml`), which this tool does not write.
- An unrecognised option, profile name or leading flag is matched against the
  real names by `suggest.py`, which ignores case and treats `-`/`_` alike and
  accepts a prefix (so `-v` reaches `--version` and `sshkey` reaches
  `ssh_key_preconfigured`). Options are matched against the vocabulary valid
  *at that path*, never the whole schema. Nothing close enough means no
  suggestion: a wrong guess sends someone off after a field they never wanted.
- `SysinfoConfig.all_targets` is `nodes.ssh` plus `serving.node` (deduplicated)
  if it names a machine not already in that list — a node only mentioned as
  where the server runs is still part of the system and gets reached during
  `check` and collected during `capture`. Use this, not `nodes.ssh` or
  `nodes.targets` directly, anywhere the effective node list matters.
- A `Profile` (`profiles/`, one YAML file per working group) declares
  `requires` (dotted path → why; absence is a hard stop), `recommends`
  (absence is a warning), `collect` flags (which optional steps run), and
  `shape` (`nested` keeps `node_types` for heterogeneous/disaggregated
  systems; `flat` lifts hardware to the top level), and `benchmark` (which
  field set to collect). `shape` alone stopped identifying a field set once
  `inference` and `training` were both flat, so `output_key_for` and
  `output.shape` dispatch on `benchmark` and the provenance block stamps it.
  Profiles
  always track the *current* MLPerf round — there is no pinning, so a profile
  change applies to everyone on the next release, which is why profile changes
  get careful review. `round` is stamped into the output file but never printed
  to the terminal: with nothing to choose, showing it on every line is noise
  that also implies there is a choice. `round` is optional — `training` sets
  none, because MLPerf Training numbers its rulesets independently, and
  `profile_round` is then omitted rather than written empty.

### Three checkers, not one

`inference` and `training` are both flat and are validated by *different*
checkers — the Inference `submission_checker` and
`mlperf_logging/system_desc_checker` in mlcommons/logging. They drift
independently, so verify a training change against that checker rather than by
analogy to `_FLAT_KEYS`. Training's own rules, all of which have bitten:
every value is a string (`"8"`, not `8`); a detection-failure marker becomes
`""`, since "Not detected: ..." in a submission field reads as a real answer;
`status` takes exactly four strings, and bare `available` is refused rather
than guessed at because training splits it into on-premise and cloud; and the
field set is *closed*, so `build_training` composes the document rather than
passing the automation's through the way `build_flat` does.

### Logging

**Output splits by shape, not by command.** This is the rule to preserve.

- **Sentence-shaped output is log records** (`logs.py`), so it carries a date, a
  level and the name of whatever acted: `init`, `capture`'s status lines, and
  every command's verdict (`check: ready to capture`, `report: 3 problem(s)`).
- **Aligned tables stay tables** (`ui.py`): the check report, `show`'s summary,
  `validate`'s problem list, `profiles`. A 31-character prefix on every row
  costs the columns their scannability, which is what a table is for. So `check`
  is a record, then a table, then a record.
- **Nothing is said twice.** Where a table renders a fact, the record is still
  made -- the run log needs it, and a library caller has `run_check()` and no
  report -- but it is marked `extra=logs.STYLED` and the terminal handler drops
  it. `--log-level debug` shows those too.
- **Facts belong to whoever knows them.** `collector` logs the run's facts so
  they exist with no CLI present; `cli` logs only what the collector cannot know
  (its summary line, where the run log went, what to run next). The `progress`
  callback stays for embedders and is logged at DEBUG, since every event it
  carries is already logged by the collector under its own name.
- **Levels are chosen for severity, not for the CLI.** An unreachable node is a
  WARNING because a library caller has no styled report to read it from.
- **The run log file always takes every level**, whatever the terminal shows.
  Records logged before the file exists are buffered and replayed into it.
- `setup()` sets `propagate = False`, so an embedder that never calls it still
  sees these records through its own root handlers -- but when this package owns
  the terminal, nothing is printed twice.
- Terminal default is `info`. `--log-level` defaults to `None` rather than to
  `"info"` so an explicit `--log-level info` stays distinguishable from an
  absent one; otherwise `--verbose` would silently override it.

### Two kinds of problem, handled differently

This distinction is the core of `check`/`capture`'s design:

| Kind | Examples | Behaviour |
| --- | --- | --- |
| Config problem | Missing required field, `CHANGEME` left in *anywhere* — required, recommended or unmentioned — unset `${VAR}`, node-group counts exceeding configured nodes | Hard stop, no override |
| Reachability problem | A node that won't answer, fewer nodes returning hardware than asked for | Stops the run, but `--allow-partial` proceeds and marks the output partial |

`capture` always runs the check first (no flag skips it). Node counts are
taken from what the collected data actually contains, never from what was
asked for — a reachable node can still return nothing (unsupported OS, a
probe needing `sudo`), and trusting the request over the result is how a
capture would silently ship half a system. Zero nodes collected is always an
error, even with `--allow-partial`; no file gets written in that case.

### Output layout

Only the deliverable (`system_desc.json` by default) is written to
`output.dir`. Everything the automation itself produces — the field set it
returned, per-node files, its own log — goes into
`output.dir/.mlperf-sysinfo/` (`--verbose` prints that log to the terminal
instead). Redfish captures are deliverables in their own right and get lifted
back out into `output.dir`.

### Known limitations

- Credentials kept in `${VAR}` stay out of the config file and git, but the
  underlying automation prints its own command lines, so a Redfish password
  can still end up in the run log under `.mlperf-sysinfo/`.
- Remote scratch is only *mostly* confinable. `remote:` forwards mlcflow's
  `remote_isolated`, `remote_isolated_base_dir` and `remote_python_venv`, which
  move the virtualenv and the MLC tree — the two large ones — and delete the
  tree afterwards. mlcflow still stages collected files through
  `~/mlc-remote-artifacts` on each node, outside what the isolated run cleans
  up, so an isolated run is not yet a run that leaves nothing behind.
- A node the automation cannot reach fails the whole collection as of
  mlc-scripts 1.2.0a5, by design upstream. `--allow-partial` therefore drops
  unreachable nodes from `ssh_ids` (and drops `serving_node` when that is the
  one that is down) rather than sending them and forgiving the result: there
  is no result to forgive. `nodes_expected` deliberately stays at what the
  config asked for, so the capture still reports itself as partial.
