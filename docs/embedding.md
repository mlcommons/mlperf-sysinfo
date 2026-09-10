# Embedding it

The CLI is a thin layer over a small API. A benchmark harness can call the same
code and render results its own way.

```python
from mlperf_sysinfo import load_config, capture

config = load_config("sysinfo.yaml")
result = capture(config)

result.output_path      # Path to the file that was written
result.nodes            # per-node status, so callers can report properly
result.complete         # False if any node did not answer
result.nodes_collected  # what actually reported back
result.nodes_expected   # what the config asked for
result.profile          # the resolved Profile
result.extra_files      # e.g. Redfish captures
```

## Check early, capture late

Because `check` is a separate, cheap call, run it at the **start** of a
benchmark rather than discovering a config mistake at the end of an hour-long
run.

```python
from mlperf_sysinfo import load_config, check, capture

config = load_config("sysinfo.yaml")

report = check(config)          # seconds: validation + reachability
if not report.ok:
    for problem in report.config_problems:
        log.error("system_info: %s", problem)
    raise SystemExit("fix the sysinfo config before starting the run")

...                             # hours of benchmarking

result = capture(config)        # runs its own check again; never skipped
```

## Failure modes

Every deliberate failure is a `SysinfoError` subclass:

| Exception | Raised when |
| --- | --- |
| `ConfigError` | The config is missing, malformed, or fails validation |
| `ProfileError` | The profile does not exist or is invalid |
| `CheckFailed` | Pre-flight did not pass. Carries `.report` so you can render it |
| `CaptureError` | Collection ran and failed, or returned nothing |
| `DependencyMissing` | `mlc-scripts` is not installed |

```python
from mlperf_sysinfo import capture, CheckFailed, CaptureError

try:
    result = capture(config, allow_partial=False)
except CheckFailed as e:
    for path, why in e.report.missing_required:
        log.error("%s is required: %s", path, why)
except CaptureError as e:
    log.error("system info capture failed: %s", e)
```

!!! tip "Never lose benchmark results to a sysinfo failure"
    Write your results first, then capture. A system-info failure at the end of
    a run should be logged loudly and exit zero, because the benchmark output is
    the expensive artifact.

    ```python
    write_results(report_dir)
    try:
        capture(config, run_metadata_path=report_dir / "run_metadata.json")
    except SysinfoError as e:
        log.error("system_info failed: %s\n  Results are complete at %s\n"
                  "  Re-run: mlperf-sysinfo capture -c sysinfo.yaml",
                  e, report_dir)
    ```

## Full signature

```python
def capture(
    config: SysinfoConfig,
    profile: Profile | None = None,   # defaults to config.profile
    *,
    allow_partial: bool = False,      # forgives unreachable nodes, never config problems
    run_metadata_path: Path | None = None,
    progress: Callable[[str, str, str], None] | None = None,
    report: CheckReport | None = None,  # reuse an earlier check
    verbose: bool = False,            # also echo the automation to the terminal
) -> CaptureResult: ...
```

`progress` is called as `(kind, label, detail)` where `kind` is one of `ok`,
`bad`, `warn`, `skip`. The caller owns all rendering.

`CaptureResult.log_path` is the run log for that capture, which is the thing to
surface or attach when a capture comes back partial. It is written whether or
not `verbose` is set.

## Logging

The package logs its own actions under the `mlperf_sysinfo` logger, at a level,
with the module that acted:

```python
import logging

logging.getLogger("mlperf_sysinfo").addHandler(my_handler)
```

Nothing needs configuring for this to work. The logger is levelled to `DEBUG` at
import so records always reach whatever handlers exist, and propagation is left
**on**, so your root handlers see them with no setup. Levels are chosen for
severity rather than for the CLI's own rendering: an unreachable node is a
`WARNING` because you have `run_check()` and no styled report to read it from.

`mlperf_sysinfo.logs.setup()` is what the CLI calls to take over the terminal,
and it disables propagation. Do not call it from an embedded use unless you want
this package writing to stderr itself.

Passing an earlier `report` avoids re-running reachability checks, but the
config validation is applied either way. There is no way to capture without a
check having passed.

## Reading a file back

```python
from mlperf_sysinfo.report import summarise, validate

summary = summarise("results/run1/system_desc.json")
summary.system_name, summary.complete, summary.accelerator_total

report = validate("results/run1/system_desc.json")
if not report.ok:
    for problem in report.problems:
        log.error(problem)
```

Both work on the file alone. A capture carries the profile it was made under,
so neither needs the original config.

## In a benchmark config

Nest the whole thing under `system_info:` in a benchmark config and it validates
identically. If there is no `output` block, a top-level `report_dir` is used:

```yaml
name: llama3-perf-run
report_dir: results/run1
datasets:
  - name: cnn_dailymail

system_info:
  profile: endpoints
  system:
    name: H100x8_vLLM
  nodes:
    ssh: [root@node1]
  submission:
    division: standardized
    # ...
```
