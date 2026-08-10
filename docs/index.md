# mlperf-sysinfo

Capture MLPerf system descriptions. One config format, one command line, and a
profile per working group that decides what is required and what the output
file looks like.

Collection is done by [mlc-scripts](https://github.com/mlcommons/mlperf-automations).
This package owns everything a person touches: the config, the checks, the
error messages, and the shape of the result.

---

## Install

```bash
pip install mlperf-sysinfo
```

## Sixty seconds

```bash
mlperf-sysinfo init endpoints          # a starter config with only your fields
$EDITOR sysinfo.yaml                   # fill in what cannot be detected
mlperf-sysinfo check   -c sysinfo.yaml # validate + reach everything, seconds
mlperf-sysinfo capture -c sysinfo.yaml # collect and write the file
mlperf-sysinfo validate results/sysinfo/system_desc.json
```

`check` is not optional. `capture` always runs it first and there is no flag to
skip it, so a missing field or an unreachable node surfaces **before** anything
is collected rather than after.

## What it looks like

```console
$ mlperf-sysinfo capture -c sysinfo.yaml

  ✓ pre-flight check     passed -- 1 node(s), profile endpoints (v6.0 rules)
  ✓ collection           1 of 1 node(s) returned hardware

  1 node(s) - 8 accelerators - profile endpoints

  Written  /data/common/anandhu/sysinfo-qa/verify/out/system_desc.json

  Next: mlperf-sysinfo show .../out/system_desc.json
```

## Three rules worth knowing

<div class="grid cards" markdown>

-   **Anything detectable is never a config field**

    ---

    CPU, memory, accelerators, node counts and framework version are probed. If
    they appear in your config at all, they are overrides.

-   **Required means required**

    ---

    A field a profile needs and cannot detect must be filled in. A config still
    saying `CHANGEME` is not filled in, and the checker says so.

-   **`${VAR}` reads from the environment**

    ---

    BMC and API credentials are referenced, never written into the file, never
    committed.

</div>

## Where to go next

| If you want to… | Read |
| --- | --- |
| Understand how the pieces fit together | [Architecture](architecture.md) |
| Write or fix a config | [The config file](configuration.md) |
| Look up a command or an exit code | [Commands](commands.md) |
| See what a real capture produces | [Sample outputs](outputs.md) |
| Onboard your working group | [Profiles](profiles.md) |
| Call it from your own tool | [Embedding it](embedding.md) |
