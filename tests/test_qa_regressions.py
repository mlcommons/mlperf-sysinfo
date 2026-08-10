# Copyright 2026 MLCommons contributors
# SPDX-License-Identifier: Apache-2.0
"""Regressions for defects found in QA.

Each test here maps to a defect that shipped in the first cut. They exist
because the original suite passed while the bugs were live.
"""

from __future__ import annotations

import copy
import json

import pytest

from mlperf_sysinfo import collector as capture_mod
from mlperf_sysinfo import preflight as check_mod
from mlperf_sysinfo import profiles
from mlperf_sysinfo.collector import RAW_FILENAME, WORK_DIRNAME, capture
from mlperf_sysinfo.config import find_placeholders, is_placeholder, load_config
from mlperf_sysinfo.errors import CaptureError
from mlperf_sysinfo.output import build_nested, compute_system_size, is_not_detected
from mlperf_sysinfo.preflight import NodeStatus, ProbeStatus, run_check
from mlperf_sysinfo.report import validate

from .conftest import GOOD_CONFIG, write_yaml
from .test_check_and_capture import _FakeMlc
from .test_report import GOOD_CAPTURE, write_capture


@pytest.fixture
def endpoints_profile():
    return profiles.load("endpoints")


@pytest.fixture
def all_reachable(monkeypatch):
    monkeypatch.setattr(check_mod, "check_node", lambda t, a: NodeStatus(t, True, "H100 x 8"))
    monkeypatch.setattr(check_mod, "probe_endpoint", lambda u: ProbeStatus(u, True, "vLLM"))
    monkeypatch.setattr(check_mod, "check_serving_log", lambda t, p: ProbeStatus(p, True, ""))


class TestValidateCatchesChangeme:
    """Defect 1: validate's placeholder list omitted the one `init` writes."""

    def test_changeme_in_output_is_refused(self, tmp_path):
        data = copy.deepcopy(GOOD_CAPTURE)
        data["submitter_org_names"] = "CHANGEME"
        report = validate(write_capture(tmp_path, data))
        assert not report.ok
        assert any("placeholder" in p for p in report.problems)

    def test_changeme_with_a_suffix_is_refused(self, tmp_path):
        data = copy.deepcopy(GOOD_CAPTURE)
        data["submitter_contact"] = "CHANGEME@example.com"
        assert not validate(write_capture(tmp_path, data)).ok

    def test_angle_bracket_placeholder_is_refused(self, tmp_path):
        data = copy.deepcopy(GOOD_CAPTURE)
        data["model_name"] = "<your model here>"
        assert not validate(write_capture(tmp_path, data)).ok


class TestValidateWalksNestedData:
    """Defect 3: the sweep only looked at top-level keys."""

    def test_placeholder_inside_node_types_is_found(self, tmp_path):
        data = copy.deepcopy(GOOD_CAPTURE)
        data["node_types"][0]["cooling"] = "CHANGEME"
        report = validate(write_capture(tmp_path, data))
        assert not report.ok
        assert any("node_types[0].cooling" in p for p in report.problems)

    def test_provenance_block_is_not_scanned(self, tmp_path):
        """The stamp is ours; it must never be mistaken for user content."""
        assert validate(write_capture(tmp_path, GOOD_CAPTURE)).ok


class TestCheckScansEveryField:
    """Defect 2: only profile-named fields were scanned for placeholders."""

    def test_placeholder_in_an_unrequired_field_blocks(
        self, tmp_path, endpoints_profile, all_reachable
    ):
        data = copy.deepcopy(GOOD_CONFIG)
        data["system"]["cooling"] = "CHANGEME"
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        report = run_check(cfg, endpoints_profile)
        assert report.has_config_problems
        assert any(p == "system.cooling" for p, _ in report.placeholder_other)

    def test_insert_here_text_blocks(self, tmp_path, endpoints_profile, all_reachable):
        data = copy.deepcopy(GOOD_CONFIG)
        data["submission"]["notes"] = {"hardware": "Insert your hardware notes here"}
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        assert run_check(cfg, endpoints_profile).has_config_problems

    def test_genuine_free_text_is_not_a_placeholder(
        self, tmp_path, endpoints_profile, all_reachable
    ):
        """'insert' is anchored, so real prose survives."""
        data = copy.deepcopy(GOOD_CONFIG)
        data["submission"]["notes"] = {"hardware": "insert card in slot 3 before boot"}
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        assert run_check(cfg, endpoints_profile).ok

    def test_capture_refuses_a_config_with_a_stray_placeholder(
        self, tmp_path, endpoints_profile, all_reachable, monkeypatch, collected
    ):
        fake = _FakeMlc(collected)
        monkeypatch.setattr(capture_mod, "_require_mlc", lambda: fake)
        data = copy.deepcopy(GOOD_CONFIG)
        data["system"]["cooling"] = "CHANGEME"
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        with pytest.raises(Exception, match="not ready to capture"):
            capture(cfg, endpoints_profile)
        assert fake.calls == []


class TestPlaceholderHelpers:
    @pytest.mark.parametrize(
        "value",
        ["CHANGEME", "changeme@x.com", "Insert your organization name here",
         "insert system category here", "<your registry url>"],
    )
    def test_detected(self, value):
        assert is_placeholder(value)

    @pytest.mark.parametrize(
        "value",
        ["MyOrg", "insert card in slot 3", "here we go", "a<b", "Inserted"],
    )
    def test_not_detected(self, value):
        assert not is_placeholder(value)

    def test_find_walks_lists_and_dicts(self):
        found = find_placeholders({"a": [{"b": "CHANGEME"}], "c": "fine"})
        assert found == [("a[0].b", "CHANGEME")]


class TestStaleIntermediate:
    """Defect 4: a leftover raw file was shaped into a fresh output."""

    def test_stale_raw_is_not_reused(
        self, good_config_file, endpoints_profile, all_reachable, monkeypatch, collected
    ):
        cfg = load_config(good_config_file)
        work = cfg.output_dir / WORK_DIRNAME
        work.mkdir(parents=True, exist_ok=True)
        stale = copy.deepcopy(collected)
        stale["node_types"][0]["accelerator_model_name"] = "ANCIENT A100"
        (work / RAW_FILENAME).write_text(json.dumps(stale))

        class SilentMlc:
            calls: list = []

            def access(self, kwargs):
                return {"return": 0, "new_env": {}}

        monkeypatch.setattr(capture_mod, "_require_mlc", lambda: SilentMlc())
        with pytest.raises(CaptureError, match="wrote no data"):
            capture(cfg, endpoints_profile)

    def test_scratch_is_kept_out_of_the_output_directory(
        self, good_config_file, endpoints_profile, all_reachable, monkeypatch, collected
    ):
        monkeypatch.setattr(capture_mod, "_require_mlc", lambda: _FakeMlc(collected))
        cfg = load_config(good_config_file)
        result = capture(cfg, endpoints_profile)
        assert result.output_path.parent == cfg.output_dir
        assert (cfg.output_dir / WORK_DIRNAME).is_dir()
        siblings = {p.name for p in cfg.output_dir.iterdir() if p.is_file()}
        assert siblings == {"system_desc.json"}


class TestNestedKeepsEveryField:
    """Defect 5: sw_notes and type_detail were silently dropped."""

    def test_software_notes_survive(self, tmp_path, collected):
        data = copy.deepcopy(GOOD_CONFIG)
        data["submission"]["notes"] = {"hardware": "hw note", "software": "sw note"}
        data["system"]["type_detail"] = "rack detail"
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        out = build_nested(collected, cfg)
        assert out["sw_notes"] == "sw note"
        assert out["hw_notes"] == "hw note"
        assert out["system_type_detail"] == "rack detail"

    def test_every_config_field_reaches_one_of_the_shapes(self, good_config_file, collected):
        cfg = load_config(good_config_file)
        out = build_nested(collected, cfg)
        for key in ("cooling", "container_link", "other_hardware"):
            assert key in out


class TestNumericCoercion:
    """Defect 6: string counts multiplied as strings; counts read as undetected."""

    def test_string_node_count(self):
        entries = [
            {"number_of_nodes": "2", "accelerator_model_name": "H100", "accelerators_per_node": 8}
        ]
        assert compute_system_size(entries) == "16x H100"

    def test_string_per_node_count(self):
        entries = [
            {"number_of_nodes": 2, "accelerator_model_name": "H100", "accelerators_per_node": "8"}
        ]
        assert compute_system_size(entries) == "16x H100"

    def test_numeric_strings_are_valid_counts(self):
        assert not is_not_detected("8")
        assert not is_not_detected("112")

    def test_a_numeric_model_name_is_still_a_failure(self):
        entries = [
            {"number_of_nodes": 1, "accelerator_model_name": "0", "accelerators_per_node": 8}
        ]
        assert compute_system_size(entries) == ""


class TestGroupsCheckedEarly:
    """Defect 7: over-declared groups only failed minutes into capture."""

    def test_declaring_more_nodes_than_exist_fails_check(
        self, tmp_path, endpoints_profile, all_reachable
    ):
        data = copy.deepcopy(GOOD_CONFIG)
        data["nodes"]["groups"] = {
            "prefill": [{"match": "NVIDIA H100", "count": 2}],
            "decode": [{"match": "NVIDIA H100", "count": 5}],
        }
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        report = run_check(cfg, endpoints_profile)
        assert report.has_config_problems
        assert any(p == "nodes.groups" for p, _ in report.missing_required)

    def test_matching_counts_pass(self, tmp_path, endpoints_profile, all_reachable):
        data = copy.deepcopy(GOOD_CONFIG)
        data["nodes"]["groups"] = {"prefill": [{"match": "NVIDIA H100", "count": 2}]}
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        assert run_check(cfg, endpoints_profile).ok


class TestCollectionProvenance:
    """mlc-scripts 1.2.0a1 runs from the installed package, so there is no git
    checkout to stamp. The output must still say what collected it."""

    def test_git_checkout_version_is_preserved(self, good_config_file, collected):
        from mlperf_sysinfo.output import _collection_provenance

        assert _collection_provenance(collected)["commit"] == "abc123"

    def test_falls_back_to_the_installed_release(self):
        from mlperf_sysinfo.output import _collection_provenance

        block = _collection_provenance({"node_types": []})
        assert "package_version" in block
        assert block["package_version"]

    def test_output_always_records_something(
        self, good_config_file, endpoints_profile, all_reachable, monkeypatch, collected
    ):
        no_version = copy.deepcopy(collected)
        no_version.pop("mlc_scripts_version")
        monkeypatch.setattr(capture_mod, "_require_mlc", lambda: _FakeMlc(no_version))
        result = capture(load_config(good_config_file), endpoints_profile)
        stamp = json.loads(result.output_path.read_text())["mlperf_sysinfo"]
        assert stamp["mlc_scripts"], "a capture must always record its collection version"
