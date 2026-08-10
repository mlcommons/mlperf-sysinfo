# Copyright 2026 MLCommons contributors
# SPDX-License-Identifier: Apache-2.0
"""Output shaping.

Collection returns facts. This module decides what the file looks like, which
is the part that differs per working group -- so it lives here, driven by the
profile, rather than as a branch inside a shared automation script.

Every metadata field is written from the config, never from a default string.
A field nobody supplied comes out empty, because the checker refuses to run
without the ones that matter.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .config import SysinfoConfig
from .profiles import Profile

#: Node-type bookkeeping, not hardware to lift into a flat file.
_NODE_METADATA_FIELDS = {
    "system_node_ensemble_id",
    "number_of_nodes",
    "system_node_name",
}

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
        if val.strip().lstrip("-").isdigit():
            return True
    return False


def compute_system_size(node_entries: list[dict]) -> str:
    """Per the MLPerf Per Submission Data Dictionary: '8x NVIDIA H100 + 2x ...'."""
    parts: list[str] = []
    for entry in node_entries:
        n_nodes = entry.get("number_of_nodes", 1)
        accel_name = entry.get("accelerator_model_name", "")
        accel_per_node = entry.get("accelerators_per_node", 0)

        if not is_not_detected(accel_name) and not is_not_detected(accel_per_node):
            try:
                qty = n_nodes * int(accel_per_node)
            except (ValueError, TypeError):
                qty = n_nodes
            parts.append(f"{qty}x {accel_name}")
            continue

        cpu_name = entry.get("host_processor_model_name", "")
        cpu_per_node = entry.get("host_processors_per_node", 1)
        if not is_not_detected(cpu_name):
            try:
                qty = n_nodes * int(cpu_per_node)
            except (ValueError, TypeError):
                qty = n_nodes
            parts.append(f"{qty}x {cpu_name}")
    return " + ".join(parts)


def _is_homogeneous(node_types: list[dict]) -> bool:
    if len(node_types) <= 1:
        return True
    ref_accel = node_types[0].get("accelerator_model_name", "")
    ref_cpu = node_types[0].get("host_processor_model_name", "")
    return all(
        nt.get("accelerator_model_name", "") == ref_accel
        and nt.get("host_processor_model_name", "") == ref_cpu
        for nt in node_types[1:]
    )


def _merge_heterogeneous_nodes(node_types: list[dict]) -> dict:
    """Comma-separate the distinct values of each field across node types."""
    all_fields = list(
        dict.fromkeys(k for nt in node_types for k in nt if k not in _NODE_METADATA_FIELDS)
    )
    merged: dict[str, str] = {}
    for name in all_fields:
        seen: list[str] = []
        for nt in node_types:
            raw = nt.get(name, "")
            val = "" if raw is None else str(raw)
            if val and val not in seen:
                seen.append(val)
        merged[name] = ", ".join(seen)
    return merged


# ---------------------------------------------------------------------------
# shapes
# ---------------------------------------------------------------------------


def build_nested(collected: dict, config: SysinfoConfig) -> dict:
    """Grouped output: keeps ``node_types`` so multi-node structure survives."""
    node_types: list[dict] = collected.get("node_types", []) or []
    sub = config.submission

    node_meta = {
        "other_hardware": _s(sub.notes.other_hardware),
        "hw_notes": _s(sub.notes.hardware),
        "cooling": _s(config.system.cooling),
        "container_link": _s(sub.container_link),
    }
    for node_type in node_types:
        node_type.update(node_meta)
        node_type.pop("serving_framework", None)
        node_type.pop("system_node_name", None)

    system_size = config.system.size or collected.get("system_size") or compute_system_size(
        node_types
    )

    return {
        "submitter_org_names": _s(sub.submitter),
        "submitter_contact": _s(sub.contact),
        "submission_id": "",
        "submission_date": "",
        "publish_date": "",
        "system_name": config.system.name,
        "system_category": _s(config.system.category),
        "system_availability_status": _s(config.system.availability),
        "system_size": system_size,
        "system_node_ensemble_count": len(node_types),
        "system_node_ensemble_total": sum(e.get("number_of_nodes", 1) for e in node_types),
        "serving_framework": _s(collected.get("serving_framework")),
        "node_types": node_types,
        "division": _s(sub.division),
        "model_id": _s(sub.model.id),
        "model_name": _s(sub.model.name),
        "model_precision": _s(sub.model.precision),
        "link_to_model": _s(sub.model.link),
        "link_to_model_transformation": _s(sub.model.transformation_link),
        "model_notes": _s(sub.model.notes),
        "dataset_id": _s(sub.dataset.id),
        "dataset_name": _s(sub.dataset.name),
        "dataset_type": _s(sub.dataset.type),
        "dataset_link": _s(sub.dataset.link),
        "input_token_average": _s(sub.dataset.input_token_average),
        "output_token_average": _s(sub.dataset.output_token_average),
        "measured_accuracy_score": _s(sub.measured_accuracy_score),
    }


def build_flat(nested: dict, config: SysinfoConfig, profile: Profile) -> dict:
    """Flat output matching the MLPerf Inference submission checker."""
    node_types: list[dict] = nested.get("node_types", []) or []
    total_nodes = nested.get(
        "system_node_ensemble_total", sum(nt.get("number_of_nodes", 1) for nt in node_types)
    )

    if not node_types:
        hw: dict = {}
    elif _is_homogeneous(node_types):
        hw = {k: v for k, v in node_types[0].items() if k not in _NODE_METADATA_FIELDS}
    else:
        hw = _merge_heterogeneous_nodes(node_types)

    def _hw(key: str) -> Any:
        v = hw.get(key)
        return "" if v is None or v in _NOT_DETECTED else v

    flat = {
        "submitter": nested.get("submitter_org_names", ""),
        "submitter_contact": nested.get("submitter_contact", ""),
        "system_name": nested.get("system_name", ""),
        "status": nested.get("system_availability_status", ""),
        "system_type": nested.get("system_category", ""),
        "division": nested.get("division", ""),
        "system_size": nested.get("system_size", ""),
        "number_of_nodes": total_nodes,
        "host_processor_model_name": _hw("host_processor_model_name"),
        "host_processors_per_node": _hw("host_processors_per_node"),
        "host_processor_core_count": _hw("host_processor_core_count"),
        "host_processor_vcpu_count": _hw("host_processor_vcpu_count"),
        "host_processor_frequency": _hw("host_processor_frequency"),
        "host_processor_caches": _hw("host_processor_caches"),
        "host_processor_interconnect": _hw("host_processor_interconnect"),
        "host_memory_capacity": _hw("host_memory_capacity"),
        "host_storage_type": _hw("host_storage_type"),
        "host_storage_capacity": _hw("host_storage_capacity"),
        "host_memory_configuration": _hw("host_memory_configuration"),
        "host_networking": _hw("host_networking"),
        "host_networking_topology": "",
        "host_network_card_count": _hw("host_network_card_count"),
        "accelerator_model_name": _hw("accelerator_model_name"),
        "accelerators_per_node": _hw("accelerators_per_node"),
        "accelerator_memory_capacity": _hw("accelerator_memory_capacity"),
        "accelerator_memory_configuration": _hw("accelerator_memory_configuration"),
        "accelerator_host_interconnect": _hw("accelerator_host_interconnect"),
        "accelerator_interconnect": _hw("accelerator_interconnect"),
        "accelerator_interconnect_topology": _hw("accelerator_interconnect_topology"),
        "accelerator_frequency": _hw("accelerator_frequency"),
        "accelerator_on-chip_memories": _hw("accelerator_on-chip_memories"),
        "framework": nested.get("serving_framework", ""),
        "operating_system": _hw("operating_system"),
        "other_software_stack": _hw("other_software_stack"),
        "hw_notes": _s(config.submission.notes.hardware),
        "sw_notes": _s(config.submission.notes.software),
        "other_hardware": _s(config.submission.notes.other_hardware),
        "cooling": _s(config.system.cooling),
        "system_type_detail": _s(config.system.type_detail),
    }

    if "network" in profile.extra_field_groups:
        flat.update({f: "" for f in _NETWORK_EXTRA_FIELDS if f not in flat})
    if "power" in profile.extra_field_groups:
        flat.update({f: "" for f in _POWER_EXTRA_FIELDS if f not in flat})

    return flat


# ---------------------------------------------------------------------------
# config path -> output key, so a captured file can be validated on its own
# ---------------------------------------------------------------------------

_NESTED_KEYS = {
    "system.name": "system_name",
    "system.category": "system_category",
    "system.availability": "system_availability_status",
    "submission.submitter": "submitter_org_names",
    "submission.contact": "submitter_contact",
    "submission.division": "division",
    "submission.model.id": "model_id",
    "submission.model.name": "model_name",
    "submission.model.precision": "model_precision",
    "submission.model.link": "link_to_model",
    "submission.dataset.id": "dataset_id",
    "submission.dataset.name": "dataset_name",
    "submission.dataset.type": "dataset_type",
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
    mlc_version = collected.get("mlc_scripts_version")
    if mlc_version:
        block["mlc_scripts"] = mlc_version
    return block


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
    nested = build_nested(collected, config)
    result = nested if profile.shape == "nested" else build_flat(nested, config, profile)
    result["mlperf_sysinfo"] = provenance(
        profile=profile,
        collected=collected,
        nodes_expected=nodes_expected,
        nodes_collected=nodes_collected,
        partial=partial,
        package_version=package_version,
    )
    return result
