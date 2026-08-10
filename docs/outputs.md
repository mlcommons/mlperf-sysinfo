# Sample outputs

Every file on this page is a real capture from an 8×H100 node, trimmed only
where noted. Nothing here is invented.

The profile decides the shape. Both were produced from the same machine and
almost the same config — only `profile:` differed.

## Provenance: the block every output carries

```json
"mlperf_sysinfo": {
  "version": "0.1.0",
  "profile": "endpoints",
  "profile_round": "6.0",
  "shape": "nested",
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
| `profile` + `profile_round` | Which rules produced this file. Profiles track the current round, so the file records which round that was |
| `nodes_expected` / `nodes_collected` | What was asked for versus what answered |
| `complete` | `false` means a partial capture. `validate` refuses these |
| `mlc_scripts` | Which collection code produced the file |

### Two forms of `mlc_scripts`

The collection layer can run either from a git checkout or from the installed
package, so the stamp takes whichever form applies:

```json
"mlc_scripts": { "package_version": "1.2.0a1" }
```

That is what a `pip install` produces — `mlc-scripts` runs from the installed
release and there is no repository to read a commit from.

A git checkout stamps the commit instead, plus a per-node breakdown and a
`consistent` flag showing whether every node ran the same version. A
mixed-version run stays visible rather than hidden.

---

## `endpoints` — grouped

Keeps `node_types`, so multi-node and disaggregated systems stay legible. One
entry per node type, each with its own hardware and a `number_of_nodes` count.

```json
{
  "submitter_org_names": "MyOrg",
  "submitter_contact": "mlperf@myorg.example",
  "submission_id": "",
  "submission_date": "",
  "publish_date": "",
  "system_name": "H100x8",
  "system_category": "datacenter",
  "system_availability_status": "available",
  "system_size": "8x NVIDIA H100 80GB HBM3",
  "system_node_ensemble_count": 1,
  "system_node_ensemble_total": 1,
  "serving_framework": "",
  "node_types": [
    {
      "system_node_ensemble_id": 0,
      "number_of_nodes": 1,
      "host_processor_model_name": "Intel(R) Xeon(R) Platinum 8480+",
      "host_processors_per_node": 2,
      "host_processor_core_count": 112,
      "host_processor_vcpu_count": 224,
      "host_processor_frequency": "3.80 GHz",
      "host_processor_caches": "L1d: 5.3 MiB (112 instances); L1i: 3.5 MiB (112 instances); L2: 224 MiB (112 instances); L3: 210 MiB (2 instances)",
      "host_processor_interconnect": "UPI (2 NUMA nodes)",
      "host_memory_capacity": "2.2T",
      "accelerator_model_name": "NVIDIA H100 80GB HBM3",
      "accelerators_per_node": 8,
      "accelerator_memory_capacity": "80GiB",
      "accelerator_memory_type": "HBM3",
      "accelerator_interconnect": "NVLink",
      "accelerator_host_interconnect": "PCIe Gen5 x16",
      "accelerator_frequency": "1980.000000 MHz",
      "accelerator_interconnect_topology": "Mesh",
      "host_network_card_count": "3x mlx5_0: native InfiniBand",
      "host_networking": "mlx5_0: native InfiniBand",
      "host_storage_capacity": "1.1 GB NVMe SSD, 1.8 TB SSD",
      "host_storage_type": "NVMe SSD",
      "operating_system": "ubuntu 24.04",
      "other_software_stack": "CUDA 12.9, Driver 575.57.08",
      "filesystem": "ext4 vfat zfs",
      "hw_notes": "hw note",
      "cooling": "air"
    }
  ],
  "division": "standardized",
  "model_name": "Llama-3.1-8B-Instruct",
  "model_precision": "fp8",
  "dataset_name": "cnn_dailymail",
  "dataset_type": "text",
  "hw_notes": "hw note",
  "sw_notes": "sw note",
  "cooling": "air",
  "system_type_detail": "rack detail here-ish",
  "mlperf_sysinfo": { "...": "as above" }
}
```

!!! info "Empty strings, never placeholders"
    `model_id`, `link_to_model`, `measured_accuracy_score` and friends are
    omitted above for length; in the real file they are present and empty.
    A field nobody supplied comes out as `""` — never as
    `"Insert your organization name here"`, which is what the pre-package
    pipeline used to write into submissions.

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
| `submitter_org_names` | `submitter` |
| `system_category` | `system_type` |
| `system_availability_status` | `status` |
| `serving_framework` | `framework` |
| `node_types[]` with per-node hardware | Hardware lifted to the top level |
| `system_node_ensemble_total` | `number_of_nodes` |

When a flat capture covers several node types, values are merged: identical
hardware collapses to one value, and genuinely different hardware is
comma-joined so nothing is silently dropped.

---

## `system_size`

Computed per the MLPerf Per Submission Data Dictionary: for each node type,
`number_of_nodes × accelerators_per_node` of the accelerator model, falling back
to host processors when no accelerator was detected. Node types are joined with
`+`.

```text
8x NVIDIA H100 80GB HBM3
16x NVIDIA H100 80GB HBM3 + 8x NVIDIA A100-SXM4-80GB
4x Intel(R) Xeon(R) Platinum 8480+
```

Set `system.size` in the config to override it. That is rarely needed.

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

A partial capture cannot be mistaken for a whole one — that is the point of
stamping it rather than just logging a warning at capture time.
