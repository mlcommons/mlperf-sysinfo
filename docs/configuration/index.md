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
produces goes into `dir/.mlperf-sysinfo/`. See
[Architecture](../architecture.md#where-files-land).

## `remote` — what the nodes are left holding

Collecting from an SSH node means installing mlcflow on it and leaving an MLC
tree behind. Unset, both land in the remote user's `$HOME` — the virtualenv at
`~/mlcflow`, the cache under `~/MLC` — and stay there afterwards, so the next
run reuses them. On your own hardware that is exactly what you want. On a
shared login node, on a home directory with a quota, or anywhere a cached
answer could outlive the hardware it describes, it is not.

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `isolated` | bool | `false` | Fresh throwaway MLC tree per run, deleted on the way out |
| `isolated_base_dir` | path **on the node** | `/tmp` | Where that tree goes. Must already exist |
| `python_venv` | path **on the node** | `~/mlcflow` | Where the virtualenv goes. Applies with or without isolation |

```yaml
remote:
  isolated: true
  isolated_base_dir: /data/scratch
  python_venv: /data/scratch/mlcflow-venv
```

Both paths are remote paths, sent exactly as written and never resolved
against this machine. Give absolute ones — a `~` or a relative path is
interpreted by whatever shell the node hands us, which is not something to
guess at.

`isolated_base_dir` must exist on every node already. mlcflow fails the node
rather than creating it, so a typo stops the run instead of quietly writing
somewhere else.

!!! note "`isolated_base_dir` on its own is refused"
    mlcflow only reads the base directory when it is actually doing an
    isolated run, so setting one without `isolated: true` is not milder
    isolation — it is a line that does nothing:

    ```console
    $ mlperf-sysinfo check -c sysinfo.yaml
    error  sysinfo.yaml: config is not valid
      remote: Value error, remote.isolated_base_dir only applies to an isolated
        run, and remote.isolated is false. Set remote.isolated: true, or use
        remote.python_venv to move just the virtualenv.
    ```

    `python_venv` has no such restriction. It is the setting to reach for when
    all you need is the virtualenv off a full home directory.

### What isolation costs, and what it still leaves

Nothing is reused between runs, so every capture reinstalls mlcflow on every
node. Expect a slower run in exchange for a node that ends as it started, and
for a capture that cannot be answered by a stale cache entry.

It is not yet complete. mlcflow stages the collected files through
`~/mlc-remote-artifacts` on each node, and that directory is outside the tree
the isolated run cleans up — so an isolated run still leaves a small amount
behind in `$HOME`. Tracked upstream; the MLC tree and the virtualenv, which
are the large ones, do go where you point them.

These settings apply to the SSH nodes only. The machine running the command
writes where `output.dir` says, isolated or not.

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

The last one is why deleting the final entry under `nodes.ssh`, or under `run:`,
is not an error.

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

Every string is scanned, not only the fields your profile requires, because
starter text in any field still reaches the submission file. The check report calls
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
