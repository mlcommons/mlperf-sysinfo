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

from .config import find_placeholders
from .errors import ConfigError
from .output import NODE_SCOPE, accelerator_count, output_key_for
from .profiles import Profile
from .profiles import load as load_profile


@dataclass
class ValidationReport:
    path: Path
    profile_name: str
    #: The round whose rules were applied. Read from the file and offered to
    #: embedders, but deliberately not printed: a profile always tracks the
    #: current round, so showing it on every line is noise that also implies
    #: there is a round to choose.
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
        key = output_key_for(config_path, profile)
        if key is None:
            continue
        report.checked += 1
        if _is_empty(data, key):
            report.problems.append(f"{_label(key)} is empty -- {why}")

    # The whole document, not just the top level: for the nested shape, the
    # per-node metadata copied out of the config lives inside node_types.
    for path, value in find_placeholders({k: v for k, v in data.items() if k != "mlperf_sysinfo"}):
        report.problems.append(f"{path} still holds placeholder text: {value!r}")

    if profile.shape == "nested":
        node_types = data.get("node_types")
        if not node_types:
            report.problems.append("node_types is empty -- no hardware was collected")
    else:
        # Training writes every value as a string, so "0" is the empty case
        # here and a bare falsiness test would pass it.
        if str(data.get("number_of_nodes") or "0").strip() in ("", "0"):
            report.problems.append("number_of_nodes is zero -- no hardware was collected")
        if not data.get("host_processor_model_name"):
            report.warnings.append(
                "host_processor_model_name is empty -- CPU detection did not return a name"
            )

    for config_path, why in profile.recommends.items():
        key = output_key_for(config_path, profile)
        if key is None:
            continue
        if _is_empty(data, key):
            report.warnings.append(f"{_label(key)} is empty -- {why}")

    for where in _detection_failures(data):
        report.warnings.append(f"{where} was not detected -- fill it in before submitting")

    return report


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _label(key: str) -> str:
    """How a mapped key is named in a report.

    A node-scoped field is named with that scope: a submitter told "cooling is
    empty" would grep the top level of the file for it and find nothing.
    """
    if not key.startswith(NODE_SCOPE):
        return key
    return f"{key.removeprefix(NODE_SCOPE)} on every node type"


def _is_empty(data: dict, key: str) -> bool:
    """Whether a mapped output key has no answer.

    A node-scoped key counts as empty only when *every* node type leaves it
    blank: one node type legitimately differing from another is not a problem
    with the file.
    """
    if not key.startswith(NODE_SCOPE):
        return _blank(data.get(key))
    field = key.removeprefix(NODE_SCOPE)
    node_types = data.get("node_types") or []
    if not node_types:
        return True
    return all(_blank(node.get(field)) for node in node_types)


def _detection_failures(data: dict) -> list[str]:
    """Fields a probe explicitly could not answer.

    "N/A" and "Not detected: ..." are the collection script's way of saying it
    looked and found nothing, which is worth telling a submitter about --
    blanking them here would hide it, and a reviewer reading the file cannot
    tell the difference between "not detected" and "not applicable".
    """
    def undetected(value) -> bool:
        return isinstance(value, str) and (
            value.strip() in ("N/A", "Not available")
            or value.lower().startswith("not detected")
        )

    found: list[str] = []
    node_types = data.get("node_types")
    if not node_types:
        # A flat capture has no node_types -- its hardware is the top level.
        # The collection script blanks "N/A" there but not "Not detected: ...",
        # so those reach the file and would otherwise go unmentioned.
        return [
            name
            for name, value in data.items()
            if name != "mlperf_sysinfo" and undetected(value)
        ]
    for index, node in enumerate(node_types):
        if not isinstance(node, dict):
            continue
        for name, value in node.items():
            if undetected(value):
                found.append(f"node_types[{index}].{name}")
        # The accelerators are a level down, and interconnect detection is one
        # of the likeliest things to come back empty on a consumer card.
        for accel_index, accelerator in enumerate(node.get("accelerator_info") or []):
            if not isinstance(accelerator, dict):
                continue
            for name, value in accelerator.items():
                if undetected(value):
                    found.append(
                        f"node_types[{index}].accelerator_info[{accel_index}].{name}"
                    )
    return found


@dataclass
class Summary:
    """A readable view of a captured file."""

    path: Path
    profile: str
    #: As on ValidationReport: available, not displayed.
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


#: Model, dataset and submitter details are measurement point metadata now
#: (endpoints rules 8.3) and are not in this file to show.
_SUPPLIED_NESTED = [
    ("division", "division"),
    ("category", "system_category"),
    ("availability", "system_availability_status"),
    ("endpoint", "endpoint_url"),
    ("node config", "node_config"),
    ("config link", "link_config"),
]

_SUPPLIED_FLAT = [
    ("submitter", "submitter"),
    ("contact", "submitter_contact"),
    ("division", "division"),
    ("system type", "system_type"),
    ("status", "status"),
]

#: Training has no submitter_contact and no system_type, and carries two
#: fields the other two shapes do not. Reusing _SUPPLIED_FLAT would print a
#: row for each missing one and none for these.
_SUPPLIED_TRAINING = [
    ("submitter", "submitter"),
    ("division", "division"),
    ("status", "status"),
    ("framework", "framework"),
    ("net topology", "host_networking_topology"),
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

#: 8.2.1 nests a node type's accelerators, and a node type can hold more than
#: one model. Flat accelerator_* keys still appear at the top level of a flat
#: capture, and in a file written before the nesting landed.
_ACCELERATOR_FIELDS = ("accelerator_model_name", "accelerators_per_node")


def _accelerator_view(entry: dict) -> dict:
    """One dict to read accelerator_* out of, nested or flat."""
    nested = entry.get("accelerator_info")
    if isinstance(nested, list) and nested and isinstance(nested[0], dict):
        merged = dict(entry)
        for field in _ACCELERATOR_FIELDS:
            values = [
                str(a.get(field, "")) for a in nested if isinstance(a, dict) and a.get(field)
            ]
            merged[field] = " + ".join(dict.fromkeys(values))
        return merged
    return entry


def summarise(path: str | Path) -> Summary:
    path = Path(path)
    data = load_capture(path)
    stamp = data.get("mlperf_sysinfo") or {}
    shape_name = stamp.get("shape") or ("nested" if "node_types" in data else "flat")
    # Files written before the stamp carried a benchmark are read by their
    # fields: only training has host_networking_topology at the top level.
    benchmark = stamp.get("benchmark") or (
        "training" if "host_networking_topology" in data and "system_type" not in data else ""
    )

    node_types = data.get("node_types") or []
    accel_total = accelerator_count(node_types or [data])
    nodes: list[tuple[str, int, str]] = []
    for nt in node_types:
        view = _accelerator_view(nt)
        accel = view.get("accelerator_model_name") or "no accelerator detected"
        nodes.append(
            (
                accel,
                nt.get("number_of_nodes", 1),
                str(nt.get("host_processor_model_name") or ""),
            )
        )

    first = _accelerator_view(node_types[0]) if node_types else data
    detected = [
        (label, str(first.get(key)))
        for label, key in _DETECTED_FIELDS
        if first.get(key) not in (None, "", "N/A", "Not available")
    ]

    if benchmark == "training":
        table = _SUPPLIED_TRAINING
    elif shape_name == "nested":
        table = _SUPPLIED_NESTED
    else:
        table = _SUPPLIED_FLAT
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
