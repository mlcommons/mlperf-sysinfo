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

from . import logs
from .config import SysinfoConfig
from .errors import CaptureError
from .profiles import Profile

log = logs.get(__name__)

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
# the training field set (mlperf_logging/system_desc_checker)
# ---------------------------------------------------------------------------
#
# Training is validated by a different checker from Inference --
# mlperf_logging/system_desc_checker in mlcommons/logging, not the Inference
# submission_checker -- so this field set is derived from that checker's
# required_fields list rather than by analogy to the flat inference one. The
# two drift independently; verify a change against the checker, not against
# _FLAT_KEYS.

#: The four values ``status`` may hold. The checker rejects anything else for
#: ruleset major version 4 and above (``availability_options`` in
#: system_desc_checker.py), so a wrong one fails at submission time rather
#: than here -- which is why check refuses it up front instead.
TRAINING_STATUS_OPTIONS = (
    "Available on-premise",
    "Available cloud",
    "Research, Development, or Internal (RDI)",
    "Preview",
)

#: Lower-cased shorthands accepted on top of the canonical spellings, so a
#: submitter need not copy the exact punctuation of "Research, Development, or
#: Internal (RDI)".
#:
#: Bare "available" is deliberately absent. It is what MLPerf Inference uses,
#: but training splits availability into on-premise and cloud, and guessing
#: which one a submitter meant would silently mislabel the submission.
_TRAINING_STATUS_ALIASES = {
    "on-premise": "Available on-premise",
    "on-prem": "Available on-premise",
    "onprem": "Available on-premise",
    "on premise": "Available on-premise",
    "available on-prem": "Available on-premise",
    "available onprem": "Available on-premise",
    "cloud": "Available cloud",
    "rdi": "Research, Development, or Internal (RDI)",
    "research, development, or internal": "Research, Development, or Internal (RDI)",
    "internal": "Research, Development, or Internal (RDI)",
    "preview": "Preview",
}


def normalize_training_status(value: Any) -> tuple[str, str | None]:
    """Map ``system.availability`` onto one of ``TRAINING_STATUS_OPTIONS``.

    Returns ``(value, None)`` when it maps, or ``("", reason)`` when it does
    not. An empty input maps to an empty string with no complaint: that is a
    missing required field, which the profile's ``requires`` already reports,
    and saying it twice in different words helps nobody.
    """
    raw = "" if value is None else str(value).strip()
    if not raw:
        return "", None

    lowered = raw.lower()
    for option in TRAINING_STATUS_OPTIONS:
        if lowered == option.lower():
            return option, None
    if lowered in _TRAINING_STATUS_ALIASES:
        return _TRAINING_STATUS_ALIASES[lowered], None

    hint = ""
    if lowered in ("available", "avail"):
        hint = (
            " MLPerf Training splits availability into on-premise and cloud, "
            "so 'available' on its own is ambiguous -- pick one."
        )
    return "", (
        f"{raw!r} is not a valid MLPerf Training availability. It must be one "
        f"of: {', '.join(TRAINING_STATUS_OPTIONS)}.{hint}"
    )


#: Every field the training checker requires, in the order it lists them, plus
#: ``framework_name`` where submissions that carry it put it. The checker only
#: tests for presence, but keeping the published order makes a generated file
#: diffable against the ones already in the training_results repos.
TRAINING_FIELDS = (
    "submitter",
    "division",
    "status",
    "system_name",
    "number_of_nodes",
    "host_processors_per_node",
    "host_processor_model_name",
    "host_processor_core_count",
    "host_processor_vcpu_count",
    "host_processor_frequency",
    "host_processor_caches",
    "host_processor_interconnect",
    "host_memory_capacity",
    "host_storage_type",
    "host_storage_capacity",
    "host_networking",
    "host_networking_topology",
    "host_memory_configuration",
    "accelerators_per_node",
    "accelerator_model_name",
    "accelerator_host_interconnect",
    "accelerator_frequency",
    "accelerator_on-chip_memories",
    "accelerator_memory_configuration",
    "accelerator_memory_capacity",
    "accelerator_interconnect",
    "accelerator_interconnect_topology",
    "cooling",
    "hw_notes",
    "framework",
    "framework_name",
    "other_software_stack",
    "operating_system",
    "sw_notes",
)

#: Written only when the config sets it. The checker does not ask for it and
#: existing submissions disagree about whether to carry it, so an empty one is
#: noise rather than a blank to fill in.
_TRAINING_OPTIONAL_FIELDS = frozenset({"framework_name"})


def _training_value(value: Any) -> str:
    """Coerce one field to the string form training submissions use.

    Every value in a training system description is a string, counts included
    ("8", not 8). A detection-failure marker becomes an empty string, which is
    how existing submissions express "not disclosed" -- carrying "Not
    detected: ..." into a submission field would read as a real answer.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        stripped = value.strip()
        return "" if is_not_detected(stripped) else stripped
    return str(value)


def _training_overlay(config: SysinfoConfig) -> dict:
    """Values the config owns, not a probe.

    ``status`` is normalized here as well as refused by ``check``: a caller
    using the library directly never runs the check, and a file that fails the
    training checker on its first field is not worth writing.
    """
    sub = config.submission
    status, _ = normalize_training_status(config.system.availability)
    return {
        "submitter": _s(sub.submitter),
        "division": _s(sub.division).lower(),
        "status": status,
        "system_name": config.system.name,
        "host_networking_topology": _s(config.system.networking_topology),
        "cooling": _s(config.system.cooling),
        "hw_notes": _s(sub.notes.hardware),
        "sw_notes": _s(sub.notes.software),
        "framework": _s(config.training.framework),
        "framework_name": _s(config.training.framework_name),
    }


def build_training(collected: dict, config: SysinfoConfig) -> dict:
    """The training system description: the checker's fields, in its order.

    Unlike ``build_flat`` this does not pass the automation's document through
    untouched. The training field set is closed -- it is exactly what the
    checker lists -- so an inference-shaped extra like ``submitter_contact``
    or ``system_type`` reaching the file would be a field no training reviewer
    has a column for.
    """
    if "node_types" in collected:
        raise CaptureError(
            "the collection layer returned the nested field set, not the flat "
            "training one. mlc-scripts was asked for '_training' and did not "
            "supply it -- the installed release is too old. Every hardware "
            "field would have been written empty, so nothing was written."
        )

    overlay = _training_overlay(config)
    if not overlay["framework"]:
        # Not fatal here -- the profile requires it, so check has already
        # refused this config unless a library caller skipped the check.
        log.warning(
            "training.framework is not set. MLPerf Training expects the "
            "framework and version there (e.g. \"NVIDIA PyTorch Release "
            "25.04\"); writing it empty."
        )
    out: dict[str, Any] = {}
    for field in TRAINING_FIELDS:
        value = overlay[field] if field in overlay else _training_value(collected.get(field))
        if not value and field in _TRAINING_OPTIONAL_FIELDS:
            continue
        out[field] = value
    return out


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


#: Training is flat like inference but not the same field set: it has no
#: system_type, no submitter_contact and no system_type_detail, and it adds
#: two fields of its own. Sharing _FLAT_KEYS would make ``validate`` look for
#: fields that are not in the file and miss the ones that are.
_TRAINING_KEYS = {
    "system.name": "system_name",
    "system.availability": "status",
    "system.cooling": "cooling",
    "system.networking_topology": "host_networking_topology",
    "submission.submitter": "submitter",
    "submission.division": "division",
    "submission.notes.hardware": "hw_notes",
    "submission.notes.software": "sw_notes",
    "training.framework": "framework",
    "training.framework_name": "framework_name",
}


def output_key_for(config_path: str, profile: Profile) -> str | None:
    """Which output field a required config path lands in, if any.

    Keyed off the profile's ``benchmark`` rather than its ``shape``: training
    and inference are both flat documents with different field sets, so shape
    alone no longer identifies one.
    """
    if profile.benchmark == "training":
        table = _TRAINING_KEYS
    elif profile.shape == "nested":
        table = _NESTED_KEYS
    else:
        table = _FLAT_KEYS
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
    block: dict[str, Any] = {"version": package_version, "profile": profile.name}
    # Omitted rather than written empty when a profile does not name a round.
    # "profile_round": "" would read as a round that failed to record, which is
    # a different thing from a profile that deliberately does not track one.
    if profile.round:
        block["profile_round"] = profile.round
    block.update(
        {
            "shape": profile.shape,
            # Which field set this file holds. shape alone stopped being enough
            # to say once training and inference were both flat documents, and
            # a reader with only the file has to be able to tell them apart.
            "benchmark": profile.benchmark,
            "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "nodes_expected": nodes_expected,
            "nodes_collected": nodes_collected,
            "complete": not partial,
        }
    )
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


def _blank_paths(result: dict) -> set[str]:
    """Dotted paths that came out blank, for whatever reason.

    Deliberately does not claim *why*. A blank field is either one the config
    never supplied or one the automation could not determine, and this walker
    cannot tell them apart -- naming it after either would mislead in half the
    cases.

    Only the shaped document is walked, and only one level into ``node_types``
    and ``accelerator_info`` -- those are where probed values live, and the
    intent is a short list for the log rather than a full report. ``validate``
    is what produces the reviewable version.
    """
    found = set()

    def scan(source: dict, prefix: str) -> None:
        for name, value in source.items():
            if isinstance(value, dict):
                scan(value, f"{prefix}{name}.")
            elif not isinstance(value, list) and is_not_detected(value):
                found.add(f"{prefix}{name}")

    scan({k: v for k, v in result.items() if k != "node_types"}, "")
    for node in result.get("node_types") or []:
        if isinstance(node, dict):
            scan({k: v for k, v in node.items() if k != "accelerator_info"}, NODE_SCOPE)
            accelerator = node.get("accelerator_info")
            if isinstance(accelerator, dict):
                scan(accelerator, f"{NODE_SCOPE}accelerator_info.")
    return found


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
    if profile.benchmark == "training":
        result = build_training(collected, config)
    elif profile.shape == "nested":
        result = build_endpoints(collected, config)
    else:
        result = build_flat(collected, config, profile)
    log.debug(
        "shaped %d field(s) for profile %s (%s)", len(result), profile.name, profile.shape
    )
    # Recorded so "was this field always blank?" has an answer without a
    # re-run. validate is what turns this into a reviewable report.
    blank = sorted(_blank_paths(result))
    if blank:
        log.info(
            "%d field(s) blank in the written file (unset in config, or not detected): %s",
            len(blank),
            ", ".join(blank),
        )
    result["mlperf_sysinfo"] = provenance(
        profile=profile,
        collected=collected,
        nodes_expected=nodes_expected,
        nodes_collected=nodes_collected,
        partial=partial,
        package_version=package_version,
    )
    return result
