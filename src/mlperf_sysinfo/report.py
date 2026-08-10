# Copyright 2026 MLCommons contributors
# SPDX-License-Identifier: Apache-2.0
"""Reading a captured file back: ``show`` and ``validate``.

Both work on the file alone. Once a capture is written it carries the profile
it was made under, so neither command needs the original config.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError
from .output import output_key_for
from .profiles import Profile
from .profiles import load as load_profile

#: Text the old pipeline used to write when nobody filled a field in.
PLACEHOLDER_MARKERS = ("insert ", "your organization name", "insert a contact")


@dataclass
class ValidationReport:
    path: Path
    profile_name: str
    profile_round: str
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems


def load_capture(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"file not found: {path}")
    try:
        data = json.loads(path.read_text())
    except ValueError as e:
        raise ConfigError(f"{path}: not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a JSON object")
    return data


def _resolve_profile(data: dict, override: str | None) -> Profile:
    name = override or (data.get("mlperf_sysinfo") or {}).get("profile")
    if not name:
        raise ConfigError(
            "this file has no profile stamp, so there is nothing to validate it "
            "against. Pass --profile to say which rules should apply."
        )
    return load_profile(name)


def validate(path: str | Path, *, profile_name: str | None = None) -> ValidationReport:
    """Check a captured file against a profile's requirements."""
    path = Path(path)
    data = load_capture(path)
    profile = _resolve_profile(data, profile_name)
    stamp = data.get("mlperf_sysinfo") or {}

    report = ValidationReport(
        path=path, profile_name=profile.name, profile_round=profile.round
    )

    if stamp.get("complete") is False:
        expected = stamp.get("nodes_expected", "?")
        collected = stamp.get("nodes_collected", "?")
        report.problems.append(
            f"partial capture: {collected} of {expected} nodes answered. "
            f"This file does not describe the whole system."
        )

    for config_path, why in profile.requires.items():
        key = output_key_for(config_path, profile.shape)
        if key is None:
            continue
        report.checked += 1
        value = data.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            report.problems.append(f"{key} is empty -- {why}")

    for key, value in data.items():
        if isinstance(value, str):
            lowered = value.lower()
            if any(marker in lowered for marker in PLACEHOLDER_MARKERS):
                report.problems.append(f"{key} still holds placeholder text: {value!r}")

    if profile.shape == "nested":
        node_types = data.get("node_types")
        if not node_types:
            report.problems.append("node_types is empty -- no hardware was collected")
    else:
        if not data.get("number_of_nodes"):
            report.problems.append("number_of_nodes is zero -- no hardware was collected")
        if not data.get("host_processor_model_name"):
            report.warnings.append(
                "host_processor_model_name is empty -- CPU detection did not return a name"
            )

    for config_path, why in profile.recommends.items():
        key = output_key_for(config_path, profile.shape)
        if key is None:
            continue
        value = data.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            report.warnings.append(f"{key} is empty -- {why}")

    return report


@dataclass
class Summary:
    """A readable view of a captured file."""

    path: Path
    profile: str
    profile_round: str
    captured_at: str
    complete: bool
    shape: str
    system_name: str
    system_size: str
    node_count: int
    accelerator_total: int
    framework: str
    nodes: list[tuple[str, int, str]] = field(default_factory=list)
    detected: list[tuple[str, str]] = field(default_factory=list)
    supplied: list[tuple[str, str]] = field(default_factory=list)


_SUPPLIED_NESTED = [
    ("submitter", "submitter_org_names"),
    ("contact", "submitter_contact"),
    ("division", "division"),
    ("category", "system_category"),
    ("availability", "system_availability_status"),
    ("model", "model_name"),
    ("precision", "model_precision"),
    ("dataset", "dataset_name"),
]

_SUPPLIED_FLAT = [
    ("submitter", "submitter"),
    ("contact", "submitter_contact"),
    ("division", "division"),
    ("system type", "system_type"),
    ("status", "status"),
]

_DETECTED_FIELDS = [
    ("cpu", "host_processor_model_name"),
    ("cores", "host_processor_core_count"),
    ("memory", "host_memory_capacity"),
    ("accelerator", "accelerator_model_name"),
    ("per node", "accelerators_per_node"),
    ("os", "operating_system"),
    ("software", "other_software_stack"),
]


def summarise(path: str | Path) -> Summary:
    path = Path(path)
    data = load_capture(path)
    stamp = data.get("mlperf_sysinfo") or {}
    shape_name = stamp.get("shape") or ("nested" if "node_types" in data else "flat")

    node_types = data.get("node_types") or []
    accel_total = 0
    nodes: list[tuple[str, int, str]] = []
    for nt in node_types:
        count = nt.get("number_of_nodes", 1)
        accel = nt.get("accelerator_model_name") or "no accelerator detected"
        per_node = nt.get("accelerators_per_node")
        try:
            accel_total += int(count) * int(per_node)
        except (TypeError, ValueError):
            pass
        nodes.append((accel, count, str(nt.get("host_processor_model_name") or "")))

    first = node_types[0] if node_types else data
    detected = [
        (label, str(first.get(key)))
        for label, key in _DETECTED_FIELDS
        if first.get(key) not in (None, "", "N/A", "Not available")
    ]

    table = _SUPPLIED_NESTED if shape_name == "nested" else _SUPPLIED_FLAT
    supplied = [(label, str(data.get(key))) for label, key in table if data.get(key)]

    return Summary(
        path=path,
        profile=stamp.get("profile", "unknown"),
        profile_round=stamp.get("profile_round", "?"),
        captured_at=stamp.get("captured_at", "unknown"),
        complete=bool(stamp.get("complete", True)),
        shape=shape_name,
        system_name=str(data.get("system_name", "")),
        system_size=str(data.get("system_size", "")),
        node_count=int(data.get("number_of_nodes") or data.get("system_node_ensemble_total") or 0),
        accelerator_total=accel_total,
        framework=str(data.get("serving_framework") or data.get("framework") or ""),
        nodes=nodes,
        detected=detected,
        supplied=supplied,
    )
