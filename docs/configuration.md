# The config file

Grouped by **who owns the answer**, not by which environment variable it sets.
The system under test, the machines to look at, the stack being served, optional
power, and the submission paperwork.

```yaml
profile: endpoints                 # endpoints | inference | ./my-profile.yaml
extends: ~/.mlperf/org.yaml        # optional: shared org defaults

output:
  dir: results/h100_run1
  file: system_desc.json           # defaults to the profile's own filename

system:                            # what is being described
  name: H100x8_vLLM
  category: datacenter
  availability: available
  accelerator: cuda                # cuda | rocm | xpu | none
  cooling: air

nodes:                             # where to look
  include_local: false             # is this machine part of the system?
  ssh:
    - root@node1
    - root@node2:2222
  ssh_key_preconfigured: false
  groups:                          # optional: disaggregated setups
    prefill:
      - { match: NVIDIA H100, count: 2 }
    decode:
      - { match: NVIDIA H100, count: 5 }

serving:                           # the stack under test
  url: http://node1:8000
  node: root@node1
  log: /tmp/serving.log
  framework: auto                  # auto | vllm | sglang | trtllm

power:                             # optional, opt-in
  redfish:
    endpoint: https://bmc.node1
    username: ${BMC_USER}
    password: ${BMC_PASSWORD}

submission:                        # the paperwork
  submitter: MyOrg
  contact: mlperf@myorg.example
  division: standardized
  model:
    name: Llama-3.1-8B-Instruct
    precision: fp8
  dataset:
    name: cnn_dailymail
    type: text
  notes:
    hardware: ""
    software: ""
```

## Reference

### Top level

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `profile` | string | `endpoints` | A built-in name, or a path to a profile file |
| `extends` | path | — | Merge a parent config underneath this one |

### `output`

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `dir` | path | `.` | Relative paths resolve against the config file, not the cwd |
| `file` | string | profile's own | Output filename |

### `system`

| Key | Type | Notes |
| --- | --- | --- |
| `name` | string | **Always required.** Identifier for the system under test |
| `category` | string | e.g. `datacenter`, `edge` |
| `availability` | string | e.g. `available`, `preview`, `rdi` |
| `accelerator` | enum | `cuda` \| `rocm` \| `xpu` \| `none`. Without it, accelerators are not probed |
| `cooling` | string | e.g. `air`, `liquid` |
| `type_detail` | string | Free text |
| `size` | string | Overrides the computed `system_size`. Rarely needed |

### `nodes`

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `include_local` | bool | `false` | Whether the machine running the command is part of the system |
| `ssh` | list | `[]` | `user@host` or `user@host:port` |
| `ssh_key_preconfigured` | bool | `false` | Key auth is already set up; skip the key-file lookup |
| `groups` | map | — | Function name → list of `{match, count}` |

!!! note "Why `include_local` defaults to false"
    An orchestrator machine driving a cluster should not describe itself by
    accident. A config with none of `include_local: true`, an `ssh` entry, or
    a `serving.node` is rejected — there would be nothing to collect.

### `serving`

Only used by profiles that enable it. The `inference` profile ignores this
section entirely.

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `url` | string | — | Probed over HTTP for framework name and version |
| `node` | string | — | SSH target where the server process runs |
| `log` | path | `/tmp/serving.log` | Startup log, parsed for parallelism and batch settings |
| `framework` | enum | `auto` | `auto` \| `vllm` \| `sglang` \| `trtllm` |

`node` does not have to also appear under `nodes.ssh`. If it names a machine
not already listed there, it is still reached and its hardware still
collected — listing it twice is not required.

!!! warning "The serving log must actually exist"
    Server stdout/stderr has to be redirected to `serving.log` on that node.
    `check` verifies it is there before you spend a capture finding out.

### `power`

| Key | Type | Notes |
| --- | --- | --- |
| `redfish.endpoint` | string | BMC address |
| `redfish.username` | string | Use `${VAR}` |
| `redfish.password` | string | Use `${VAR}` |

### `submission`

| Key | Notes |
| --- | --- |
| `submitter`, `contact`, `division` | Required by both built-in profiles |
| `model.{id,name,precision,link,transformation_link,notes}` | |
| `dataset.{id,name,type,link,input_token_average,output_token_average}` | |
| `notes.{hardware,software,other_hardware}` | Become `hw_notes` / `sw_notes` / `other_hardware` |
| `container_link`, `measured_accuracy_score` | |

## Three mechanisms

### `${VAR}` — secrets stay out of the file

Any `${VAR}` anywhere in the config is replaced from the environment, including
inside lists. An unset variable is a config problem, reported against its path:

```console
  ✗ power.redfish.password -> ${BMC_PASSWORD}    unset    not found in the environment
```

### `extends` — share org defaults

```yaml
# ~/.mlperf/org.yaml
submission:
  submitter: MyOrg
  contact: mlperf@myorg.example
```

```yaml
# sysinfo.yaml
extends: ~/.mlperf/org.yaml
system:
  name: H100x8_vLLM
```

The child wins. Nested maps merge; lists replace wholesale. Cycles are caught.

### Embedding — one format, two homes

Nest the whole thing under `system_info:` in a benchmark config and it validates
identically. If there is no `output` block, a top-level `report_dir` is used:

```yaml
name: llama3-perf-run
report_dir: results/run1
datasets:
  - name: cnn_dailymail

system_info:
  profile: endpoints
  system:
    name: H100x8_vLLM
  nodes:
    ssh: [root@node1]
  submission:
    submitter: MyOrg
    # ...
```

## Placeholders

A config still carrying starter text has not been filled in, and `check` refuses
it. Two patterns are recognised anywhere in the file:

- anything containing `changeme` (so `CHANGEME@example.com` counts)
- anything matching `Insert … here` or `<…>`

The `insert` rule is anchored on purpose, so genuine prose survives:

```yaml
notes:
  hardware: "insert card in slot 3 before boot"   # fine, not a placeholder
```

Every string is scanned, not only the fields your profile requires — a
placeholder in any field still reaches the submission file.
