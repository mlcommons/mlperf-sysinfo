# Copyright 2026 MLCommons contributors
# SPDX-License-Identifier: Apache-2.0
"""Collection.

mlc-scripts collects; this module composes. The automation scripts are called
exactly as they are today and asked for their grouped intermediate; every
decision about what the final file contains is made here, driven by the
profile.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import __version__
from .config import SysinfoConfig
from .errors import CaptureError, CheckFailed, DependencyMissing
from .preflight import CheckReport, NodeStatus, run_check
from .profiles import Profile
from .profiles import load as load_profile

#: Grouped intermediate written by the automation script before we shape it.
RAW_FILENAME = "raw-system-info.json"

#: Scratch directory inside the output directory. Everything the automation
#: writes lands here so the deliverable sits on its own.
WORK_DIRNAME = ".mlperf-sysinfo"

#: Automation output that is a deliverable in its own right, lifted back out
#: of the scratch directory on success.
_KEEP_FILES = ("redfish_nameplate_power.yaml", "redfish_capture.yaml")


@contextmanager
def _captured_output(log_path: Path, *, enabled: bool):
    """Send the automation's chatter to a log file instead of the terminal.

    It writes from subprocesses as well as Python, so this redirects the real
    file descriptors rather than ``sys.stdout``.
    """
    if not enabled:
        yield
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w") as log:
        saved_out, saved_err = os.dup(1), os.dup(2)
        try:
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(log.fileno(), 1)
            os.dup2(log.fileno(), 2)
            yield
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(saved_out, 1)
            os.dup2(saved_err, 2)
            os.close(saved_out)
            os.close(saved_err)

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
    duration: float = 0.0
    #: Nodes that actually returned hardware, counted from the collected data
    #: rather than from what we asked for. These are not the same thing.
    nodes_collected: int = 0
    nodes_expected: int = 0

    @property
    def accelerator_total(self) -> int:
        """Best-effort count for the summary line."""
        total = 0
        try:
            data = json.loads(self.output_path.read_text())
        except (OSError, ValueError):
            return 0
        for nt in data.get("node_types", []) or []:
            per_node = nt.get("accelerators_per_node")
            try:
                total += int(nt.get("number_of_nodes", 1)) * int(per_node)
            except (TypeError, ValueError):
                continue
        return total


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
) -> dict[str, Any]:
    """Translate the config into one automation-script invocation.

    No benchmark variation is passed on purpose: we always want the grouped
    intermediate and do the shaping ourselves.
    """
    tags = ["get-mlperf-multi-node-system-info"]
    if config.system.accelerator != "none":
        tags.append(f"_{config.system.accelerator}")
    if not config.nodes.include_local:
        tags.append("_exclude_current_node")

    use_redfish = profile.collect.redfish and config.power.redfish is not None
    if use_redfish:
        tags.append("_redfish")

    kwargs: dict[str, Any] = {
        "action": "run",
        "automation": "script",
        "tags": ",".join(tags),
        "ssh_ids": ",".join(str(t) for t in config.nodes.targets),
        "out_dir_path": str(out_dir.resolve()),
        "out_file_name": RAW_FILENAME,
        "skip_ssh_key_file": "yes" if config.nodes.ssh_key_preconfigured else "",
        "system_name": config.system.name,
        "quiet": True,
    }

    if profile.collect.endpoint_probe and config.serving.url:
        kwargs["endpoint_url"] = config.serving.url
    if profile.collect.serving_log and config.serving.node:
        kwargs["serving_node"] = config.serving.node
        kwargs["log_path"] = config.serving.log
        kwargs["serving_framework_type"] = config.serving.framework
    if node_config_file:
        kwargs["node_config_file"] = node_config_file
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
    emit(
        "ok",
        "pre-flight check",
        f"{'passed' if not partial else 'passed with warnings'} -- "
        f"{report.collectable_nodes} node(s), profile {profile.name} (v{profile.round} rules)",
    )

    out_dir = config.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # The automation's own scratch -- raw intermediate, per-node files, and any
    # relative paths it writes -- is kept out of the directory the submitter
    # will copy from.
    work_dir = out_dir / WORK_DIRNAME
    work_dir.mkdir(parents=True, exist_ok=True)

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
    )

    mlc = _require_mlc()
    log_path = work_dir / "automation.log"
    previous_cwd = os.getcwd()
    try:
        os.chdir(work_dir)
        with _captured_output(log_path, enabled=not verbose):
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
    nodes_expected = len(config.nodes.ssh) + (1 if config.nodes.include_local else 0)
    nodes_collected = count_collected_nodes(collected)

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

    output_path = out_dir / (config.output.file or profile.output_file)
    output_path.write_text(json.dumps(final, indent=2) + "\n")

    extra: list[Path] = []
    for name in _KEEP_FILES:
        produced = work_dir / name
        if produced.exists():
            destination = out_dir / name
            produced.replace(destination)
            extra.append(destination)

    return CaptureResult(
        output_path=output_path,
        profile=profile,
        report=report,
        nodes=report.nodes,
        complete=not partial,
        raw_path=raw_path,
        extra_files=extra,
        duration=time.monotonic() - started,
        nodes_collected=nodes_collected,
        nodes_expected=nodes_expected,
    )


def count_collected_nodes(collected: dict) -> int:
    """How many machines actually reported hardware."""
    node_types = collected.get("node_types") or []
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
