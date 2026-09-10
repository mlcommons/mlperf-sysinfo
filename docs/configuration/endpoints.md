# The `endpoints` config

`mlperf-sysinfo init endpoints` writes a commented starter config covering the
same fields.

## A working config

```yaml
profile: endpoints

output:
  dir: results/h100_run1

system:
  name: H100x8_vLLM
  shortened_name: H100x8
  availability: available
  accelerator: cuda
  cooling: air

nodes:
  include_local: false
  ssh:
    - root@node1

serving:
  url: http://node1:8000
  node: root@node1

submission:
  division: standardized
  notes:
    hardware: "8x H100 SXM, single node"
    software: "vLLM 0.9.0, CUDA 12.9"

run:
  link_config: https://github.com/myorg/submission/tree/main/configs
```

## Required

`check` stops if any of these is missing or still holding starter text.

| Field | Why |
| --- | --- |
| `system.name` | Identifier for the system under test |
| `system.shortened_name` | Short form of the name, at most 20 characters |
| `system.availability` | Availability at submission time, e.g. available, preview or rdi |
| `submission.division` | One of standardized, serviced or rdi |
| `serving.url` | The endpoint under test |

## Worth filling in

Absence is a warning, not a stop. Starter text in one of them *is* a stop,
wherever it turns up.

| Field | Why |
| --- | --- |
| `system.accelerator` | Without it, accelerators are not probed at all |
| `system.cooling` | Reviewers ask how the nodes are cooled |
| `serving.node` | Enables parallelism and batch settings to be read from the startup log |
| `submission.notes.hardware` | Hardware detail no probe can report, such as interconnect topology or firmware |
| `submission.notes.software` | Software detail no probe can report, such as versions, flags or patches |
| `run.link_config` | Reviewers use it to reproduce the run |

## Options

### `system`

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `name` | string | — | **Required.** Identifier for the system under test |
| `shortened_name` | string | — | **Required.** Short form of `name` for tables and charts. At most 20 characters, and `check` stops if it is longer. Written as `shortened_system_name` |
| `availability` | string | — | **Required.** e.g. `available`, `preview`, `rdi` |
| `accelerator` | enum | `none` | **Recommended.** `cuda` \| `rocm` \| `xpu` \| `none`. Left out it defaults to `none`, which skips accelerator probing entirely, so a GPU system left at the default captures no accelerator at all |
| `cooling` | string | — | **Recommended.** e.g. `air`, `liquid`, `passive`. Written into every node type |
| `size` | string | computed | Overrides `system_size`. Rarely needed. See [Sample outputs](../outputs.md#system_size) for what this profile computes |

### `nodes`

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `include_local` | bool | `false` | Whether the machine running the command is part of the system |
| `ssh` | list | `[]` | `user@host` or `user@host:port` |
| `ssh_key_preconfigured` | bool | `false` | Key auth is already set up; skip the key-file lookup |
| `groups` | map | — | Function name → list of `{match, count}`, for disaggregated setups |

!!! note "Why `include_local` defaults to false"
    An orchestrator machine driving a cluster should not describe itself by
    accident. A config with none of `include_local: true`, an `ssh` entry, or a
    `serving.node` is rejected, because there would be nothing to collect.

### `serving`

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `url` | string | — | **Required.** The endpoint under test, written as `endpoint_url`. An `http(s)` URL is also probed for the framework name and version; rules 8.2 allow a plain description instead (`"Managed endpoint, no public URL"`), which is accepted and simply not probed |
| `node` | string | — | **Recommended.** SSH target where the server process runs |
| `log` | path | `/tmp/serving.log` | Startup log, parsed for parallelism and batch settings |
| `framework` | enum | `auto` | `auto` \| `vllm` \| `sglang` \| `trtllm` |

`node` does not have to also appear under `nodes.ssh`. If it names a machine not
already listed there, it is still reached and its hardware still collected.
Listing it twice is not required.

!!! warning "The serving log must actually exist"
    Server stdout/stderr has to be redirected to `serving.log` on that node.
    `check` verifies it is there before you spend a capture finding out.

### `submission`

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `division` | string | — | **Required.** One of standardized, serviced or rdi |
| `notes.hardware` | string | — | **Recommended.** Written into every node type as `hw_notes` |
| `notes.software` | string | — | **Recommended.** Written into every node type as `sw_notes` |
| `notes.other_hardware` | string | — | Written into every node type as `other_hardware` |
| `container_link` | string | — | Link to the container the submission ran in. Written into every node type |

### `run`

How the stack was configured. The parallelism degrees (`tensor_parallel` and
friends) and `batch` are read from `serving.log`, so they are not settable here.
What is left is the three keys below: the parts of a run configuration that
nothing on the machine can report.

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `node_config` | string | from `nodes.groups` | Prose description of the node layout |
| `config_summary_notes` | string | — | Anything the parallelism fields do not capture. Folded into `config_summary` |
| `link_config` | string | — | **Recommended.** Link to the full configuration logs for the run |

## What this profile ignores

`submission.submitter`, `submission.contact` and `system.type_detail` are
Inference fields with no place in an Endpoints system description. They still
parse, so a config switched over from `inference` is accepted; the values are
simply not read.

## Rules that apply to every profile

- [`${VAR}` — secrets stay out of the file](index.md#var-secrets-stay-out-of-the-file)
- [`extends` — share org defaults](index.md#extends-share-org-defaults)
- [Empty, unset, and absent](index.md#empty-unset-and-absent)
- [Leftover starter text](index.md#leftover-starter-text)
- [Unknown and misspelled options](index.md#unknown-and-misspelled-options)
