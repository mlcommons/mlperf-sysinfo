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

Publishing to PyPI is driven by GitHub Releases, and it is gated:

1. Bump `version` in `pyproject.toml` on `main`.
2. Draft a GitHub Release on a `v<version>` tag and publish it.
3. Approve the `pypi` environment in the Actions tab.

A draft release publishes nothing, and neither does a tag on its own. The
build re-runs the full test suite and fails if the release tag disagrees with
`pyproject.toml`, so the version on PyPI always matches a tag you can check
out. The sdist and wheel are attached to the release once the upload
succeeds.
