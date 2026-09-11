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


def _only_node2_answers(monkeypatch):
    """node1 is down, node2 is up. A genuine partial, rather than the total
    blackout that every node failing actually is."""
    monkeypatch.setattr(
        check_mod,
        "check_node",
        lambda t, a: NodeStatus(t, t.host != "node1", "timed out" if t.host == "node1" else "ok"),
    )
    monkeypatch.setattr(check_mod, "probe_endpoint", lambda u: ProbeStatus(u, True, ""))
    monkeypatch.setattr(check_mod, "check_serving_log", lambda t, p: ProbeStatus(p, True, ""))


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
        del data["submission"]["division"]
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        report = run_check(cfg, endpoints_profile)
        assert not report.ok
        assert report.has_config_problems
        assert report.missing_required[0][0] == "submission.division"

    def test_placeholder_is_a_config_problem(self, tmp_path, endpoints_profile, all_reachable):
        data = copy.deepcopy(GOOD_CONFIG)
        data["system"]["name"] = "CHANGEME"
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        report = run_check(cfg, endpoints_profile)
        assert report.has_config_problems
        assert report.placeholder_required[0][0] == "system.name"

    def test_a_short_name_over_20_characters_is_a_config_problem(
        self, tmp_path, endpoints_profile, all_reachable
    ):
        """Rules 8.2 caps it at 20. Catching it here rather than at write time
        is the point: the alternative is hearing it from a reviewer after a
        multi-node capture has already run."""
        data = copy.deepcopy(GOOD_CONFIG)
        data["system"]["shortened_name"] = "H100x8_vLLM_disaggregated_prefill"
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        report = run_check(cfg, endpoints_profile)
        assert report.has_config_problems
        path, why = next(p for p in report.missing_required if p[0] == "system.shortened_name")
        assert "20 characters" in why

    def test_a_short_name_of_exactly_20_characters_is_accepted(
        self, tmp_path, endpoints_profile, all_reachable
    ):
        """"At most 20" includes 20. An off-by-one here rejects a legal name."""
        data = copy.deepcopy(GOOD_CONFIG)
        data["system"]["shortened_name"] = "x" * 20
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        report = run_check(cfg, endpoints_profile)
        assert not report.has_config_problems

    def test_the_short_name_is_required(self, tmp_path, endpoints_profile, all_reachable):
        data = copy.deepcopy(GOOD_CONFIG)
        del data["system"]["shortened_name"]
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        report = run_check(cfg, endpoints_profile)
        assert report.has_config_problems
        assert any(p == "system.shortened_name" for p, _ in report.missing_required)

    def test_the_length_rule_does_not_apply_to_the_other_profiles(self, tmp_path):
        """It is an endpoints rule. Training and inference have no such field,
        so a long value there is nobody's business to complain about."""
        from mlperf_sysinfo import profiles

        data = copy.deepcopy(GOOD_CONFIG)
        data["profile"] = "inference"
        data["system"]["shortened_name"] = "x" * 40
        data["system"]["category"] = "datacenter"
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        report = run_check(cfg, profiles.load("inference"), skip_network=True)
        assert not any(p == "system.shortened_name" for p, _ in report.missing_required)

    def test_the_endpoint_is_required(self, tmp_path, endpoints_profile, all_reachable):
        """endpoint_url is rules 8.2 metadata: an endpoints submission without
        the endpoint it served is not submittable."""
        data = copy.deepcopy(GOOD_CONFIG)
        del data["serving"]["url"]
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        report = run_check(cfg, endpoints_profile)
        assert report.has_config_problems
        assert any(p == "serving.url" for p, _ in report.missing_required)

    @pytest.mark.parametrize(
        "path", ["submission.model.name", "submission.dataset.name", "submission.submitter"]
    )
    def test_measurement_point_metadata_is_not_asked_for(self, endpoints_profile, path):
        """Model, dataset and submitter details left the system description."""
        assert path not in endpoints_profile.requires
        assert path not in endpoints_profile.recommends

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
        del data["system"]["cooling"]
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        report = run_check(cfg, endpoints_profile)
        assert report.ok
        assert any(p == "system.cooling" for p, _ in report.missing_recommended)

    def test_a_placeholder_in_a_recommended_field_still_blocks(
        self, tmp_path, endpoints_profile, all_reachable
    ):
        """Empty is a warning; starter text is not. It would reach the file
        looking like an answer."""
        data = copy.deepcopy(GOOD_CONFIG)
        data["system"]["cooling"] = "CHANGEME"
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        report = run_check(cfg, endpoints_profile)
        assert report.has_config_problems
        assert any(p == "system.cooling" for p, _ in report.placeholder_recommended)


class TestMlcInvocation:
    def test_accelerator_and_exclusion_become_variations(self, good_config_file, endpoints_profile, tmp_path):
        cfg = load_config(good_config_file)
        kwargs = build_mlc_kwargs(cfg, endpoints_profile, tmp_path)
        assert "_cuda" in kwargs["tags"]
        assert "_exclude_current_node" in kwargs["tags"]

    def test_the_profiles_benchmark_variation_is_passed(
        self, good_config_file, endpoints_profile, tmp_path
    ):
        """The automation returns a different field set per benchmark, so the
        profile has to name the one it wants. Sending nothing gets endpoints
        fields under a flat profile, with no accelerator in them."""
        cfg = load_config(good_config_file)
        tags = build_mlc_kwargs(cfg, endpoints_profile, tmp_path)["tags"]
        assert "_endpoints" in tags
        assert "_inference" not in tags

    def test_the_inference_profile_asks_for_the_flat_field_set(
        self, good_config_file, tmp_path
    ):
        cfg = load_config(good_config_file)
        tags = build_mlc_kwargs(cfg, profiles.load("inference"), tmp_path)["tags"]
        assert "_inference" in tags
        assert "_endpoints" not in tags

    def test_run_configuration_is_sent_so_config_summary_stays_consistent(
        self, tmp_path
    ):
        data = copy.deepcopy(GOOD_CONFIG)
        data["run"] = {
            "node_config": "prefill: 2x H100",
            "config_summary_notes": "chunked prefill on",
            "link_config": "https://example.invalid/configs",
        }
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        kwargs = build_mlc_kwargs(cfg, profiles.load("endpoints"), tmp_path)
        assert kwargs["node_config"] == "prefill: 2x H100"
        assert kwargs["config_summary_notes"] == "chunked prefill on"
        assert kwargs["link_config"] == "https://example.invalid/configs"

    def test_an_unset_run_field_is_not_sent_at_all(self, good_config_file, tmp_path):
        cfg = load_config(good_config_file)
        kwargs = build_mlc_kwargs(cfg, profiles.load("endpoints"), tmp_path)
        assert "node_config" not in kwargs
        assert "config_summary_notes" not in kwargs

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

    def _with_power(self, tmp_path):
        data = copy.deepcopy(GOOD_CONFIG)
        data["power"] = {"redfish": {"endpoint": "https://bmc", "username": "u", "password": "p"}}
        return load_config(write_yaml(tmp_path / "c.yaml", data))

    def test_redfish_when_configured_and_the_profile_allows_it(self, tmp_path):
        cfg = self._with_power(tmp_path)
        kwargs = build_mlc_kwargs(cfg, profiles.load("inference"), tmp_path)
        assert "_redfish" in kwargs["tags"]
        assert kwargs["redfish_endpoint"] == "https://bmc"

    def test_no_redfish_when_the_profile_does_not_allow_it(self, tmp_path, endpoints_profile):
        """Endpoints leaves it off until the working group decides whether
        power belongs in the submission. A power section in the config is not
        enough on its own -- both halves have to agree."""
        cfg = self._with_power(tmp_path)
        kwargs = build_mlc_kwargs(cfg, endpoints_profile, tmp_path)
        assert "_redfish" not in kwargs["tags"]
        assert "redfish_endpoint" not in kwargs

    def test_no_redfish_tag_without_power_section(self, good_config_file, tmp_path):
        cfg = load_config(good_config_file)
        kwargs = build_mlc_kwargs(cfg, profiles.load("inference"), tmp_path)
        assert "_redfish" not in kwargs["tags"]


class TestRemoteFootprint:
    """remote: reaches the automation, or it is a config section that lies."""

    def _kwargs(self, tmp_path, endpoints_profile, remote):
        data = copy.deepcopy(GOOD_CONFIG)
        data["remote"] = remote
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        return build_mlc_kwargs(cfg, endpoints_profile, tmp_path)

    def test_nothing_is_sent_when_the_section_is_absent(
        self, good_config_file, endpoints_profile, tmp_path
    ):
        """The whole promise of the default: an existing config produces the
        invocation it always produced."""
        cfg = load_config(good_config_file)
        kwargs = build_mlc_kwargs(cfg, endpoints_profile, tmp_path)
        assert "remote_isolated" not in kwargs
        assert "remote_isolated_base_dir" not in kwargs
        assert "remote_python_venv" not in kwargs

    def test_isolation_is_sent_as_a_word_mlcflow_reads_as_true(
        self, tmp_path, endpoints_profile
    ):
        """mlcflow runs the value through is_true(), whose vocabulary is
        1/true/on/yes. A Python bool would arrive as "True" and happen to
        work; "yes" is in that list on purpose rather than by luck."""
        kwargs = self._kwargs(tmp_path, endpoints_profile, {"isolated": True})
        assert kwargs["remote_isolated"] == "yes"

    def test_isolation_off_sends_nothing_rather_than_a_falsy_string(
        self, tmp_path, endpoints_profile
    ):
        """The automation forwards every non-empty value it is handed, and
        "False" is non-empty. is_true() rejects it today; the way not to
        depend on that is not to send it."""
        kwargs = self._kwargs(tmp_path, endpoints_profile, {"isolated": False})
        assert "remote_isolated" not in kwargs

    def test_the_base_directory_travels_with_isolation(self, tmp_path, endpoints_profile):
        kwargs = self._kwargs(
            tmp_path, endpoints_profile, {"isolated": True, "isolated_base_dir": "/data/scratch"}
        )
        assert kwargs["remote_isolated_base_dir"] == "/data/scratch"

    def test_the_venv_path_is_sent_without_isolation(self, tmp_path, endpoints_profile):
        kwargs = self._kwargs(
            tmp_path, endpoints_profile, {"python_venv": "/data/scratch/venv"}
        )
        assert kwargs["remote_python_venv"] == "/data/scratch/venv"
        assert "remote_isolated" not in kwargs

    def test_the_input_names_are_the_automations_own(self, tmp_path, endpoints_profile):
        """These three keys are input_mapping entries in the automation's
        meta.yaml. A renamed one is not an error anywhere -- it is silently
        dropped, and the nodes go on writing to $HOME."""
        kwargs = self._kwargs(
            tmp_path,
            endpoints_profile,
            {
                "isolated": True,
                "isolated_base_dir": "/data/scratch",
                "python_venv": "/data/scratch/venv",
            },
        )
        assert {"remote_isolated", "remote_isolated_base_dir", "remote_python_venv"} <= set(
            kwargs
        )

    def test_they_are_inputs_and_never_variations(self, tmp_path, endpoints_profile):
        """Isolation is not a tag. Appending _remote_isolated to the tag
        string would make mlcflow look for a variation that does not exist."""
        kwargs = self._kwargs(
            tmp_path,
            endpoints_profile,
            {"isolated": True, "isolated_base_dir": "/data/scratch"},
        )
        assert "isolated" not in kwargs["tags"]


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
        del data["submission"]["division"]
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
        _only_node2_answers(monkeypatch)
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
        self, tmp_path, all_reachable, monkeypatch, collected_flat
    ):
        monkeypatch.setattr(capture_mod, "_require_mlc", lambda: _FakeMlc(collected_flat))
        data = copy.deepcopy(GOOD_CONFIG)
        data["profile"] = "inference"
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        result = capture(cfg, profiles.load("inference"))
        out = json.loads(result.output_path.read_text())
        assert "node_types" not in out
        assert out["submitter"] == "MyOrg"
        assert out["accelerator_model_name"] == "NVIDIA H100 80GB HBM3"


class TestEndpointDescription:
    """endpoint_url may be prose, so nothing should try to reach it."""

    def test_a_description_is_not_probed(self, tmp_path, endpoints_profile, monkeypatch):
        monkeypatch.setattr(check_mod, "check_node", lambda t, a: NodeStatus(t, True, ""))
        monkeypatch.setattr(check_mod, "check_serving_log", lambda t, p: ProbeStatus(p, True, ""))

        def explode(url):
            raise AssertionError(f"probed a description: {url!r}")

        monkeypatch.setattr(check_mod, "probe_endpoint", explode)
        data = copy.deepcopy(GOOD_CONFIG)
        data["serving"]["url"] = "Managed endpoint, no public URL"
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        report = run_check(cfg, endpoints_profile)
        assert report.endpoint is None
        assert report.ok

    def test_a_description_still_reaches_the_submission(self, tmp_path, endpoints_profile):
        data = copy.deepcopy(GOOD_CONFIG)
        data["serving"]["url"] = "Managed endpoint, no public URL"
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        kwargs = build_mlc_kwargs(cfg, endpoints_profile, tmp_path)
        assert kwargs["endpoint_url"] == "Managed endpoint, no public URL"


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


class TestPartialNarrowsTheRequest:
    """--allow-partial has to mean "do not ask that node", not "ignore what it
    said". Since mlc-scripts 1.2.0a5 a node the automation cannot reach fails
    the whole collection, so forgiving the answer afterwards is no longer a
    thing that can happen -- there is no answer, there is an error."""

    def test_an_unreachable_node_is_left_out_of_ssh_ids(
        self, good_config_file, endpoints_profile, monkeypatch, fake_mlc
    ):
        _only_node2_answers(monkeypatch)
        cfg = load_config(good_config_file)
        capture(cfg, endpoints_profile, allow_partial=True)
        assert fake_mlc.calls[0]["ssh_ids"] == "root@node2:2222"

    def test_the_serving_node_goes_too_when_it_is_the_one_that_is_down(
        self, good_config_file, endpoints_profile, monkeypatch, fake_mlc
    ):
        """GOOD_CONFIG serves from node1. Fetching its config is a second ssh
        round trip, and an equally fatal one."""
        _only_node2_answers(monkeypatch)
        cfg = load_config(good_config_file)
        assert cfg.serving.node == "root@node1"
        capture(cfg, endpoints_profile, allow_partial=True)
        assert "serving_node" not in fake_mlc.calls[0]

    def test_a_reachable_serving_node_is_still_asked(
        self, tmp_path, endpoints_profile, monkeypatch, fake_mlc
    ):
        data = copy.deepcopy(GOOD_CONFIG)
        data["serving"]["node"] = "root@node2:2222"
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        _only_node2_answers(monkeypatch)
        capture(cfg, endpoints_profile, allow_partial=True)
        assert fake_mlc.calls[0]["serving_node"] == "root@node2:2222"

    def test_the_count_still_reports_against_what_was_configured(
        self, good_config_file, endpoints_profile, monkeypatch, fake_mlc
    ):
        """Narrowing the request must not narrow the expectation too, or a
        partial capture would describe itself as complete."""
        _only_node2_answers(monkeypatch)
        cfg = load_config(good_config_file)
        result = capture(cfg, endpoints_profile, allow_partial=True)
        assert result.nodes_expected == 2
        assert not result.complete

    def test_every_node_down_is_not_a_partial_capture(
        self, good_config_file, endpoints_profile, monkeypatch, fake_mlc
    ):
        """With nothing left to ask and include_local false, the old code sent
        an empty ssh_ids list and let the automation explain. Say it here,
        where the reason is known."""
        monkeypatch.setattr(check_mod, "check_node", lambda t, a: NodeStatus(t, False, "timed out"))
        monkeypatch.setattr(check_mod, "probe_endpoint", lambda u: ProbeStatus(u, True, ""))
        monkeypatch.setattr(check_mod, "check_serving_log", lambda t, p: ProbeStatus(p, True, ""))
        cfg = load_config(good_config_file)
        with pytest.raises(CheckFailed, match="nothing left to collect from"):
            capture(cfg, endpoints_profile, allow_partial=True)
        assert fake_mlc.calls == [], "nothing should have been collected"

    def test_the_local_machine_alone_is_enough_to_go_on(
        self, tmp_path, endpoints_profile, monkeypatch, fake_mlc
    ):
        """Same blackout, but this machine is part of the system. There is
        still something to describe."""
        data = copy.deepcopy(GOOD_CONFIG)
        data["nodes"]["include_local"] = True
        data["serving"].pop("node")
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        monkeypatch.setattr(check_mod, "check_node", lambda t, a: NodeStatus(t, False, "timed out"))
        monkeypatch.setattr(check_mod, "probe_endpoint", lambda u: ProbeStatus(u, True, ""))
        capture(cfg, endpoints_profile, allow_partial=True)
        assert fake_mlc.calls[0]["ssh_ids"] == ""
        assert "_exclude_current_node" not in fake_mlc.calls[0]["tags"]

    def test_a_complete_run_asks_for_every_node(
        self, good_config_file, endpoints_profile, all_reachable, fake_mlc
    ):
        cfg = load_config(good_config_file)
        capture(cfg, endpoints_profile)
        assert fake_mlc.calls[0]["ssh_ids"] == "root@node1:22,root@node2:2222"
