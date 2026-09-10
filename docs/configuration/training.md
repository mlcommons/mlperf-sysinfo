# The `training` config

Developed with the training
[`system_desc_checker`](https://github.com/mlcommons/logging/tree/master/mlperf_logging/system_desc_checker)
in mind.

`mlperf-sysinfo init training` writes a commented starter config covering the
same fields.

## A working config

```yaml
profile: training

output:
  dir: results/training_run1

system:
  name: dgx-h100-n8
  availability: Available on-premise
  accelerator: cuda
  cooling: air
  networking_topology: "rail-optimized fat tree, 8x400G per node"

nodes:
  include_local: true

training:
  framework: NVIDIA PyTorch Release 25.04
  framework_name: ngc25.04_pytorch

submission:
  submitter: MyOrg
  division: closed
  notes:
    hardware: "8-node DGX H100 SuperPOD"
    software: "NCCL 2.21, CUDA 12.4"
```

This collects sysinfo from the machine from where the command is run, as
`include_local` is set to true and no ssh targets were listed.

## `system.availability` — four values, and no others

The checker rejects anything outside this list for ruleset major version 4 and
above, so `check` refuses it up front rather than letting you spend a
multi-node capture finding out:

| Value | Shorthand also accepted |
| --- | --- |
| `Available on-premise` | `on-premise`, `on-prem`, `onprem` |
| `Available cloud` | `cloud` |
| `Preview` | — |
| `Research, Development, or Internal (RDI)` | `rdi`, `internal` |

Matching ignores case, so you need not reproduce the punctuation of
`Research, Development, or Internal (RDI)`.

!!! warning "`available` on its own is not one of them"
    It is what MLPerf Inference uses, and training splits it into on-premise
    and cloud. Guessing which one you meant would silently mislabel the
    submission, so it is refused instead:

    ```console
    $ mlperf-sysinfo check -c sysinfo.yaml
      ✗ system.availability    'available' is not a valid MLPerf Training
        availability. It must be one of: Available on-premise, Available cloud,
        Research, Development, or Internal (RDI), Preview. MLPerf Training
        splits availability into on-premise and cloud, so 'available' on its
        own is ambiguous -- pick one.
    ```

## Required

`check` stops if any of these is missing or still holding starter text. The
checker requires 33 fields; everything absent from this table is probed, and
what is here is what no probe can answer.

| Field | Why |
| --- | --- |
| `system.name` | Identifier for the system under test, and the name of the file a training submission stores under its systems directory |
| `system.availability` | One of the four availability strings training accepts |
| `system.cooling` | How the nodes are cooled, e.g. air or liquid |
| `system.networking_topology` | How the nodes are wired to each other. Nothing can see past the local NIC, so a single-node system should say so |
| `submission.submitter` | Organisation making the submission |
| `submission.division` | Either closed or open |
| `training.framework` | What the run was trained with, and its version. It lives inside the container image, which this tool never opens |

## Worth filling in

Absence is a warning, not a stop. Starter text in one of them *is* a stop,
wherever it turns up.

| Field | Why |
| --- | --- |
| `system.accelerator` | Without it, accelerators are not probed at all |
| `submission.notes.hardware` | Hardware detail no probe can report, such as interconnect topology or firmware |
| `submission.notes.software` | Software detail no probe can report, such as versions, flags or patches |
| `training.framework_name` | Short tag for that build, e.g. `ngc25.04_pytorch`. Written only when set, since the checker does not ask for it |

## Options

### `system`

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `name` | string | — | **Required.** Identifier for the system under test. Also names the output file |
| `availability` | string | — | **Required.** One of the [four values above](#systemavailability-four-values-and-no-others). Written as `status` |
| `cooling` | string | — | **Required.** e.g. `air`, `liquid`, `passive` |
| `networking_topology` | string | — | **Required.** Written as `host_networking_topology` |
| `accelerator` | enum | `none` | **Recommended.** `cuda` \| `rocm` \| `xpu` \| `none`. Left at the default, a GPU system captures no accelerator at all |

### `training`

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `framework` | string | — | **Required.** Training framework and version, e.g. `NVIDIA PyTorch Release 25.04`. Written as `framework` |
| `framework_name` | string | — | **Recommended.** Short tag for that build. Written only when set |

### `nodes`

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `include_local` | bool | `false` | Whether the machine running the command is part of the system |
| `ssh` | list | `[]` | `user@host` or `user@host:port` |
| `ssh_key_preconfigured` | bool | `false` | Key auth is already set up; skip the key-file lookup |

A multi-node run lists every node, and `number_of_nodes` is counted from what
actually reported hardware rather than from the length of this list:

```yaml
profile: training

system:
  name: dgx-h100-n8
  availability: Available on-premise
  accelerator: cuda
  cooling: liquid
  networking_topology: "rail-optimized fat tree, 8x400G per node"

nodes:
  include_local: false
  ssh:
    - root@node1
    - root@node2
    - root@node3
    - root@node4
  ssh_key_preconfigured: true

training:
  framework: NVIDIA PyTorch Release 25.04

submission:
  submitter: MyOrg
  division: closed
```

### `submission`

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `submitter` | string | — | **Required.** Organisation making the submission |
| `division` | string | — | **Required.** Either closed or open. Written lower-cased |
| `notes.hardware` | string | — | **Recommended.** Written as `hw_notes` |
| `notes.software` | string | — | **Recommended.** Written as `sw_notes` |

## Rules that apply to every profile

- [`${VAR}` — secrets stay out of the file](index.md#var-secrets-stay-out-of-the-file)
- [`extends` — share org defaults](index.md#extends-share-org-defaults)
- [Empty, unset, and absent](index.md#empty-unset-and-absent)
- [Leftover starter text](index.md#leftover-starter-text)
- [Unknown and misspelled options](index.md#unknown-and-misspelled-options)
