# Profiles

A profile answers three questions:

1. Which config fields must be filled in?
2. Which extra collection steps should run?
3. What should the output file look like?

That is the whole mechanism for supporting more than one working group. Adding a
group is one YAML file — no Python, and no changes to shared collection code.

## Built-in profiles

| Profile | Requires | Also collects | Writes |
| --- | --- | --- | --- |
| `endpoints` | System name, category, availability, submitter, contact, division, model name | Serving config from the server's startup log; framework version from the live endpoint | Grouped, with `node_types` |
| `inference` | System name, category, availability, submitter, contact, division | — | Flat, matching the submission checker |

Both allow Redfish power capture when `power.redfish` is configured.

```console
$ mlperf-sysinfo profiles

  endpoints    MLPerf Endpoints  round 6.0
    7 required field(s), writes nested system_desc.json

  inference    MLPerf Inference  round 6.0
    6 required field(s), writes flat system_desc.json
```

## Where profiles live

All profiles live in this repository, under `src/mlperf_sysinfo/profiles/`, and
ship with the package. One place to look, one place to change — and because a
change to a profile is a change to what a submission requires, it gets reviewed
like any other change to the tool.

A config may also point at a file for a profile that has not been upstreamed
yet:

```yaml
profile: ./profiles/my-group.yaml
```

Relative paths resolve against the config file. Use this while developing a
profile, then open a PR to move it in.

## Rounds

Required fields change between MLPerf rounds, so a profile is not one fixed
thing. `profile: endpoints` always means the rules for the **current** round.
There is no version to choose and nothing extra to maintain in a config.

```console
$ mlperf-sysinfo check -c pinned.yaml
error  profile 'endpoints@v6.0' pins a version. Profiles always track the current
       round -- use 'profile: endpoints' instead (this run would have used round 'v6.0').
```

The resolved round is stamped into every output file, so a captured file still
records the rules that produced it. If reproducing an older round ever becomes a
real need, pinning can be added later without invalidating any existing config.

## Writing one

```yaml
# src/mlperf_sysinfo/profiles/my-group.yaml
name: my-group
title: My Working Group
round: "6.0"
description: >-
  One sentence on what this group submits and why the output looks like it does.

output_file: system_desc.json
shape: nested            # nested | flat

collect:
  serving_log: false     # SSH to serving.node and parse the startup log
  endpoint_probe: false  # HTTP-probe serving.url for framework and version
  redfish: true          # allow BMC capture when power.redfish is configured

requires:                # absence stops a run
  system.name: Identifier for the system under test
  submission.submitter: Organisation making the submission

recommends:              # absence is a warning
  system.accelerator: Without it, accelerators are not probed at all

extra_field_groups: []   # power | network -- adds the checker's extra blank fields
```

### Fields

| Key | Notes |
| --- | --- |
| `name` | Must match the filename |
| `title`, `description` | Shown by `mlperf-sysinfo profiles` |
| `round` | The MLPerf round these requirements describe. Stamped into output |
| `shape` | `nested` keeps `node_types`; `flat` lifts hardware to the top level |
| `output_file` | Default filename, overridable by `output.file` |
| `collect.*` | Which optional collection steps to run |
| `requires` | Dotted config path → why it is needed. Shown verbatim when missing |
| `recommends` | Same, but only warns |
| `extra_field_groups` | `power` and/or `network` blank field blocks, flat shape only |

!!! tip "Write the `why` for a human"
    The text beside each required field is printed straight into the check
    report when the field is missing. `"Contact email for questions about this
    submission"` is useful; `"required"` is not.

### Checklist for a new profile

1. Add `src/mlperf_sysinfo/profiles/<name>.yaml`.
2. Add `src/mlperf_sysinfo/templates/<name>.yaml` so `init <name>` works. Put
   `CHANGEME` in every required field — the checker will catch it, which is
   exactly the behaviour you want to demonstrate.
3. Add a test that the profile loads and shapes output the way your group
   expects.
4. Open a PR. Changing what a submission requires deserves a review.

## Choosing a shape

Pick `nested` when node structure carries meaning your group cares about —
heterogeneous clusters, or disaggregated prefill/decode splits, where collapsing
to one row would lose information.

Pick `flat` when a downstream tool expects a fixed field list, as the MLPerf
Inference submission checker does.

If a group needs field names neither shape provides, that is a change to
`output.py` rather than a profile — open an issue first.
