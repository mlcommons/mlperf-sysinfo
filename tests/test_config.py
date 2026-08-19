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

    def test_a_prose_endpoint_description_is_accepted(self, tmp_path):
        """Endpoints rules 8.2 define endpoint_url as "URL or description of
        the endpoint under test". A Serviced submission against a hosted API
        with no public URL has only prose to give."""
        data = copy.deepcopy(GOOD_CONFIG)
        data["serving"]["url"] = "Managed endpoint, no public URL"
        cfg = load_config(write_yaml(tmp_path / "c.yaml", data))
        assert cfg.serving.url == "Managed endpoint, no public URL"
        assert not cfg.serving.is_probeable

    def test_an_http_url_is_probeable(self, good_config_file):
        assert load_config(good_config_file).serving.is_probeable


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


class TestMigratedOptions:
    """An option that moved should say where it went, not "check the spelling"."""

    @pytest.mark.parametrize("section", ["model", "dataset"])
    def test_model_and_dataset_point_at_the_measurement_point_config(
        self, tmp_path, section
    ):
        data = copy.deepcopy(GOOD_CONFIG)
        data["submission"][section] = {"name": "something"}
        with pytest.raises(ConfigError, match="measurement point config") as e:
            load_config(write_yaml(tmp_path / "c.yaml", data))
        assert "check the spelling" not in str(e.value)

    def test_measured_accuracy_score_is_gone(self, tmp_path):
        data = copy.deepcopy(GOOD_CONFIG)
        data["submission"]["measured_accuracy_score"] = 0.9
        with pytest.raises(ConfigError, match="no longer part of the system description"):
            load_config(write_yaml(tmp_path / "c.yaml", data))

    def test_a_genuine_typo_still_says_check_the_spelling(self, tmp_path):
        data = copy.deepcopy(GOOD_CONFIG)
        data["submission"]["submiter"] = "MyOrg"
        with pytest.raises(ConfigError, match="check the spelling"):
            load_config(write_yaml(tmp_path / "c.yaml", data))


class TestEmptySections:
    def test_a_section_with_everything_commented_out_is_not_an_error(self, tmp_path):
        """`run:` followed by only comments parses as None. Rejecting that
        punishes the submitter for tidying up the template."""
        raw = tmp_path / "c.yaml"
        raw.write_text(
            "profile: endpoints\n"
            "system:\n"
            "  name: sut\n"
            "nodes:\n"
            "  include_local: true\n"
            "run:\n"
            "  # link_config: https://example.invalid\n"
        )
        cfg = load_config(raw)
        assert cfg.run.link_config is None

    def test_a_nested_empty_section_is_also_forgiven(self, tmp_path):
        """The shipped endpoints template has commented entries under
        submission.notes, so this is the shape a submitter actually reaches."""
        raw = tmp_path / "c.yaml"
        raw.write_text(
            "profile: endpoints\n"
            "system:\n"
            "  name: sut\n"
            "nodes:\n"
            "  include_local: true\n"
            "submission:\n"
            "  division: standardized\n"
            "  notes:\n"
            "    # hardware: \"\"\n"
        )
        cfg = load_config(raw)
        assert cfg.submission.notes.hardware is None
        assert cfg.submission.division == "standardized"

    @pytest.mark.parametrize(
        ("body", "check"),
        [
            # An unset scalar means "unset", which is a valid answer. Coercing
            # it to {} would report 'cooling: Input should be a valid string'.
            ("system:\n  name: sut\n  cooling:\n", lambda c: c.system.cooling is None),
            (
                "system:\n  name: sut\nsubmission:\n  container_link:\n",
                lambda c: c.submission.container_link is None,
            ),
            # power.redfish is Optional: a bare 'redfish:' means "no BMC
            # capture", not "a RedfishConfig with no endpoint".
            (
                "system:\n  name: sut\npower:\n  redfish:\n    # endpoint: https://bmc\n",
                lambda c: c.power.redfish is None,
            ),
            # A list whose every entry is commented out is an empty list.
            (
                "system:\n  name: sut\nnodes:\n  include_local: true\n  ssh:\n    # - u@h\n",
                lambda c: c.nodes.ssh == [],
            ),
        ],
    )
    def test_an_unset_value_is_not_forced_into_a_section(self, tmp_path, body, check):
        """Which keys get the empty-section treatment comes from the schema.

        Coercing every None would turn these valid configs into type errors.
        """
        raw = tmp_path / "c.yaml"
        raw.write_text("profile: endpoints\nnodes:\n  include_local: true\n" + body)
        assert check(load_config(raw))

    def test_an_empty_child_section_inherits_instead_of_erasing(self, tmp_path):
        """The coercion runs per document, before the merge. Doing it after
        would turn the parent's whole section into {} and lose it silently."""
        write_yaml(
            tmp_path / "org.yaml",
            {
                "submission": {"division": "standardized", "notes": {"hardware": "inherited"}},
                "run": {"link_config": "https://example.invalid/inherited"},
            },
        )
        child = tmp_path / "c.yaml"
        child.write_text(
            "extends: org.yaml\n"
            "profile: endpoints\n"
            "system:\n"
            "  name: sut\n"
            "nodes:\n"
            "  include_local: true\n"
            "submission:\n"
            "  notes:\n"
            "    # hardware: \"\"\n"
            "run:\n"
            "  # link_config: ...\n"
        )
        cfg = load_config(child)
        assert cfg.submission.division == "standardized"
        assert cfg.submission.notes.hardware == "inherited"
        assert cfg.run.link_config == "https://example.invalid/inherited"


class TestDottedGet:
    def test_reaches_into_models(self, good_config_file):
        cfg = load_config(good_config_file)
        assert dotted_get(cfg, "submission.notes.hardware") == "hw note"

    def test_missing_path_is_none(self, good_config_file):
        cfg = load_config(good_config_file)
        assert dotted_get(cfg, "submission.notes.nope") is None
        assert dotted_get(cfg, "no.such.thing") is None
