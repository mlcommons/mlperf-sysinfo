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
    "submitter_org_names": "MyOrg",
    "submitter_contact": "a@b.com",
    "system_name": "H100x8_vLLM",
    "system_category": "datacenter",
    "system_availability_status": "available",
    "system_size": "16x NVIDIA H100",
    "division": "standardized",
    "model_name": "Llama-3.1-8B",
    "model_precision": "fp8",
    "dataset_name": "cnn_dailymail",
    "serving_framework": "vLLM 0.9.0",
    "system_node_ensemble_total": 2,
    "node_types": [
        {
            "number_of_nodes": 2,
            "accelerator_model_name": "NVIDIA H100",
            "accelerators_per_node": 8,
            "host_processor_model_name": "AMD EPYC 9654",
        }
    ],
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
        data["submitter_contact"] = ""
        report = validate(write_capture(tmp_path, data))
        assert not report.ok
        assert any("submitter_contact" in p for p in report.problems)

    def test_placeholder_text_is_caught(self, tmp_path):
        data = copy.deepcopy(GOOD_CAPTURE)
        data["submitter_org_names"] = "Insert your organization name here"
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
        assert "submitter" in supplied
        assert "cpu" not in supplied

    def test_marks_a_partial_file(self, tmp_path):
        data = copy.deepcopy(GOOD_CAPTURE)
        data["mlperf_sysinfo"]["complete"] = False
        assert not summarise(write_capture(tmp_path, data)).complete
