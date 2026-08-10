# Copyright 2026 MLCommons contributors
# SPDX-License-Identifier: Apache-2.0
"""The command line.

Five commands: init, check, capture, show, validate. Check is not optional --
capture always runs it first and there is no flag to skip it.
"""

from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path
from typing import Annotated

import cyclopts

from . import __version__, ui
from .collector import capture as run_capture
from .config import load_config
from .errors import CheckFailed, SysinfoError
from .preflight import CheckReport, run_check
from .profiles import available as available_profiles
from .profiles import load as load_profile
from .report import summarise, validate

_TEMPLATE_DIR = Path(__file__).parent / "templates"

EXIT_OK = 0
EXIT_PROBLEMS = 1
EXIT_ERROR = 2

app = cyclopts.App(
    name="mlperf-sysinfo",
    version=__version__,
    help="Capture MLPerf system descriptions. One config, one CLI, a profile per working group.",
)

ConfigOpt = Annotated[
    Path, cyclopts.Parameter(name=["--config", "-c"], help="Path to the config file.")
]


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s  %(message)s",
    )


def _load(config_path: Path):
    """Load config and its profile together -- they are always needed as a pair."""
    config = load_config(config_path)
    profile = load_profile(config.profile, relative_to=Path(config_path).resolve().parent)
    return config, profile


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def _render_check(report: CheckReport, *, out_file: Path) -> None:
    config, profile = report.config, report.profile

    ui.blank()
    ui.kv("profile", f"{ui.bold(profile.name)} {ui.dim(f'(v{profile.round} rules)')}")
    ui.kv("output", str(out_file))

    labels = [n.label for n in report.nodes] + config.nodes.ssh + ["this machine"]
    if report.endpoint:
        labels.append(report.endpoint.label)
    if report.serving_log:
        labels.append(report.serving_log.label)
    width = max((len(x) for x in labels), default=12) + 2

    ui.heading("nodes")
    if not report.network_checked:
        for raw in config.nodes.ssh:
            ui.row(ui.SKIP, raw, ui.dim("not checked"), "--offline was passed", width)
    for node in report.nodes:
        if node.reachable:
            ui.row(ui.OK, node.label, ui.green("reachable"), node.detail, width)
        else:
            ui.row(ui.BAD, node.label, ui.red("unreachable"), node.detail, width)
    if config.nodes.include_local:
        ui.row(ui.OK, "this machine", ui.green("included"), "", width)
    else:
        ui.row(ui.SKIP, "this machine", ui.dim("excluded"), "nodes.include_local is false", width)

    if report.endpoint or report.serving_log:
        ui.heading("serving")
        if report.endpoint:
            e = report.endpoint
            ui.row(
                ui.OK if e.ok else ui.WARN,
                e.label,
                ui.green("reachable") if e.ok else ui.yellow("no answer"),
                e.detail,
                width,
            )
        if report.serving_log:
            s = report.serving_log
            ui.row(
                ui.OK if s.ok else ui.WARN,
                s.label,
                ui.green("present") if s.ok else ui.yellow("missing"),
                s.detail,
                width,
            )

    # The field block has its own column width -- dotted paths are much longer
    # than host names and would otherwise ragged-edge against them.
    field_labels = [
        p
        for p, _ in (
            report.missing_required
            + report.placeholder_required
            + report.missing_recommended
            + report.placeholder_recommended
        )
    ] + report.unresolved_env
    fwidth = max((len(x) for x in field_labels), default=20) + 2

    ui.heading(f"required by profile '{profile.name}'")
    if report.satisfied_count:
        ui.row(ui.OK, f"{report.satisfied_count} fields set", "", "", fwidth)
    for path, why in report.missing_required:
        ui.row(ui.BAD, path, ui.red("missing"), why, fwidth)
    for path, why in report.placeholder_required:
        ui.row(ui.BAD, path, ui.red("placeholder"), f"still the starter value -- {why}", fwidth)
    for ref in report.unresolved_env:
        ui.row(ui.BAD, ref, ui.red("unset"), "not found in the environment", fwidth)

    if report.missing_recommended or report.placeholder_recommended:
        ui.heading("worth filling in")
        for path, why in report.missing_recommended:
            ui.row(ui.WARN, path, ui.yellow("empty"), why, fwidth)
        for path, why in report.placeholder_recommended:
            ui.row(
                ui.WARN, path, ui.yellow("placeholder"), f"still the starter value -- {why}", fwidth
            )

    ui.blank()
    if report.ok:
        if report.network_checked:
            print(f"  {ui.green('Ready to capture.')}")
        else:
            print(f"  {ui.green('Config is valid.')}")
            ui.hint("Nothing was reached -- run without --offline before capturing.")
        return

    count = report.problem_count
    noun = "problem" if count == 1 else "problems"
    print(f"  {ui.red(f'{count} {noun}.')}")
    if report.has_config_problems:
        ui.hint("Fill in the missing fields and run check again.")
    if report.has_reach_problems:
        ui.hint(
            "Fix the unreachable nodes, or run capture --allow-partial "
            "to proceed without them."
        )


_SYMBOLS = {"ok": ui.OK, "bad": ui.BAD, "warn": ui.WARN, "skip": ui.SKIP}


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


@app.command
def init(
    profile: str = "endpoints",
    *,
    output: Annotated[
        Path, cyclopts.Parameter(name=["--output", "-o"], help="Where to write the config.")
    ] = Path("sysinfo.yaml"),
    force: Annotated[
        bool, cyclopts.Parameter(help="Overwrite the file if it already exists.")
    ] = False,
) -> int:
    """Write a starter config containing only the fields your profile needs."""
    template = _TEMPLATE_DIR / f"{profile}.yaml"
    if not template.exists():
        ui.error(
            f"no starter config for profile {profile!r}. "
            f"Built-in profiles: {', '.join(available_profiles())}"
        )
        return EXIT_ERROR
    if output.exists() and not force:
        ui.error(f"{output} already exists. Pass --force to overwrite it.")
        return EXIT_ERROR

    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template, output)

    loaded = load_profile(profile)
    ui.blank()
    print(f"  {ui.green('Written')} {output}")
    ui.blank()
    ui.hint(f"profile {loaded.name} -- {loaded.title}, round {loaded.round}")
    ui.hint(f"{len(loaded.requires)} fields you must fill in; the rest is detected.")
    ui.blank()
    ui.hint(f"Next: edit {output}, then run  mlperf-sysinfo check -c {output}")
    return EXIT_OK


@app.command
def check(
    *,
    config: ConfigOpt,
    offline: Annotated[
        bool,
        cyclopts.Parameter(help="Validate the config only; do not touch the network."),
    ] = False,
    verbose: Annotated[bool, cyclopts.Parameter(name=["--verbose", "-v"])] = False,
) -> int:
    """Validate the config and reach every node it names. Nothing is collected."""
    _setup_logging(verbose)
    cfg, profile = _load(config)
    report = run_check(cfg, profile, skip_network=offline)
    out_file = cfg.output_dir / (cfg.output.file or profile.output_file)
    _render_check(report, out_file=out_file)
    return EXIT_OK if report.ok else EXIT_PROBLEMS


@app.command
def capture(
    *,
    config: ConfigOpt,
    allow_partial: Annotated[
        bool,
        cyclopts.Parameter(
            help="Proceed even if a node is unreachable. The output is marked partial."
        ),
    ] = False,
    run_metadata: Annotated[
        Path | None,
        cyclopts.Parameter(help="A run_metadata file to patch with serving config values."),
    ] = None,
    verbose: Annotated[bool, cyclopts.Parameter(name=["--verbose", "-v"])] = False,
) -> int:
    """Run the check, then collect and write the system description."""
    _setup_logging(verbose)
    cfg, profile = _load(config)

    ui.blank()

    def progress(kind: str, label: str, detail: str) -> None:
        ui.row(_SYMBOLS.get(kind, " "), label, detail, "", 20)

    try:
        result = run_capture(
            cfg,
            profile,
            allow_partial=allow_partial,
            run_metadata_path=run_metadata,
            progress=progress,
        )
    except CheckFailed as e:
        if e.report is not None:
            out_file = cfg.output_dir / (cfg.output.file or profile.output_file)
            _render_check(e.report, out_file=out_file)
        else:  # pragma: no cover - defensive
            ui.error(str(e))
        ui.blank()
        ui.error("nothing was collected")
        return EXIT_PROBLEMS

    ui.blank()
    bits = [f"{result.nodes_collected} node(s)"]
    accel = result.accelerator_total
    if accel:
        bits.append(f"{accel} accelerators")
    bits.append(f"profile {profile.name}")
    print("  " + ui.dim(" - ".join(bits)))
    ui.blank()

    if result.complete:
        print(f"  {ui.green('Written')}  {result.output_path}")
    else:
        print(f"  {ui.yellow('Written (partial)')}  {result.output_path}")
        ui.hint("One or more nodes did not answer. The file records this.")
    for extra in result.extra_files:
        print(f"  {ui.dim('also')}     {extra}")
    ui.blank()
    ui.hint(f"Next: mlperf-sysinfo show {result.output_path}")
    return EXIT_OK if result.complete else EXIT_PROBLEMS


@app.command
def show(path: Path) -> int:
    """Print a readable summary of a captured file."""
    s = summarise(path)

    ui.blank()
    ui.kv("system", ui.bold(s.system_name), 12)
    ui.kv("profile", f"{s.profile} {ui.dim(f'(v{s.profile_round} rules)')}", 12)
    ui.kv("captured", s.captured_at, 12)
    if not s.complete:
        ui.kv("state", ui.yellow("PARTIAL -- not the whole system"), 12)
    if s.system_size:
        ui.kv("size", s.system_size, 12)
    if s.framework:
        ui.kv("serving", s.framework, 12)

    if s.nodes:
        ui.heading("node types")
        for accel, count, cpu in s.nodes:
            detail = f"on {cpu}" if cpu else ""
            ui.row(ui.dim("·"), f"{count} x {accel}", "", detail, 44)

    if s.detected:
        ui.heading("detected")
        for label, value in s.detected:
            ui.kv(label, value, 14)

    if s.supplied:
        ui.heading("from your config")
        for label, value in s.supplied:
            ui.kv(label, value, 14)

    ui.blank()
    return EXIT_OK


@app.command(name="validate")
def validate_cmd(
    path: Path,
    *,
    profile: Annotated[
        str | None,
        cyclopts.Parameter(help="Validate against this profile instead of the stamped one."),
    ] = None,
) -> int:
    """Check a captured file against a profile before submitting it."""
    report = validate(path, profile_name=profile)

    ui.blank()
    ui.kv("file", str(report.path), 10)
    ui.kv("profile", f"{report.profile_name} {ui.dim(f'(v{report.profile_round} rules)')}", 10)

    if report.problems:
        ui.heading("problems")
        for problem in report.problems:
            ui.row(ui.BAD, problem, "", "", 0)
    if report.warnings:
        ui.heading("warnings")
        for warning in report.warnings:
            ui.row(ui.WARN, warning, "", "", 0)

    ui.blank()
    if report.ok:
        extra = f" {len(report.warnings)} warning(s)." if report.warnings else ""
        print(f"  {ui.green('Valid.')} {report.checked} required field(s) present.{extra}")
        return EXIT_OK
    count = len(report.problems)
    print(f"  {ui.red(f'{count} problem(s).')} This file is not ready to submit.")
    return EXIT_PROBLEMS


@app.command(name="profiles")
def profiles_cmd() -> int:
    """List the built-in profiles."""
    ui.blank()
    for name in available_profiles():
        p = load_profile(name)
        print(f"  {ui.bold(p.name.ljust(12))} {p.title}  {ui.dim(f'round {p.round}')}")
        ui.hint(f"  {p.description.strip()}")
        ui.hint(f"  {len(p.requires)} required field(s), writes {p.shape} {p.output_file}")
        ui.blank()
    return EXIT_OK


def main() -> None:
    """Entry point. Every deliberate failure exits 2 with one clear line."""
    try:
        code = app()
    except SysinfoError as e:
        ui.error(str(e))
        sys.exit(EXIT_ERROR)
    except KeyboardInterrupt:  # pragma: no cover
        ui.error("interrupted")
        sys.exit(130)
    sys.exit(code if isinstance(code, int) else EXIT_OK)


if __name__ == "__main__":  # pragma: no cover
    main()
