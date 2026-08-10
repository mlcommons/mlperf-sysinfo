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
        "model": {"name": "Llama-3.1-8B-Instruct", "precision": "fp8"},
        "dataset": {"name": "cnn_dailymail", "type": "text"},
    },
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
    """A grouped intermediate of the kind the automation script produces."""
    return {
        "system_name": "ignored -- the config wins",
        "system_size": "16x NVIDIA H100 80GB HBM3",
        "serving_framework": "vLLM 0.9.0",
        "node_types": [
            {
                "system_node_ensemble_id": 1,
                "number_of_nodes": 2,
                "system_node_name": "node1",
                "host_processor_model_name": "AMD EPYC 9654",
                "host_processors_per_node": 2,
                "host_processor_core_count": 96,
                "host_memory_capacity": "1.5 TB",
                "accelerator_model_name": "NVIDIA H100 80GB HBM3",
                "accelerators_per_node": 8,
                "operating_system": "Ubuntu 22.04",
                "other_software_stack": "CUDA 12.4",
            }
        ],
        "mlc_scripts_version": {"repo": "mlperf-automations", "commit": "abc123"},
    }
