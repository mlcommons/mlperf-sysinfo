# Copyright 2026 MLCommons contributors
# SPDX-License-Identifier: Apache-2.0
"""Pre-flight and the capture contract. The network and mlc-scripts are stubbed."""

from __future__ import annotations

import copy
import json

import pytest

from mlperf_sysinfo import collector as capture_mod
from mlperf_sysinfo import preflight as check_mod
from mlperf_sysinfo import profiles
from mlperf_sysinfo.collector import build_mlc_kwargs, capture
from mlperf_sysinfo.config import SshTarget, load_config
from mlperf_sysinfo.errors import CheckFailed
from mlperf_sysinfo.preflight import NodeStatus, ProbeStatus, run_check

from .conftest import GOOD_CONFIG, write_yaml


@pytest.fixture
def endpoints_profile():
    return profiles.load("endpoints")


@pytest.fixture
def all_reachable(monkeypatch):
    monkeypatch.setattr(
        check_mod, "check_node", lambda t, a: NodeStatus(t, True, "NVIDIA H100 x 8")
    )
    monkeypatch.setattr(
        check_mod, "probe_endpoint", lambda url: ProbeStatus(url, True, "vLLM 0.9.0")
    )
    monkeypatch.setattr(
        check_mod, "check_serving_log", lambda t, p: ProbeStatus(p, True, f"on {t}")
    )


class TestCheck:
    def test_a_complete_config_passes(self, good_config_file, endpoints_profile, all_reachable):
        report = run_check(load_config(good_config_file), endpoints_profile)
        assert report.ok
        assert report.satisfied_count == len(endpoints_profile.requires)

    def test_offline_skips_the_network(self, good_config_file, endpoints_profile):
        report = run_check(load_config(good_config_file), endpoints_profile, skip_network=True)
        assert report.nodes == []
        assert report.network_checked is False

    def test_missing_required_field_is_a_config_problem(
        self, tmp_path, endpoints_profile, all_reachable
    ):
        data = copy.deepcopy(GOOD_CONFIG)
        del data["submission"]["contact"]
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        report = run_check(cfg, endpoints_profile)
        assert not report.ok
        assert report.has_config_problems
        assert report.missing_required[0][0] == "submission.contact"

    def test_placeholder_is_a_config_problem(self, tmp_path, endpoints_profile, all_reachable):
        data = copy.deepcopy(GOOD_CONFIG)
        data["submission"]["submitter"] = "CHANGEME"
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        report = run_check(cfg, endpoints_profile)
        assert report.has_config_problems
        assert report.placeholder_required[0][0] == "submission.submitter"

    def test_unresolved_env_var_is_a_config_problem(
        self, tmp_path, endpoints_profile, all_reachable, monkeypatch
    ):
        monkeypatch.delenv("BMC_PASSWORD", raising=False)
        data = copy.deepcopy(GOOD_CONFIG)
        data["power"] = {
            "redfish": {"endpoint": "https://bmc", "password": "${BMC_PASSWORD}"}
        }
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        report = run_check(cfg, endpoints_profile)
        assert report.has_config_problems
        assert any("BMC_PASSWORD" in ref for ref in report.unresolved_env)

    def test_unreachable_node_is_a_reach_problem_not_a_config_one(
        self, good_config_file, endpoints_profile, monkeypatch
    ):
        def one_down(target, accel):
            return NodeStatus(target, target.host != "node2", "connection timed out")

        monkeypatch.setattr(check_mod, "check_node", one_down)
        monkeypatch.setattr(
            check_mod, "probe_endpoint", lambda url: ProbeStatus(url, True, "vLLM")
        )
        monkeypatch.setattr(
            check_mod, "check_serving_log", lambda t, p: ProbeStatus(p, True, "")
        )
        report = run_check(load_config(good_config_file), endpoints_profile)
        assert not report.ok
        assert not report.has_config_problems
        assert report.has_reach_problems
        assert len(report.unreachable) == 1

    def test_serving_node_missing_from_ssh_is_still_reached(
        self, tmp_path, endpoints_profile, all_reachable
    ):
        data = copy.deepcopy(GOOD_CONFIG)
        data["nodes"]["ssh"] = ["root@node1"]
        data["serving"]["node"] = "root@node3"
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        report = run_check(cfg, endpoints_profile)
        assert {n.label for n in report.nodes} == {"root@node1:22", "root@node3:22"}

    def test_recommended_fields_only_warn(self, tmp_path, endpoints_profile, all_reachable):
        data = copy.deepcopy(GOOD_CONFIG)
        del data["submission"]["dataset"]
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        report = run_check(cfg, endpoints_profile)
        assert report.ok
        assert any(p == "submission.dataset.name" for p, _ in report.missing_recommended)


class TestMlcInvocation:
    def test_accelerator_and_exclusion_become_variations(self, good_config_file, endpoints_profile, tmp_path):
        cfg = load_config(good_config_file)
        kwargs = build_mlc_kwargs(cfg, endpoints_profile, tmp_path)
        assert "_cuda" in kwargs["tags"]
        assert "_exclude_current_node" in kwargs["tags"]

    def test_no_benchmark_variation_is_passed(self, good_config_file, endpoints_profile, tmp_path):
        """Shaping is ours, so the automation must return its grouped intermediate."""
        cfg = load_config(good_config_file)
        tags = build_mlc_kwargs(cfg, endpoints_profile, tmp_path)["tags"]
        assert "_inference" not in tags
        assert "_endpoints" not in tags

    def test_ssh_ids_are_normalised_with_ports(self, good_config_file, endpoints_profile, tmp_path):
        cfg = load_config(good_config_file)
        kwargs = build_mlc_kwargs(cfg, endpoints_profile, tmp_path)
        assert kwargs["ssh_ids"] == "root@node1:22,root@node2:2222"

    def test_ssh_ids_include_a_serving_node_missing_from_ssh(
        self, endpoints_profile, tmp_path
    ):
        data = copy.deepcopy(GOOD_CONFIG)
        data["nodes"]["ssh"] = ["root@node1"]
        data["serving"]["node"] = "root@node3"
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        kwargs = build_mlc_kwargs(cfg, endpoints_profile, tmp_path)
        assert kwargs["ssh_ids"] == "root@node1:22,root@node3:22"

    def test_inference_profile_skips_serving_inputs(self, good_config_file, tmp_path):
        cfg = load_config(good_config_file)
        kwargs = build_mlc_kwargs(cfg, profiles.load("inference"), tmp_path)
        assert "serving_node" not in kwargs
        assert "endpoint_url" not in kwargs

    def test_redfish_only_when_configured_and_allowed(self, tmp_path, endpoints_profile):
        data = copy.deepcopy(GOOD_CONFIG)
        data["power"] = {"redfish": {"endpoint": "https://bmc", "username": "u", "password": "p"}}
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        kwargs = build_mlc_kwargs(cfg, endpoints_profile, tmp_path)
        assert "_redfish" in kwargs["tags"]
        assert kwargs["redfish_endpoint"] == "https://bmc"

    def test_no_redfish_tag_without_power_section(self, good_config_file, endpoints_profile, tmp_path):
        cfg = load_config(good_config_file)
        assert "_redfish" not in build_mlc_kwargs(cfg, endpoints_profile, tmp_path)["tags"]


class _FakeMlc:
    """Stands in for mlc.access: writes the intermediate the real one would."""

    def __init__(self, raw: dict, returncode: int = 0):
        self.raw = raw
        self.returncode = returncode
        self.calls: list[dict] = []

    def access(self, kwargs):
        self.calls.append(kwargs)
        if self.returncode != 0:
            return {"return": self.returncode, "error": "the automation exploded"}
        from pathlib import Path

        out = Path(kwargs["out_dir_path"]) / kwargs["out_file_name"]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.raw))
        return {
            "return": 0,
            "new_env": {"MLC_MULTI_NODE_SYSTEM_INFO_FILE_PATH": str(out)},
        }


@pytest.fixture
def fake_mlc(monkeypatch, collected):
    fake = _FakeMlc(collected)
    monkeypatch.setattr(capture_mod, "_require_mlc", lambda: fake)
    return fake


class TestCapture:
    def test_writes_the_profile_output_file(
        self, good_config_file, endpoints_profile, all_reachable, fake_mlc
    ):
        cfg = load_config(good_config_file)
        result = capture(cfg, endpoints_profile)
        assert result.output_path.name == "system_desc.json"
        assert result.output_path.exists()
        assert result.complete

    def test_check_runs_first_and_blocks_on_config_problems(
        self, tmp_path, endpoints_profile, all_reachable, fake_mlc
    ):
        data = copy.deepcopy(GOOD_CONFIG)
        data["submission"]["submitter"] = "CHANGEME"
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        with pytest.raises(CheckFailed):
            capture(cfg, endpoints_profile)
        assert fake_mlc.calls == [], "nothing should have been collected"

    def test_allow_partial_does_not_forgive_config_problems(
        self, tmp_path, endpoints_profile, all_reachable, fake_mlc
    ):
        data = copy.deepcopy(GOOD_CONFIG)
        del data["submission"]["contact"]
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        with pytest.raises(CheckFailed):
            capture(cfg, endpoints_profile, allow_partial=True)
        assert fake_mlc.calls == []

    def test_unreachable_node_blocks_by_default(
        self, good_config_file, endpoints_profile, monkeypatch, fake_mlc
    ):
        monkeypatch.setattr(check_mod, "check_node", lambda t, a: NodeStatus(t, False, "timed out"))
        monkeypatch.setattr(check_mod, "probe_endpoint", lambda u: ProbeStatus(u, True, ""))
        monkeypatch.setattr(check_mod, "check_serving_log", lambda t, p: ProbeStatus(p, True, ""))
        cfg = load_config(good_config_file)
        with pytest.raises(CheckFailed, match="allow-partial"):
            capture(cfg, endpoints_profile)
        assert fake_mlc.calls == []

    def test_allow_partial_proceeds_and_marks_the_output(
        self, good_config_file, endpoints_profile, monkeypatch, fake_mlc
    ):
        monkeypatch.setattr(check_mod, "check_node", lambda t, a: NodeStatus(t, False, "timed out"))
        monkeypatch.setattr(check_mod, "probe_endpoint", lambda u: ProbeStatus(u, True, ""))
        monkeypatch.setattr(check_mod, "check_serving_log", lambda t, p: ProbeStatus(p, True, ""))
        cfg = load_config(good_config_file)
        result = capture(cfg, endpoints_profile, allow_partial=True)
        assert not result.complete
        data = json.loads(result.output_path.read_text())
        assert data["mlperf_sysinfo"]["complete"] is False
        assert "PARTIAL" in data["mlperf_sysinfo"]["warning"]

    def test_automation_failure_is_surfaced(
        self, good_config_file, endpoints_profile, all_reachable, monkeypatch, collected
    ):
        monkeypatch.setattr(capture_mod, "_require_mlc", lambda: _FakeMlc(collected, returncode=1))
        cfg = load_config(good_config_file)
        with pytest.raises(Exception, match="exploded"):
            capture(cfg, endpoints_profile)

    def test_node_groups_are_written_as_a_temp_file(
        self, tmp_path, endpoints_profile, all_reachable, fake_mlc
    ):
        data = copy.deepcopy(GOOD_CONFIG)
        data["nodes"]["groups"] = {"prefill": [{"match": "NVIDIA H100", "count": 2}]}
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        capture(cfg, endpoints_profile)
        assert "node_config_file" in fake_mlc.calls[0]

    def test_inference_profile_produces_the_flat_shape(
        self, tmp_path, all_reachable, fake_mlc
    ):
        data = copy.deepcopy(GOOD_CONFIG)
        data["profile"] = "inference"
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        result = capture(cfg, profiles.load("inference"))
        out = json.loads(result.output_path.read_text())
        assert "node_types" not in out
        assert out["submitter"] == "MyOrg"


class TestSshTargetsInCheck:
    def test_targets_are_parsed_once(self, good_config_file):
        cfg = load_config(good_config_file)
        assert cfg.nodes.targets == [
            SshTarget(user="root", host="node1", port=22),
            SshTarget(user="root", host="node2", port=2222),
        ]


class TestCollectionIsVerified:
    """A reachable node can still return nothing. Believing the request over
    the result is how a capture silently ships half a system."""

    def test_zero_nodes_collected_is_always_an_error(
        self, good_config_file, endpoints_profile, all_reachable, monkeypatch
    ):
        empty = {"node_types": [], "system_node_ensemble_total": 0}
        monkeypatch.setattr(capture_mod, "_require_mlc", lambda: _FakeMlc(empty))
        cfg = load_config(good_config_file)
        with pytest.raises(Exception, match="no hardware at all"):
            capture(cfg, endpoints_profile)

    def test_zero_nodes_not_forgiven_by_allow_partial(
        self, good_config_file, endpoints_profile, all_reachable, monkeypatch
    ):
        empty = {"node_types": []}
        monkeypatch.setattr(capture_mod, "_require_mlc", lambda: _FakeMlc(empty))
        cfg = load_config(good_config_file)
        with pytest.raises(Exception, match="no hardware at all"):
            capture(cfg, endpoints_profile, allow_partial=True)

    def test_fewer_nodes_than_asked_for_blocks_by_default(
        self, good_config_file, endpoints_profile, all_reachable, monkeypatch, collected
    ):
        one = copy.deepcopy(collected)
        one["node_types"][0]["number_of_nodes"] = 1  # two were asked for
        monkeypatch.setattr(capture_mod, "_require_mlc", lambda: _FakeMlc(one))
        cfg = load_config(good_config_file)
        with pytest.raises(Exception, match="only 1 of 2"):
            capture(cfg, endpoints_profile)

    def test_fewer_nodes_with_allow_partial_is_marked_partial(
        self, good_config_file, endpoints_profile, all_reachable, monkeypatch, collected
    ):
        one = copy.deepcopy(collected)
        one["node_types"][0]["number_of_nodes"] = 1
        monkeypatch.setattr(capture_mod, "_require_mlc", lambda: _FakeMlc(one))
        cfg = load_config(good_config_file)
        result = capture(cfg, endpoints_profile, allow_partial=True)
        assert not result.complete
        assert result.nodes_collected == 1
        assert result.nodes_expected == 2
        data = json.loads(result.output_path.read_text())
        assert data["mlperf_sysinfo"]["complete"] is False

    def test_full_collection_is_complete(
        self, good_config_file, endpoints_profile, all_reachable, fake_mlc
    ):
        result = capture(load_config(good_config_file), endpoints_profile)
        assert result.complete
        assert result.nodes_collected == 2 == result.nodes_expected
