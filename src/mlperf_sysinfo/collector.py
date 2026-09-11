# Copyright 2026 MLCommons contributors
# SPDX-License-Identifier: Apache-2.0
"""Collection.

mlc-scripts collects, this module composes. The automation is asked for the
field set the profile's benchmark declares, and what it returns is treated as
the probed truth about the hardware. Which nodes to reach, what the config
says about them, how much of a partial answer is acceptable, and what gets
written where are all decided here.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import __version__, logs
from .config import SshTarget, SysinfoConfig
from .errors import CaptureError, CheckFailed, DependencyMissing
from .preflight import CheckReport, NodeStatus, run_check
from .profiles import Profile
from .profiles import load as load_profile
from .runlog import RunLog

log = logs.get(__name__)

#: Grouped intermediate written by the automation script before we shape it.
RAW_FILENAME = "raw-system-info.json"

#: Scratch directory inside the output directory. Everything the automation
#: writes lands here so the deliverable sits on its own.
WORK_DIRNAME = ".mlperf-sysinfo"

#: Automation output that is a deliverable in its own right, lifted back out
#: of the scratch directory on success.
_KEEP_FILES = ("redfish_nameplate_power.yaml", "redfish_capture.yaml")


Progress = Callable[[str, str, str], None]
"""Called as (symbol_key, label, detail) so the CLI owns all rendering."""


@dataclass
class CaptureResult:
    """What a capture produced, and how much of it is real."""

    output_path: Path
    profile: Profile
    report: CheckReport
    nodes: list[NodeStatus] = field(default_factory=list)
    complete: bool = True
    raw_path: Path | None = None
    extra_files: list[Path] = field(default_factory=list)
    #: The run log for this capture. Named after the start time, so a retry
    #: leaves the log of the failure that prompted it in place.
    log_path: Path | None = None
    duration: float = 0.0
    #: Nodes that actually returned hardware, counted from the collected data
    #: rather than from what we asked for. These are not the same thing.
    nodes_collected: int = 0
    nodes_expected: int = 0

    @property
    def accelerator_total(self) -> int:
        """Best-effort count for the summary line."""
        from .output import accelerator_count  # local: avoids an import cycle

        try:
            data = json.loads(self.output_path.read_text())
        except (OSError, ValueError):
            return 0
        # A flat capture has no node_types -- its hardware is the top level, so
        # the whole document is the one "node type" to count.
        return accelerator_count(data.get("node_types") or [data])


def _log_details(
    config: SysinfoConfig, profile: Profile, report: CheckReport, out_file: Path
) -> dict[str, str]:
    """The run log's header: enough to reconstruct what this run was asked for.

    The round belongs here even though it is never printed to the terminal. It
    is stamped into every output file for the same reason -- a log read weeks
    later has to say which rules produced it.
    """
    # report.nodes holds only the SSH targets -- the local machine is implicit,
    # and leaving it out here once read as "Nodes: 0" on a single-box capture.
    labels = ["this machine"] if config.nodes.include_local else []
    labels += [
        node.label if node.reachable else f"{node.label} (unreachable)"
        for node in report.nodes
    ]
    return {
        "Profile": (
            f"{profile.name} (round {profile.round}, benchmark {profile.benchmark})"
            if profile.round
            else f"{profile.name} (benchmark {profile.benchmark})"
        ),
        "Config": str(config.source_path or "(assembled in memory)"),
        "Output": str(out_file),
        "Nodes": f"{len(labels)} -- {', '.join(labels)}" if labels else "0",
    }


def output_filename(config: SysinfoConfig, profile: Profile) -> str:
    """The deliverable's name: the config's, or the profile's with the system
    name substituted in.

    A training submission stores its system description as
    ``<submitter>/systems/<system_name>.json``, so for that profile the
    filename carries meaning rather than being a convention. Path separators
    in a system name are flattened -- the name is a filename here, and one
    containing a slash would otherwise write outside the output directory.
    """
    name = config.output.file or profile.output_file
    if "{system_name}" not in name:
        return name
    safe = re.sub(r"[/\\\s]+", "_", config.system.name.strip()).strip("._") or "system_desc"
    return name.replace("{system_name}", safe)


def _require_mlc():
    try:
        import mlc  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise DependencyMissing(
            "mlc-scripts is not installed. Install it with: pip install mlc-scripts"
        ) from exc
    return mlc


def _write_node_config(config: SysinfoConfig) -> str:
    """Serialise nodes.groups into the YAML shape the automation expects."""
    assert config.nodes.groups is not None
    data = {
        "system_info": {
            "node_config": {
                func: [
                    {"node_name": entry.match, "no_of_nodes": entry.count}
                    for entry in entries
                ]
                for func, entries in config.nodes.groups.items()
            }
        }
    }
    fd, path = tempfile.mkstemp(suffix=".yaml", prefix="mlperf_sysinfo_nodes_")
    try:
        with os.fdopen(fd, "w") as fh:
            yaml.safe_dump(data, fh, default_flow_style=False)
    except Exception:
        os.unlink(path)
        raise
    return path


def build_mlc_kwargs(
    config: SysinfoConfig,
    profile: Profile,
    out_dir: Path,
    *,
    node_config_file: str | None = None,
    run_metadata_path: Path | None = None,
    targets: list[SshTarget] | None = None,
) -> dict[str, Any]:
    """Translate the config into one automation-script invocation.

    ``targets`` is which machines to actually ask for, defaulting to every one
    the config names. ``capture`` narrows it under ``--allow-partial``; see
    there for why an unreachable node has to be left out rather than sent and
    forgiven afterwards.

    The profile's ``benchmark`` variation selects which field set the
    automation assembles. It has to be sent: since mlc-scripts 1.2.0a2 the
    script no longer returns one shared intermediate that a caller can reshape
    -- ``_endpoints`` keeps only the fields in endpoints rules 8.2, and
    ``_inference`` keeps the ones the Inference submission checker wants.
    Sending nothing silently gets the endpoints field set, which is how a
    ``profile: inference`` capture ended up with no accelerator at all.

    What each field *says* is still decided here, from the config: the
    automation's own defaults are placeholder strings ("Insert ... here"), and
    those must never reach a deliverable.
    """
    if targets is None:
        targets = config.all_targets

    tags = ["get-mlperf-multi-node-system-info"]
    if config.system.accelerator != "none":
        tags.append(f"_{config.system.accelerator}")
    if not config.nodes.include_local:
        tags.append("_exclude_current_node")
    if profile.benchmark:
        tags.append(f"_{profile.benchmark}")

    use_redfish = profile.collect.redfish and config.power.redfish is not None
    if use_redfish:
        tags.append("_redfish")

    kwargs: dict[str, Any] = {
        "action": "run",
        "automation": "script",
        "tags": ",".join(tags),
        "ssh_ids": ",".join(str(t) for t in targets),
        "out_dir_path": str(out_dir.resolve()),
        "out_file_name": RAW_FILENAME,
        "skip_ssh_key_file": "yes" if config.nodes.ssh_key_preconfigured else "",
        "system_name": config.system.name,
        "quiet": True,
    }

    # The profiles that write endpoint_url are the ones that probe it, so one
    # flag covers both. A value that is prose rather than a URL is still sent:
    # it is the submission's answer, and the automation's probe gives up on it
    # quietly.
    if profile.collect.endpoint_probe and config.serving.url:
        kwargs["endpoint_url"] = config.serving.url
    # Fetching the serving config is its own ssh round trip, and since
    # mlc-scripts 1.2.0a5 a failed one ends the run. Asking for it from a node
    # we have already dropped as unreachable would turn a partial capture back
    # into a total failure, by the one route --allow-partial exists to avoid.
    serving_collectable = config.serving.node and (
        SshTarget.parse(config.serving.node) in targets
    )
    if profile.collect.serving_log and serving_collectable:
        kwargs["serving_node"] = config.serving.node
        kwargs["log_path"] = config.serving.log
        kwargs["serving_framework_type"] = config.serving.framework
    if node_config_file:
        kwargs["node_config_file"] = node_config_file

    # Sent only when asked for, never as a falsy value. The automation
    # forwards every non-empty string it is handed, so an unconditional
    # remote_isolated would put "False" in front of mlcflow's is_true() --
    # which rejects it today by accident of that function's word list, and is
    # not a thing to depend on. mlcflow picks the location itself: a
    # /tmp/mlcflow-isolated-<uid> it creates, with the virtualenv inside and
    # a trap that removes the lot.
    if config.remote.isolated:
        kwargs["remote_isolated"] = "yes"

    # Sent rather than overlaid afterwards: config_summary is a concatenation
    # of the parallelism degrees and these notes, and the automation is what
    # derives it. Overlaying the notes here would leave config_summary
    # disagreeing with its own config_summary_notes.
    if config.run.node_config:
        kwargs["node_config"] = config.run.node_config
    if config.run.config_summary_notes:
        kwargs["config_summary_notes"] = config.run.config_summary_notes
    if config.run.link_config:
        kwargs["link_config"] = config.run.link_config
    if run_metadata_path is not None:
        kwargs["run_metadata_path"] = str(Path(run_metadata_path).resolve())
    if use_redfish:
        rf = config.power.redfish
        kwargs["redfish_endpoint"] = rf.endpoint
        if rf.username:
            kwargs["redfish_username"] = rf.username
        if rf.password:
            kwargs["redfish_password"] = rf.password

    return kwargs


def capture(
    config: SysinfoConfig,
    profile: Profile | None = None,
    *,
    allow_partial: bool = False,
    run_metadata_path: Path | None = None,
    progress: Progress | None = None,
    report: CheckReport | None = None,
    verbose: bool = False,
) -> CaptureResult:
    """Check, collect, shape, write.

    The check is not optional. ``allow_partial`` only forgives an unreachable
    node -- a config problem always stops the run.
    """
    started = time.monotonic()

    if profile is None:
        relative_to = config.source_path.parent if config.source_path else None
        profile = load_profile(config.profile, relative_to=relative_to)

    def emit(kind: str, label: str, detail: str = "") -> None:
        if progress is not None:
            progress(kind, label, detail)

    if report is None:
        report = run_check(config, profile)

    if report.has_config_problems:
        raise CheckFailed(
            "pre-flight check failed: the config is not ready to capture", report
        )
    if report.has_reach_problems and not allow_partial:
        names = ", ".join(n.label for n in report.unreachable)
        raise CheckFailed(
            f"pre-flight check failed: cannot reach {names}. "
            f"Fix it, or pass --allow-partial to capture without those nodes.",
            report,
        )

    partial = report.has_reach_problems

    # An unreachable node is dropped from the request, not sent and forgiven
    # afterwards. Since mlc-scripts 1.2.0a5 the automation treats a node it
    # cannot reach as a failed run and returns an error for the whole
    # collection -- deliberately, because a system description quietly missing
    # a machine is a wrong answer rather than a small one. That is the right
    # default and it is not what --allow-partial promises, so the flag has to
    # mean "do not ask about that node" instead of "ignore what it said".
    unreachable = {node.target for node in report.unreachable}
    targets = [t for t in config.all_targets if t not in unreachable]
    if not targets and not config.nodes.include_local:
        names = ", ".join(n.label for n in report.unreachable)
        raise CheckFailed(
            f"every node is unreachable ({names}), and nodes.include_local is "
            "false -- there is nothing left to collect from. --allow-partial "
            "forgives some of the nodes, not all of them.",
            report,
        )
    log.info(
        "pre-flight check %s -- %d node(s), profile %s",
        "passed" if not partial else "passed with warnings",
        report.collectable_nodes,
        profile.name,
    )
    emit(
        "ok",
        "pre-flight check",
        f"{'passed' if not partial else 'passed with warnings'} -- "
        f"{report.collectable_nodes} node(s), profile {profile.name}",
    )

    out_dir = config.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # The automation's own scratch -- raw intermediate, per-node files, and any
    # relative paths it writes -- is kept out of the directory the submitter
    # will copy from.
    work_dir = out_dir / WORK_DIRNAME
    work_dir.mkdir(parents=True, exist_ok=True)

    out_file = out_dir / output_filename(config, profile)
    with RunLog.open(
        work_dir, command="capture", details=_log_details(config, profile, report, out_file)
    ) as runlog:
        # A leftover intermediate from an earlier run must never be mistaken for
        # this run's result: if the automation reports success but writes nothing,
        # we have to notice rather than shape stale hardware into a fresh file.
        stale = work_dir / RAW_FILENAME
        if stale.exists():
            stale.unlink()

        node_config_file = _write_node_config(config) if config.nodes.groups else None
        mlc_kwargs = build_mlc_kwargs(
            config,
            profile,
            work_dir,
            node_config_file=node_config_file,
            run_metadata_path=run_metadata_path,
            targets=targets,
        )

        mlc = _require_mlc()
        log_path = runlog.path
        previous_cwd = os.getcwd()
        log.info("collecting with tags: %s", mlc_kwargs.get("tags", ""))
        log.debug("automation working directory: %s", work_dir)
        try:
            os.chdir(work_dir)
            with runlog.capturing(echo=verbose):
                result = mlc.access(mlc_kwargs)
        except Exception as e:  # the automation layer raises a variety of types
            raise CaptureError(
                f"collection failed: {type(e).__name__}: {e}\n  See {log_path}"
            ) from e
        finally:
            os.chdir(previous_cwd)
            if node_config_file and os.path.exists(node_config_file):
                os.unlink(node_config_file)

        if result.get("return", 1) != 0:
            raise CaptureError(
                f"collection failed: {result.get('error', 'the automation script reported an error')}"
            )

        for node in report.nodes:
            if not node.reachable:
                log.warning("%s skipped -- %s", node.label, node.detail)
                emit("bad", node.label, f"skipped -- {node.detail}")

        raw_path = _locate_raw(result, work_dir)
        try:
            collected = json.loads(raw_path.read_text())
        except (OSError, ValueError) as e:
            raise CaptureError(f"could not read collected data from {raw_path}: {e}") from e

        # What came back, not what we asked for. A node can be perfectly reachable
        # and still return nothing -- an unsupported OS, a probe that needs sudo.
        # Believing the request over the result is how a capture silently ships
        # half a system.
        nodes_expected = len(config.all_targets) + (1 if config.nodes.include_local else 0)
        nodes_collected = count_collected_nodes(collected)

        if nodes_collected < nodes_expected:
            log.warning(
                "only %d of %d node(s) returned hardware", nodes_collected, nodes_expected
            )
        else:
            log.info("%d of %d node(s) returned hardware", nodes_collected, nodes_expected)

        if nodes_collected == 0:
            raise CaptureError(
                "collection returned no hardware at all. "
                f"{nodes_expected} node(s) were asked; none reported back. "
                f"See the automation log at {log_path}."
            )
        if nodes_collected < nodes_expected:
            if not allow_partial:
                raise CaptureError(
                    f"only {nodes_collected} of {nodes_expected} node(s) returned hardware. "
                    f"See {log_path} for which probe failed, or pass "
                    "--allow-partial to write what was collected."
                )
            partial = True

        if nodes_collected == nodes_expected:
            emit("ok", "collection", f"{nodes_collected} of {nodes_expected} node(s) returned hardware")
        else:
            emit(
                "warn",
                "collection",
                f"only {nodes_collected} of {nodes_expected} node(s) returned hardware",
            )
        if report.serving_log and report.serving_log.ok:
            emit("ok", str(config.serving.node), "serving config parsed")
        if report.endpoint and report.endpoint.ok:
            emit("ok", str(config.serving.url), f"framework detected -- {report.endpoint.detail}")

        from .output import shape  # local import keeps module import order simple

        final = shape(
            collected,
            config,
            profile,
            nodes_expected=nodes_expected,
            nodes_collected=nodes_collected,
            partial=partial,
            package_version=__version__,
        )

        out_file.write_text(json.dumps(final, indent=2) + "\n")
        log.info("wrote %s", out_file)

        extra: list[Path] = []
        for name in _KEEP_FILES:
            produced = work_dir / name
            if produced.exists():
                destination = out_dir / name
                produced.replace(destination)
                extra.append(destination)
                log.info("wrote %s", destination)

        runlog.outcome = (
            f"complete -- {nodes_collected} of {nodes_expected} node(s)"
            if not partial
            else f"partial -- only {nodes_collected} of {nodes_expected} node(s) answered"
        )

        return CaptureResult(
            output_path=out_file,
            profile=profile,
            report=report,
            nodes=report.nodes,
            complete=not partial,
            raw_path=raw_path,
            extra_files=extra,
            log_path=runlog.path,
            duration=time.monotonic() - started,
            nodes_collected=nodes_collected,
            nodes_expected=nodes_expected,
        )


def count_collected_nodes(collected: dict) -> int:
    """How many machines actually reported hardware.

    The endpoints field set groups nodes under ``node_types``; the flat
    inference one has already summed them into ``number_of_nodes``. Reading
    only the grouped form would score every flat capture as zero nodes and
    abort it as having collected nothing.
    """
    node_types = collected.get("node_types")
    if node_types is None:
        try:
            return int(collected.get("number_of_nodes", 0))
        except (TypeError, ValueError):
            return 0
    total = 0
    for entry in node_types:
        try:
            total += int(entry.get("number_of_nodes", 1))
        except (TypeError, ValueError):
            total += 1
    return total


def _locate_raw(result: dict, out_dir: Path) -> Path:
    """Find the intermediate the automation wrote."""
    from_env = (result.get("new_env") or {}).get("MLC_MULTI_NODE_SYSTEM_INFO_FILE_PATH")
    if from_env and Path(from_env).exists():
        return Path(from_env)
    fallback = out_dir / RAW_FILENAME
    if fallback.exists():
        return fallback
    raise CaptureError(
        "collection reported success but wrote no data -- "
        f"expected {fallback}. See the automation log alongside it."
    )
