# Sample outputs

Every file on this page is a real capture from an 8×H100 node, trimmed only
where noted. Nothing here is invented.

The profile decides the shape. All three were produced from the same machine
and almost the same config, with only `profile:` differing.

## Provenance: the block every output carries

```json
"mlperf_sysinfo": {
  "version": "0.1.0",
  "profile": "endpoints",
  "profile_round": "6.0",
  "shape": "nested",
  "benchmark": "endpoints",
  "captured_at": "2026-08-10T14:01:20+00:00",
  "nodes_expected": 1,
  "nodes_collected": 1,
  "complete": true,
  "mlc_scripts": {
    "repo": "mlcommons@mlperf-automations",
    "source": "git",
    "commit": "ccc3cde0db5350b504af82e513da7434a638ba2a",
    "branch": "dev",
    "dirty": false,
    "consistent": true,
    "nodes": {
      "0": { "commit": "ccc3cde0db5350b504af82e513da7434a638ba2a", "dirty": false }
    }
  }
}
```

This is what makes a captured file self-describing:

| Field | Why it matters |
| --- | --- |
| `profile` + `profile_round` | Which rules produced this file. Profiles track the current round, so the file records which round that was. A profile that names no round omits `profile_round` rather than writing it empty. See [`training`](#training-flat) |
| `benchmark` | Which field set the file holds. `shape` alone stopped being enough to say once `inference` and `training` were both flat |
| `nodes_expected` / `nodes_collected` | What was asked for versus what answered |
| `complete` | `false` means a partial capture. `validate` refuses these |
| `mlc_scripts` | Which collection code produced the file |

### Two forms of `mlc_scripts`

The collection layer can run either from a git checkout or from the installed
package, so the stamp takes whichever form applies:

```json
"mlc_scripts": { "package_version": "1.2.0a5" }
```

That is what a `pip install` produces. `mlc-scripts` runs from the installed
release and there is no repository to read a commit from.

A git checkout stamps the commit instead, plus a per-node breakdown and a
`consistent` flag showing whether every node ran the same version. A
mixed-version run stays visible rather than hidden.

---

## `endpoints` — grouped

The field set defined by [endpoints rules
8.2](https://github.com/mlcommons/endpoints_policies/blob/main/endpoints_rules.md),
in the order of the 8.2.1 template. `node_types` keeps multi-node and
disaggregated systems legible, with one entry per node type, each with its own
hardware and a `number_of_nodes` count. `accelerator_info` nests the
accelerators inside it, so a node type holding more than one accelerator model
can say so.

`tps_utilization` is the one field from that template this tool does not write.
It is this run's throughput over the best of every run, so it cannot be known
until every run exists. Submission tooling fills it in.

```json
{
  "division": "standardized",
  "system_name": "H100x8",
  "shortened_system_name": "H100x8",
  "system_availability_status": "available",
  "system_size": "8 accelerators",
  "system_node_ensemble_count": 1,
  "system_node_ensemble_total": 1,
  "endpoint_url": "http://node1:8000",
  "serving_framework": "vLLM 0.9.0",
  "node_types": [
    {
      "system_node_ensemble_id": 1,
      "number_of_nodes": 1,
      "host_processor_model_name": "Intel(R) Xeon(R) Platinum 8480+",
      "host_processors_per_node": 2,
      "host_processor_core_count": 112,
      "host_processor_vcpu_count": 224,
      "host_memory_capacity": "2.2T",
      "host_memory_configuration": "32x 64GB DDR5-4800",
      "accelerator_info": [
        {
          "accelerator_model_name": "NVIDIA H100 80GB HBM3",
          "accelerators_per_node": 8,
          "accelerator_memory_capacity": "80GiB",
          "accelerator_memory_type": "HBM3",
          "accelerator_interconnect": "NVLink",
          "accelerator_host_interconnect": "PCIe Gen5 x16"
        }
      ],
      "host_network_card_count": "3x mlx5_0: native InfiniBand",
      "host_networking": "mlx5_0: native InfiniBand",
      "host_storage_capacity": "1.1 GB NVMe SSD, 1.8 TB SSD",
      "host_storage_type": "NVMe SSD",
      "other_hardware": "",
      "cooling": "air",
      "hw_notes": "hw note",
      "inference_backend": "CUDA 12.9",
      "driver": "Driver 575.57.08",
      "operating_system": "ubuntu 24.04",
      "filesystem": "ext4 vfat zfs",
      "container_link": "",
      "other_software_stack": "CUDA 12.9, Driver 575.57.08",
      "sw_notes": "sw note"
    }
  ],
  "node_config": "prefill: 2x H100; decode: 6x H100",
  "disaggregated": 0,
  "expert_parallel": 1,
  "tensor_parallel": 8,
  "pipeline_parallel": 1,
  "data_parallel": 1,
  "batch": 256,
  "config_summary": "TP 8",
  "config_summary_notes": "",
  "link_config": "https://github.com/myorg/submission/tree/main/configs",
  "mlperf_sysinfo": { "...": "as above" }
}
```

Nothing outside that field set is written. Two groups of fields used to be
here and are not any more:

| Was in the file | Where it is now |
| --- | --- |
| `model_name`, `model_precision`, `link_to_model`, `link_to_model_transformation`, `model_notes`, `dataset_name`, `dataset_type`, `dataset_link`, `max_supported_concurrency` | Measurement point metadata (rules 8.3), in each point's `points/<point>/config.yml`. This tool does not write that file |
| `submitter_org_names`, `submitter_contact`, `submission_id`, `submission_date`, `publish_date`, `measured_accuracy_score`, `system_type_detail`, `input_token_average`, `output_token_average` | Dropped from the field table |

`hw_notes`, `sw_notes`, `other_hardware`, `cooling` and `container_link` are
still written, but per node type rather than once at the top level. The same
config value is copied onto every entry.

!!! info "\"N/A\" is an answer, and it is left alone"
    Where a probe looked and found nothing it writes `N/A` or
    `Not detected: ...`, and that survives into the file, including in fields
    the template types as a number. Blanking it would lose the distinction
    between "not detected" and "not applicable", and writing `0` would hide a
    failed detection behind a plausible answer. `validate` warns about every
    one of them, so they are visible before you submit rather than after. The
    consequence is that a capture with failed detections will not pass a strict
    JSON-schema check of the template: fill those fields in first.

!!! info "Empty strings, never placeholders"
    A field nobody supplied comes out as `""` (or `0` for a count), never as
    `"Insert your organization name here"`, which is what the underlying
    automation defaults to and what the pre-package pipeline used to write
    into submissions. Overwriting those defaults from the config is the
    reason the shaping step exists.

---

## `inference` — flat

The field set the MLPerf Inference submission checker expects, with node
hardware lifted to the top level and renamed to the checker's names.

```json
{
  "submitter": "MyOrg",
  "submitter_contact": "mlperf@myorg.example",
  "system_name": "H100x8",
  "status": "available",
  "system_type": "datacenter",
  "division": "closed",
  "system_size": "8x NVIDIA H100 80GB HBM3",
  "number_of_nodes": 1,
  "host_processor_model_name": "Intel(R) Xeon(R) Platinum 8480+",
  "host_processors_per_node": 2,
  "host_processor_core_count": 112,
  "host_processor_vcpu_count": 224,
  "host_processor_frequency": "3.80 GHz",
  "host_processor_caches": "L1d: 5.3 MiB (112 instances); L1i: 3.5 MiB (112 instances); L2: 224 MiB (112 instances); L3: 210 MiB (2 instances)",
  "host_processor_interconnect": "UPI (2 NUMA nodes)",
  "host_memory_capacity": "2.2T",
  "host_storage_type": "NVMe SSD",
  "host_storage_capacity": "1.1 GB NVMe SSD, 1.8 TB SSD",
  "host_memory_configuration": "",
  "host_networking": "mlx5_0: native InfiniBand",
  "host_networking_topology": "",
  "host_network_card_count": "3x mlx5_0: native InfiniBand",
  "accelerator_model_name": "NVIDIA H100 80GB HBM3",
  "accelerators_per_node": 8,
  "accelerator_memory_capacity": "80GiB",
  "accelerator_memory_configuration": "80 GiB HBM3",
  "accelerator_host_interconnect": "PCIe Gen5 x16",
  "accelerator_interconnect": "NVLink",
  "accelerator_interconnect_topology": "Mesh",
  "accelerator_frequency": "1980.000000 MHz",
  "accelerator_on-chip_memories": "Shared Memory: 48 KB/block",
  "framework": "",
  "operating_system": "ubuntu 24.04",
  "other_software_stack": "CUDA 12.9, Driver 575.57.08",
  "hw_notes": "hw note",
  "sw_notes": "sw note",
  "other_hardware": "",
  "cooling": "air",
  "system_type_detail": "rack detail here-ish",
  "mlperf_sysinfo": { "...": "as above" }
}
```

### How the two shapes differ

| Grouped (`endpoints`) | Flat (`inference`) |
| --- | --- |
| `system_availability_status` | `status` |
| `serving_framework` | `framework` |
| `node_types[]`, with `accelerator_info[]` inside | Hardware lifted to the top level |
| `system_node_ensemble_total` | `number_of_nodes` |
| — | `system_type`, `submitter`, `submitter_contact`, `system_type_detail` |
| `endpoint_url` and the run configuration | — |

They are also two different field sets on the collection side, not one file
trimmed two ways: the profile's `benchmark` names which one to gather, because
each keeps fields the other has no use for. The flat shape keeps
`host_processor_frequency`, `accelerator_frequency` and the on-chip memory
sizes that the checker asks for; the grouped shape keeps the run configuration
and drops everything outside rules 8.2.

When a flat capture covers several node types, values are merged: identical
hardware collapses to one value, and genuinely different hardware is
comma-joined so nothing is silently dropped.

---

## `training` — flat

The field set
[`mlperf_logging/system_desc_checker`](https://github.com/mlcommons/logging/tree/master/mlperf_logging/system_desc_checker)
validates, in the order that checker lists its `required_fields`. Flat like
`inference`, but **not the same field set and not the same checker**.

The file is named after `system.name`, because a training submission stores it
as `<submitter>/systems/<system_name>.json`.

```json
{
  "submitter": "MyOrg",
  "division": "closed",
  "status": "Available on-premise",
  "system_name": "dgx-h100-n8",
  "number_of_nodes": "8",
  "host_processors_per_node": "2",
  "host_processor_model_name": "Intel(R) Xeon(R) Platinum 8480+",
  "host_processor_core_count": "112",
  "host_processor_vcpu_count": "224",
  "host_processor_frequency": "3.80 GHz",
  "host_processor_caches": "L1d: 5.3 MiB (112 instances); L2: 224 MiB (112 instances); L3: 210 MiB (2 instances)",
  "host_processor_interconnect": "UPI (2 NUMA nodes)",
  "host_memory_capacity": "2.2T",
  "host_storage_type": "NVMe SSD",
  "host_storage_capacity": "1.1 GB NVMe SSD, 1.8 TB SSD",
  "host_networking": "mlx5_0: native InfiniBand",
  "host_networking_topology": "rail-optimized fat tree, 8x400G per node",
  "host_memory_configuration": "",
  "accelerators_per_node": "8",
  "accelerator_model_name": "NVIDIA H100 80GB HBM3",
  "accelerator_host_interconnect": "PCIe Gen5 x16",
  "accelerator_frequency": "1980.000000 MHz",
  "accelerator_on-chip_memories": "Shared Memory: 48 KB/block",
  "accelerator_memory_configuration": "80 GiB HBM3",
  "accelerator_memory_capacity": "80GiB",
  "accelerator_interconnect": "NVLink",
  "accelerator_interconnect_topology": "Mesh",
  "cooling": "air",
  "hw_notes": "8-node DGX H100 SuperPOD",
  "framework": "NVIDIA PyTorch Release 25.04",
  "framework_name": "ngc25.04_pytorch",
  "other_software_stack": "CUDA 12.9, Driver 575.57.08",
  "operating_system": "ubuntu 24.04",
  "sw_notes": "NCCL 2.21, CUDA 12.4",
  "mlperf_sysinfo": { "...": "as above, without profile_round" }
}
```

Three things about this file are not true of the flat `inference` one:

**Every value is a string.** Counts included, so `"8"`, not `8`. That is the form
existing training submissions use.

**A failed probe becomes empty, not prose.** `host_memory_configuration` above
came back as `Not detected: dmidecode requires sudo`, and carrying that into a
submission field would read as a real answer. Empty is how a training
submission says "not disclosed"; `validate` still lists it as a field to fill
in before submitting.

**No `profile_round` is stamped.** MLPerf Training numbers its rulesets
independently of Inference, so the profile records no round rather than one
that might be wrong. The field is omitted rather than written empty, because an empty
one would read as a round that failed to record.

---

## `system_size`

The two profiles define this field differently, so they compute it differently.

**`endpoints`** follows rules 8.2: *"Number of accelerators per node type"*. Per
node type, `number_of_nodes × accelerators_per_node` summed over every
accelerator model it hosts, joined with `+`. The field counts accelerators and
nothing else. A node type with none reports `0`, rather than falling back to
host processors and answering a different question.

```text
8 accelerators
72 accelerators + 144 accelerators
0 accelerators
```

**`inference`** follows the MLPerf Per Submission Data Dictionary, which names
the model as well and does fall back to host processors:

```text
8x NVIDIA H100 80GB HBM3
16x NVIDIA H100 80GB HBM3 + 8x NVIDIA A100-SXM4-80GB
4x Intel(R) Xeon(R) Platinum 8480+
```

Set `system.size` in the config to override either. That is rarely needed.

---

## A partial capture

Produced by `--allow-partial` when a node did not answer:

```json
"mlperf_sysinfo": {
  "profile": "endpoints",
  "nodes_expected": 2,
  "nodes_collected": 1,
  "complete": false,
  "warning": "PARTIAL CAPTURE -- one or more nodes did not answer. This file does not describe the whole system."
}
```

`validate` refuses it:

```console
PROBLEMS
  ✗ partial capture: 1 of 2 nodes answered. This file does not describe the whole system.

  1 problem(s). This file is not ready to submit.
```

A partial capture cannot be mistaken for a whole one. That is the point of
stamping it rather than just logging a warning at capture time.
