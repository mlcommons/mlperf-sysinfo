# The `inference` config

MLPerf Inference submissions, written as the flat field set the submission
checker expects, with node hardware lifted to the top level.

`mlperf-sysinfo init inference` writes a commented starter config covering the
same fields.

## A working config

```yaml
profile: inference

output:
  dir: results/h100_run1

system:
  name: H100x8
  category: datacenter
  availability: available
  accelerator: cuda
  cooling: air
  type_detail: "1-node 8x H100"

nodes:
  include_local: true

submission:
  submitter: MyOrg
  contact: mlperf@myorg.example
  division: closed
  notes:
    hardware: "8x H100 SXM, single node"
    software: "TensorRT-LLM 0.12"
```

## Required

`check` stops if any of these is missing or still holding starter text.

| Field | Why |
| --- | --- |
| `system.name` | Identifier for the system under test |
| `system.category` | Whether this is a datacenter or an edge system |
| `system.availability` | Availability at submission time, e.g. available, preview or rdi |
| `submission.submitter` | Organisation making the submission |
| `submission.contact` | Contact email for questions about this submission |
| `submission.division` | Either closed or open |

## Worth filling in

Absence is a warning, not a stop. Starter text in one of them *is* a stop,
wherever it turns up.

| Field | Why |
| --- | --- |
| `system.accelerator` | Without it, accelerators are not probed at all |
| `submission.notes.software` | Software detail no probe can report, such as versions, flags or patches |
| `submission.notes.hardware` | Hardware detail no probe can report, such as interconnect topology or firmware |

## Options

### `system`

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `name` | string | — | **Required.** Identifier for the system under test |
| `category` | string | — | **Required.** `datacenter` or `edge`. Written as `system_type` |
| `availability` | string | — | **Required.** e.g. `available`, `preview`, `rdi`. Written as `status` |
| `accelerator` | enum | `none` | **Recommended.** `cuda` \| `rocm` \| `xpu` \| `none`. Left out it defaults to `none`, which skips accelerator probing entirely — a GPU system left at the default captures no accelerator at all |
| `cooling` | string | — | e.g. `air`, `liquid`, `passive` |
| `type_detail` | string | — | Free text, written as `system_type_detail`. Rack or chassis detail the field list has no column of its own for |
| `size` | string | computed | Overrides `system_size`. Rarely needed — see [Sample outputs](../outputs.md#system_size) for what this profile computes |

### `nodes`

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `include_local` | bool | `false` | Whether the machine running the command is part of the system |
| `ssh` | list | `[]` | `user@host` or `user@host:port` |
| `ssh_key_preconfigured` | bool | `false` | Key auth is already set up; skip the key-file lookup |
| `groups` | map | — | Function name → list of `{match, count}` |

!!! note "Why `include_local` defaults to false"
    An orchestrator machine driving a cluster should not describe itself by
    accident. A config with none of `include_local: true`, an `ssh` entry, or a
    `serving.node` is rejected — there would be nothing to collect.

### `power`

Optional, opt-in. Enables Redfish capture from the BMC.

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `redfish.endpoint` | string | — | BMC address |
| `redfish.username` | string | — | Use `${VAR}` |
| `redfish.password` | string | — | Use `${VAR}` |

### `submission`

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `submitter` | string | — | **Required.** Organisation making the submission |
| `contact` | string | — | **Required.** Contact email for questions about the submission. Written as `submitter_contact` |
| `division` | string | — | **Required.** Either closed or open |
| `notes.hardware` | string | — | **Recommended.** Written as `hw_notes` |
| `notes.software` | string | — | **Recommended.** Written as `sw_notes` |
| `notes.other_hardware` | string | — | Written as `other_hardware` |

## What this profile ignores

There is no `serving` or `run` section here — an inference capture has nowhere
to put a URL, a run configuration, or `submission.container_link`, and those
values are not read.

!!! warning "`serving.node` is the exception"
    Every profile treats it as a machine to collect from, on the grounds that a
    node only mentioned as where the server runs is still part of the system. So
    a config carried over from `endpoints` will quietly capture that node too,
    and `number_of_nodes` will say so. Delete the `serving` block, or move the
    host to `nodes.ssh` where you can see it.

## Rules that apply to every profile

- [`${VAR}` — secrets stay out of the file](index.md#var-secrets-stay-out-of-the-file)
- [`extends` — share org defaults](index.md#extends-share-org-defaults)
- [Empty, unset, and absent](index.md#empty-unset-and-absent)
- [Leftover starter text](index.md#leftover-starter-text)
- [Unknown and misspelled options](index.md#unknown-and-misspelled-options)
