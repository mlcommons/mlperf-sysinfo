# Copyright 2026 MLCommons contributors
# SPDX-License-Identifier: Apache-2.0
"""Config loading: schema, extends, ${VAR}, embedding, placeholders."""

from __future__ import annotations

import copy

import pytest

from mlperf_sysinfo.config import (
    SshTarget,
    deep_merge,
    dotted_get,
    interpolate_env,
    is_filled,
    is_placeholder,
    load_config,
)
from mlperf_sysinfo.errors import ConfigError

from .conftest import GOOD_CONFIG, write_yaml


class TestSshTarget:
    def test_user_host(self):
        t = SshTarget.parse("root@node1")
        assert (t.user, t.host, t.port) == ("root", "node1", 22)

    def test_explicit_port(self):
        assert SshTarget.parse("u@h:2222").port == 2222

    def test_str_roundtrip_always_has_port(self):
        assert str(SshTarget.parse("root@node1")) == "root@node1:22"

    @pytest.mark.parametrize("bad", ["node1", "root@", "@node1", "root@node1:0", "root@node1:70000", ""])
    def test_rejects_malformed(self, bad):
        with pytest.raises(ValueError):
            SshTarget.parse(bad)


class TestInterpolation:
    def test_substitutes_from_environment(self, monkeypatch):
        monkeypatch.setenv("BMC_PASSWORD", "s3cret")
        out = interpolate_env({"password": "${BMC_PASSWORD}"})
        assert out["password"] == "s3cret"

    def test_records_unset_with_its_path(self):
        missing: list[str] = []
        out = interpolate_env({"power": {"redfish": {"password": "${NOPE}"}}}, missing=missing)
        assert out["power"]["redfish"]["password"] == "${NOPE}"
        assert missing == ["power.redfish.password -> ${NOPE}"]

    def test_walks_lists(self, monkeypatch):
        monkeypatch.setenv("H", "node9")
        assert interpolate_env(["root@${H}"]) == ["root@node9"]

    def test_leaves_other_types_alone(self):
        assert interpolate_env({"n": 8, "b": True}) == {"n": 8, "b": True}


class TestDeepMerge:
    def test_child_wins(self):
        assert deep_merge({"a": 1}, {"a": 2}) == {"a": 2}

    def test_nested_dicts_merge(self):
        assert deep_merge({"a": {"x": 1, "y": 2}}, {"a": {"y": 3}}) == {"a": {"x": 1, "y": 3}}

    def test_lists_replace_rather_than_append(self):
        assert deep_merge({"a": [1, 2]}, {"a": [3]}) == {"a": [3]}


class TestPlaceholders:
    @pytest.mark.parametrize(
        "value",
        ["CHANGEME", "changeme", "CHANGEME@example.com", "Insert your organization name here"],
    )
    def test_detected(self, value):
        assert is_placeholder(value)
        assert not is_filled(value)

    @pytest.mark.parametrize("value", ["MyOrg", "H100x8", "a@b.com"])
    def test_real_values_pass(self, value):
        assert not is_placeholder(value)
        assert is_filled(value)

    def test_empty_is_not_filled(self):
        assert not is_filled("")
        assert not is_filled("   ")
        assert not is_filled(None)


class TestLoad:
    def test_loads_a_good_file(self, good_config_file):
        cfg = load_config(good_config_file)
        assert cfg.system.name == "H100x8_vLLM"
        assert cfg.profile == "endpoints"
        assert len(cfg.nodes.targets) == 2

    def test_output_dir_is_relative_to_the_config(self, good_config_file):
        cfg = load_config(good_config_file)
        assert cfg.output_dir == (good_config_file.parent / "out").resolve()

    def test_missing_file(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "nope.yaml")

    def test_invalid_yaml(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("key: [unclosed\n")
        with pytest.raises(ConfigError, match="invalid YAML"):
            load_config(p)

    def test_unknown_option_names_the_field(self, tmp_path):
        data = copy.deepcopy(GOOD_CONFIG)
        data["system"]["colour"] = "blue"
        p = write_yaml(tmp_path / "c.yaml", data)
        with pytest.raises(ConfigError, match="system.colour"):
            load_config(p)

    def test_nothing_to_collect_is_rejected(self, tmp_path):
        data = copy.deepcopy(GOOD_CONFIG)
        data["nodes"] = {"include_local": False, "ssh": []}
        data["serving"] = {}
        p = write_yaml(tmp_path / "c.yaml", data)
        with pytest.raises(ConfigError, match="nothing to collect"):
            load_config(p)

    def test_local_only_is_allowed(self, tmp_path):
        data = copy.deepcopy(GOOD_CONFIG)
        data["nodes"] = {"include_local": True, "ssh": []}
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        assert cfg.nodes.include_local

    def test_serving_node_alone_is_allowed(self, tmp_path):
        data = copy.deepcopy(GOOD_CONFIG)
        data["nodes"] = {"include_local": False, "ssh": []}
        data["serving"] = {"node": "root@node1"}
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        assert cfg.all_targets == [SshTarget(user="root", host="node1", port=22)]

    def test_bad_ssh_target_is_reported(self, tmp_path):
        data = copy.deepcopy(GOOD_CONFIG)
        data["nodes"]["ssh"] = ["not-a-target"]
        p = write_yaml(tmp_path / "c.yaml", data)
        with pytest.raises(ConfigError):
            load_config(p)

    def test_url_without_scheme_is_rejected(self, tmp_path):
        data = copy.deepcopy(GOOD_CONFIG)
        data["serving"]["url"] = "node1:8000"
        p = write_yaml(tmp_path / "c.yaml", data)
        with pytest.raises(ConfigError, match="http"):
            load_config(p)


class TestAllTargets:
    """serving.node names a real node in the system, even when it is not
    also listed under nodes.ssh. It should be reached and collected, not
    just used for the startup-log check."""

    def test_serving_node_already_in_ssh_is_not_duplicated(self, good_config_file):
        cfg = load_config(good_config_file)
        assert cfg.all_targets == cfg.nodes.targets

    def test_serving_node_not_in_ssh_is_added(self, tmp_path):
        data = copy.deepcopy(GOOD_CONFIG)
        data["nodes"]["ssh"] = ["root@node1"]
        data["serving"]["node"] = "root@node3"
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        assert cfg.all_targets == [
            SshTarget(user="root", host="node1", port=22),
            SshTarget(user="root", host="node3", port=22),
        ]

    def test_no_serving_node_leaves_targets_unchanged(self, tmp_path):
        data = copy.deepcopy(GOOD_CONFIG)
        data["serving"] = {"url": "http://node1:8000"}
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        assert cfg.all_targets == cfg.nodes.targets


class TestExtends:
    def test_parent_supplies_defaults(self, tmp_path):
        write_yaml(
            tmp_path / "org.yaml",
            {"submission": {"submitter": "MyOrg", "contact": "a@b.com"}},
        )
        child = copy.deepcopy(GOOD_CONFIG)
        child["extends"] = "org.yaml"
        del child["submission"]["submitter"]
        cfg = load_config(write_yaml(tmp_path / "c.yaml", child))
        assert cfg.submission.submitter == "MyOrg"

    def test_child_overrides_parent(self, tmp_path):
        write_yaml(tmp_path / "org.yaml", {"submission": {"submitter": "Parent"}})
        child = copy.deepcopy(GOOD_CONFIG)
        child["extends"] = "org.yaml"
        cfg = load_config(write_yaml(tmp_path / "c.yaml", child))
        assert cfg.submission.submitter == "MyOrg"

    def test_cycle_is_caught(self, tmp_path):
        write_yaml(tmp_path / "a.yaml", {"extends": "b.yaml"})
        write_yaml(tmp_path / "b.yaml", {"extends": "a.yaml"})
        with pytest.raises(ConfigError, match="cycle"):
            load_config(tmp_path / "a.yaml")


class TestEmbedding:
    def test_system_info_section_of_a_benchmark_config(self, tmp_path):
        host = {
            "name": "some-benchmark",
            "report_dir": "results/run1",
            "datasets": [{"name": "cnn"}],
            "system_info": copy.deepcopy(GOOD_CONFIG),
        }
        del host["system_info"]["output"]
        cfg = load_config(write_yaml(tmp_path / "bench.yaml", host))
        assert cfg.system.name == "H100x8_vLLM"
        assert cfg.output_dir == (tmp_path / "results/run1").resolve()

    def test_embedded_output_block_wins_over_report_dir(self, tmp_path):
        host = {
            "report_dir": "results/ignored",
            "system_info": copy.deepcopy(GOOD_CONFIG),
        }
        cfg = load_config(write_yaml(tmp_path / "bench.yaml", host))
        assert cfg.output_dir == (tmp_path / "out").resolve()


class TestDottedGet:
    def test_reaches_into_models(self, good_config_file):
        cfg = load_config(good_config_file)
        assert dotted_get(cfg, "submission.model.name") == "Llama-3.1-8B-Instruct"

    def test_missing_path_is_none(self, good_config_file):
        cfg = load_config(good_config_file)
        assert dotted_get(cfg, "submission.model.nope") is None
        assert dotted_get(cfg, "no.such.thing") is None
