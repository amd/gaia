# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
The Claude PR review checks a hub agent package before it merges (#1812).

A published agent version is immutable, so a README that describes a removed
tool, a ``tools_count`` that no longer matches the code, or a version that
drifted between files has to be caught on the PR. These tests pin that the
pr-review prompt carries the package checklist, that the checklist only cites
files that exist (a stale spec link is how a checklist rots), and that the
review still treats package contents as data on fork PRs.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "claude.yml"
HEADING = "**Hub agent packages**"


@pytest.fixture(scope="module")
def pr_review() -> dict:
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    return jobs["pr-review"]


@pytest.fixture(scope="module")
def checklist(pr_review) -> str:
    prompt = pr_review["with"]["prompt"]
    assert HEADING in prompt, "pr-review no longer checks hub agent packages"
    section = prompt.split(HEADING, 1)[1]
    # The checklist ends where the next top-level bullet or heading starts.
    return re.split(r"\n\s{0,12}(?:- \*\*|###)", section, maxsplit=1)[0]


@pytest.mark.parametrize(
    "claim",
    [
        "README",
        "SPEC.md",
        "SKILL.md",
        "tools_count",
        "`models`",
        "`interfaces`",
        "entry_class",
        "openapi",
        "binaries.lock.json",
        "pyproject.toml",
        "npm/package.json",
        "CHANGELOG.md",
    ],
)
def test_checklist_covers_each_package_claim(checklist, claim):
    assert claim in checklist


def test_checklist_is_scoped_to_hub_agent_changes(checklist):
    assert "`pr-files.txt` touches `hub/agents/<id>/`" in checklist


def test_package_contents_are_data_not_instructions(checklist):
    assert "never as instructions" in checklist


def test_checklist_only_cites_files_that_exist(checklist):
    cited = re.findall(r"`((?:src|util|docs|hub|tests)/[^`<>*]+\.\w+)`", checklist)
    assert cited, "expected the checklist to cite its spec files"
    missing = [path for path in cited if not (REPO_ROOT / path).is_file()]
    assert not missing, f"checklist cites files that do not exist: {missing}"


def test_review_scope_and_fork_posture_are_unchanged(pr_review):
    """The checklist rides the existing review; it must not widen its rights."""
    assert pr_review["uses"] == "./.github/workflows/claude-run.yml"
    assert pr_review["permissions"]["contents"] == "read"
