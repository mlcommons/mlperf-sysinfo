# Copyright 2026 MLCommons contributors
# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures. Nothing here touches the network or mlc-scripts."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

GOOD_CONFIG = {
    "profile": "endpoints",
    "output": {"dir": "out"},
    "system": {
        "name": "H100x8_vLLM",
        "shortened_name": "H100x8",
        "category": "datacenter",
        "availability": "available",
        "accelerator": "cuda",
        "cooling": "air",
    },
    "nodes": {"include_local": False, "ssh": ["root@node1", "root@node2:2222"]},
    "serving": {"url": "http://node1:8000", "node": "root@node1"},
    "submission": {
        "submitter": "MyOrg",
        "contact": "mlperf@myorg.example",
        "division": "standardized",
        "notes": {"hardware": "hw note", "software": "sw note"},
    },
    "run": {"link_config": "https://example.invalid/configs"},
}


def write_yaml(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


@pytest.fixture
def good_config_file(tmp_path: Path) -> Path:
    return write_yaml(tmp_path / "sysinfo.yaml", GOOD_CONFIG)


@pytest.fixture
def collected() -> dict:
    """What the automation returns for ``_endpoints``: the rules 8.2 field set.

    The submitter-supplied fields carry the automation's own defaults, which
    are placeholder strings. Overwriting those from the config is the whole job
    of ``build_endpoints``, so they belong in the fixture.
    """
    return {
        "division": "Insert model division here",
        "system_name": "H100x8_vLLM",
        "shortened_system_name": "Insert shortened system name here",
        "system_availability_status": "Insert system availability status here",
        "system_category": "Insert system category here",
        "system_size": "16x NVIDIA H100 80GB HBM3",
        "system_node_ensemble_count": 1,
        "system_node_ensemble_total": 2,
        "endpoint_url": "http://node1:8000",
        "serving_framework": "vLLM 0.9.0",
        "node_types": [
            {
                "system_node_ensemble_id": 1,
                "number_of_nodes": 2,
                "host_processor_model_name": "AMD EPYC 9654",
                "host_processors_per_node": 2,
                "host_processor_core_count": 96,
                "host_processor_vcpu_count": 192,
                "host_memory_capacity": "1.5 TB",
                "host_memory_configuration": "24x 64GB DDR5-4800",
                "accelerator_info": [
                    {
                        "accelerator_model_name": "NVIDIA H100 80GB HBM3",
                        "accelerators_per_node": 8,
                        "accelerator_memory_capacity": "80GiB",
                        "accelerator_memory_type": "HBM3",
                        "accelerator_interconnect": "NVLink",
                        "accelerator_host_interconnect": "PCIe Gen5 x16",
                    }
                ],
                "host_network_card_count": "2x Ethernet",
                "host_networking": "Ethernet",
                "host_storage_capacity": "8 TB NVMe SSD",
                "host_storage_type": "NVMe SSD",
                "other_hardware": "",
                "cooling": "",
                "hw_notes": "",
                "inference_backend": "CUDA 12.4",
                "driver": "Driver 550.54.15",
                "operating_system": "Ubuntu 22.04",
                "filesystem": "ext4",
                "container_link": "",
                "other_software_stack": "CUDA 12.4",
                "sw_notes": "",
            }
        ],
        "node_config": "",
        "disaggregated": 0,
        "expert_parallel": 0,
        "tensor_parallel": 8,
        "pipeline_parallel": 1,
        "data_parallel": 1,
        "batch": 256,
        "config_summary": "TP 8",
        "config_summary_notes": "",
        "link_config": "",
        "mlc_scripts_version": {"repo": "mlperf-automations", "commit": "abc123"},
    }


@pytest.fixture
def collected_flat() -> dict:
    """What the automation returns for ``_inference``: the checker's field set.

    Hardware is already lifted to the top level and the submitter fields are
    again the automation's placeholder defaults.
    """
    return {
        "submitter": "Insert your organization name here",
        "submitter_contact": "Insert a contact email here",
        "system_name": "H100x8_vLLM",
        "status": "Insert system availability status here",
        "system_type": "Insert system category here",
        "division": "Insert model division here",
        "system_size": "16x NVIDIA H100 80GB HBM3",
        "number_of_nodes": 2,
        "host_processor_model_name": "AMD EPYC 9654",
        "host_processors_per_node": 2,
        "host_processor_core_count": 96,
        "host_processor_vcpu_count": 192,
        "host_processor_frequency": "3.7 GHz",
        "host_processor_caches": "L3: 384 MiB",
        "host_processor_interconnect": "2 NUMA nodes",
        "host_memory_capacity": "1.5 TB",
        "host_storage_type": "NVMe SSD",
        "host_storage_capacity": "8 TB NVMe SSD",
        "host_memory_configuration": "24x 64GB DDR5-4800",
        "host_networking": "Ethernet",
        "host_networking_topology": "",
        "host_network_card_count": "2x Ethernet",
        "accelerator_model_name": "NVIDIA H100 80GB HBM3",
        "accelerators_per_node": 8,
        "accelerator_memory_capacity": "80GiB",
        "accelerator_memory_configuration": "80 GiB HBM3",
        "accelerator_host_interconnect": "PCIe Gen5 x16",
        "accelerator_interconnect": "NVLink",
        "accelerator_interconnect_topology": "",
        "accelerator_frequency": "1980 MHz",
        "accelerator_on-chip_memories": "Shared Memory: 228 KB/block",
        "framework": "vLLM 0.9.0",
        "operating_system": "Ubuntu 22.04",
        "other_software_stack": "CUDA 12.4",
        "hw_notes": "",
        "sw_notes": "",
        "other_hardware": "",
        "cooling": "",
        "system_type_detail": "",
        "mlc_scripts_version": {"repo": "mlperf-automations", "commit": "abc123"},
    }
