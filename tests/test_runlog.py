# Copyright 2026 MLCommons contributors
# SPDX-License-Identifier: Apache-2.0
"""The run log's format.

The log is the artifact someone attaches to a support thread weeks after the
run, so the parts worth pinning are the ones that make it readable on its own:
a header saying what produced it, one stamp per line and no more, and a footer
that says how it ended even when it ended badly.
"""

from __future__ import annotations

import datetime
import os
import re
import subprocess
import sys

import pytest

from mlperf_sysinfo import preflight as check_mod
from mlperf_sysinfo import profiles
from mlperf_sysinfo.collector import _log_details
from mlperf_sysinfo.config import load_config
from mlperf_sysinfo.preflight import NodeStatus, ProbeStatus, run_check
from mlperf_sysinfo.runlog import RunLog, log_filename

from .test_check_and_capture import _FakeMlc

STAMP = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] ")

WHEN = datetime.datetime(2026, 8, 19, 20, 21, 22)


@pytest.fixture
def log(tmp_path):
    """A log opened at a fixed time, so filenames and headers are assertable."""
    made = RunLog.open(
        tmp_path,
        command="capture",
        details={"Profile": "endpoints (round 6.0)", "Nodes": "1 -- this machine"},
        started=WHEN,
    )
    yield made
    made.finish()


@pytest.fixture
def endpoints_profile():
    return profiles.load("endpoints")


@pytest.fixture
def all_reachable(monkeypatch):
    monkeypatch.setattr(check_mod, "check_node", lambda t, a: NodeStatus(t, True, "H100 x 8"))
    monkeypatch.setattr(check_mod, "probe_endpoint", lambda u: ProbeStatus(u, True, "vLLM"))
    monkeypatch.setattr(check_mod, "check_serving_log", lambda t, p: ProbeStatus(p, True, ""))


@pytest.fixture
def fake_mlc(monkeypatch, collected):
    monkeypatch.setattr("mlperf_sysinfo.collector._require_mlc", lambda: _FakeMlc(collected))


def body(log: RunLog) -> list[str]:
    """The stamped lines, without the header and footer."""
    text = log.path.read_text()
    after_header = text.split("\n\n", 1)[1]
    return after_header.split("\n" + "-" * 72)[0].splitlines()


class TestFilename:
    def test_it_carries_the_start_time(self):
        assert log_filename("capture", WHEN) == "capture_20260819_202122.log"

    def test_a_retry_does_not_overwrite_the_failure_that_prompted_it(self, tmp_path):
        first = RunLog.open(tmp_path, command="capture", details={}, started=WHEN)
        first.finish("failed")
        second = RunLog.open(
            tmp_path,
            command="capture",
            details={},
            started=WHEN + datetime.timedelta(seconds=30),
        )
        second.finish("complete")
        assert first.path != second.path
        assert "failed" in first.path.read_text()
        assert len(list(tmp_path.glob("capture_*.log"))) == 2


class TestHeader:
    def test_it_opens_with_the_command_and_the_date(self, log):
        assert log.path.read_text().startswith("mlperf-sysinfo capture — 20260819_202122")

    def test_it_records_when_the_run_started(self, log):
        assert "Started   : 2026-08-19T20:21:22" in log.path.read_text()

    def test_it_records_both_versions(self, log):
        """Which mlc-scripts produced a capture is the first thing asked when
        the collected fields look wrong."""
        text = log.path.read_text()
        assert "Versions  : mlperf-sysinfo " in text
        assert "mlc-scripts " in text

    def test_it_carries_the_details_it_was_given(self, log):
        text = log.path.read_text()
        assert "Profile   : endpoints (round 6.0)" in text
        assert "Nodes     : 1 -- this machine" in text

    def test_it_records_the_command_line(self, log):
        assert "Invoked   : " in log.path.read_text()


class TestStamping:
    def test_a_bare_line_gets_a_stamp(self, log):
        log._emit_line(b"Raw system information written to /tmp/x.json")
        assert STAMP.match(body(log)[0])

    def test_a_line_mlcflow_already_stamped_is_left_alone(self, log):
        """Two stamps in front of one message is noise, and the second one
        would push the real timestamp out of the reader's eye line."""
        original = "[2026-08-19 20:23:33,496 deprecation.py :  66 WARN ] - deprecated"
        log._emit_line(original.encode())
        assert body(log)[0] == original

    def test_a_continuation_line_is_indented_rather_than_stamped(self, log):
        """An indented line is the tail of the message above it. Stamping each
        frame of a traceback pulls it apart exactly when it is being read."""
        log._emit_line(b"Traceback (most recent call last):")
        log._emit_line(b'  File "x.py", line 1, in <module>')
        first, second = body(log)
        assert STAMP.match(first)
        assert not STAMP.match(second)
        assert second.startswith(" " * 22)
        assert second.strip() == 'File "x.py", line 1, in <module>'

    def test_a_blank_line_stays_blank(self, log):
        """Blank lines are how the automation groups its output."""
        log._emit_line(b"first")
        log._emit_line(b"")
        log._emit_line(b"second")
        assert body(log)[1] == ""

    def test_a_note_records_what_this_package_concluded(self, log):
        log.note("1 of 2 node(s) returned hardware")
        assert body(log)[0].endswith("1 of 2 node(s) returned hardware")
        assert STAMP.match(body(log)[0])


class TestCapturing:
    def test_it_catches_output_from_a_subprocess(self, log):
        """The automation shells out, so redirecting sys.stdout would miss most
        of what it says -- the real file descriptors have to move."""
        with log.capturing():
            subprocess.run([sys.executable, "-c", "print('from a child')"], check=True)
        assert any("from a child" in line for line in body(log))

    def test_it_catches_writes_to_the_real_stderr(self, log):
        with log.capturing():
            os.write(2, b"straight to fd 2\n")
        assert any("straight to fd 2" in line for line in body(log))

    def test_the_terminal_is_restored_afterwards(self, log):
        before = os.fstat(1).st_ino
        with log.capturing():
            pass
        assert os.fstat(1).st_ino == before

    def test_echo_also_leaves_a_log_behind(self, log, tmp_path):
        """--verbose used to mean no log file at all, which left a failed
        verbose run with nothing to attach afterwards."""
        sink = tmp_path / "terminal"
        saved = os.dup(1)
        try:
            with open(sink, "wb") as fh:
                os.dup2(fh.fileno(), 1)
                with log.capturing(echo=True):
                    os.write(1, b"seen twice\n")
        finally:
            os.dup2(saved, 1)
            os.close(saved)
        assert "seen twice" in sink.read_text()
        assert any("seen twice" in line for line in body(log))


class TestFooter:
    def test_it_reports_the_outcome_and_how_long_it_took(self, log):
        log.finish("complete -- 1 of 1 node(s)")
        text = log.path.read_text()
        assert "Finished  : " in text
        assert "Duration  : " in text
        assert "Outcome   : complete -- 1 of 1 node(s)" in text

    def test_it_is_written_even_when_the_run_raises(self, tmp_path):
        """A log that stops mid-sentence is the case where the reader most
        needs to be told what happened."""
        with pytest.raises(RuntimeError):
            with RunLog.open(tmp_path, command="capture", details={}, started=WHEN) as raising:
                raising.note("got this far")
                raise RuntimeError("boom")
        text = raising.path.read_text()
        assert "got this far" in text
        assert "Outcome   : failed -- RuntimeError: boom" in text

    def test_the_outcome_stays_on_one_line(self, tmp_path):
        """Errors carry a multi-line 'See <log>' tail for the terminal, which
        would break the key : value shape the header and footer keep to."""
        with RunLog.open(tmp_path, command="capture", details={}, started=WHEN) as one_line:
            one_line.outcome = f"failed -- collection died\n  See {one_line.path}"
        outcome = [
            line for line in one_line.path.read_text().splitlines() if line.startswith("Outcome")
        ]
        assert outcome == ["Outcome   : failed -- collection died See this log"]

    def test_finishing_twice_does_not_append_a_second_footer(self, log):
        log.finish("complete")
        log.finish("complete")
        assert log.path.read_text().count("Outcome   :") == 1


class TestDetailsFromARun:
    """What the collector puts in the header, built from a real config."""

    def test_the_local_machine_is_counted(self, good_config_file):
        """report.nodes holds only the SSH targets, so building this from it
        alone reported 'Nodes: 0' for a single-box capture."""
        config = load_config(good_config_file)
        config.nodes.include_local = True
        config.nodes.ssh = []
        profile = profiles.load("endpoints")
        details = _log_details(
            config, profile, run_check(config, profile, skip_network=True), config.output_dir / "x"
        )
        assert details["Nodes"] == "1 -- this machine"

    def test_the_round_is_recorded_even_though_it_is_never_printed(self, good_config_file):
        """A log is a file, and which rules produced a run is exactly what a
        file has to say for itself."""
        config = load_config(good_config_file)
        profile = profiles.load("endpoints")
        details = _log_details(
            config, profile, run_check(config, profile, skip_network=True), config.output_dir / "x"
        )
        assert "round 6.0" in details["Profile"]
        assert "benchmark endpoints" in details["Profile"]


class TestACaptureLeavesOne:
    """End to end: a capture's log is a returned path, not something to hunt for."""

    def test_the_result_points_at_the_log(
        self, good_config_file, endpoints_profile, all_reachable, fake_mlc
    ):
        from mlperf_sysinfo.collector import capture

        result = capture(load_config(good_config_file), endpoints_profile)
        assert result.log_path is not None
        assert result.log_path.exists()
        assert result.log_path.name.startswith("capture_")

    def test_it_records_the_verdict_as_well_as_the_chatter(
        self, good_config_file, endpoints_profile, all_reachable, fake_mlc
    ):
        """The automation never says what this package concluded from what it
        returned, which is what a reader is usually reconstructing."""
        from mlperf_sysinfo.collector import capture

        result = capture(load_config(good_config_file), endpoints_profile)
        text = result.log_path.read_text()
        assert "node(s) returned hardware" in text
        assert f"wrote {result.output_path}" in text
        assert "Outcome   : complete" in text

    def test_a_partial_capture_says_so_in_the_footer(
        self, good_config_file, endpoints_profile, all_reachable, monkeypatch, collected
    ):
        """A partial file is the one someone comes back to the log about."""
        import copy

        from mlperf_sysinfo.collector import capture

        one = copy.deepcopy(collected)
        one["node_types"][0]["number_of_nodes"] = 1  # two were asked for
        monkeypatch.setattr("mlperf_sysinfo.collector._require_mlc", lambda: _FakeMlc(one))
        result = capture(load_config(good_config_file), endpoints_profile, allow_partial=True)
        assert (
            "Outcome   : partial -- only 1 of 2 node(s) answered" in result.log_path.read_text()
        )

    def test_the_cli_prints_where_it_went(
        self, good_config_file, endpoints_profile, all_reachable, fake_mlc, capsys
    ):
        from mlperf_sysinfo.cli import capture as capture_cmd

        capture_cmd(config=good_config_file)
        out = capsys.readouterr().out
        assert "log " in out
        assert "capture_" in out and ".log" in out
