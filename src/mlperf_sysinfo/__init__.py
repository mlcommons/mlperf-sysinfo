# Copyright 2026 MLCommons contributors
# SPDX-License-Identifier: Apache-2.0
"""mlperf-sysinfo -- capture MLPerf system descriptions.

One config format, one command line, and a profile per working group that
decides what is required and what the output looks like.

Embedding it in another tool:

    from mlperf_sysinfo import load_config, capture

    config = load_config("sysinfo.yaml")
    result = capture(config)

    result.output_path   # where the file landed
    result.nodes         # per-node status, so callers can report properly
    result.complete      # False if any node did not answer
"""

from __future__ import annotations

# Read from the installed distribution so this can never drift from
# pyproject.toml -- the release workflow checks the tag against pyproject,
# and a hand-maintained copy here would not be caught by that check.
from importlib.metadata import PackageNotFoundError  # noqa: E402
from importlib.metadata import version as _version  # noqa: E402

try:
    __version__ = _version("mlperf-sysinfo")
except PackageNotFoundError:  # running from a source tree, not installed
    __version__ = "0.0.0+unknown"

from .config import SysinfoConfig, load_config  # noqa: E402
from .errors import (  # noqa: E402
    CaptureError,
    CheckFailed,
    ConfigError,
    DependencyMissing,
    ProfileError,
    SysinfoError,
)

__all__ = [
    "__version__",
    "SysinfoConfig",
    "load_config",
    "capture",
    "check",
    "SysinfoError",
    "ConfigError",
    "ProfileError",
    "CheckFailed",
    "CaptureError",
    "DependencyMissing",
]


def capture(config, profile=None, **kwargs):
    """Check, collect, shape, write. See :mod:`mlperf_sysinfo.collector`."""
    from .collector import capture as _capture

    return _capture(config, profile, **kwargs)


def check(config, profile=None, **kwargs):
    """Run pre-flight only. See :mod:`mlperf_sysinfo.preflight`."""
    from .preflight import run_check
    from .profiles import load as load_profile

    if profile is None:
        relative_to = config.source_path.parent if config.source_path else None
        profile = load_profile(config.profile, relative_to=relative_to)
    return run_check(config, profile, **kwargs)
