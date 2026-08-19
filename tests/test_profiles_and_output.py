# Copyright 2026 MLCommons contributors
# SPDX-License-Identifier: Apache-2.0
"""Profiles, output shaping, and provenance."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from mlperf_sysinfo import profiles
from mlperf_sysinfo.config import find_placeholders, load_config
from mlperf_sysinfo.errors import ProfileError
from mlperf_sysinfo.output import (
    ENDPOINTS_ACCELERATOR_FIELDS,
    ENDPOINTS_NODE_FIELDS,
    ENDPOINTS_TOP_FIELDS,
    accelerator_count,
    build_endpoints,
    build_flat,
    endpoints_system_size,
    shape,
)

from .conftest import GOOD_CONFIG, write_yaml

#: The JSON skeleton from endpoints rules 8.2.1, verbatim.
TEMPLATE_PATH = Path(__file__).parent / "fixtures" / "endpoints_8_2_1_template.json"


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


class TestBenchmarkVariation:
    """Which field set the automation is asked to assemble.

    Getting this wrong is silent: the automation just returns the other
    benchmark's field set, and the shaping step reports every field it cannot
    find as empty.
    """

    def test_endpoints_asks_for_the_endpoints_field_set(self):
        assert profiles.load("endpoints").benchmark == "endpoints"

    def test_inference_asks_for_the_inference_field_set(self):
        assert profiles.load("inference").benchmark == "inference"

    def test_every_builtin_names_one(self):
        for name in profiles.available():
            assert profiles.load(name).benchmark, f"{name} would get the default field set"


class TestEndpointsSystemSize:
    def test_the_example_from_the_rules(self):
        """8.2's own worked example: two node types, 72 and 144 accelerators."""
        node_types = [
            {"number_of_nodes": 1, "accelerator_info": [{"accelerators_per_node": 72}]},
            {"number_of_nodes": 2, "accelerator_info": [{"accelerators_per_node": 72}]},
        ]
        assert endpoints_system_size(node_types) == "72 accelerators + 144 accelerators"

    def test_a_cpu_only_system_says_zero(self):
        """The field counts accelerators. Falling back to host processors, as
        the Inference convention does, would answer a different question."""
        node_types = [{"number_of_nodes": 4, "host_processor_model_name": "EPYC"}]
        assert endpoints_system_size(node_types) == "0 accelerators"

    def test_several_models_in_one_node_type_are_summed(self):
        node_types = [
            {
                "number_of_nodes": 2,
                "accelerator_info": [
                    {"accelerators_per_node": 4},
                    {"accelerators_per_node": 2},
                ],
            }
        ]
        assert endpoints_system_size(node_types) == "12 accelerators"

    def test_nothing_collected_is_empty(self):
        assert endpoints_system_size([]) == ""

    def test_an_undetected_count_is_not_reported_as_zero(self):
        """"0 accelerators" would be indistinguishable from a CPU-only SUT."""
        node_types = [
            {
                "number_of_nodes": 1,
                "accelerator_info": [
                    {"accelerator_model_name": "GPU", "accelerators_per_node": "N/A"}
                ],
            }
        ]
        assert endpoints_system_size(node_types) == "not detected"


class TestAcceleratorCount:
    def test_counts_the_nested_list(self):
        node_types = [
            {"number_of_nodes": 2, "accelerator_info": [{"accelerators_per_node": 8}]}
        ]
        assert accelerator_count(node_types) == 16

    def test_sums_every_model_in_a_node_type(self):
        node_types = [
            {
                "number_of_nodes": 1,
                "accelerator_info": [
                    {"accelerators_per_node": 2},
                    {"accelerators_per_node": 1},
                ],
            }
        ]
        assert accelerator_count(node_types) == 3

    def test_falls_back_to_the_flat_field(self):
        assert accelerator_count([{"number_of_nodes": 2, "accelerators_per_node": 4}]) == 8

    def test_a_node_type_with_no_accelerator_counts_zero(self):
        assert accelerator_count([{"number_of_nodes": 4, "accelerator_info": []}]) == 0

    def test_detection_failures_do_not_count(self):
        node_types = [
            {"number_of_nodes": 2, "accelerator_info": [{"accelerators_per_node": "N/A"}]}
        ]
        assert accelerator_count(node_types) == 0


class TestEndpointsShape:
    def test_metadata_comes_from_the_config_not_the_collection(
        self, good_config_file, collected
    ):
        cfg = load_config(good_config_file)
        out = build_endpoints(collected, cfg)
        assert out["system_name"] == "H100x8_vLLM"
        assert out["system_category"] == "datacenter"
        assert out["system_availability_status"] == "available"
        assert out["division"] == "standardized"

    def test_no_placeholder_text_survives(self, good_config_file, collected):
        cfg = load_config(good_config_file)
        out = build_endpoints(collected, cfg)
        assert not find_placeholders(out), find_placeholders(out)

    def test_unsupplied_fields_are_empty_not_placeholders(self, tmp_path, collected):
        data = copy.deepcopy(GOOD_CONFIG)
        data["submission"].pop("division")
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        assert build_endpoints(collected, cfg)["division"] == ""

    def test_writes_exactly_the_8_2_1_template(self, good_config_file, collected):
        """Checked against the published skeleton, not against our own lists.

        `tests/fixtures/endpoints_8_2_1_template.json` is the JSON from
        endpoints rules 8.2.1 verbatim. Asserting `tuple(out) ==
        ENDPOINTS_TOP_FIELDS` would only prove the code agrees with itself, so
        a mistyped or forgotten field name would pass.
        """
        cfg = load_config(good_config_file)
        out = build_endpoints(collected, cfg)
        template = json.loads(TEMPLATE_PATH.read_text())

        assert list(out) == list(template)
        assert list(out["node_types"][0]) == list(template["node_types"][0])
        assert list(out["node_types"][0]["accelerator_info"][0]) == list(
            template["node_types"][0]["accelerator_info"][0]
        )

    def test_the_field_lists_match_the_template(self):
        """The lists the shaping code is driven by, against the same skeleton."""
        template = json.loads(TEMPLATE_PATH.read_text())
        assert ENDPOINTS_TOP_FIELDS == tuple(template)
        assert ENDPOINTS_NODE_FIELDS == tuple(template["node_types"][0])
        assert ENDPOINTS_ACCELERATOR_FIELDS == tuple(
            template["node_types"][0]["accelerator_info"][0]
        )

    def test_the_template_still_has_47_fields(self):
        """8.2.1 says 47, checked field for field against the 8.2 table. If a
        rules update changes that, this file needs a deliberate look."""
        template = json.loads(TEMPLATE_PATH.read_text())

        def data_fields(obj: dict) -> int:
            total = 0
            for value in obj.values():
                if isinstance(value, list) and value and isinstance(value[0], dict):
                    total += data_fields(value[0])  # the container carries no data
                else:
                    total += 1
            return total

        assert data_fields(template) == 47

    def test_value_types_match_the_template(self, good_config_file, collected):
        """A count written as "" or a string written as 0 is a schema break."""
        cfg = load_config(good_config_file)
        template = json.loads(TEMPLATE_PATH.read_text())
        node = build_endpoints({}, cfg)["node_types"]
        assert node == []  # nothing to compare per-node against on an empty capture

        out = build_endpoints(collected, cfg)
        pairs = [(out, template), (out["node_types"][0], template["node_types"][0])]
        pairs.append(
            (
                out["node_types"][0]["accelerator_info"][0],
                template["node_types"][0]["accelerator_info"][0],
            )
        )
        for produced, expected in pairs:
            for key, sample in expected.items():
                if isinstance(sample, list):
                    continue
                assert isinstance(produced[key], type(sample)), (
                    f"{key}: expected {type(sample).__name__}, "
                    f"got {type(produced[key]).__name__} ({produced[key]!r})"
                )

    @pytest.mark.parametrize(
        "gone",
        [
            "submitter_org_names",
            "submitter_contact",
            "submission_id",
            "model_name",
            "model_precision",
            "link_to_model",
            "dataset_name",
            "dataset_type",
            "input_token_average",
            "measured_accuracy_score",
            "system_type_detail",
            "hw_notes",
            "sw_notes",
            "cooling",
            "container_link",
        ],
    )
    def test_fields_that_left_the_system_description(
        self, good_config_file, collected, gone
    ):
        """Model, dataset and submitter metadata moved out; notes moved inward."""
        cfg = load_config(good_config_file)
        assert gone not in build_endpoints(collected, cfg)

    def test_the_run_configuration_is_carried_through(self, good_config_file, collected):
        cfg = load_config(good_config_file)
        out = build_endpoints(collected, cfg)
        assert out["tensor_parallel"] == 8
        assert out["batch"] == 256
        assert out["config_summary"] == "TP 8"
        assert out["endpoint_url"] == "http://node1:8000"

    def test_notes_land_on_every_node_type(self, good_config_file, collected):
        cfg = load_config(good_config_file)
        node = build_endpoints(collected, cfg)["node_types"][0]
        assert node["hw_notes"] == "hw note"
        assert node["sw_notes"] == "sw note"
        assert node["cooling"] == "air"

    def test_a_missing_run_field_is_zero_not_empty_string(self, good_config_file, collected):
        cfg = load_config(good_config_file)
        collected.pop("batch")
        assert build_endpoints(collected, cfg)["batch"] == 0

    def test_the_collection_stamp_does_not_leak_into_the_field_set(
        self, good_config_file, collected
    ):
        cfg = load_config(good_config_file)
        assert "mlc_scripts_version" not in build_endpoints(collected, cfg)

    def test_keeps_node_types(self, good_config_file, collected):
        cfg = load_config(good_config_file)
        out = build_endpoints(collected, cfg)
        assert len(out["node_types"]) == 1
        assert out["system_node_ensemble_total"] == 2

    def test_config_can_override_the_computed_system_size(self, tmp_path, collected):
        data = copy.deepcopy(GOOD_CONFIG)
        data["system"]["size"] = "one very large computer"
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        assert build_endpoints(collected, cfg)["system_size"] == "one very large computer"

    def test_system_size_counts_accelerators_not_models(self, good_config_file, collected):
        """8.2 redefined this field for endpoints: "Number of accelerators per
        node type". The collection script computes the Inference convention
        ("16x NVIDIA H100 80GB HBM3"), which is a different thing."""
        cfg = load_config(good_config_file)
        assert build_endpoints(collected, cfg)["system_size"] == "16 accelerators"

    def test_no_node_types_does_not_explode(self, good_config_file):
        cfg = load_config(good_config_file)
        out = build_endpoints({}, cfg)
        assert out["node_types"] == []
        assert out["system_node_ensemble_total"] == 0


class TestFlatShape:
    def test_hardware_comes_through_from_the_collection(
        self, good_config_file, collected_flat
    ):
        cfg = load_config(good_config_file)
        flat = build_flat(collected_flat, cfg, profiles.load("inference"))
        assert flat["host_processor_model_name"] == "AMD EPYC 9654"
        assert flat["accelerator_model_name"] == "NVIDIA H100 80GB HBM3"
        assert flat["number_of_nodes"] == 2

    def test_fields_only_the_inference_checker_asks_for_survive(
        self, good_config_file, collected_flat
    ):
        """These are absent from the endpoints field set, so they are the
        regression canary for asking the automation for the wrong one."""
        cfg = load_config(good_config_file)
        flat = build_flat(collected_flat, cfg, profiles.load("inference"))
        assert flat["host_processor_frequency"] == "3.7 GHz"
        assert flat["accelerator_frequency"] == "1980 MHz"
        assert flat["accelerator_on-chip_memories"] == "Shared Memory: 228 KB/block"

    def test_config_metadata_overwrites_the_automation_defaults(
        self, good_config_file, collected_flat
    ):
        cfg = load_config(good_config_file)
        flat = build_flat(collected_flat, cfg, profiles.load("inference"))
        assert flat["submitter"] == "MyOrg"
        assert flat["system_type"] == "datacenter"
        assert flat["status"] == "available"
        assert flat["framework"] == "vLLM 0.9.0"
        assert not find_placeholders(flat), find_placeholders(flat)

    def test_notes_and_cooling_come_from_the_config(self, good_config_file, collected_flat):
        cfg = load_config(good_config_file)
        flat = build_flat(collected_flat, cfg, profiles.load("inference"))
        assert flat["hw_notes"] == "hw note"
        assert flat["sw_notes"] == "sw note"
        assert flat["cooling"] == "air"

    def test_the_collection_stamp_does_not_leak(self, good_config_file, collected_flat):
        cfg = load_config(good_config_file)
        assert "mlc_scripts_version" not in build_flat(
            collected_flat, cfg, profiles.load("inference")
        )

    def test_no_collection_does_not_explode(self, good_config_file):
        cfg = load_config(good_config_file)
        flat = build_flat({}, cfg, profiles.load("inference"))
        assert flat["system_name"] == "H100x8_vLLM"

    def test_extra_field_groups(self, good_config_file, collected_flat):
        cfg = load_config(good_config_file)
        p = profiles.load("inference").model_copy(update={"extra_field_groups": ["power"]})
        flat = build_flat(collected_flat, cfg, p)
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
