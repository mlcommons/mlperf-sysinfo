# Copyright 2026 MLCommons contributors
# SPDX-License-Identifier: Apache-2.0
"""Profiles, output shaping, and provenance."""

from __future__ import annotations

import copy

import pytest
import yaml

from mlperf_sysinfo import profiles
from mlperf_sysinfo.config import load_config
from mlperf_sysinfo.errors import ProfileError
from mlperf_sysinfo.output import build_flat, build_nested, compute_system_size, shape

from .conftest import GOOD_CONFIG, write_yaml


class TestProfileLoading:
    def test_builtins_exist(self):
        assert {"endpoints", "inference"} <= set(profiles.available())

    def test_endpoints_is_nested(self):
        assert profiles.load("endpoints").shape == "nested"

    def test_inference_is_flat(self):
        assert profiles.load("inference").shape == "flat"

    def test_unknown_name_lists_the_alternatives(self):
        with pytest.raises(ProfileError, match="endpoints"):
            profiles.load("storage")

    def test_pinning_is_refused_with_an_explanation(self):
        with pytest.raises(ProfileError, match="current round"):
            profiles.load("endpoints@v6.0")

    def test_path_to_a_custom_profile(self, tmp_path):
        p = tmp_path / "mine.yaml"
        p.write_text(
            yaml.safe_dump(
                {
                    "name": "mine",
                    "title": "My Group",
                    "round": "6.0",
                    "shape": "flat",
                    "requires": {"system.name": "needed"},
                }
            )
        )
        loaded = profiles.load(str(p))
        assert loaded.name == "mine"
        assert not loaded.is_builtin

    def test_missing_custom_profile(self, tmp_path):
        with pytest.raises(ProfileError, match="not found"):
            profiles.load(str(tmp_path / "gone.yaml"))

    def test_every_builtin_requires_a_system_name(self):
        for name in profiles.available():
            assert "system.name" in profiles.load(name).requires


class TestSystemSize:
    def test_accelerators_win(self):
        entries = [
            {"number_of_nodes": 2, "accelerator_model_name": "H100", "accelerators_per_node": 8}
        ]
        assert compute_system_size(entries) == "16x H100"

    def test_falls_back_to_cpu(self):
        entries = [
            {
                "number_of_nodes": 2,
                "accelerator_model_name": "",
                "host_processor_model_name": "EPYC",
                "host_processors_per_node": 2,
            }
        ]
        assert compute_system_size(entries) == "4x EPYC"

    def test_joins_multiple_node_types(self):
        entries = [
            {"number_of_nodes": 1, "accelerator_model_name": "H100", "accelerators_per_node": 8},
            {"number_of_nodes": 2, "accelerator_model_name": "A100", "accelerators_per_node": 4},
        ]
        assert compute_system_size(entries) == "8x H100 + 8x A100"

    def test_ignores_detection_failures(self):
        entries = [
            {
                "number_of_nodes": 1,
                "accelerator_model_name": "Not available",
                "host_processor_model_name": "N/A",
            }
        ]
        assert compute_system_size(entries) == ""


class TestNestedShape:
    def test_metadata_comes_from_the_config_not_the_collection(
        self, good_config_file, collected
    ):
        cfg = load_config(good_config_file)
        out = build_nested(collected, cfg)
        assert out["system_name"] == "H100x8_vLLM"
        assert out["submitter_org_names"] == "MyOrg"
        assert out["model_name"] == "Llama-3.1-8B-Instruct"

    def test_no_placeholder_text_anywhere(self, good_config_file, collected):
        cfg = load_config(good_config_file)
        out = build_nested(collected, cfg)
        for value in out.values():
            if isinstance(value, str):
                assert "insert" not in value.lower()

    def test_unsupplied_fields_are_empty_strings(self, tmp_path, collected):
        data = copy.deepcopy(GOOD_CONFIG)
        data["submission"].pop("division")
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        assert build_nested(collected, cfg)["division"] == ""

    def test_keeps_node_types(self, good_config_file, collected):
        cfg = load_config(good_config_file)
        out = build_nested(collected, cfg)
        assert len(out["node_types"]) == 1
        assert out["system_node_ensemble_total"] == 2

    def test_strips_internal_node_name(self, good_config_file, collected):
        cfg = load_config(good_config_file)
        out = build_nested(collected, cfg)
        assert "system_node_name" not in out["node_types"][0]


class TestFlatShape:
    def test_lifts_hardware_to_the_top_level(self, good_config_file, collected):
        cfg = load_config(good_config_file)
        flat = build_flat(build_nested(collected, cfg), cfg, profiles.load("inference"))
        assert flat["host_processor_model_name"] == "AMD EPYC 9654"
        assert flat["accelerator_model_name"] == "NVIDIA H100 80GB HBM3"
        assert flat["number_of_nodes"] == 2

    def test_renames_to_checker_field_names(self, good_config_file, collected):
        cfg = load_config(good_config_file)
        flat = build_flat(build_nested(collected, cfg), cfg, profiles.load("inference"))
        assert flat["submitter"] == "MyOrg"
        assert flat["system_type"] == "datacenter"
        assert flat["status"] == "available"
        assert flat["framework"] == "vLLM 0.9.0"

    def test_heterogeneous_nodes_are_comma_joined(self, good_config_file, collected):
        cfg = load_config(good_config_file)
        two = copy.deepcopy(collected)
        second = copy.deepcopy(two["node_types"][0])
        second["accelerator_model_name"] = "NVIDIA A100"
        second["system_node_ensemble_id"] = 2
        two["node_types"].append(second)
        flat = build_flat(build_nested(two, cfg), cfg, profiles.load("inference"))
        assert "NVIDIA H100 80GB HBM3" in flat["accelerator_model_name"]
        assert "NVIDIA A100" in flat["accelerator_model_name"]

    def test_no_node_types_does_not_explode(self, good_config_file):
        cfg = load_config(good_config_file)
        flat = build_flat(build_nested({}, cfg), cfg, profiles.load("inference"))
        assert flat["host_processor_model_name"] == ""

    def test_extra_field_groups(self, good_config_file, collected):
        cfg = load_config(good_config_file)
        p = profiles.load("inference").model_copy(update={"extra_field_groups": ["power"]})
        flat = build_flat(build_nested(collected, cfg), cfg, p)
        assert "power_supply_details" in flat


class TestProvenance:
    def test_stamps_profile_and_round(self, good_config_file, collected):
        cfg = load_config(good_config_file)
        out = shape(
            collected,
            cfg,
            profiles.load("endpoints"),
            nodes_expected=2,
            nodes_collected=2,
            partial=False,
            package_version="9.9.9",
        )
        stamp = out["mlperf_sysinfo"]
        assert stamp["profile"] == "endpoints"
        assert stamp["profile_round"] == "6.0"
        assert stamp["version"] == "9.9.9"
        assert stamp["complete"] is True
        assert "warning" not in stamp

    def test_partial_capture_is_marked_and_warned(self, good_config_file, collected):
        cfg = load_config(good_config_file)
        out = shape(
            collected,
            cfg,
            profiles.load("endpoints"),
            nodes_expected=2,
            nodes_collected=1,
            partial=True,
            package_version="9.9.9",
        )
        stamp = out["mlperf_sysinfo"]
        assert stamp["complete"] is False
        assert "PARTIAL" in stamp["warning"]
        assert stamp["nodes_collected"] == 1

    def test_carries_the_automation_version(self, good_config_file, collected):
        cfg = load_config(good_config_file)
        out = shape(
            collected,
            cfg,
            profiles.load("endpoints"),
            nodes_expected=1,
            nodes_collected=1,
            partial=False,
            package_version="0.1.0",
        )
        assert out["mlperf_sysinfo"]["mlc_scripts"]["commit"] == "abc123"
