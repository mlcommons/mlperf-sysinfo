# Copyright 2026 MLCommons contributors
# SPDX-License-Identifier: Apache-2.0
"""Exception types. Every user-facing failure is one of these."""

from __future__ import annotations


class SysinfoError(Exception):
    """Base class for every error this package raises deliberately."""


class ConfigError(SysinfoError):
    """The config file is missing, malformed, or fails schema validation."""


class ProfileError(SysinfoError):
    """The requested profile does not exist or is itself invalid."""


class CheckFailed(SysinfoError):
    """Pre-flight check found problems. Capture must not proceed.

    Carries the report so callers can render it themselves instead of
    re-running the check.
    """

    def __init__(self, message: str, report=None):
        super().__init__(message)
        self.report = report


class CaptureError(SysinfoError):
    """Collection ran but failed."""


class DependencyMissing(SysinfoError):
    """mlc-scripts is not importable."""
