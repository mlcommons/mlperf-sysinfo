# Copyright 2026 MLCommons contributors
# SPDX-License-Identifier: Apache-2.0
"""Leveled logging of this package's own actions.

Two properties carry most of the weight. The run log file must take every level
regardless of what the terminal is set to, because it is the durable record.
And no fact should reach the terminal twice -- the styled blocks own the
verdicts, so a warning rendered there must not also print as a log line three
lines above it.
"""

from __future__ import annotations

import datetime
import logging
import re

import pytest

from mlperf_sysinfo import logs, profiles
from mlperf_sysinfo.cli import DEFAULT_LOG_LEVEL, _setup_logging
from mlperf_sysinfo.config import load_config
from mlperf_sysinfo.preflight import run_check
from mlperf_sysinfo.runlog import RunLog

LINE = re.compile(
    r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] (DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+(\S+): (.*)$"
)

WHEN = datetime.datetime(2026, 8, 19, 20, 21, 22)


@pytest.fixture(autouse=True)
def quiet_terminal(monkeypatch):
    """Keep these tests off the real terminal handler's stream.

    Also empties the replay buffer. It is deliberately not cleared on drain
    failure in production, so without this every test would find the previous
    test's records replayed into its file.
    """
    monkeypatch.setattr(logs, "_terminal", None)
    logs._buffer.records.clear()
    yield
    logs._buffer.records.clear()


@pytest.fixture
def log(tmp_path):
    made = RunLog.open(tmp_path, command="capture", details={}, started=WHEN)
    yield made
    made.finish()


def body(log: RunLog) -> list[str]:
    text = log.path.read_text()
    return text.split("\n\n", 1)[1].split("\n" + "-" * 72)[0].splitlines()


class TestLineShape:
    def test_it_carries_a_date_a_level_and_the_module(self, log):
        logs.get("mlperf_sysinfo.preflight").info("5 required field(s) set")
        match = LINE.match(body(log)[0])
        assert match, body(log)[0]
        assert match.group(1) == "INFO"
        assert match.group(2) == "preflight"
        assert match.group(3) == "5 required field(s) set"

    @pytest.mark.parametrize(
        "level,name",
        [
            (logging.DEBUG, "DEBUG"),
            (logging.INFO, "INFO"),
            (logging.WARNING, "WARNING"),
            (logging.ERROR, "ERROR"),
            (logging.CRITICAL, "CRITICAL"),
        ],
    )
    def test_every_level_is_named(self, log, level, name):
        logs.get("mlperf_sysinfo.collector").log(level, "something happened")
        assert LINE.match(body(log)[0]).group(1) == name

    def test_the_module_column_drops_the_package_prefix(self, log):
        """'preflight:' reads; 'mlperf_sysinfo.preflight:' is 15 characters of
        the same word on every line."""
        logs.get("mlperf_sysinfo.output").warning("blank field")
        assert "output: blank field" in body(log)[0]
        assert "mlperf_sysinfo.output" not in body(log)[0]

    def test_a_multi_line_message_is_indented_under_itself(self, log):
        logs.get("mlperf_sysinfo.config").error("first line\nsecond line")
        first, second = body(log)
        assert LINE.match(first)
        assert not LINE.match(second)
        assert second.strip() == "second line"


class TestTheFileTakesEverything:
    def test_debug_reaches_the_file_even_when_the_terminal_is_at_error(self, log):
        """The file is the durable record; what a person wants on screen is a
        separate question from what is worth keeping."""
        logs.setup("error")
        logs.get("mlperf_sysinfo.preflight").debug("probing endpoint")
        assert any("DEBUG" in line and "probing endpoint" in line for line in body(log))

    def test_records_from_before_the_file_existed_are_replayed(self, tmp_path):
        """Loading the config and the whole pre-flight check happen before a run
        log exists. Those are usually the interesting records."""
        logs.get("mlperf_sysinfo.config").info("loaded config early.yaml")
        later = RunLog.open(tmp_path, command="capture", details={}, started=WHEN)
        try:
            assert any("loaded config early.yaml" in line for line in body(later))
        finally:
            later.finish()

    def test_the_buffer_does_not_grow_without_bound(self):
        """An embedder that never opens a run log must not accumulate forever."""
        logs._buffer.records.clear()
        logger = logs.get("mlperf_sysinfo.collector")
        for i in range(logs._BUFFER_LIMIT + 50):
            logger.debug("record %d", i)
        assert len(logs._buffer.records) == logs._BUFFER_LIMIT
        logs._buffer.records.clear()

    def test_detaching_stops_the_writes(self, tmp_path):
        finished = RunLog.open(tmp_path, command="capture", details={}, started=WHEN)
        finished.finish("complete")
        before = finished.path.read_text()
        logs.get("mlperf_sysinfo.collector").error("after the file closed")
        assert finished.path.read_text() == before


class TestLevelResolution:
    def test_a_name_resolves(self):
        assert logs._resolve("info") == logging.INFO
        assert logs._resolve("DEBUG") == logging.DEBUG

    def test_an_int_passes_through(self):
        assert logs._resolve(logging.WARNING) == logging.WARNING

    def test_an_unknown_name_is_rejected(self):
        with pytest.raises(ValueError, match="unknown log level"):
            logs._resolve("shout")

    def test_verbose_means_debug(self):
        _setup_logging(verbose=True)
        assert logs._terminal.level == logging.DEBUG

    def test_an_explicit_level_beats_verbose(self):
        """--verbose --log-level info still echoes the automation without the
        debug detail."""
        _setup_logging(verbose=True, level="info")
        assert logs._terminal.level == logging.INFO

    def test_the_default_keeps_the_terminal_for_the_styled_blocks(self):
        """Every warning the check produces is already rendered as a styled row,
        so printing it as a log line as well says it twice in two formats."""
        _setup_logging(verbose=False)
        assert logs._terminal.level == logging.ERROR
        assert DEFAULT_LOG_LEVEL == "error"


class TestSetupIsIdempotent:
    def test_calling_twice_does_not_stack_handlers(self):
        logs.setup("info")
        first = len(logging.getLogger(logs.LOGGER_NAME).handlers)
        logs.setup("debug")
        assert len(logging.getLogger(logs.LOGGER_NAME).handlers) == first

    def test_the_second_call_relevels(self):
        logs.setup("info")
        logs.setup("error")
        assert logs._terminal.level == logging.ERROR


class TestLibraryUseWithoutTheCli:
    def test_records_are_produced_without_setup_ever_running(self, tmp_path, monkeypatch):
        """Embedders call capture() directly. Left at NOTSET the logger would
        inherit the root's WARNING and drop every INFO before a handler saw it,
        so an embedded capture's log would come out with only its warnings."""
        monkeypatch.setattr(logs, "_terminal", None)
        assert logging.getLogger(logs.LOGGER_NAME).level == logging.DEBUG
        opened = RunLog.open(tmp_path, command="capture", details={}, started=WHEN)
        try:
            logs.get("mlperf_sysinfo.collector").info("collected 1 node")
            assert any("collected 1 node" in line for line in body(opened))
        finally:
            opened.finish()


class TestARealCheckIsTraced:
    def test_the_config_load_and_the_probes_are_recorded(self, good_config_file, tmp_path):
        config = load_config(good_config_file)
        opened = RunLog.open(tmp_path, command="capture", details={}, started=WHEN)
        try:
            run_check(config, profiles.load("endpoints"), skip_network=True)
        finally:
            opened.finish()
        text = opened.path.read_text()
        assert "preflight: " in text
        assert "required field(s) set" in text
        assert "skipping every network probe" in text
