# Copyright 2026 MLCommons contributors
# SPDX-License-Identifier: Apache-2.0
"""Terminal output. Plain ANSI, no dependency, honours NO_COLOR and pipes."""

from __future__ import annotations

import os
import re
import sys

_ENABLED = (
    sys.stdout.isatty()
    and os.environ.get("NO_COLOR") is None
    and os.environ.get("TERM") != "dumb"
)


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _ENABLED else text


def bold(t: str) -> str:
    return _c("1", t)


def dim(t: str) -> str:
    return _c("2", t)


def green(t: str) -> str:
    return _c("32", t)


def red(t: str) -> str:
    return _c("31", t)


def yellow(t: str) -> str:
    return _c("33", t)


def cyan(t: str) -> str:
    return _c("36", t)


OK = green("✓")
BAD = red("✗")
WARN = yellow("!")
SKIP = dim("-")


def heading(text: str) -> None:
    print()
    print(dim(text.upper()))


def line(*parts: str) -> None:
    print(*parts)


def blank() -> None:
    print()


def kv(key: str, value: str, width: int = 10) -> None:
    print(f"  {dim(key.ljust(width))} {value}")


_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _visible_len(text: str) -> int:
    return len(_ANSI_RE.sub("", text))


def row(
    symbol: str,
    label: str,
    status: str,
    detail: str = "",
    label_width: int = 20,
    status_width: int = 12,
) -> None:
    """One aligned status line: symbol, what, verdict, and any extra.

    Padding is computed on visible characters so colour codes do not skew the
    columns.
    """
    padded_status = status + " " * max(0, status_width - _visible_len(status))
    text = f"  {symbol} {label.ljust(label_width)} {padded_status}"
    if detail:
        text = f"{text}  {dim(detail)}"
    print(text.rstrip())


def error(message: str) -> None:
    print(f"{red('error')}  {message}", file=sys.stderr)


def hint(message: str) -> None:
    print(dim(f"  {message}"))
