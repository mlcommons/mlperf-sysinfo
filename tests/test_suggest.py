# Copyright 2026 MLCommons contributors
# SPDX-License-Identifier: Apache-2.0
""""Did you mean ...?" suggestions, and the places that use them."""

from __future__ import annotations

import copy

import pytest
import yaml

from mlperf_sysinfo import profiles
from mlperf_sysinfo.config import SysinfoConfig, load_config
from mlperf_sysinfo.errors import ConfigError, ProfileError
from mlperf_sysinfo.profiles import Profile
from mlperf_sysinfo.suggest import closest, did_you_mean, options_at

from .conftest import GOOD_CONFIG, write_yaml

COMMANDS = ["init", "check", "capture", "show", "validate", "profiles"]


class TestClosest:
    @pytest.mark.parametrize(
        ("typed", "expected"),
        [
            ("capure", "capture"),      # transposition
            ("chekc", "check"),         # transposition
            ("valdate", "validate"),    # dropped letter
            ("initt", "init"),          # doubled letter
            ("Check", "check"),         # wrong case
            ("prof", "profiles"),       # abbreviated
        ],
    )
    def test_near_misses_resolve(self, typed, expected):
        assert closest(typed, COMMANDS)[0] == expected

    @pytest.mark.parametrize("typed", ["zzz", "deploy", "x"])
    def test_unrelated_words_suggest_nothing(self, typed):
        """A wrong guess is worse than no guess: it sends someone off to read
        about a command that was never what they wanted."""
        assert closest(typed, COMMANDS) == []

    def test_dashes_are_ignored_so_a_flag_can_reach_a_flag(self):
        assert closest("-v", ["--help", "--version"]) == ["--version"]
        assert closest("-h", ["--help", "--version"]) == ["--help"]

    def test_hyphen_and_underscore_are_the_same_mistake(self):
        assert closest("include-local", ["include_local", "ssh"]) == ["include_local"]

    def test_a_prefix_matches_a_much_longer_name(self):
        """Against a name this much longer a ratio is dominated by the length
        difference -- "sshkey" vs "ssh_key_preconfigured" scores 0.44."""
        assert closest("sshkey", ["ssh", "ssh_key_preconfigured"])[0] == (
            "ssh_key_preconfigured"
        )

    def test_a_substring_that_is_not_a_prefix_does_not_match(self):
        """Matching anywhere would make every short word a hit inside a long
        one -- "up" would suggest "groups"."""
        assert closest("up", ["groups", "ssh"]) == []

    def test_an_exact_name_ranks_above_a_prefix_of_a_longer_one(self):
        assert closest("ssh", ["ssh_key_preconfigured", "ssh"])[0] == "ssh"

    def test_suggestions_are_capped(self):
        many = [f"option_{i}" for i in range(10)]
        assert len(closest("option", many)) == 3

    def test_ties_are_ordered_predictably(self):
        """Otherwise the message changes with the order the caller built the
        candidate list in."""
        assert closest("hardwar", ["other_hardware", "hardware"])[0] == "hardware"


class TestDidYouMean:
    def test_one_match_reads_as_a_question(self):
        assert did_you_mean("capure", COMMANDS) == ' Did you mean "capture"?'

    def test_several_matches_are_listed(self):
        hint = did_you_mean("ssh", ["ssh", "ssh_key_preconfigured"])
        assert hint.startswith(" Did you mean one of ")
        assert '"ssh"' in hint

    def test_nothing_close_is_an_empty_string(self):
        """So a call site can always build one f-string, with no None check."""
        assert did_you_mean("zzz", COMMANDS) == ""


class TestConfigOptions:
    @pytest.mark.parametrize(
        ("section", "typo", "expected"),
        [
            ("system", "categry", "category"),
            ("system", "availabilty", "availability"),
            ("submission", "divison", "division"),
            ("submission", "submiter", "submitter"),
            ("nodes", "include-local", "include_local"),
        ],
    )
    def test_a_mistyped_option_names_the_real_one(self, tmp_path, section, typo, expected):
        data = copy.deepcopy(GOOD_CONFIG)
        data[section][typo] = "whatever"
        with pytest.raises(ConfigError) as e:
            load_config(write_yaml(tmp_path / "c.yaml", data))
        assert f'Did you mean "{expected}"?' in str(e.value)

    def test_a_nested_section_suggests_from_that_section(self, tmp_path):
        """The vocabulary depends on where the key is: 'hardwar' under notes
        must not be matched against the top-level option names."""
        data = copy.deepcopy(GOOD_CONFIG)
        data["submission"]["notes"] = {"hardwar": "x"}
        with pytest.raises(ConfigError) as e:
            load_config(write_yaml(tmp_path / "c.yaml", data))
        message = str(e.value)
        assert '"hardware"' in message
        assert "submitter" not in message  # a sibling of notes, not of hardware

    def test_a_typo_inside_a_node_group_is_suggested(self, tmp_path):
        """nodes.groups is a free-form mapping of lists, so reaching the entry
        schema means walking through the dict's value type and the list's item
        type, not just through model fields."""
        data = copy.deepcopy(GOOD_CONFIG)
        data["nodes"]["groups"] = {"prefill": [{"matchh": "NVIDIA H100", "cout": 2}]}
        with pytest.raises(ConfigError) as e:
            load_config(write_yaml(tmp_path / "c.yaml", data))
        message = str(e.value)
        assert 'Did you mean "match"?' in message
        assert 'Did you mean "count"?' in message

    def test_an_unrecognisable_option_still_says_what_to_do(self, tmp_path):
        data = copy.deepcopy(GOOD_CONFIG)
        data["system"]["quantum_flux"] = 1
        with pytest.raises(ConfigError, match="check the spelling"):
            load_config(write_yaml(tmp_path / "c.yaml", data))

    def test_a_migrated_option_keeps_its_own_message(self, tmp_path):
        """'model' is a real former option, not a typo -- where it went is more
        useful than the nearest surviving name."""
        data = copy.deepcopy(GOOD_CONFIG)
        data["submission"]["model"] = {"name": "x"}
        with pytest.raises(ConfigError) as e:
            load_config(write_yaml(tmp_path / "c.yaml", data))
        assert "measurement point config" in str(e.value)
        assert "Did you mean" not in str(e.value)


class TestOptionsAt:
    """The vocabulary valid at a path, which is what keeps suggestions honest."""

    def test_the_root_model(self):
        assert "system" in options_at(SysinfoConfig, ("anything",))

    def test_a_nested_model(self):
        assert set(options_at(SysinfoConfig, ("submission", "notes", "x"))) == {
            "hardware",
            "software",
            "other_hardware",
        }

    def test_through_an_optional_model(self):
        assert "endpoint" in options_at(SysinfoConfig, ("power", "redfish", "x"))

    def test_through_a_mapping_and_a_sequence(self):
        assert set(options_at(SysinfoConfig, ("nodes", "groups", "prefill", 0, "x"))) == {
            "match",
            "count",
        }

    def test_a_path_with_no_fixed_vocabulary_suggests_nothing(self):
        """requires is dict[str, str] -- any key is legal, so there is no set of
        names a mistyped one could be measured against."""
        assert options_at(Profile, ("requires", "system.nmae")) == []

    def test_a_path_that_does_not_exist_suggests_nothing(self):
        assert options_at(SysinfoConfig, ("nope", "deeper", "x")) == []


class TestProfileNames:
    @pytest.mark.parametrize(("typed", "expected"), [("endpoint", "endpoints"),
                                                     ("endpoins", "endpoints"),
                                                     ("inferece", "inference")])
    def test_a_mistyped_profile_names_the_real_one(self, typed, expected):
        with pytest.raises(ProfileError) as e:
            profiles.load(typed)
        assert f'Did you mean "{expected}"?' in str(e.value)

    def test_an_unrelated_name_just_lists_the_builtins(self):
        with pytest.raises(ProfileError) as e:
            profiles.load("storage")
        assert "Did you mean" not in str(e.value)
        assert "endpoints, inference" in str(e.value)

    def test_a_mistyped_profile_option_names_the_real_one(self, tmp_path):
        p = tmp_path / "mine.yaml"
        p.write_text(
            yaml.safe_dump(
                {"name": "mine", "title": "T", "round": "6.0", "shap": "nested"}
            )
        )
        with pytest.raises(ProfileError) as e:
            profiles.load(str(p))
        assert 'Did you mean "shape"?' in str(e.value)

    def test_a_mistyped_collect_flag_names_the_real_one(self, tmp_path):
        p = tmp_path / "mine.yaml"
        p.write_text(
            yaml.safe_dump(
                {
                    "name": "mine",
                    "title": "T",
                    "round": "6.0",
                    "collect": {"serving_logs": True},
                }
            )
        )
        with pytest.raises(ProfileError) as e:
            profiles.load(str(p))
        assert 'Did you mean "serving_log"?' in str(e.value)
