# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for ``gaia.coder.self_fix.fixer``."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gaia.coder.self_fix.fixer import (
    DEFAULT_BASE_REF,
    SELF_FIX_BRANCH_PREFIX,
    EditHunk,
    generate_fix,
    verify_test_differential,
    write_regression_test,
)
from gaia.coder.self_fix.planner import (
    CostEstimate,
    FileTouchPlan,
    Plan,
)
from gaia.coder.self_fix.triage import FixClassResult


def _make_plan(fid: str = "fb-77") -> Plan:
    return Plan(
        feedback_id=fid,
        fix_class="tool",
        root_cause="bug in classify_failure",
        proposed_change="widen cache key",
        regression_test_sketch="fails on main, passes on fix branch",
        files=(FileTouchPlan(path="src/gaia/coder/sample.py", loc_estimate=10),),
        alternatives_considered=(),
        risks=(),
        success_criterion="fb-77 verified",
        cost_estimate=CostEstimate(tokens=1000, usd=0.02, wall_clock_minutes=5.0),
    )


def _fix_class() -> FixClassResult:
    return FixClassResult(
        fix_class="tool",
        root_cause_hypothesis="bug in classify_failure",
        candidate_files=(),
        prior_pattern_hit=None,
        confidence=90,
    )


def test_fix_writes_on_correct_branch(tmp_git_repo: Path) -> None:
    """The fix branch must be ``auto/gaia-coder/<fid>`` — NEVER the coder base."""
    plan = _make_plan()
    hunks = [
        EditHunk(
            path="src/gaia/coder/sample.py",
            old_string="# BUG: cache collision on timestamped errors",
            new_string="# FIXED: widened cache key by including error class name",
        )
    ]
    diff = generate_fix(
        plan=plan,
        fix_class=_fix_class(),
        edits=hunks,
        repo_root=tmp_git_repo,
        base_ref=DEFAULT_BASE_REF,
    )
    assert diff.branch == f"{SELF_FIX_BRANCH_PREFIX}/{plan.feedback_id}"
    # Sanity: the current branch in the repo should now be the fix branch.
    current = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(tmp_git_repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert current == diff.branch
    assert current != DEFAULT_BASE_REF

    # The edit must have been applied in the working tree on that branch.
    contents = (tmp_git_repo / "src" / "gaia" / "coder" / "sample.py").read_text()
    assert "FIXED: widened cache key" in contents
    assert "BUG: cache collision" not in contents


def test_generate_fix_rejects_empty_edits(tmp_git_repo: Path) -> None:
    """Refuse to create a ghost branch with no edits."""
    with pytest.raises(ValueError, match="at least one EditHunk"):
        generate_fix(
            plan=_make_plan(),
            fix_class=_fix_class(),
            edits=[],
            repo_root=tmp_git_repo,
        )


def test_write_regression_test_requires_fix_branch(tmp_git_repo: Path) -> None:
    """Writing the regression test on ``coder`` is refused."""
    # Standing on the coder base after tmp_git_repo is created.
    plan = _make_plan(fid="fb-78")
    with pytest.raises(RuntimeError, match="self-fix branch"):
        write_regression_test(
            plan=plan,
            changed_files=["src/gaia/coder/sample.py"],
            repo_root=tmp_git_repo,
        )


def test_write_regression_test_lands_on_fix_branch(tmp_git_repo: Path) -> None:
    """After ``generate_fix`` creates the branch, the test lands on it."""
    plan = _make_plan(fid="fb-79")
    generate_fix(
        plan=plan,
        fix_class=_fix_class(),
        edits=[
            EditHunk(
                path="src/gaia/coder/sample.py",
                old_string="# BUG: cache collision on timestamped errors",
                new_string="# FIXED placeholder",
            )
        ],
        repo_root=tmp_git_repo,
    )
    tp = write_regression_test(
        plan=plan,
        changed_files=["src/gaia/coder/sample.py"],
        repo_root=tmp_git_repo,
    )
    assert tp.branch.startswith(f"{SELF_FIX_BRANCH_PREFIX}/")
    assert tp.path.startswith("tests/coder/regression/")
    # File actually exists on disk under the fix branch's working tree.
    assert (tmp_git_repo / tp.path).exists()


def test_verify_test_differential_enforces_fail_then_pass(tmp_git_repo: Path) -> None:
    """A test that passes on both refs must raise — fail-then-pass is required."""
    # Create a passing test on the coder base and commit it.
    test_dir = tmp_git_repo / "tests" / "coder" / "regression"
    test_dir.mkdir(parents=True, exist_ok=True)
    test_path = test_dir / "test_always_green.py"
    test_path.write_text(
        "def test_always_green():\n    assert True\n", encoding="utf-8"
    )
    subprocess.run(
        ["git", "add", str(test_path)],
        cwd=str(tmp_git_repo),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", "add test that always passes"],
        cwd=str(tmp_git_repo),
        check=True,
        capture_output=True,
    )
    # Now branch off and leave it untouched — both refs will pass the test.
    subprocess.run(
        ["git", "checkout", "-b", "auto/gaia-coder/fb-always-green", "coder"],
        cwd=str(tmp_git_repo),
        check=True,
        capture_output=True,
    )
    with pytest.raises(RuntimeError, match="fail-then-pass"):
        verify_test_differential(
            test_path="tests/coder/regression/test_always_green.py",
            base_ref="coder",
            fix_branch="auto/gaia-coder/fb-always-green",
            repo_root=tmp_git_repo,
        )


def test_verify_test_differential_accepts_fail_then_pass(tmp_git_repo: Path) -> None:
    """A test that fails on base and passes on fix must succeed."""
    # The fixer's default test writer is the cleanest way to get genuine
    # fail-then-pass: the marker flag only exists on the fix branch.
    plan = _make_plan(fid="fb-diff")
    generate_fix(
        plan=plan,
        fix_class=_fix_class(),
        edits=[
            EditHunk(
                path="src/gaia/coder/sample.py",
                old_string="# BUG: cache collision on timestamped errors",
                new_string="# FIXED placeholder",
            )
        ],
        repo_root=tmp_git_repo,
    )
    tp = write_regression_test(
        plan=plan,
        changed_files=["src/gaia/coder/sample.py"],
        repo_root=tmp_git_repo,
    )
    # Commit the generated test + marker so the fix branch has them.
    subprocess.run(
        ["git", "add", "-A"],
        cwd=str(tmp_git_repo),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", "self-fix: regression test + marker"],
        cwd=str(tmp_git_repo),
        check=True,
        capture_output=True,
    )
    res = verify_test_differential(
        test_path=tp.path,
        base_ref="coder",
        fix_branch=tp.branch,
        repo_root=tmp_git_repo,
    )
    assert res.verified is True
    assert res.base_returncode != 0
    assert res.fix_returncode == 0
