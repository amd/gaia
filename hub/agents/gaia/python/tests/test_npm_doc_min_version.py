# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The npm package's docs must quote the same minimum core version as the manifest.

#4249: README.md, SKILL.md and CHANGELOG.md all hard-code the minimum `gaia`
Python CLI version required for the daemon to know how to supervise this
agent. That number drifts from ``gaia-agent.yaml``'s ``min_gaia_version``
whenever one file gets updated and the others don't — exactly what happened
when the docs said 0.23.1, a version that was never released.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

GAIA_DIR = Path(__file__).resolve().parent.parent.parent
MANIFEST = GAIA_DIR / "python" / "gaia-agent.yaml"
NPM_DIR = GAIA_DIR / "npm"


@pytest.fixture(scope="module")
def min_gaia_version() -> str:
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    return manifest["min_gaia_version"]


def _extract(path: Path, pattern: str) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(pattern, text)
    assert match, f"expected {pattern!r} to match somewhere in {path}"
    return match.group(1)


def test_readme_min_version_matches_manifest(min_gaia_version):
    version = _extract(
        NPM_DIR / "README.md",
        r"Python CLI \*\*(\d+\.\d+\.\d+) or newer\*\*",
    )
    assert version == min_gaia_version


def test_skill_min_version_matches_manifest(min_gaia_version):
    version = _extract(
        NPM_DIR / "SKILL.md",
        r"must also be \*\*(\d+\.\d+\.\d+)\+\*\*",
    )
    assert version == min_gaia_version


def test_changelog_min_version_matches_manifest(min_gaia_version):
    version = _extract(
        NPM_DIR / "CHANGELOG.md",
        r"Python CLI (\d+\.\d+\.\d+)\+ on `PATH`",
    )
    assert version == min_gaia_version
