# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
The gate must be quiet about real, honest skills (issue #2468).

A security gate that cries wolf gets ignored, and then it protects nothing. The
strongest evidence available in-repo is the set of skills already checked in:
``.claude/skills/`` holds a dozen substantial, human-written skill bodies full of
shell commands, imperative instructions ("never", "always", "do not"), and quoted
examples — exactly the material a naive injection scanner mangles.

These are also the skills GAIA's own CI would audit, since the skill-audit
workflow matches ``**/skills/**``. If this test ever fails, either a rule got too
greedy or a real problem landed in a repo skill; both are worth stopping for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gaia.skills.audit import audit_skill
from gaia.skills.format import SKILL_FILENAME

REPO_ROOT = Path(__file__).resolve().parents[2]
CLAUDE_SKILLS = REPO_ROOT / ".claude" / "skills"


def _repo_skill_dirs() -> list[Path]:
    if not CLAUDE_SKILLS.is_dir():
        return []
    return sorted(d for d in CLAUDE_SKILLS.iterdir() if (d / SKILL_FILENAME).is_file())


REPO_SKILLS = _repo_skill_dirs()


def test_the_repo_actually_has_skills_to_check_against():
    """Guards the parametrized tests below from silently covering nothing."""
    assert len(REPO_SKILLS) >= 5, (
        f"Expected several skills under {CLAUDE_SKILLS}; found "
        f"{[d.name for d in REPO_SKILLS]}. If they moved, retarget this test "
        "rather than deleting it — it is the anti-false-positive guard."
    )


@pytest.mark.parametrize("directory", REPO_SKILLS, ids=lambda d: d.name)
def test_a_real_repo_skill_audits_clean(directory: Path):
    """Every checked-in skill must ALLOW with zero findings.

    Not 'not BLOCK' — zero findings. An advisory finding on an honest skill is
    still noise a reader has to dismiss, and the whole gate's credibility rests
    on it staying at zero here.
    """
    report = audit_skill(directory)
    assert report.verdict == "ALLOW", [
        f"{f.severity} {f.rule_id} {f.location}: {f.message}" for f in report.findings
    ]
    assert report.findings == (), [
        f"{f.severity} {f.rule_id} {f.location}: {f.message}" for f in report.findings
    ]


@pytest.mark.parametrize("directory", REPO_SKILLS, ids=lambda d: d.name)
def test_a_real_repo_skill_clears_the_community_tier(directory: Path):
    """The advisory tier passing proves little; community is the real bar."""
    from gaia.skills.audit import clears_tier

    report = audit_skill(directory)
    assert clears_tier(report.findings, "community")
