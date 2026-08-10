# Copyright 2026 MLCommons contributors
# SPDX-License-Identifier: Apache-2.0
"""Profiles: what a working group requires, collects, and writes.

A profile answers three questions -- which fields must be filled in, which
extra collection steps to run, and what the output file looks like. Built-in
profiles live in this directory and ship with the package. A config may also
name a path to a profile file that has not been upstreamed yet.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..errors import ProfileError

_BUILTIN_DIR = Path(__file__).parent


class CollectFlags(BaseModel):
    """Which optional collection steps this profile turns on."""

    model_config = ConfigDict(extra="forbid")

    serving_log: bool = Field(
        default=False, description="SSH to serving.node and parse the startup log."
    )
    endpoint_probe: bool = Field(
        default=False, description="HTTP-probe serving.url for framework and version."
    )
    redfish: bool = Field(
        default=False, description="Allow BMC capture when power.redfish is configured."
    )


class Profile(BaseModel):
    """A named bundle of requirements, collection steps, and an output shape."""

    model_config = ConfigDict(extra="forbid")

    name: str
    title: str
    round: str = Field(description="The MLPerf round these requirements describe.")
    description: str = ""
    output_file: str = "system_desc.json"
    shape: Literal["nested", "flat"] = "nested"
    collect: CollectFlags = Field(default_factory=CollectFlags)
    requires: dict[str, str] = Field(
        default_factory=dict,
        description="Dotted config path -> why it is needed. Absence stops a run.",
    )
    recommends: dict[str, str] = Field(
        default_factory=dict,
        description="Dotted config path -> why it helps. Absence is a warning.",
    )
    extra_field_groups: list[str] = Field(
        default_factory=list,
        description="Named blocks of empty submission fields to add, e.g. power, network.",
    )

    @property
    def is_builtin(self) -> bool:
        return (_BUILTIN_DIR / f"{self.name}.yaml").exists()


def available() -> list[str]:
    """Names of the built-in profiles, sorted."""
    return sorted(p.stem for p in _BUILTIN_DIR.glob("*.yaml"))


def _load_file(path: Path) -> Profile:
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ProfileError(f"{path}: invalid YAML: {e}") from e
    if not isinstance(raw, dict):
        raise ProfileError(f"{path}: expected a YAML mapping")
    try:
        return Profile.model_validate(raw)
    except ValidationError as e:
        raise ProfileError(f"{path}: not a valid profile:\n{e}") from e


def load(ref: str, *, relative_to: Path | None = None) -> Profile:
    """Load a profile by built-in name, or by path when the name looks like one."""
    ref = ref.strip()
    if not ref:
        raise ProfileError("no profile named; set 'profile:' in the config")

    if "@" in ref:
        base, _, suffix = ref.partition("@")
        raise ProfileError(
            f"profile {ref!r} pins a version. Profiles always track the current "
            f"round -- use 'profile: {base}' instead (this run would have used "
            f"round {suffix!r})."
        )

    looks_like_path = ref.endswith((".yaml", ".yml")) or "/" in ref or ref.startswith(".")
    if looks_like_path:
        path = Path(ref).expanduser()
        if not path.is_absolute() and relative_to is not None:
            path = (relative_to / path).resolve()
        if not path.exists():
            raise ProfileError(f"profile file not found: {path}")
        return _load_file(path)

    builtin = _BUILTIN_DIR / f"{ref}.yaml"
    if not builtin.exists():
        names = ", ".join(available())
        raise ProfileError(
            f"unknown profile {ref!r}. Built-in profiles: {names}. "
            f"To use your own, point 'profile:' at a .yaml file."
        )
    return _load_file(builtin)
