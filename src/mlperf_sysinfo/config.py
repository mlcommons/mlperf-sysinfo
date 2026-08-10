# Copyright 2026 MLCommons contributors
# SPDX-License-Identifier: Apache-2.0
"""The config file: schema, loading, ``extends`` merging, ``${VAR}`` interpolation.

Sections are grouped by who owns the answer -- the system under test, the
machines to look at, the stack being served, optional power, and the
submission paperwork -- rather than by which environment variable they set.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .errors import ConfigError

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_SSH_RE = re.compile(r"^(?P<user>[^@\s]+)@(?P<host>[^:\s]+)(?::(?P<port>\d+))?$")
_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

def interpolate_env(value: Any, *, path: str = "", missing: list[str] | None = None) -> Any:
    """Recursively replace ``${VAR}`` with the environment's value.

    An unset variable is recorded in ``missing`` and left as the literal
    ``${VAR}`` so the checker can report it against a real config path.
    """
    if missing is None:
        missing = []
    if isinstance(value, dict):
        return {
            k: interpolate_env(v, path=f"{path}.{k}" if path else str(k), missing=missing)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [
            interpolate_env(v, path=f"{path}[{i}]", missing=missing)
            for i, v in enumerate(value)
        ]
    if isinstance(value, str):

        def _sub(m: re.Match[str]) -> str:
            name = m.group(1)
            env_value = os.environ.get(name)
            if env_value is None:
                missing.append(f"{path} -> ${{{name}}}")
                return m.group(0)
            return env_value

        return _ENV_RE.sub(_sub, value)
    return value


def deep_merge(base: dict, override: dict) -> dict:
    """Merge ``override`` onto ``base``. Child wins; nested dicts merge, lists replace."""
    out = dict(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def dotted_get(data: Any, path: str) -> Any:
    """Look up ``a.b.c`` in nested models/dicts. Returns None if any hop is absent."""
    cur = data
    for part in path.split("."):
        if isinstance(cur, BaseModel):
            cur = getattr(cur, part, None)
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


#: Text that means "nobody has filled this in yet". A starter config is full of
#: CHANGEME; the pre-mlperf-sysinfo pipeline wrote "Insert ... here" into real
#: submission files whenever a field was left unset.
#:
#: The "insert" rule is anchored rather than a bare substring so that genuine
#: free text -- "insert card in slot 3" in a hardware note -- is not mistaken
#: for an unfilled field.
PLACEHOLDER_TOKENS = ("changeme",)
_PLACEHOLDER_RE = re.compile(r"^(insert\b.*\bhere|<.*>)$", re.IGNORECASE)


def is_placeholder(value: Any) -> bool:
    """True when a value is present but is still starter text."""
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    lowered = stripped.lower()
    if any(token in lowered for token in PLACEHOLDER_TOKENS):
        return True
    return bool(_PLACEHOLDER_RE.match(stripped))


def find_placeholders(data: Any, *, path: str = "") -> list[tuple[str, str]]:
    """Walk any nested structure and return every (path, value) still unfilled.

    Used on the whole config -- a placeholder in a field no profile happens to
    require still ends up in a submission file.
    """
    found: list[tuple[str, str]] = []
    if isinstance(data, dict):
        for key, value in data.items():
            found += find_placeholders(value, path=f"{path}.{key}" if path else str(key))
    elif isinstance(data, list):
        for i, value in enumerate(data):
            found += find_placeholders(value, path=f"{path}[{i}]")
    elif is_placeholder(data):
        found.append((path or "(root)", data.strip()))
    return found


def is_filled(value: Any) -> bool:
    """True when a required field actually carries an answer.

    Starter text does not count. A config that still says CHANGEME has not
    been filled in, and saying so here is the whole point of the checker.
    """
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != "" and not is_placeholder(value)
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return True


# ---------------------------------------------------------------------------
# ssh targets
# ---------------------------------------------------------------------------


class SshTarget(BaseModel):
    """A parsed ``user@host`` or ``user@host:port``."""

    model_config = ConfigDict(frozen=True)

    user: str
    host: str
    port: int = 22

    @classmethod
    def parse(cls, raw: str) -> SshTarget:
        m = _SSH_RE.match(raw.strip())
        if not m:
            raise ValueError(
                f"invalid SSH target {raw!r}: expected 'user@host' or 'user@host:port'"
            )
        port = int(m.group("port")) if m.group("port") else 22
        if not 1 <= port <= 65535:
            raise ValueError(f"invalid port in {raw!r}: {port} is not in 1-65535")
        return cls(user=m.group("user"), host=m.group("host"), port=port)

    def __str__(self) -> str:
        return f"{self.user}@{self.host}:{self.port}"


def _validate_ssh_list(values: list[str]) -> list[str]:
    for entry in values:
        SshTarget.parse(entry)  # raises ValueError with a useful message
    return values


# ---------------------------------------------------------------------------
# sections
# ---------------------------------------------------------------------------


class OutputConfig(BaseModel):
    """Where the result goes."""

    model_config = ConfigDict(extra="forbid")

    dir: Path = Field(default=Path("."), description="Directory for all written output.")
    file: str | None = Field(
        default=None,
        description="Output filename. Defaults to the profile's own filename.",
    )


class SystemConfig(BaseModel):
    """What is being described."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Identifier for the system under test, e.g. 'H100x8_vLLM'.")
    category: str | None = Field(default=None, description="e.g. datacenter, edge.")
    availability: str | None = Field(
        default=None, description="e.g. available, preview, rdi."
    )
    accelerator: Literal["cuda", "rocm", "xpu", "none"] = "none"
    cooling: str | None = None
    type_detail: str | None = None
    size: str | None = Field(
        default=None, description="Override the computed system size. Rarely needed."
    )

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("system.name must not be empty")
        return v.strip()


class NodeGroupEntry(BaseModel):
    """One node type inside a function group."""

    model_config = ConfigDict(extra="forbid")

    match: str = Field(
        description="Matched case-insensitively against the detected accelerator model name."
    )
    count: int = Field(default=1, ge=1, description="How many nodes of this type.")


class NodesConfig(BaseModel):
    """Where to look."""

    model_config = ConfigDict(extra="forbid")

    include_local: bool = Field(
        default=False,
        description=(
            "Whether the machine running this command is part of the system. "
            "Defaults to false: an orchestrator should not describe itself by accident."
        ),
    )
    ssh: list[str] = Field(default_factory=list)
    ssh_key_preconfigured: bool = Field(
        default=False,
        description="Key auth is already set up; skip the mlcflow key-file lookup.",
    )
    groups: dict[str, list[NodeGroupEntry]] | None = Field(
        default=None,
        description="Function-based groupings for disaggregated setups, e.g. prefill/decode.",
    )

    _validate_ssh = field_validator("ssh")(_validate_ssh_list)

    @property
    def targets(self) -> list[SshTarget]:
        return [SshTarget.parse(s) for s in self.ssh]


class ServingConfig(BaseModel):
    """The stack under test."""

    model_config = ConfigDict(extra="forbid")

    url: str | None = Field(default=None, description="Probed for framework name and version.")
    node: str | None = Field(default=None, description="Where the server process runs.")
    log: str = Field(
        default="/tmp/serving.log", description="Startup log parsed for parallelism settings."
    )
    framework: Literal["auto", "vllm", "sglang", "trtllm"] = "auto"

    @field_validator("url")
    @classmethod
    def _url_scheme(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith(("http://", "https://")):
            raise ValueError(f"serving.url must start with http:// or https://, got {v!r}")
        return v

    @field_validator("node")
    @classmethod
    def _node_target(cls, v: str | None) -> str | None:
        if v is not None:
            SshTarget.parse(v)
        return v


class RedfishConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str
    username: str | None = None
    password: str | None = None


class PowerConfig(BaseModel):
    """Optional, opt-in BMC capture."""

    model_config = ConfigDict(extra="forbid")

    redfish: RedfishConfig | None = None


class ModelInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    name: str | None = None
    precision: str | None = None
    link: str | None = None
    transformation_link: str | None = None
    notes: str | None = None


class DatasetInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    name: str | None = None
    type: str | None = None
    link: str | None = None
    input_token_average: str | int | None = None
    output_token_average: str | int | None = None


class NotesInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hardware: str | None = None
    software: str | None = None
    other_hardware: str | None = None


class SubmissionConfig(BaseModel):
    """The paperwork."""

    model_config = ConfigDict(extra="forbid")

    submitter: str | None = None
    contact: str | None = None
    division: str | None = None
    model: ModelInfo = Field(default_factory=ModelInfo)
    dataset: DatasetInfo = Field(default_factory=DatasetInfo)
    notes: NotesInfo = Field(default_factory=NotesInfo)
    container_link: str | None = None
    measured_accuracy_score: str | float | None = None


# ---------------------------------------------------------------------------
# top level
# ---------------------------------------------------------------------------


class SysinfoConfig(BaseModel):
    """A whole config file.

    Extra top-level keys are ignored on purpose so the same document can be a
    benchmark config that happens to carry a ``system_info:`` section.
    """

    model_config = ConfigDict(extra="ignore")

    profile: str = Field(default="endpoints", description="Profile name, or a path to one.")
    output: OutputConfig = Field(default_factory=OutputConfig)
    system: SystemConfig
    nodes: NodesConfig = Field(default_factory=NodesConfig)
    serving: ServingConfig = Field(default_factory=ServingConfig)
    power: PowerConfig = Field(default_factory=PowerConfig)
    submission: SubmissionConfig = Field(default_factory=SubmissionConfig)

    #: Populated by the loader, not by the file.
    source_path: Path | None = Field(default=None, exclude=True)
    unresolved_env: list[str] = Field(default_factory=list, exclude=True)

    def model_post_init(self, _context: Any) -> None:
        if not self.nodes.include_local and not self.nodes.ssh:
            raise ValueError(
                "nothing to collect from: set nodes.include_local to true, "
                "or list at least one target under nodes.ssh"
            )

    @property
    def output_dir(self) -> Path:
        base = self.output.dir
        if not base.is_absolute() and self.source_path is not None:
            return (self.source_path.parent / base).resolve()
        return base.resolve()


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

#: A config may nest everything under one of these keys. Lets a benchmark
#: config carry a sysinfo section without a second file.
EMBED_KEYS = ("system_info", "sysinfo")

_MAX_EXTENDS_DEPTH = 8


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: invalid YAML: {e}") from e
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: expected a YAML mapping, got {type(raw).__name__}")
    return raw


def _unwrap(data: dict) -> dict:
    """Pull the sysinfo section out of a host config, if it is nested in one."""
    for key in EMBED_KEYS:
        section = data.get(key)
        if isinstance(section, dict):
            merged = dict(section)
            # A nested section may still rely on the host document's report_dir.
            if "output" not in merged:
                for host_key in ("report_dir", "output_dir"):
                    if data.get(host_key):
                        merged["output"] = {"dir": data[host_key]}
                        break
            return merged
    return data


def _resolve_extends(data: dict, base_dir: Path, depth: int = 0) -> dict:
    parent_ref = data.pop("extends", None)
    if not parent_ref:
        return data
    if depth >= _MAX_EXTENDS_DEPTH:
        raise ConfigError(
            f"'extends' nested more than {_MAX_EXTENDS_DEPTH} deep -- is there a cycle?"
        )
    parent_path = Path(str(parent_ref)).expanduser()
    if not parent_path.is_absolute():
        parent_path = (base_dir / parent_path).resolve()
    parent = _unwrap(_read_yaml(parent_path))
    parent = _resolve_extends(parent, parent_path.parent, depth + 1)
    return deep_merge(parent, data)


def load_config(path: str | Path) -> SysinfoConfig:
    """Read a config file, apply ``extends``, interpolate ``${VAR}``, validate."""
    path = Path(path).expanduser().resolve()
    data = _unwrap(_read_yaml(path))
    data = _resolve_extends(data, path.parent)

    missing: list[str] = []
    data = interpolate_env(data, missing=missing)

    try:
        config = SysinfoConfig.model_validate(data)
    except ValidationError as e:
        raise ConfigError(_format_validation_error(path, e)) from e
    except ValueError as e:
        raise ConfigError(f"{path}: {e}") from e

    config.source_path = path
    config.unresolved_env = missing
    return config


def _format_validation_error(path: Path, error: ValidationError) -> str:
    """Turn pydantic's output into something a person can act on."""
    lines = [f"{path}: config is not valid"]
    for err in error.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        msg = err["msg"]
        if err["type"] == "extra_forbidden":
            msg = "unknown option -- check the spelling, or see 'mlperf-sysinfo init'"
        lines.append(f"  {loc}: {msg}")
    return "\n".join(lines)
