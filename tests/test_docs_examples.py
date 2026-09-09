# Copyright 2026 MLCommons contributors
# SPDX-License-Identifier: Apache-2.0
"""Every complete config in the docs is a config that works.

The same minimal config appears in several places on purpose -- Home's Quick Run
needs a CPU-only one someone can run on the laptop they are holding, and each
profile page needs a realistic multi-node one. Keeping the count down was never
the point; keeping them all *true* is. So they are extracted from the published
markdown and put through the same validation a reader's copy would face.

A doc example that no longer loads is worse than a missing one: it is read as an
instruction.
"""

from __future__ import annotations

import re

import pytest

from mlperf_sysinfo import profiles
from mlperf_sysinfo.config import load_config
from mlperf_sysinfo.preflight import run_check

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

#: README.md is scanned too -- it ships to PyPI, where it is the only page most
#: people will ever read.
PAGES = [ROOT / "README.md", *sorted(DOCS.rglob("*.md"))]

_YAML_BLOCK = re.compile(r"```yaml\n(.*?)```", re.S)


def _is_whole_config(block: str) -> bool:
    """A complete sysinfo config, as opposed to a fragment.

    The docs also show an ``extends`` parent, a ``notes:`` snippet, a profile
    definition, a host config with the whole thing nested under ``system_info:``,
    and a bare ``profile: ./profiles/my-group.yaml`` demonstrating the path
    syntax. None of those load on their own, and none is what a reader copies as
    a starting point.

    A top-level ``profile:`` plus a ``system:`` block is what marks the ones
    that are: ``system.name`` is required by every profile, so a config without
    it was never meant to stand alone. (The bare one-liner is why this needs two
    conditions rather than one -- it passed the first and then failed to load.)

    Both flags are collected before deciding, so the order the two keys appear
    in does not matter. An earlier version returned at the first ``system:`` and
    would have silently skipped a config that put ``system:`` on top.
    """
    has_profile = has_system = False
    for line in block.splitlines():
        has_profile |= line.startswith("profile:")
        has_system |= line.startswith("system:")
    return has_profile and has_system


def _examples() -> list[tuple[str, str]]:
    found = []
    for page in PAGES:
        for match in _YAML_BLOCK.finditer(page.read_text()):
            block = match.group(1)
            if _is_whole_config(block):
                found.append((str(page.relative_to(ROOT)), block))
    return found


EXAMPLES = _examples()


def test_the_extractor_found_them() -> None:
    """A regex that quietly matches nothing would make every test below pass."""
    pages = {label for label, _ in EXAMPLES}
    assert "README.md" in pages, "the README ships to PyPI and is not being checked"
    assert "docs/index.md" in pages, "Home's Quick Run config is not being checked"
    assert "docs/configuration/endpoints.md" in pages
    assert "docs/configuration/inference.md" in pages


@pytest.mark.parametrize("label,block", EXAMPLES, ids=[label for label, _ in EXAMPLES])
def test_a_documented_config_is_ready_to_capture(label: str, block: str, tmp_path) -> None:
    path = tmp_path / "sysinfo.yaml"
    path.write_text(block)

    config = load_config(path)  # schema, unknown options, ${VAR}
    profile = profiles.load(config.profile)
    report = run_check(config, profile, skip_network=True)

    assert not report.has_config_problems, (
        f"{label} would not capture:\n  " + "\n  ".join(report.config_problems)
    )
