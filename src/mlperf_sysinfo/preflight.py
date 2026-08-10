# Copyright 2026 MLCommons contributors
# SPDX-License-Identifier: Apache-2.0
"""Pre-flight. Answers "will this work?" in seconds, before anything is collected.

Two kinds of problem, deliberately handled differently:

* config problems -- missing required fields, unresolved ``${VAR}``. Cheap to
  fix and they produce bad submissions. No override.
* reachability problems -- a node that will not answer. Also stops the run,
  but ``--allow-partial`` exists for when proceeding is a real decision.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .config import (
    SshTarget,
    SysinfoConfig,
    dotted_get,
    find_placeholders,
    is_filled,
    is_placeholder,
)
from .profiles import Profile

SSH_TIMEOUT = 15
HTTP_TIMEOUT = 5

_SSH_BASE_OPTS = [
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", f"ConnectTimeout={SSH_TIMEOUT}",
]

_ACCEL_QUERY = {
    "cuda": "nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | sort | uniq -c",
    "rocm": "rocm-smi --showproductname 2>/dev/null | head -20",
    "xpu": "xpu-smi discovery 2>/dev/null | head -20",
}


@dataclass
class NodeStatus:
    target: SshTarget
    reachable: bool
    detail: str = ""

    @property
    def label(self) -> str:
        return str(self.target)


@dataclass
class ProbeStatus:
    label: str
    ok: bool
    detail: str = ""
    checked: bool = True


@dataclass
class CheckReport:
    """Everything pre-flight learned. Rendered by the CLI, consumed by capture."""

    config: SysinfoConfig
    profile: Profile
    nodes: list[NodeStatus] = field(default_factory=list)
    endpoint: ProbeStatus | None = None
    serving_log: ProbeStatus | None = None
    missing_required: list[tuple[str, str]] = field(default_factory=list)
    placeholder_required: list[tuple[str, str]] = field(default_factory=list)
    missing_recommended: list[tuple[str, str]] = field(default_factory=list)
    placeholder_recommended: list[tuple[str, str]] = field(default_factory=list)
    placeholder_other: list[tuple[str, str]] = field(default_factory=list)
    unresolved_env: list[str] = field(default_factory=list)
    satisfied_count: int = 0
    network_checked: bool = True

    @property
    def config_problems(self) -> list[str]:
        out = [f"missing  {path}" for path, _ in self.missing_required]
        out += [f"placeholder  {path}" for path, _ in self.placeholder_required]
        out += [f"placeholder  {path}" for path, _ in self.placeholder_other]
        out += [f"unset environment variable at {ref}" for ref in self.unresolved_env]
        return out

    @property
    def unreachable(self) -> list[NodeStatus]:
        return [n for n in self.nodes if not n.reachable]

    @property
    def has_config_problems(self) -> bool:
        return bool(
            self.missing_required
            or self.placeholder_required
            or self.placeholder_other
            or self.unresolved_env
        )

    @property
    def has_reach_problems(self) -> bool:
        return bool(self.unreachable)

    @property
    def ok(self) -> bool:
        return not self.has_config_problems and not self.has_reach_problems

    @property
    def problem_count(self) -> int:
        return len(self.config_problems) + len(self.unreachable)

    @property
    def collectable_nodes(self) -> int:
        return sum(1 for n in self.nodes if n.reachable) + (
            1 if self.config.nodes.include_local else 0
        )


# ---------------------------------------------------------------------------
# individual probes
# ---------------------------------------------------------------------------


def _ssh_command(target: SshTarget, remote_cmd: str, timeout: int) -> subprocess.CompletedProcess:
    cmd = [
        "ssh",
        *_SSH_BASE_OPTS,
        "-p",
        str(target.port),
        f"{target.user}@{target.host}",
        remote_cmd,
    ]
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, check=False
    )


def check_node(target: SshTarget, accelerator: str) -> NodeStatus:
    """Reach one node and, best effort, name what accelerators it has."""
    if shutil.which("ssh") is None:
        return NodeStatus(target, False, "ssh client not found on this machine")
    probe = _ACCEL_QUERY.get(accelerator)
    remote = f"echo __up__; {probe}" if probe else "echo __up__"
    try:
        result = _ssh_command(target, remote, SSH_TIMEOUT + 10)
    except subprocess.TimeoutExpired:
        return NodeStatus(target, False, "timed out")
    except OSError as e:
        return NodeStatus(target, False, str(e))

    if "__up__" not in result.stdout:
        stderr = (result.stderr or "").strip().splitlines()
        reason = stderr[-1] if stderr else f"ssh exited {result.returncode}"
        return NodeStatus(target, False, reason)

    detail = _summarise_accelerators(result.stdout)
    return NodeStatus(target, True, detail)


def _summarise_accelerators(stdout: str) -> str:
    """Turn ``uniq -c`` output into something like 'NVIDIA H100 80GB x 8'."""
    parts: list[str] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line or line == "__up__":
            continue
        chunks = line.split(None, 1)
        if len(chunks) == 2 and chunks[0].isdigit():
            parts.append(f"{chunks[1].strip()} x {chunks[0]}")
        else:
            parts.append(line)
    return "; ".join(parts[:3])


def probe_endpoint(url: str) -> ProbeStatus:
    """Identify the serving framework behind a URL. Mirrors the automation's probe."""
    base = url.rstrip("/")

    def _get_json(suffix: str) -> dict | None:
        try:
            with urllib.request.urlopen(f"{base}{suffix}", timeout=HTTP_TIMEOUT) as r:
                data = json.loads(r.read())
                return data if isinstance(data, dict) else None
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            return None

    try:
        with urllib.request.urlopen(f"{base}/perf_metrics", timeout=HTTP_TIMEOUT) as r:
            r.read()
        version = _get_json("/version") or {}
        name = f"TRT-LLM {version.get('version', '')}".strip()
        return ProbeStatus(url, True, name)
    except (urllib.error.URLError, OSError, ValueError):
        pass

    version = _get_json("/version")
    if version and "version" in version:
        return ProbeStatus(url, True, f"vLLM {version['version']}")

    info = _get_json("/get_server_info")
    if info is not None:
        v = info.get("version") or info.get("server_version") or info.get("sglang_version") or ""
        return ProbeStatus(url, True, f"SGLang {v}".strip())

    return ProbeStatus(url, False, "no serving framework answered")


def check_serving_log(target: SshTarget, log_path: str) -> ProbeStatus:
    try:
        result = _ssh_command(
            target, f"test -f {log_path!r} && echo __found__", SSH_TIMEOUT + 5
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return ProbeStatus(log_path, False, f"could not check: {e}")
    if "__found__" in result.stdout:
        return ProbeStatus(log_path, True, f"on {target}")
    return ProbeStatus(
        log_path, False, f"not present on {target} -- serving output must be redirected there"
    )


# ---------------------------------------------------------------------------
# the whole check
# ---------------------------------------------------------------------------


def run_check(
    config: SysinfoConfig, profile: Profile, *, skip_network: bool = False
) -> CheckReport:
    """Validate the config against the profile, then reach everything it names."""
    report = CheckReport(config=config, profile=profile)
    report.unresolved_env = list(config.unresolved_env)

    for path, why in profile.requires.items():
        value = dotted_get(config, path)
        if is_placeholder(value):
            report.placeholder_required.append((path, why))
        elif is_filled(value):
            report.satisfied_count += 1
        else:
            report.missing_required.append((path, why))

    for path, why in profile.recommends.items():
        value = dotted_get(config, path)
        if is_placeholder(value):
            report.placeholder_recommended.append((path, why))
        elif not is_filled(value):
            report.missing_recommended.append((path, why))

    # Every remaining string, not just the ones a profile happens to name. A
    # placeholder in an unrequired field still reaches the submission file.
    already = {p for p, _ in report.placeholder_required + report.placeholder_recommended}
    for path, value in find_placeholders(config.model_dump(exclude={"source_path"})):
        if path not in already:
            report.placeholder_other.append((path, value))

    if config.nodes.groups:
        declared = sum(
            entry.count for entries in config.nodes.groups.values() for entry in entries
        )
        available = len(config.nodes.ssh) + (1 if config.nodes.include_local else 0)
        if declared > available:
            report.missing_required.append(
                (
                    "nodes.groups",
                    f"declares {declared} node(s) but only {available} are configured -- "
                    f"add SSH targets or lower the counts",
                )
            )

    if profile.collect.redfish and config.power.redfish is not None:
        rf = config.power.redfish
        if not rf.endpoint:
            report.missing_required.append(
                ("power.redfish.endpoint", "BMC address for power capture")
            )

    if skip_network:
        report.network_checked = False
        return report

    targets = config.nodes.targets
    if targets:
        with ThreadPoolExecutor(max_workers=min(8, len(targets))) as pool:
            report.nodes = list(
                pool.map(lambda t: check_node(t, config.system.accelerator), targets)
            )

    if profile.collect.endpoint_probe and config.serving.url:
        report.endpoint = probe_endpoint(config.serving.url)

    if profile.collect.serving_log and config.serving.node:
        report.serving_log = check_serving_log(
            SshTarget.parse(config.serving.node), config.serving.log
        )

    return report
