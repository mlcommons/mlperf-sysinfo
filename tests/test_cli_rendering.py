# Copyright 2026 MLCommons contributors
# SPDX-License-Identifier: Apache-2.0
"""What the terminal output does and does not contain.

The round is the case worth pinning. It is stamped into every output file, so
it is easy to reach for when rendering a profile name -- but a profile always
tracks the current round, so printing it on every line is noise that also
implies there is a round to choose.
"""

from __future__ import annotations

import json

import pytest
from cyclopts.exceptions import CycloptsError

from mlperf_sysinfo import profiles
from mlperf_sysinfo.cli import (
    _registered,
    _render_check,
    _report_parse_error,
    app,
    profiles_cmd,
    show,
    validate_cmd,
)
from mlperf_sysinfo.config import load_config
from mlperf_sysinfo.preflight import run_check

from .test_report import GOOD_CAPTURE, write_capture


@pytest.fixture
def captured_file(tmp_path):
    return write_capture(tmp_path, GOOD_CAPTURE)


def _rendered(capsys) -> str:
    out = capsys.readouterr()
    return out.out + out.err


class TestRoundIsNotDisplayed:
    def test_check(self, good_config_file, tmp_path, capsys):
        config = load_config(good_config_file)
        report = run_check(config, profiles.load("endpoints"), skip_network=True)
        _render_check(report, out_file=tmp_path / "system_desc.json")
        text = _rendered(capsys)
        assert "endpoints" in text
        assert "6.0" not in text
        assert "round" not in text.lower()

    def test_profiles(self, capsys):
        profiles_cmd()
        text = _rendered(capsys)
        assert "endpoints" in text and "inference" in text
        assert "6.0" not in text

    def test_show(self, captured_file, capsys):
        show(captured_file)
        text = _rendered(capsys)
        assert "endpoints" in text
        assert "6.0" not in text

    def test_validate(self, captured_file, capsys):
        validate_cmd(captured_file)
        text = _rendered(capsys)
        assert "endpoints" in text
        assert "6.0" not in text


class TestRoundIsStillRecorded:
    def test_the_captured_file_keeps_it(self, captured_file):
        """Removing it from the terminal must not remove it from the file --
        that stamp is what tells a reviewer which rules produced a capture."""
        stamp = json.loads(captured_file.read_text())["mlperf_sysinfo"]
        assert stamp["profile_round"] == "6.0"

    def test_it_is_still_parsed_back_for_embedders(self, captured_file):
        from mlperf_sysinfo.report import summarise, validate

        assert summarise(captured_file).profile_round == "6.0"
        assert validate(captured_file).profile_round == "6.0"


class TestHelpDiscovery:
    """Per-command help is where the arguments are written down, so both the
    top-level help and a failed parse should point at it."""

    def test_the_top_level_help_says_how_to_get_command_help(self):
        assert "COMMAND --help" in app.help_epilogue

    def test_a_subcommand_does_not_repeat_the_advice(self):
        """On 'show --help' it is advice the reader has already taken."""
        for subapp in app.subapps:
            assert not subapp.help_epilogue

    def test_a_real_command_called_wrongly_is_pointed_at_its_own_help(
        self, monkeypatch, capsys
    ):
        monkeypatch.setattr("sys.argv", ["mlperf-sysinfo", "show"])
        _report_parse_error(CycloptsError("Command \"show\" parameter --path requires an argument."))
        text = _rendered(capsys)
        assert "mlperf-sysinfo show --help" in text

    def test_an_unknown_command_is_not_pointed_at_a_help_page_that_does_not_exist(
        self, monkeypatch, capsys
    ):
        monkeypatch.setattr("sys.argv", ["mlperf-sysinfo", "capure"])
        _report_parse_error(CycloptsError('Unknown command "capure".'))
        text = _rendered(capsys)
        assert "capure --help" not in text

    def test_a_flag_like_token_is_matched_against_flags_not_commands(
        self, monkeypatch, capsys
    ):
        monkeypatch.setattr("sys.argv", ["mlperf-sysinfo", "-v"])
        _report_parse_error(CycloptsError('Unknown command "-v".'))
        text = _rendered(capsys)
        assert '"--version"' in text
        assert "validate" not in text  # the nearest *command*, and not the point

    def test_the_registered_names_come_from_the_app(self):
        commands, flags = _registered(flags=False), _registered(flags=True)
        assert {"init", "check", "capture", "show", "validate", "profiles"} == set(commands)
        assert "--version" in flags and "--help" in flags
        assert not any(c.startswith("-") for c in commands)
