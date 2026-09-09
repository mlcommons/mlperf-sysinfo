# The config file

The config file serves as the input to the mlperf-sysinfo tool. The fields inside it determines which fields get
collected, execution path of the tool , and what shape the output file takes(As determined by the particular benchmark rules).

## Which profile

| Profile | For |
| --- | --- |
| [`endpoints`](endpoints.md) | MLPerf Endpoints submissions |
| [`inference`](inference.md) | MLPerf Inference submissions |
| [`training`](training.md) | MLPerf Training submissions |

`profile:` defaults to `endpoints`.

## Getting one

`init` writes a commented starter config for the profile you name, containing
only the fields that profile reads:

```bash
mlperf-sysinfo init endpoints     # writes sysinfo.yaml
mlperf-sysinfo check -c sysinfo.yaml
```

`check` names every field still missing or still holding starter text, and
reaches every machine the config mentions, before you spend a capture finding
out.

Every `CHANGEME` below is a field `check` will stop on.

??? example "What `init endpoints` writes"

    ```yaml
    --8<-- "src/mlperf_sysinfo/templates/endpoints.yaml"
    ```

??? example "What `init inference` writes"

    ```yaml
    --8<-- "src/mlperf_sysinfo/templates/inference.yaml"
    ```

??? example "What `init training` writes"

    ```yaml
    --8<-- "src/mlperf_sysinfo/templates/training.yaml"
    ```

## `output` — where the file lands

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `dir` | path | `.` | Relative paths resolve against the config file, not the cwd |
| `file` | string | profile's own | Output filename |

Only the deliverable is written to `dir`. Everything the collection layer
produces goes into `dir/.mlperf-sysinfo/` — see
[Architecture](../architecture.md#where-files-land).

## `${VAR}` — secrets stay out of the file

Any `${VAR}` anywhere in the config is replaced from the environment, including
inside lists. An unset variable is a config problem, reported against its path:

```console
  ✗ power.redfish.password -> ${BMC_PASSWORD}    unset    not found in the environment
```

## `extends` — share org defaults

```yaml
# ~/.mlperf/org.yaml
submission:
  division: standardized
run:
  link_config: https://github.com/myorg/submission/tree/main/configs
```

```yaml
# sysinfo.yaml
extends: ~/.mlperf/org.yaml
system:
  name: H100x8_vLLM
```

The child wins. Nested maps merge; lists replace wholesale. A chain that loops
back on itself stops at a depth limit rather than following it round forever:

```console
error  'extends' nested more than 8 deep -- is there a cycle?
```

## Empty, unset, and absent

Three states that look alike in YAML and are not the same thing:

| Written as | Means |
| --- | --- |
| `cooling: air` | Set |
| `cooling:` | **Unset.** Stays unset; nothing is guessed |
| `ssh:` with every entry commented out | **Absent.** Treated as if the key were not there |

The last one is why deleting the final entry under `nodes.ssh` — or under `run:`
— is not an error.

!!! note "Emptying `nodes.ssh` *is* an error when nothing else is left to look at"
    An absent `ssh` list is only fine while some other machine is still named.
    With `include_local: false` and no `serving.node` either, there is nothing
    to collect from, and the config is rejected before any node is contacted:

    ```console
    $ mlperf-sysinfo check -c sysinfo.yaml
    error  sysinfo.yaml: config is not valid
      (root): Value error, nothing to collect from: set nodes.include_local to true,
        list at least one target under nodes.ssh, or set serving.node
    ```

## Leftover starter text

A config still carrying starter text has not been filled in, and `check`
refuses it. Two patterns are recognised anywhere in the file:

- anything containing `changeme` (so `CHANGEME@example.com` counts)
- anything matching `Insert … here` or `<…>`

The `insert` rule is anchored on purpose, so genuine prose survives:

```yaml
notes:
  hardware: "insert card in slot 3 before boot"   # fine, not starter text
```

Every string is scanned, not only the fields your profile requires — starter
text in any field still reaches the submission file. The check report calls
these `placeholder`.

## Unknown and misspelled options

`check` reports an error for any option it does not recognise, and names a
real option where one is close enough:

```console
$ mlperf-sysinfo check -c sysinfo.yaml
error  sysinfo.yaml: config is not valid
  system.categry: unknown option. Did you mean "category"?
  nodes.include-local: unknown option. Did you mean "include_local"?
  submission.divison: unknown option. Did you mean "division"?
```

Matching ignores case, and treats `-` and `_` as the same character.
