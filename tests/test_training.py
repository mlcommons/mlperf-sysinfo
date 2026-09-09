# Copyright 2026 MLCommons contributors
# SPDX-License-Identifier: Apache-2.0
"""The training profile: field set, availability vocabulary, and what refuses.

The authority for this format is ``mlperf_logging/system_desc_checker`` in
mlcommons/logging -- a different checker from the Inference one, with its own
field list. Every expectation here is traceable to that file rather than to
the inference shape, which is why the field list is repeated in full below
instead of being imported from the code under test.
"""

from __future__ import annotations

import json

import pytest

from mlperf_sysinfo import profiles
from mlperf_sysinfo.collector import output_filename
from mlperf_sysinfo.config import SysinfoConfig
from mlperf_sysinfo.errors import CaptureError
from mlperf_sysinfo.output import (
    TRAINING_STATUS_OPTIONS,
    build_training,
    normalize_training_status,
    output_key_for,
    shape,
)
from mlperf_sysinfo.preflight import run_check
from mlperf_sysinfo.report import summarise, validate

#: required_fields in mlperf_logging/system_desc_checker/system_desc_checker.py,
#: verbatim and in order. If the checker changes, this list is what should fail.
CHECKER_REQUIRED_FIELDS = [
    "submitter",
    "division",
    "status",
    "system_name",
    "number_of_nodes",
    "host_processors_per_node",
    "host_processor_model_name",
    "host_processor_core_count",
    "host_processor_vcpu_count",
    "host_processor_frequency",
    "host_processor_caches",
    "host_processor_interconnect",
    "host_memory_capacity",
    "host_storage_type",
    "host_storage_capacity",
    "host_networking",
    "host_networking_topology",
    "host_memory_configuration",
    "accelerators_per_node",
    "accelerator_model_name",
    "accelerator_host_interconnect",
    "accelerator_frequency",
    "accelerator_on-chip_memories",
    "accelerator_memory_configuration",
    "accelerator_memory_capacity",
    "accelerator_interconnect",
    "accelerator_interconnect_topology",
    "cooling",
    "hw_notes",
    "framework",
    "other_software_stack",
    "operating_system",
    "sw_notes",
]

TRAINING_CONFIG = {
    "profile": "training",
    "system": {
        "name": "dgx-h100-n8",
        "availability": "Available on-premise",
        "accelerator": "cuda",
        "cooling": "air",
        "networking_topology": "rail-optimized fat tree, 8x400G per node",
    },
    "nodes": {"include_local": True},
    "training": {
        "framework": "NVIDIA PyTorch Release 25.04",
        "framework_name": "ngc25.04_pytorch",
    },
    "submission": {
        "submitter": "MyOrg",
        "division": "closed",
        "notes": {"hardware": "8-node DGX H100", "software": "NCCL 2.21"},
    },
}


@pytest.fixture
def training_config() -> SysinfoConfig:
    return SysinfoConfig.model_validate(TRAINING_CONFIG)


@pytest.fixture
def training_profile():
    return profiles.load("training")


@pytest.fixture
def collected_training() -> dict:
    """What the automation returns for ``_training``.

    The submitter-supplied fields carry the automation's own defaults, which
    are placeholder strings, and two probes have failed -- both are what the
    shaping step exists to deal with.
    """
    return {
        "submitter": "Insert submitter here",
        "division": "",
        "status": "",
        "system_name": "Insert system name here",
        "number_of_nodes": "8",
        "host_processors_per_node": "2",
        "host_processor_model_name": "Intel(R) Xeon(R) Platinum 8480C",
        "host_processor_core_count": "112",
        "host_processor_vcpu_count": "224",
        "host_processor_frequency": "Not detected: dmidecode requires sudo",
        "host_processor_caches": "L1d 5.3 MiB, L2 224 MiB, L3 210 MiB",
        "host_processor_interconnect": "N/A",
        "host_memory_capacity": "2.0 TiB",
        "host_storage_type": "NVMe SSD",
        "host_storage_capacity": "8x 3.84TB NVMe",
        "host_networking": "8x ConnectX-7 400Gb/s",
        "host_networking_topology": "",
        "host_memory_configuration": "32x 64GB DDR5-4800",
        "accelerators_per_node": "8",
        "accelerator_model_name": "NVIDIA H100 80GB HBM3",
        "accelerator_host_interconnect": "PCIe Gen5 x16",
        "accelerator_frequency": "1980 MHz",
        "accelerator_on-chip_memories": "50MB L2",
        "accelerator_memory_configuration": "HBM3",
        "accelerator_memory_capacity": "80 GB",
        "accelerator_interconnect": "NVLink 4.0 900GB/s",
        "accelerator_interconnect_topology": "NVSwitch, all-to-all",
        "cooling": "Insert cooling here",
        "hw_notes": "",
        "framework": "",
        "other_software_stack": "CUDA 12.4, Driver 550.54.15",
        "operating_system": "Ubuntu 22.04.4 LTS",
        "sw_notes": "",
        "mlc_scripts_version": {"git_commit": "deadbeef"},
    }


class TestTheCheckersFieldSet:
    def test_every_required_field_is_written(self, collected_training, training_config):
        out = build_training(collected_training, training_config)
        missing = [f for f in CHECKER_REQUIRED_FIELDS if f not in out]
        assert missing == [], f"the checker would report: {missing}"

    def test_fields_are_in_the_checkers_order(self, collected_training, training_config):
        out = build_training(collected_training, training_config)
        written = [k for k in out if k in CHECKER_REQUIRED_FIELDS]
        assert written == CHECKER_REQUIRED_FIELDS

    def test_no_inference_only_field_leaks_in(self, collected_training, training_config):
        """The training field set is closed. An inference extra reaching the
        file is a field no training reviewer has a column for."""
        out = build_training(collected_training, training_config)
        for absent in (
            "system_type",
            "submitter_contact",
            "system_type_detail",
            "other_hardware",
            "system_size",
        ):
            assert absent not in out

    def test_every_value_is_a_string(self, collected_training, training_config):
        """Counts included: "8", not 8."""
        out = build_training(collected_training, training_config)
        assert all(isinstance(v, str) for v in out.values())

    def test_framework_name_is_written_when_set(self, collected_training, training_config):
        out = build_training(collected_training, training_config)
        assert out["framework_name"] == "ngc25.04_pytorch"
        assert list(out).index("framework_name") == list(out).index("framework") + 1

    def test_framework_name_is_absent_rather_than_empty(
        self, collected_training, training_config
    ):
        """The checker does not ask for it, so a blank one is noise rather
        than a field left to fill in."""
        training_config.training.framework_name = None
        assert "framework_name" not in build_training(collected_training, training_config)


class TestConfigBeatsTheAutomationsDefaults:
    def test_placeholders_never_reach_the_file(self, collected_training, training_config):
        out = build_training(collected_training, training_config)
        assert out["submitter"] == "MyOrg"
        assert out["system_name"] == "dgx-h100-n8"
        assert out["cooling"] == "air"
        assert not any("Insert" in v for v in out.values())

    def test_undetectable_fields_come_from_the_config(
        self, collected_training, training_config
    ):
        out = build_training(collected_training, training_config)
        assert out["host_networking_topology"] == "rail-optimized fat tree, 8x400G per node"
        assert out["framework"] == "NVIDIA PyTorch Release 25.04"
        assert out["hw_notes"] == "8-node DGX H100"
        assert out["sw_notes"] == "NCCL 2.21"

    def test_probed_hardware_is_kept(self, collected_training, training_config):
        out = build_training(collected_training, training_config)
        assert out["host_processor_model_name"] == "Intel(R) Xeon(R) Platinum 8480C"
        assert out["accelerator_model_name"] == "NVIDIA H100 80GB HBM3"
        assert out["number_of_nodes"] == "8"

    def test_detection_failures_become_empty_not_prose(
        self, collected_training, training_config
    ):
        """"Not detected: dmidecode requires sudo" in a submission field reads
        as a real answer. Empty is how a training submission says "not
        disclosed"."""
        out = build_training(collected_training, training_config)
        assert out["host_processor_frequency"] == ""
        assert out["host_processor_interconnect"] == ""

    def test_division_is_lowercased(self, collected_training, training_config):
        training_config.submission.division = "Closed"
        assert build_training(collected_training, training_config)["division"] == "closed"


class TestAvailabilityVocabulary:
    """Training accepts four strings. Anything else fails the checker outright."""

    @pytest.mark.parametrize("canonical", TRAINING_STATUS_OPTIONS)
    def test_canonical_values_pass_through(self, canonical):
        assert normalize_training_status(canonical) == (canonical, None)

    def test_matching_ignores_case(self):
        assert normalize_training_status("available CLOUD")[0] == "Available cloud"

    @pytest.mark.parametrize(
        ("shorthand", "expected"),
        [
            ("on-prem", "Available on-premise"),
            ("onprem", "Available on-premise"),
            ("cloud", "Available cloud"),
            ("rdi", "Research, Development, or Internal (RDI)"),
            ("internal", "Research, Development, or Internal (RDI)"),
        ],
    )
    def test_shorthands_are_accepted(self, shorthand, expected):
        assert normalize_training_status(shorthand) == (expected, None)

    def test_bare_available_is_refused_with_the_reason(self):
        """It is what Inference uses, and training splits it in two. Guessing
        would silently mislabel the submission."""
        value, error = normalize_training_status("available")
        assert value == ""
        assert "on-premise and cloud" in error

    def test_an_unknown_value_lists_the_legal_ones(self):
        _, error = normalize_training_status("sort of available")
        assert all(option in error for option in TRAINING_STATUS_OPTIONS)

    def test_empty_is_not_an_error_here(self):
        """Missing is what the profile's requires reports. Saying it twice in
        different words helps nobody."""
        assert normalize_training_status("") == ("", None)
        assert normalize_training_status(None) == ("", None)


class TestCheckRefusesBeforeCollecting:
    def _check(self, **system):
        config = SysinfoConfig.model_validate(
            {**TRAINING_CONFIG, "system": {**TRAINING_CONFIG["system"], **system}}
        )
        return run_check(config, profiles.load("training"), skip_network=True)

    def test_a_good_config_passes(self):
        assert not self._check().has_config_problems

    def test_an_inference_style_availability_stops_the_run(self):
        """Catching it here is the point: the alternative is a submitter
        finding out from the training checker after the capture has run."""
        report = self._check(availability="available")
        assert report.has_config_problems
        assert any(path == "system.availability" for path, _ in report.missing_required)

    def test_the_refusal_does_not_also_count_as_satisfied(self):
        good = self._check().satisfied_count
        assert self._check(availability="available").satisfied_count == good - 1

    def test_a_shorthand_availability_is_accepted(self):
        assert not self._check(availability="rdi").has_config_problems

    @pytest.mark.parametrize(
        "path", ["system.cooling", "system.networking_topology", "training.framework"]
    )
    def test_the_undetectable_fields_are_required(self, path):
        assert path in profiles.load("training").requires


class TestStaleAutomationIsRefused:
    def test_a_nested_document_stops_the_capture(self, training_config):
        """mlc-scripts without a _training variation silently returns the
        endpoints field set. Shaping that would write every hardware field
        empty and look like a successful capture."""
        with pytest.raises(CaptureError, match="_training"):
            build_training({"node_types": [{"host_processor_model_name": "Xeon"}]}, training_config)


class TestTheWholeDocument:
    def _shaped(self, collected, config, profile):
        return shape(
            collected,
            config,
            profile,
            nodes_expected=8,
            nodes_collected=8,
            partial=False,
            package_version="1.0.0a5",
        )

    def test_shape_routes_training_by_benchmark(
        self, collected_training, training_config, training_profile
    ):
        """Both training and inference are flat, so shape alone cannot route."""
        out = self._shaped(collected_training, training_config, training_profile)
        assert out["status"] == "Available on-premise"
        assert "system_type" not in out

    def test_no_round_is_stamped(self, collected_training, training_config, training_profile):
        """An empty profile_round would read as a round that failed to record."""
        stamp = self._shaped(collected_training, training_config, training_profile)[
            "mlperf_sysinfo"
        ]
        assert "profile_round" not in stamp
        assert stamp["benchmark"] == "training"

    def test_validate_and_show_read_the_file_back(
        self, collected_training, training_config, training_profile, tmp_path
    ):
        out = self._shaped(collected_training, training_config, training_profile)
        path = tmp_path / "dgx-h100-n8.json"
        path.write_text(json.dumps(out, indent=2) + "\n")

        report = validate(path)
        assert report.problems == []

        summary = summarise(path)
        assert summary.node_count == 8
        assert summary.framework == "NVIDIA PyTorch Release 25.04"
        # The training supplied table, not the inference one.
        assert dict(summary.supplied)["status"] == "Available on-premise"

    def test_validate_reports_an_empty_required_field(
        self, collected_training, training_config, training_profile, tmp_path
    ):
        training_config.training.framework = None
        out = self._shaped(collected_training, training_config, training_profile)
        path = tmp_path / "dgx-h100-n8.json"
        path.write_text(json.dumps(out, indent=2) + "\n")
        assert any("framework is empty" in p for p in validate(path).problems)


class TestTheOutputFilename:
    """A training submission stores the file as <submitter>/systems/<system_name>.json,
    so the name is part of the answer rather than a convention."""

    def test_the_system_name_becomes_the_filename(self, training_config, training_profile):
        assert output_filename(training_config, training_profile) == "dgx-h100-n8.json"

    def test_a_name_with_a_slash_cannot_escape_the_output_directory(
        self, training_config, training_profile
    ):
        training_config.system.name = "../../etc/passwd"
        assert "/" not in output_filename(training_config, training_profile)

    def test_the_config_still_wins(self, training_config, training_profile):
        training_config.output.file = "mine.json"
        assert output_filename(training_config, training_profile) == "mine.json"

    def test_other_profiles_are_untouched(self, training_config):
        assert output_filename(training_config, profiles.load("inference")) == "system_desc.json"


class TestValidateUsesTheTrainingKeyTable:
    def test_required_paths_map_to_training_fields(self, training_profile):
        for path in training_profile.requires:
            assert output_key_for(path, training_profile) is not None, path

    def test_it_does_not_map_to_inference_names(self, training_profile):
        assert output_key_for("system.availability", training_profile) == "status"
        assert output_key_for("system.cooling", training_profile) == "cooling"
        assert output_key_for("training.framework", training_profile) == "framework"
