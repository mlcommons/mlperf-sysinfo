# Copyright 2026 MLCommons contributors
# SPDX-License-Identifier: Apache-2.0
"""The command line.

Five commands: init, check, capture, show, validate. Check is not optional --
capture always runs it first and there is no flag to skip it.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Annotated

import cyclopts
from cyclopts.exceptions import CycloptsError

from . import __version__, logs, ui
from .collector import capture as run_capture
from .config import load_config
from .errors import CheckFailed, ConfigError, SysinfoError
from .preflight import CheckReport, run_check
from .profiles import available as available_profiles
from .profiles import load as load_profile
from .report import summarise, validate
from .suggest import closest, did_you_mean

#: One logger per command, so the module column names the command that acted
#: ("init:", "capture:") rather than repeating "cli:" on every line.
log = logs.get(__name__)
_log_init = logs.get("mlperf_sysinfo.init")
_log_capture = logs.get("mlperf_sysinfo.capture")
_log_check = logs.get("mlperf_sysinfo.check")
_log_profiles = logs.get("mlperf_sysinfo.profiles")
_log_report = logs.get("mlperf_sysinfo.report")

_TEMPLATE_DIR = Path(__file__).parent / "templates"

EXIT_OK = 0
EXIT_PROBLEMS = 1
EXIT_ERROR = 2

app = cyclopts.App(
    name="mlperf-sysinfo",
    version=__version__,
    help="Automatically capture the system description of a benchmarking machine.",
    help_epilogue="Run 'mlperf-sysinfo COMMAND --help' for the options a command takes.",
)

ConfigOpt = Annotated[
    Path, cyclopts.Parameter(name=["--config", "-c"], help="Path to the config file.")
]


#: What reaches the terminal when nothing is asked for.
#:
#: Output splits by shape rather than by command. Sentence-shaped output --
#: ``init``, and ``capture``'s status lines -- *is* log records, so it carries a
#: date and a level like everything else. The aligned tables (``check``,
#: ``show``, ``validate``, ``profiles``) stay as they are, because a 31-character
#: prefix on every row costs the columns their scannability, which is the whole
#: point of a table. Records whose fact one of those tables renders are marked
#: ``logs.STYLED`` and dropped from the terminal so nothing is said twice.
#:
#: Levels themselves are chosen for severity, not for this CLI: an unreachable
#: node is a WARNING because a library caller has ``run_check()`` and no styled
#: report to read it from.
DEFAULT_LOG_LEVEL = "info"

#: None rather than the default string, so that an explicitly passed level is
#: distinguishable from an absent one. Sharing a value with DEFAULT_LOG_LEVEL
#: would let --verbose silently override "--log-level info".
LogLevelOpt = Annotated[
    str | None,
    cyclopts.Parameter(
        name=["--log-level"],
        help=(
            "Terminal log level: debug, info, warning or error. Defaults to info, "
            "or debug with --verbose. The run log keeps every level regardless."
        ),
    ),
]


def _setup_logging(verbose: bool, level: str | None = None) -> None:
    """``--verbose`` is shorthand for ``--log-level debug``.

    An explicit level wins, so ``--verbose --log-level info`` still echoes the
    automation to the terminal without the debug detail.
    """
    if level is None:
        level = "debug" if verbose else DEFAULT_LOG_LEVEL
    try:
        logs.setup(level)
    except ValueError:
        guess = did_you_mean(level, logs.LEVELS)
        raise ConfigError(
            f"unknown --log-level {level!r}.{guess} "
            f"Available levels: {', '.join(logs.LEVELS)}."
        ) from None


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
    ui.kv("profile", ui.bold(profile.name))
    ui.kv("output", str(out_file))

    all_targets = [str(t) for t in config.all_targets]
    labels = [n.label for n in report.nodes] + all_targets + ["this machine"]
    if report.endpoint:
        labels.append(report.endpoint.label)
    if report.serving_log:
        labels.append(report.serving_log.label)
    width = max((len(x) for x in labels), default=12) + 2

    ui.heading("nodes")
    if not report.network_checked:
        for raw in all_targets:
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
            + report.placeholder_other
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

    # Recommended fields appear here rather than under "worth filling in":
    # starter text is a problem wherever it is, and only emptiness is a warning.
    # The two lists carry different second elements -- a reason for the ones a
    # profile names, the offending value for the ones it does not -- so they
    # are rendered separately rather than concatenated.
    if report.placeholder_recommended or report.placeholder_other:
        ui.heading("still starter text")
        for path, why in report.placeholder_recommended:
            ui.row(ui.BAD, path, ui.red("placeholder"), f"still the starter value -- {why}", fwidth)
        for path, value in report.placeholder_other:
            ui.row(ui.BAD, path, ui.red("placeholder"), repr(value), fwidth)

    if report.missing_recommended:
        ui.heading("worth filling in")
        for path, why in report.missing_recommended:
            ui.row(ui.WARN, path, ui.yellow("empty"), why, fwidth)

    ui.blank()
    if report.ok:
        if report.network_checked:
            _log_check.info("ready to capture")
        else:
            _log_check.info("config is valid")
            _log_check.info("nothing was reached -- run without --offline before capturing")
        return

    count = report.problem_count
    noun = "problem" if count == 1 else "problems"
    _log_check.error("%d %s", count, noun)
    if report.has_config_problems:
        _log_check.error("fill in the missing fields and run check again")
    if report.has_reach_problems:
        _log_check.error(
            "fix the unreachable nodes, or run capture --allow-partial to proceed without them"
        )


_SYMBOLS = {"ok": ui.OK, "bad": ui.BAD, "warn": ui.WARN, "skip": ui.SKIP}


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


@app.command
def init(
    profile: str = "endpoints",
    *,
    path: Annotated[
        Path,
        cyclopts.Parameter(
            name=["--path"], help="Where to write the config. Defaults to sysinfo.yaml in the current directory."
        ),
    ] = Path("sysinfo.yaml"),
    force: Annotated[
        bool, cyclopts.Parameter(help="Overwrite the file if it already exists.")
    ] = False,
) -> int:
    """Write a starter config containing only the fields your profile needs."""
    template = _TEMPLATE_DIR / f"{profile}.yaml"
    if not template.exists():
        guess = did_you_mean(profile, available_profiles())
        _log_init.error(
            "no starter config for profile %r.%s Built-in profiles: %s",
            profile,
            guess,
            ", ".join(available_profiles()),
        )
        return EXIT_ERROR
    if path.exists() and not force:
        _log_init.error("%s already exists. Pass --force to overwrite it.", path)
        return EXIT_ERROR

    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template, path)

    loaded = load_profile(profile)
    _log_init.debug("template source %s", template)
    _log_init.info(
        "wrote template %s (profile %s) to %s", path.name, loaded.name, path.resolve()
    )
    _log_init.info(
        "edit it before running 'mlperf-sysinfo capture -c %s'", path.name
    )
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
    log_level: LogLevelOpt = None,
) -> int:
    """Validate the config and reach every node it names. Nothing is collected."""
    _setup_logging(verbose, log_level)
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
    log_level: LogLevelOpt = None,
) -> int:
    """Run the check, then collect and write the system description."""
    _setup_logging(verbose, log_level)
    cfg, profile = _load(config)

    # Every progress event is a fact the collector logs itself, under its own
    # name -- the callback exists so an embedder can render them, not so they
    # get said a second time here. DEBUG keeps them out of the default terminal
    # while leaving them visible when you have asked for everything.
    def progress(kind: str, label: str, detail: str) -> None:
        _log_capture.debug("%s: %s", label, detail)

    try:
        result = run_capture(
            cfg,
            profile,
            allow_partial=allow_partial,
            run_metadata_path=run_metadata,
            progress=progress,
            verbose=verbose,
        )
    except CheckFailed as e:
        # The check report is a table, so it keeps its styled form; the verdict
        # about it is a sentence, so it is logged.
        if e.report is not None:
            out_file = cfg.output_dir / (cfg.output.file or profile.output_file)
            _render_check(e.report, out_file=out_file)
        else:  # pragma: no cover - defensive
            _log_capture.error("%s", e)
        _log_capture.error("nothing was collected")
        return EXIT_PROBLEMS

    bits = [f"{result.nodes_collected} node(s)"]
    accel = result.accelerator_total
    if accel:
        bits.append(f"{accel} accelerators")
    bits.append(f"profile {profile.name}")
    _log_capture.info(" - ".join(bits))

    if not result.complete:
        _log_capture.warning(
            "the capture is partial -- one or more nodes did not answer, and the file records it"
        )
    if result.log_path:
        _log_capture.info("run log %s", result.log_path)
    _log_capture.info("next: mlperf-sysinfo show %s", result.output_path)
    return EXIT_OK if result.complete else EXIT_PROBLEMS


@app.command
def show(path: Path) -> int:
    """Print a readable summary of a captured file."""
    s = summarise(path)
    _log_report.info("read %s", Path(path).resolve())

    ui.blank()
    ui.kv("system", ui.bold(s.system_name), 12)
    ui.kv("profile", s.profile, 12)
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
    ui.kv("profile", report.profile_name, 10)

    if report.problems:
        ui.heading("problems")
        for problem in report.problems:
            ui.row(ui.BAD, problem, "", "", 0)
    if report.warnings:
        ui.heading("warnings")
        for warning in report.warnings:
            ui.row(ui.WARN, warning, "", "", 0)

    if report.ok:
        extra = f", {len(report.warnings)} warning(s)" if report.warnings else ""
        _log_report.info(
            "valid -- %d required field(s) present%s", report.checked, extra
        )
        return EXIT_OK
    _log_report.error(
        "%d problem(s) -- this file is not ready to submit", len(report.problems)
    )
    return EXIT_PROBLEMS


@app.command(name="profiles")
def profiles_cmd() -> int:
    """List the built-in profiles."""
    _log_profiles.debug("%d built-in profile(s)", len(available_profiles()))
    ui.blank()
    for name in available_profiles():
        p = load_profile(name)
        print(f"  {ui.bold(p.name.ljust(12))} {p.title}")
        ui.hint(f"  {p.description.strip()}")
        ui.hint(f"  {len(p.requires)} required field(s), writes {p.shape} {p.output_file}")
        ui.blank()
    return EXIT_OK


#: Real flags, but only once a command has been named. Worth saying out loud
#: because "-v" is as good a guess at this as at "--version", and it *is* "-v"
#: on check and capture -- just not on its own.
_COMMAND_ONLY_FLAGS = ("--verbose",)


def _registered(*, flags: bool) -> list[str]:
    """Top-level flag names, or command names. Asked of the app, not restated,
    so adding either cannot leave this suggesting a stale set."""
    return [name for name in app.resolved_commands() if name.startswith("-") is flags]


def _report_parse_error(e: CycloptsError) -> None:
    """Explain a command line that did not parse.

    Three cases, because the useful next step differs:

    * a leading token that looks like a flag gets its own message -- listing the
      commands is no help to someone who typed "-v", and the suggestion is the
      part they need to see first;
    * a real command that was called wrongly gets pointed at its own --help,
      which is where the arguments it wants are written down;
    * anything else is passed through, since cyclopts already suggests command
      names for a near miss.
    """
    typed = sys.argv[1] if len(sys.argv) > 1 else ""

    if typed.startswith("-"):
        guess = did_you_mean(typed, _registered(flags=True))
        ui.error(f'"{typed}" is not a command or a top-level flag.{guess}')
        for flag in closest(typed, _COMMAND_ONLY_FLAGS):
            ui.hint(f'"{flag}" exists, but only on a command -- e.g. mlperf-sysinfo check {typed}')
        ui.hint("Run 'mlperf-sysinfo --help' to see the commands.")
        return

    ui.error(str(e))
    if typed in _registered(flags=False):
        ui.hint(f"Run 'mlperf-sysinfo {typed} --help' for the arguments it takes.")


# The epilogue is inherited by every subcommand, where "run COMMAND --help" is
# advice the reader has already taken. Clear it on the children rather than
# repeating help_epilogue="" on each decorator, so a seventh command cannot
# forget to.
for _subapp in app.subapps:
    _subapp.help_epilogue = ""


def main() -> None:
    """Entry point.

    The exit codes are a contract, because ``check`` is meant to be scriptable:
    0 all good, 1 the run found problems, 2 the command or config was wrong.
    A mistyped flag must not look like a failed check.
    """
    # Installed before dispatch so that commands without a --verbose flag still
    # report warnings from config loading. check and capture re-level it once
    # they have parsed their own options.
    logs.setup(DEFAULT_LOG_LEVEL)
    try:
        # print_error=False: cyclopts would otherwise render its own panel and
        # we would print the same text again underneath it.
        code = app(exit_on_error=False, print_error=False)
    except SysinfoError as e:
        ui.error(str(e))
        sys.exit(EXIT_ERROR)
    except CycloptsError as e:
        _report_parse_error(e)
        sys.exit(EXIT_ERROR)
    except KeyboardInterrupt:  # pragma: no cover
        ui.error("interrupted")
        sys.exit(130)
    sys.exit(code if isinstance(code, int) else EXIT_OK)


if __name__ == "__main__":  # pragma: no cover
    main()
