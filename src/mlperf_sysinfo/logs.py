# Copyright 2026 MLCommons contributors
# SPDX-License-Identifier: Apache-2.0
"""Leveled logging for this package's own actions.

The styled blocks the commands print are a *summary* -- what a person needs to
decide what to do next. They are deliberately short, and they say nothing about
how the tool got there: which config file won after ``extends`` merging, which
profile path resolved, how long a probe took, why a field ended up blank. When a
capture comes back looking wrong, that trace is the whole question.

So every module logs its own actions through here, at a level, with a date, in
the same shape as the run log's other lines::

    [2026-08-19 20:33:57] INFO     preflight: 5 required field(s) set

Two sinks, on purpose:

* **The run log file** takes every level, so a capture's file holds this
  package's reasoning next to the automation's output rather than in a
  different place with a different format.
* **The terminal** takes WARNING and above by default. Anything the styled
  output already reports is logged below that line, because printing it twice
  in two formats is worse than either alone. ``--verbose`` lowers the terminal
  to DEBUG when you want the trace live.

Records emitted before a run log exists -- loading the config, resolving the
profile, the whole pre-flight check -- are held in a buffer and replayed into
the file the moment it opens. Those early records are usually the interesting
ones, so losing them to ordering would defeat the point.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

from . import ui

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterable

#: The package logger. Every module uses a child of it via ``get(__name__)``.
LOGGER_NAME = "mlperf_sysinfo"

#: Levels a person may ask for by name, in the order they narrow.
LEVELS = ("debug", "info", "warning", "error")

DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

#: Widest level name, so the message column lines up.
_LEVEL_WIDTH = len("CRITICAL")

#: How many early records to hold before a run log exists. A capture emits a
#: few dozen; the cap only exists so an embedder that never opens one cannot
#: grow this without bound.
_BUFFER_LIMIT = 2000

_LEVEL_STYLE = {
    "DEBUG": ui.dim,
    "INFO": ui.cyan,
    "WARNING": ui.yellow,
    "ERROR": ui.red,
    "CRITICAL": lambda text: ui.bold(ui.red(text)),
}


def get(module_name: str) -> logging.Logger:
    """The logger for a module, named so the log reads ``preflight:``.

    Call as ``log = logs.get(__name__)`` at module scope.
    """
    short = module_name.removeprefix(f"{LOGGER_NAME}.") or LOGGER_NAME
    return logging.getLogger(f"{LOGGER_NAME}.{short}")


def _short_name(record: logging.LogRecord) -> str:
    return record.name.removeprefix(f"{LOGGER_NAME}.").removeprefix(LOGGER_NAME).lstrip(".")


class PlainFormatter(logging.Formatter):
    """``[date] LEVEL    module: message`` -- what goes in the file."""

    def __init__(self) -> None:
        super().__init__(datefmt=DATE_FORMAT)

    def format(self, record: logging.LogRecord) -> str:
        stamp = self.formatTime(record, self.datefmt)
        level = record.levelname.ljust(_LEVEL_WIDTH)
        where = _short_name(record)
        head = f"[{stamp}] {level} {where}: " if where else f"[{stamp}] {level} "
        body = record.getMessage()
        if record.exc_info:
            body = f"{body}\n{self.formatException(record.exc_info)}"
        # Continuation lines are indented under the message rather than
        # repeating the prefix, matching how the run log treats them.
        return head + body.replace("\n", "\n" + " " * len(head))


class ColourFormatter(PlainFormatter):
    """The same line for a terminal: dim furniture, the level carrying colour.

    Only the level is coloured. Colouring the whole line would put it in
    competition with the styled summary blocks, which are the thing meant to
    catch the eye.
    """

    def format(self, record: logging.LogRecord) -> str:
        stamp = self.formatTime(record, self.datefmt)
        style = _LEVEL_STYLE.get(record.levelname, str)
        level = style(record.levelname.ljust(_LEVEL_WIDTH))
        where = _short_name(record)
        prefix_width = len(f"[{stamp}] ") + _LEVEL_WIDTH + 1 + (len(where) + 2 if where else 0)
        head = ui.dim(f"[{stamp}] ") + level + " " + (ui.dim(f"{where}: ") if where else "")
        body = record.getMessage()
        if record.exc_info:
            body = f"{body}\n{self.formatException(record.exc_info)}"
        return head + body.replace("\n", "\n" + " " * prefix_width)


class _Buffer(logging.Handler):
    """Holds records until a run log exists to replay them into."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        if len(self.records) < _BUFFER_LIMIT:
            self.records.append(record)

    def drain(self) -> Iterable[logging.LogRecord]:
        held, self.records = self.records, []
        return held


_buffer = _Buffer()
_terminal: logging.Handler | None = None


def _package_logger() -> logging.Logger:
    """The package logger, levelled to pass everything to its handlers.

    Done at import rather than in ``setup``, because embedders call
    ``capture()`` without ever going through the CLI. Left at NOTSET the
    logger would inherit the root's WARNING and drop every INFO record before
    a handler could see it -- so the run log of an embedded capture would come
    out with only its warnings.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    if _buffer not in logger.handlers:
        logger.addHandler(_buffer)
    return logger


_package_logger()


def setup(level: str | int = logging.WARNING) -> None:
    """Install the terminal handler and start holding records for the run log.

    Safe to call more than once; the second call re-levels rather than
    stacking a second handler, so an embedder that configures its own logging
    is not given duplicate output.
    """
    global _terminal
    logger = _package_logger()
    # Only once the CLI is driving. An embedder that never calls setup() keeps
    # propagation, so its own root handlers still see these records -- but when
    # this package owns the terminal, propagating would print everything twice.
    logger.propagate = False
    if _terminal is None:
        _terminal = logging.StreamHandler(stream=_TerminalStream())
        _terminal.setFormatter(ColourFormatter())
        logger.addHandler(_terminal)
    _terminal.setLevel(_resolve(level))


def set_level(level: str | int) -> None:
    """Change what reaches the terminal. The file always takes everything."""
    if _terminal is not None:
        _terminal.setLevel(_resolve(level))


def _resolve(level: str | int) -> int:
    if isinstance(level, int):
        return level
    resolved = logging.getLevelName(level.upper())
    if not isinstance(resolved, int):
        raise ValueError(f"unknown log level {level!r} -- expected one of {', '.join(LEVELS)}")
    return resolved


class _TerminalStream:
    """stderr, with stdout flushed first.

    The styled output goes to stdout and these lines go to stderr, so that
    piping the styled report somewhere keeps the trace out of it. Two streams
    means two buffers, though, and a redirected run would otherwise show every
    log line bunched before the report instead of beside it.
    """

    def write(self, text: str) -> int:
        sys.stdout.flush()
        return sys.stderr.write(text)

    def flush(self) -> None:
        sys.stderr.flush()


def point_terminal_at(stream) -> object | None:
    """Swap the terminal handler's stream, returning the previous one.

    Used while the automation runs: the real stderr is redirected into the run
    log's pipe for the duration, so a record written to it would come back
    through the pump, be stamped a second time, and land in the file with its
    colour codes intact -- while never reaching the terminal it was meant for.
    """
    if _terminal is None:
        return None
    previous = _terminal.stream
    _terminal.setStream(stream)
    return previous


def attach(handler: logging.Handler) -> None:
    """Add *handler* and replay everything logged before it existed."""
    logger = _package_logger()
    if handler not in logger.handlers:
        logger.addHandler(handler)
    for record in _buffer.drain():
        if record.levelno >= handler.level:
            handler.handle(record)


def detach(handler: logging.Handler) -> None:
    logging.getLogger(LOGGER_NAME).removeHandler(handler)
