# Copyright 2026 MLCommons contributors
# SPDX-License-Identifier: Apache-2.0
"""The run log.

Everything the automation says about a run lands in one file, and until now it
landed there raw. That is a problem because the output is a *mix*: lines that
come through mlcflow's logger already carry
``[2026-08-19 12:07:51,861 module.py : 66 WARN ]``, while bare prints from the
scripts and from SSH carry nothing at all. A capture of two nodes produces the
same ``sudo: a password is required`` three times with no way to tell which
probe each belonged to.

So this module stamps every line that is not already stamped, opens the file
with a header saying what produced it, lets the collector record its own
verdicts alongside the automation's chatter, and closes with the outcome. The
header/footer shape deliberately follows the submission checker's report log in
``endpoints-submission-cli`` -- someone attaching both to a support thread
should not have to learn two layouts.

The filename carries the start time, so a retry never overwrites the log of the
failure that prompted it.
"""

from __future__ import annotations

import datetime
import importlib.metadata
import logging
import os
import re
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from . import logs

#: mlcflow stamps its own lines. A second stamp in front of one would be noise,
#: so those pass through untouched and the two styles still line up.
_ALREADY_STAMPED = re.compile(rb"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")

#: An indented line is a continuation of the one above it -- the wrapped tail
#: of a warning, or the frames of a traceback. Stamping those claims each was
#: logged on its own, and pulls a traceback apart at exactly the moment someone
#: is trying to read it.
_CONTINUATION = re.compile(rb"^[ \t]")

#: Width of the header's key column, matching the reference report log.
_KEY_WIDTH = 10

_READ_SIZE = 65536


_STAMP_FORMAT = "[%Y-%m-%d %H:%M:%S] "
_STAMP_WIDTH = len("[2026-08-19 20:23:33] ")


def _stamp(when: datetime.datetime) -> bytes:
    return when.strftime(_STAMP_FORMAT).encode()


def _iso(when: datetime.datetime) -> str:
    return when.astimezone().isoformat(timespec="seconds")


def log_filename(command: str, started: datetime.datetime) -> str:
    """``capture_20260819_193045.log`` -- sortable, and unique per run."""
    return f"{command}_{started.strftime('%Y%m%d_%H%M%S')}.log"


def package_version(name: str) -> str:
    """The installed version of *name*, or a readable stand-in.

    Worth recording even when it fails: "not installed" in a log beats a
    missing line when the question is why collection returned nothing.
    """
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover - env dependent
        return "not installed"


def _entry(key: str, value: str) -> str:
    return f"{key:<{_KEY_WIDTH}}: {value}"


class _Handler(logging.Handler):
    """Sends this package's log records into the run log file.

    Every level, unconditionally: the file is the durable record, and the
    decision about what is worth a person's attention belongs to the terminal
    handler, not here.
    """

    def __init__(self, log: RunLog) -> None:
        super().__init__(level=logging.DEBUG)
        self._log = log
        self.setFormatter(logs.PlainFormatter())

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._log._write_bytes(self.format(record).encode(errors="replace") + b"\n")
        except Exception:  # pragma: no cover - handler must never raise
            self.handleError(record)


class RunLog:
    """One run's log file: header, stamped body, footer.

    Used as a context manager so the footer is written even when the run raises
    -- a log that stops mid-sentence is the one case where the reader most
    needs to know what happened.
    """

    def __init__(
        self,
        path: Path,
        *,
        command: str,
        started: datetime.datetime,
        details: dict[str, str],
    ) -> None:
        self.path = path
        self.command = command
        self.started = started
        #: Set by the caller before exit; the footer reports it verbatim.
        self.outcome: str = "did not finish"
        self._details = details
        self._lock = threading.Lock()
        self._handle = None
        self._handler: logging.Handler | None = None

    @classmethod
    def open(
        cls,
        directory: Path,
        *,
        command: str,
        details: dict[str, str],
        started: datetime.datetime | None = None,
    ) -> RunLog:
        started = started or datetime.datetime.now()
        directory.mkdir(parents=True, exist_ok=True)
        log = cls(
            directory / log_filename(command, started),
            command=command,
            started=started,
            details=details,
        )
        log._begin()
        return log

    # -- lifecycle ---------------------------------------------------------

    def _begin(self) -> None:
        self._handle = open(self.path, "wb")
        head = [
            f"mlperf-sysinfo {self.command} — {self.started.strftime('%Y%m%d_%H%M%S')}",
            _entry("Started", _iso(self.started)),
            _entry(
                "Versions",
                f"mlperf-sysinfo {package_version('mlperf-sysinfo')}, "
                f"mlc-scripts {package_version('mlc-scripts')}",
            ),
        ]
        head += [_entry(key, value) for key, value in self._details.items()]
        head.append(_entry("Invoked", " ".join(sys.argv)))
        self._write("\n".join(head) + "\n\n")
        # Attaching replays what was logged before this file existed -- the
        # config load, the profile resolution, the whole pre-flight check.
        self._handler = _Handler(self)
        logs.attach(self._handler)

    def finish(self, outcome: str | None = None) -> None:
        """Write the footer and close. Safe to call twice."""
        if self._handle is None:
            return
        if outcome is not None:
            self.outcome = outcome
        ended = datetime.datetime.now()
        # Errors carry a "See <log path>" tail for the terminal. Inside the log
        # itself that is circular, and its newline would break the key : value
        # shape the rest of the header and footer keep to.
        summary = " ".join(self.outcome.replace(str(self.path), "this log").split())
        self._write(
            "\n".join(
                [
                    "",
                    "-" * 72,
                    _entry("Finished", _iso(ended)),
                    _entry("Duration", f"{(ended - self.started).total_seconds():.1f}s"),
                    _entry("Outcome", summary),
                ]
            )
            + "\n"
        )
        if self._handler is not None:
            logs.detach(self._handler)
            self._handler = None
        with self._lock:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> RunLog:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is not None and self.outcome == "did not finish":
            self.outcome = f"failed -- {type(exc).__name__}: {exc}"
        self.finish()
        return False

    # -- writing -----------------------------------------------------------

    def _write(self, text: str) -> None:
        self._write_bytes(text.encode(errors="replace"))

    def _write_bytes(self, data: bytes) -> None:
        with self._lock:
            if self._handle is None:
                return
            self._handle.write(data)
            self._handle.flush()

    def note(self, message: str) -> None:
        """Record one of *our* decisions, stamped like everything else.

        The automation's chatter alone never says what this package concluded
        from it, which is the thing a reader is usually trying to reconstruct.
        """
        self._write_bytes(_stamp(datetime.datetime.now()) + message.encode(errors="replace") + b"\n")

    def _emit_line(self, line: bytes) -> None:
        if not line.strip():
            self._write_bytes(b"\n")  # blank lines keep their grouping
        elif _ALREADY_STAMPED.match(line):
            self._write_bytes(line + b"\n")
        elif _CONTINUATION.match(line):
            # Indented to where our own stamp ends, so it sits under its parent.
            self._write_bytes(b" " * _STAMP_WIDTH + line + b"\n")
        else:
            self._write_bytes(_stamp(datetime.datetime.now()) + line + b"\n")

    # -- capturing the automation ------------------------------------------

    @contextmanager
    def capturing(self, *, echo: bool = False) -> Iterator[None]:
        """Funnel everything written to stdout/stderr into this log.

        The automation writes from subprocesses and over SSH as well as from
        Python, so the real file descriptors are replaced -- reassigning
        ``sys.stdout`` would miss most of it. They point at a pipe rather than
        straight at the file so each line can be stamped as it arrives.

        With *echo* the bytes also go to the real terminal unchanged, so
        ``--verbose`` looks exactly as it always has and still leaves a log
        behind.
        """
        read_fd, write_fd = os.pipe()
        saved_out, saved_err = os.dup(1), os.dup(2)
        pump = threading.Thread(
            target=self._pump,
            args=(read_fd, saved_out if echo else None),
            daemon=True,
        )
        pump.start()
        # Our own log lines must keep going to the real terminal. Left pointing
        # at sys.stderr they would follow fd 2 into the pipe, come back through
        # the pump stamped a second time, and land in the file with their colour
        # codes -- while never reaching the terminal they were written for.
        real_terminal = os.fdopen(os.dup(saved_err), "w", buffering=1, errors="replace")
        previous_stream = logs.point_terminal_at(real_terminal)
        try:
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(write_fd, 1)
            os.dup2(write_fd, 2)
            yield
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(saved_out, 1)
            os.dup2(saved_err, 2)
            if previous_stream is not None:
                logs.point_terminal_at(previous_stream)
            real_terminal.close()
            # Every copy of the write end must go before the pump sees EOF.
            os.close(write_fd)
            pump.join(timeout=10)
            os.close(saved_out)
            os.close(saved_err)

    def _pump(self, read_fd: int, echo_fd: int | None) -> None:
        """Read whole lines off the pipe, stamp them, write them out."""
        pending = b""
        try:
            while True:
                chunk = os.read(read_fd, _READ_SIZE)
                if not chunk:
                    break
                if echo_fd is not None:
                    try:
                        os.write(echo_fd, chunk)
                    except OSError:  # pragma: no cover - terminal went away
                        echo_fd = None
                pending += chunk
                *lines, pending = pending.split(b"\n")
                for raw in lines:
                    self._emit_line(raw.rstrip(b"\r"))
        except OSError:  # pragma: no cover - defensive
            pass
        finally:
            if pending:
                self._emit_line(pending.rstrip(b"\r"))
            os.close(read_fd)
