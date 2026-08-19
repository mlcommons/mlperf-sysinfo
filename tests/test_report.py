# Copyright 2026 MLCommons contributors
# SPDX-License-Identifier: Apache-2.0
"""Reading a captured file back: validate and show."""

from __future__ import annotations

import copy
import json

import pytest

from mlperf_sysinfo.errors import ConfigError
from mlperf_sysinfo.report import summarise, validate

GOOD_CAPTURE = {
    "division": "standardized",
    "system_name": "H100x8_vLLM",
    "system_availability_status": "available",
    "system_category": "datacenter",
    "system_size": "16x NVIDIA H100",
    "system_node_ensemble_count": 1,
    "system_node_ensemble_total": 2,
    "endpoint_url": "http://node1:8000",
    "serving_framework": "vLLM 0.9.0",
    "node_types": [
        {
            "system_node_ensemble_id": 1,
            "number_of_nodes": 2,
            "host_processor_model_name": "AMD EPYC 9654",
            "host_memory_capacity": "1.5 TB",
            "accelerator_info": [
                {
                    "accelerator_model_name": "NVIDIA H100",
                    "accelerators_per_node": 8,
                    "accelerator_memory_capacity": "80GiB",
                }
            ],
            "cooling": "air",
            "hw_notes": "hw note",
            "sw_notes": "sw note",
            "operating_system": "Ubuntu 22.04",
        }
    ],
    "node_config": "",
    "tensor_parallel": 8,
    "batch": 256,
    "config_summary": "TP 8",
    "config_summary_notes": "",
    "link_config": "https://example.invalid/configs",
    "mlperf_sysinfo": {
        "version": "0.1.0",
        "profile": "endpoints",
        "profile_round": "6.0",
        "shape": "nested",
        "captured_at": "2026-08-10T12:00:00+00:00",
        "complete": True,
        "nodes_expected": 2,
        "nodes_collected": 2,
    },
}


def write_capture(tmp_path, data, name="system_desc.json"):
    p = tmp_path / name
    p.write_text(json.dumps(data, indent=2))
    return p


class TestValidate:
    def test_a_good_file_is_valid(self, tmp_path):
        report = validate(write_capture(tmp_path, GOOD_CAPTURE))
        assert report.ok
        assert report.checked > 0

    def test_empty_required_field_is_a_problem(self, tmp_path):
        data = copy.deepcopy(GOOD_CAPTURE)
        data["endpoint_url"] = ""
        report = validate(write_capture(tmp_path, data))
        assert not report.ok
        assert any("endpoint_url" in p for p in report.problems)

    def test_placeholder_text_is_caught(self, tmp_path):
        data = copy.deepcopy(GOOD_CAPTURE)
        data["system_category"] = "Insert system category here"
        report = validate(write_capture(tmp_path, data))
        assert not report.ok
        assert any("placeholder" in p for p in report.problems)

    def test_placeholder_inside_a_node_type_is_caught(self, tmp_path):
        """The per-node metadata is where the config's notes and cooling land."""
        data = copy.deepcopy(GOOD_CAPTURE)
        data["node_types"][0]["cooling"] = "CHANGEME"
        report = validate(write_capture(tmp_path, data))
        assert not report.ok
        assert any("placeholder" in p for p in report.problems)

    def test_partial_capture_is_refused(self, tmp_path):
        data = copy.deepcopy(GOOD_CAPTURE)
        data["mlperf_sysinfo"]["complete"] = False
        data["mlperf_sysinfo"]["nodes_collected"] = 1
        report = validate(write_capture(tmp_path, data))
        assert not report.ok
        assert any("partial" in p.lower() for p in report.problems)

    def test_no_hardware_is_a_problem(self, tmp_path):
        data = copy.deepcopy(GOOD_CAPTURE)
        data["node_types"] = []
        report = validate(write_capture(tmp_path, data))
        assert not report.ok
        assert any("node_types" in p for p in report.problems)

    def test_uses_the_stamped_profile(self, tmp_path):
        assert validate(write_capture(tmp_path, GOOD_CAPTURE)).profile_name == "endpoints"

    def test_profile_override(self, tmp_path):
        report = validate(write_capture(tmp_path, GOOD_CAPTURE), profile_name="inference")
        assert report.profile_name == "inference"

    def test_unstamped_file_says_what_to_do(self, tmp_path):
        data = copy.deepcopy(GOOD_CAPTURE)
        del data["mlperf_sysinfo"]
        with pytest.raises(ConfigError, match="--profile"):
            validate(write_capture(tmp_path, data))

    def test_missing_file(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            validate(tmp_path / "nope.json")

    def test_not_json(self, tmp_path):
        p = tmp_path / "x.json"
        p.write_text("this is not json")
        with pytest.raises(ConfigError, match="not valid JSON"):
            validate(p)


class TestNodeScopedFields:
    """cooling, hw_notes and sw_notes moved inside node_types in 8.2.1.

    check warns when they are empty, so validate has to look at them too --
    otherwise the two commands disagree about the same file.
    """

    def test_an_empty_node_level_recommendation_warns(self, tmp_path):
        data = copy.deepcopy(GOOD_CAPTURE)
        data["node_types"][0]["cooling"] = ""
        report = validate(write_capture(tmp_path, data))
        assert report.ok  # a recommendation, so not a problem
        assert any("cooling on every node type is empty" in w for w in report.warnings)

    def test_one_node_type_differing_from_another_is_not_a_gap(self, tmp_path):
        data = copy.deepcopy(GOOD_CAPTURE)
        second = copy.deepcopy(data["node_types"][0])
        second["system_node_ensemble_id"] = 2
        second["cooling"] = ""
        data["node_types"].append(second)
        report = validate(write_capture(tmp_path, data))
        assert not any("cooling" in w for w in report.warnings)

    def test_a_detection_failure_is_surfaced(self, tmp_path):
        """"N/A" is the probe saying it looked and found nothing. Left alone it
        reads to a reviewer like "not applicable"."""
        data = copy.deepcopy(GOOD_CAPTURE)
        data["node_types"][0]["host_memory_configuration"] = "N/A"
        report = validate(write_capture(tmp_path, data))
        assert any(
            "node_types[0].host_memory_configuration was not detected" in w
            for w in report.warnings
        )

    def test_a_detection_failure_inside_accelerator_info_is_surfaced(self, tmp_path):
        data = copy.deepcopy(GOOD_CAPTURE)
        data["node_types"][0]["accelerator_info"][0]["accelerator_interconnect"] = "N/A"
        report = validate(write_capture(tmp_path, data))
        assert any(
            "node_types[0].accelerator_info[0].accelerator_interconnect was not detected" in w
            for w in report.warnings
        )

    def test_a_real_value_is_not_flagged(self, tmp_path):
        report = validate(write_capture(tmp_path, GOOD_CAPTURE))
        assert not any("was not detected" in w for w in report.warnings)

    def test_a_flat_capture_is_scanned_too(self, tmp_path):
        """A flat file has no node_types, so scanning only those found nothing.
        The collection script blanks "N/A" for flat but not "Not detected"."""
        data = {
            "submitter": "MyOrg",
            "system_name": "sut",
            "number_of_nodes": 1,
            "host_processor_model_name": "EPYC",
            "accelerator_frequency": "Not detected: nvidia-smi absent",
            "mlperf_sysinfo": {"profile": "inference", "shape": "flat", "complete": True},
        }
        report = validate(write_capture(tmp_path, data))
        assert any("accelerator_frequency was not detected" in w for w in report.warnings)


class TestShow:
    def test_summarises_the_headline_facts(self, tmp_path):
        s = summarise(write_capture(tmp_path, GOOD_CAPTURE))
        assert s.system_name == "H100x8_vLLM"
        assert s.profile == "endpoints"
        assert s.complete
        assert s.accelerator_total == 16
        assert s.framework == "vLLM 0.9.0"

    def test_separates_detected_from_supplied(self, tmp_path):
        s = summarise(write_capture(tmp_path, GOOD_CAPTURE))
        detected = dict(s.detected)
        supplied = dict(s.supplied)
        assert "cpu" in detected
        assert detected["accelerator"] == "NVIDIA H100"
        assert "division" in supplied
        assert "cpu" not in supplied

    def test_marks_a_partial_file(self, tmp_path):
        data = copy.deepcopy(GOOD_CAPTURE)
        data["mlperf_sysinfo"]["complete"] = False
        assert not summarise(write_capture(tmp_path, data)).complete
