# Copyright 2026 MLCommons contributors
# SPDX-License-Identifier: Apache-2.0
"""Output shaping.

The automation returns the probed hardware already in the field set for the
benchmark it was asked for. This module owns the rest: it puts the fields in
the order the published template uses, drops anything outside that field set,
and writes every config-supplied value over whatever the automation defaulted.

That last part is the reason this step exists at all. The automation's
defaults for submitter-supplied fields are placeholder strings ("Insert
system category here"), and a placeholder in a submission file is worse than
an empty one -- it looks filled in. Every metadata field here is written from
the config or left genuinely empty, and the checker is what refuses to run
when one that matters is empty.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .config import SysinfoConfig
from .profiles import Profile

#: Mirrors SYSTEM_DESC_REQUIRED_FIELDS_NETWORK_MODE in the submission checker.
_NETWORK_EXTRA_FIELDS = [
    "is_network",
    "network_type",
    "network_media",
    "network_rate",
    "nic_loadgen",
    "number_nic_loadgen",
    "net_software_stack_loadgen",
    "network_protocol",
    "number_connections",
    "nic_sut",
    "number_nic_sut",
    "net_software_stack_sut",
    "network_topology",
]

#: Mirrors SYSTEM_DESC_REQUIRED_FIELDS_POWER in the submission checker.
_POWER_EXTRA_FIELDS = [
    "power_management",
    "filesystem",
    "boot_firmware_version",
    "management_firmware_version",
    "number_of_type_nics_installed",
    "nics_enabled_firmware",
    "nics_enabled_os",
    "nics_enabled_connected",
    "network_speed_mbit",
    "power_supply_quantity_and_rating_watts",
    "power_supply_details",
    "disk_drives",
    "disk_controllers",
    "system_power_only",
]

_NOT_DETECTED = {"N/A", "Not available"}

#: Provenance the automation stamps into its own output. It answers the same
#: question as the ``mlperf_sysinfo.mlc_scripts`` block, so it is stripped
#: rather than passed through into a submission field set that has no room for
#: it.
_COLLECTION_STAMP = "mlc_scripts_version"


def _s(value: Any) -> str:
    """Config value to output string. None becomes empty, never a placeholder."""
    return "" if value is None else str(value)


def is_not_detected(val: Any) -> bool:
    """True when a probed value is a detection failure rather than an answer."""
    if val is None or val == "" or val == 0:
        return True
    if isinstance(val, str):
        if val.lower().startswith("not detected"):
            return True
        if val in _NOT_DETECTED:
            return True
    return False


def _as_int(value: Any, default: int = 1) -> int:
    """Counts arrive as ints today and strings tomorrow. Never multiply a str."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# the endpoints field set (endpoints rules 8.2, template 8.2.1)
# ---------------------------------------------------------------------------

#: Every field in the 8.2.1 template, in template order, so the deliverable
#: reads the same way as the published skeleton. These lists are the whole
#: definition of the endpoints file: a field not named here is not written, and
#: a field named here is always present.
#:
#: Deliberately absent, and why:
#:   * model and dataset metadata, and max_supported_concurrency -- measurement
#:     point metadata now (rules 8.3), written per point, not per system.
#:   * submitter_org_names, submitter_contact, submission_id, submission_date,
#:     publish_date, measured_accuracy_score, system_type_detail -- dropped from
#:     the field table.
#:   * shortened_system_name -- in the 8.2 table but not in the 8.2.1 template,
#:     which the template says was checked against the table field for field.
#:     Following the template until that is resolved upstream.
ENDPOINTS_ACCELERATOR_FIELDS = (
    "accelerator_model_name",
    "accelerators_per_node",
    "accelerator_memory_capacity",
    "accelerator_memory_type",
    "accelerator_interconnect",
    "accelerator_host_interconnect",
)

ENDPOINTS_NODE_FIELDS = (
    "system_node_ensemble_id",
    "number_of_nodes",
    "host_processor_model_name",
    "host_processors_per_node",
    "host_processor_core_count",
    "host_processor_vcpu_count",
    "host_memory_capacity",
    "host_memory_configuration",
    "accelerator_info",
    "host_network_card_count",
    "host_networking",
    "host_storage_capacity",
    "host_storage_type",
    "other_hardware",
    "cooling",
    "hw_notes",
    "inference_backend",
    "driver",
    "operating_system",
    "filesystem",
    "container_link",
    "other_software_stack",
    "sw_notes",
)

ENDPOINTS_TOP_FIELDS = (
    "division",
    "system_name",
    "system_availability_status",
    "system_category",
    "system_size",
    "system_node_ensemble_count",
    "system_node_ensemble_total",
    "endpoint_url",
    "serving_framework",
    "node_types",
    "node_config",
    "disaggregated",
    "expert_parallel",
    "tensor_parallel",
    "pipeline_parallel",
    "data_parallel",
    "batch",
    "config_summary",
    "config_summary_notes",
    "link_config",
)

#: Fields the template shows as numbers. An absent one is 0, not "".
_ENDPOINTS_NUMERIC_FIELDS = frozenset(
    {
        "system_node_ensemble_count",
        "system_node_ensemble_total",
        "system_node_ensemble_id",
        "number_of_nodes",
        "host_processors_per_node",
        "host_processor_core_count",
        "host_processor_vcpu_count",
        "accelerators_per_node",
        "disaggregated",
        "expert_parallel",
        "tensor_parallel",
        "pipeline_parallel",
        "data_parallel",
        "batch",
    }
)


def _blank_for(field: str) -> Any:
    return 0 if field in _ENDPOINTS_NUMERIC_FIELDS else ""


def _ordered(source: dict, fields: tuple[str, ...]) -> dict:
    """Pick ``fields`` out of ``source``, in that order, filling in blanks."""
    out: dict[str, Any] = {}
    for field in fields:
        value = source.get(field, _blank_for(field))
        out[field] = _blank_for(field) if value is None else value
    return out


def _node_type_accelerators(node: dict) -> int:
    """Accelerators in one node type, across every model it hosts."""
    nodes = _as_int(node.get("number_of_nodes", 1))
    accelerators = node.get("accelerator_info")
    if not isinstance(accelerators, list):
        per_node = node.get("accelerators_per_node")
        return 0 if is_not_detected(per_node) else nodes * _as_int(per_node, 0)
    total = 0
    for accelerator in accelerators:
        if not isinstance(accelerator, dict):
            continue
        per_node = accelerator.get("accelerators_per_node")
        if not is_not_detected(per_node):
            total += nodes * _as_int(per_node, 0)
    return total


def _count_is_undetected(node: dict) -> bool:
    """Whether a node type has an accelerator whose count came back unusable.

    Reporting that as ``0 accelerators`` would be indistinguishable from a
    genuinely CPU-only node type, in a required field -- the same mistake as
    blanking a detection failure, one level up.
    """
    accelerators = node.get("accelerator_info")
    if not isinstance(accelerators, list):
        return False
    return any(
        isinstance(a, dict)
        and not is_not_detected(a.get("accelerator_model_name"))
        and is_not_detected(a.get("accelerators_per_node"))
        for a in accelerators
    )


def endpoints_system_size(node_types: list[dict]) -> str:
    """``system_size`` as endpoints rules 8.2 defines it.

    "Number of accelerators per node type, e.g. '72 accelerators + 144
    accelerators'". This is *not* the MLPerf Inference convention, which names
    the model as well ("8x NVIDIA H100"); the collection script computes that
    one, so the endpoints value is derived here instead.

    A node type with no accelerator reports zero rather than falling back to
    host processors -- the field counts accelerators and nothing else. One
    whose accelerator was found but whose count was not reports "not detected",
    because zero there would read as a CPU-only node type.
    """
    parts = []
    for node in node_types or []:
        if _count_is_undetected(node):
            parts.append("not detected")
        else:
            parts.append(f"{_node_type_accelerators(node)} accelerators")
    return " + ".join(parts)


def accelerator_count(node_types: list[dict]) -> int:
    """Total accelerators across every node type.

    Reads the nested ``accelerator_info`` list, and falls back to the flat
    ``accelerators_per_node`` a pre-8.2.1 file carries at node level.
    """
    return sum(_node_type_accelerators(node) for node in node_types or [])


# ---------------------------------------------------------------------------
# shapes
# ---------------------------------------------------------------------------
#
# Both shapes overlay onto what the automation returned rather than rebuilding
# it. The automation owns the probed hardware and the field set for the
# benchmark it was asked for (see collector.build_mlc_kwargs); this module owns
# every value that comes from the config, because the automation's defaults for
# those are placeholder strings that must never reach a deliverable.


def build_endpoints(collected: dict, config: SysinfoConfig) -> dict:
    """The endpoints system description: rules 8.2 fields, 8.2.1 order."""
    sub = config.submission

    # Copied onto every node type: 8.2.1 puts these inside node_types rather
    # than at the top level. Every one is a submitter statement, so no probe
    # can supply them and an empty config value is the right answer.
    node_meta = {
        "other_hardware": _s(sub.notes.other_hardware),
        "hw_notes": _s(sub.notes.hardware),
        "sw_notes": _s(sub.notes.software),
        "cooling": _s(config.system.cooling),
        "container_link": _s(sub.container_link),
    }

    node_types: list[dict] = []
    for raw_node in collected.get("node_types") or []:
        node = _ordered(raw_node, ENDPOINTS_NODE_FIELDS)
        node.update(node_meta)
        node["accelerator_info"] = [
            _ordered(accelerator, ENDPOINTS_ACCELERATOR_FIELDS)
            for accelerator in (raw_node.get("accelerator_info") or [])
            if isinstance(accelerator, dict)
        ]
        node_types.append(node)

    out = _ordered(collected, ENDPOINTS_TOP_FIELDS)
    out["node_types"] = node_types
    out.update(
        {
            "division": _s(sub.division),
            "system_name": config.system.name,
            "system_availability_status": _s(config.system.availability),
            "system_category": _s(config.system.category),
            "system_size": config.system.size or endpoints_system_size(node_types),
        }
    )
    return out


#: Values in the flat file that come from the config, not from a probe. The
#: automation fills these from its own environment, which this tool does not
#: populate, so they arrive as placeholders or empty.
def _flat_overlay(config: SysinfoConfig) -> dict:
    sub = config.submission
    overlay = {
        "submitter": _s(sub.submitter),
        "submitter_contact": _s(sub.contact),
        "system_name": config.system.name,
        "status": _s(config.system.availability),
        "system_type": _s(config.system.category),
        "division": _s(sub.division),
        "hw_notes": _s(sub.notes.hardware),
        "sw_notes": _s(sub.notes.software),
        "other_hardware": _s(sub.notes.other_hardware),
        "cooling": _s(config.system.cooling),
        "system_type_detail": _s(config.system.type_detail),
    }
    if config.system.size:
        overlay["system_size"] = config.system.size
    return overlay


def build_flat(collected: dict, config: SysinfoConfig, profile: Profile) -> dict:
    """Flat output matching the MLPerf Inference submission checker."""
    flat = {k: v for k, v in collected.items() if k != _COLLECTION_STAMP}
    for key, value in _flat_overlay(config).items():
        if key in flat or value != "":
            flat[key] = value

    if "network" in profile.extra_field_groups:
        flat.update({f: "" for f in _NETWORK_EXTRA_FIELDS if f not in flat})
    if "power" in profile.extra_field_groups:
        flat.update({f: "" for f in _POWER_EXTRA_FIELDS if f not in flat})

    return flat


# ---------------------------------------------------------------------------
# config path -> output key, so a captured file can be validated on its own
# ---------------------------------------------------------------------------

#: Prefix marking a field that lives inside every ``node_types`` entry rather
#: than at the top level. 8.2.1 moved the notes and cooling in there, and
#: without this ``validate`` would silently skip the very fields ``check``
#: warns about -- the two commands would disagree about the same file.
NODE_SCOPE = "node_types[]."

#: Model and dataset paths are gone from both sides: they are measurement point
#: metadata now (rules 8.3), so there is nothing in a system description to
#: check them against.
_NESTED_KEYS = {
    "system.name": "system_name",
    "system.category": "system_category",
    "system.availability": "system_availability_status",
    "submission.division": "division",
    "serving.url": "endpoint_url",
    "system.cooling": f"{NODE_SCOPE}cooling",
    "submission.notes.hardware": f"{NODE_SCOPE}hw_notes",
    "submission.notes.software": f"{NODE_SCOPE}sw_notes",
    "submission.container_link": f"{NODE_SCOPE}container_link",
    "run.node_config": "node_config",
    "run.config_summary_notes": "config_summary_notes",
    "run.link_config": "link_config",
}

_FLAT_KEYS = {
    "system.name": "system_name",
    "system.category": "system_type",
    "system.availability": "status",
    "system.type_detail": "system_type_detail",
    "submission.submitter": "submitter",
    "submission.contact": "submitter_contact",
    "submission.division": "division",
    "submission.notes.hardware": "hw_notes",
    "submission.notes.software": "sw_notes",
}


def output_key_for(config_path: str, shape_name: str) -> str | None:
    """Which output field a required config path lands in, if any."""
    table = _NESTED_KEYS if shape_name == "nested" else _FLAT_KEYS
    return table.get(config_path)


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------


def provenance(
    *,
    profile: Profile,
    collected: dict,
    nodes_expected: int,
    nodes_collected: int,
    partial: bool,
    package_version: str,
) -> dict:
    """The block that makes an output file self-describing."""
    block: dict[str, Any] = {
        "version": package_version,
        "profile": profile.name,
        "profile_round": profile.round,
        "shape": profile.shape,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "nodes_expected": nodes_expected,
        "nodes_collected": nodes_collected,
        "complete": not partial,
    }
    if partial:
        block["warning"] = (
            "PARTIAL CAPTURE -- one or more nodes did not answer. "
            "This file does not describe the whole system."
        )
    block["mlc_scripts"] = _collection_provenance(collected)
    return block


def _collection_provenance(collected: dict) -> dict:
    """Record which collection code produced this file.

    A git checkout stamps its own commit into the intermediate. Since
    mlc-scripts 1.2.0a1 the automations run from the installed package, where
    there is no repo to read, so fall back to the release version -- which
    answers the same question and is more precise for a pip install.
    """
    from_repo = collected.get("mlc_scripts_version")
    if from_repo:
        return from_repo
    try:
        from importlib.metadata import PackageNotFoundError, version

        return {"package_version": version("mlc-scripts")}
    except (ImportError, PackageNotFoundError):  # pragma: no cover
        return {}


def shape(
    collected: dict,
    config: SysinfoConfig,
    profile: Profile,
    *,
    nodes_expected: int,
    nodes_collected: int,
    partial: bool,
    package_version: str,
) -> dict:
    """Apply the profile's shape and stamp provenance."""
    if profile.shape == "nested":
        result = build_endpoints(collected, config)
    else:
        result = build_flat(collected, config, profile)
    result["mlperf_sysinfo"] = provenance(
        profile=profile,
        collected=collected,
        nodes_expected=nodes_expected,
        nodes_collected=nodes_collected,
        partial=partial,
        package_version=package_version,
    )
    return result
