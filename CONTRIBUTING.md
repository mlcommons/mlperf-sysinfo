# Contributing

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest -q
uv run ruff check .
uv run --extra docs mkdocs serve   # docs at http://127.0.0.1:8000
```

## Adding a profile

A profile is one YAML file in `src/mlperf_sysinfo/profiles/`. It declares which
config fields are required, which collection steps to run, and what shape the
output takes. Adding one needs no Python.

```yaml
name: my-group
title: My Working Group
round: "6.0"
shape: nested          # nested | flat
collect:
  serving_log: false
  endpoint_probe: false
  redfish: false
requires:
  system.name: Identifier for the system under test
recommends:
  system.accelerator: Without it, accelerators are not probed
```

Add a matching starter config in `src/mlperf_sysinfo/templates/` so
`mlperf-sysinfo init my-group` works, and a test asserting the profile loads
and shapes output the way your group expects.

Profiles track the current MLPerf round. When requirements change, edit the
profile and bump `round`. There is no pinning, so a change applies to everyone
on the next release -- which is why profile changes get a careful review.

## Releasing

Releases are gated. Tag `v<version>` matching `pyproject.toml`, push it, then
approve the environment in the Actions tab. Tagging alone publishes nothing.
