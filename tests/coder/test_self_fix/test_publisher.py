# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for ``gaia.coder.self_fix.publisher``."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import List

import pytest

from gaia.coder.self_fix.planner import (
    CostEstimate,
    FileTouchPlan,
    Plan,
)
from gaia.coder.self_fix.publisher import (
    compose_pr_body,
    notify_em,
    open_self_fix_pr,
)


def _plan(fid: str = "fb-01HA2B3C") -> Plan:
    return Plan(
        feedback_id=fid,
        fix_class="tool",
        root_cause="classify_failure cache collides on timestamps",
        proposed_change="widen cache key by including error class name",
        regression_test_sketch="fails on coder, passes on fix branch",
        files=(FileTouchPlan(path="src/gaia/coder/sample.py", loc_estimate=12),),
        alternatives_considered=("alt A: rewrite caching layer",),
        risks=("risk: cache eviction patterns may shift",),
        success_criterion=f"Feedback {fid} transitions to 'verified'",
        cost_estimate=CostEstimate(tokens=12_000, usd=0.24, wall_clock_minutes=6.0),
    )


def _completed(stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["gh"], returncode=0, stdout=stdout, stderr=""
    )


def test_regression_test_is_required() -> None:
    """``open_self_fix_pr`` must reject a missing regression test path."""
    with pytest.raises(ValueError, match="regression_test_path"):
        open_self_fix_pr(
            fix_branch="auto/gaia-coder/fb-01HA",
            feedback_id="fb-01HA",
            plan=_plan(fid="fb-01HA"),
            review_gate_result=None,
            feedback_body="something went wrong",
            em_handle="kov",
            regression_test_path="",
        )


def test_pr_body_cites_feedback_id() -> None:
    """PR body must cite feedback_id verbatim and quote the EM's exact wording."""
    from gaia.coder.self_fix.fixer import Diff

    fid = "fb-01HA2B3C"
    em_wording = "classify_failure misfires on timestamped errors in production"
    body = compose_pr_body(
        _plan(fid=fid),
        Diff(
            feedback_id=fid,
            branch=f"auto/gaia-coder/{fid}",
            files_edited=("src/gaia/coder/sample.py",),
        ),
        feedback_body=em_wording,
        feedback_id=fid,
        em_handle="kov",
        context_url="https://github.com/amd/gaia/pull/999",
        regression_test_path="tests/coder/regression/test_fb_01HA2B3C.py",
        review_gate_result=None,
    )
    # feedback_id appears at least twice (once in "Closes feedback", once in header).
    assert fid in body
    assert re.search(
        rf"feedback_id.*{re.escape(fid)}", body, flags=re.IGNORECASE | re.DOTALL
    )
    # EM wording quoted verbatim (block-quote line).
    assert f"> {em_wording}" in body


def test_open_self_fix_pr_passes_draft_flag(tmp_path: Path) -> None:
    """Smoke: the gh argv includes --draft and --base coder."""
    invocations: List[List[str]] = []

    def runner(args, cwd=None, check=True):
        invocations.append(list(args))
        return _completed(stdout="https://github.com/amd/gaia/pull/123\n")

    pr = open_self_fix_pr(
        fix_branch="auto/gaia-coder/fb-01HA",
        feedback_id="fb-01HA",
        plan=_plan(fid="fb-01HA"),
        review_gate_result=None,
        feedback_body="body",
        em_handle="kov",
        context_url="https://github.com/amd/gaia/pull/123",
        regression_test_path="tests/coder/regression/test_fb.py",
        gh_runner=runner,
    )
    assert pr.number == 123
    assert pr.draft is True
    assert len(invocations) == 1
    argv = invocations[0]
    assert "--draft" in argv
    assert argv[:3] == ["pr", "create", "--base"]
    # Base must be 'coder', never 'main' (§5.6).
    assert argv[3] == "coder"


def test_open_self_fix_pr_rejects_mismatched_ids() -> None:
    """Guard: plan.feedback_id must match the feedback_id argument."""
    plan = _plan(fid="fb-A")
    with pytest.raises(ValueError, match="does not match"):
        open_self_fix_pr(
            fix_branch="auto/gaia-coder/fb-A",
            feedback_id="fb-B",
            plan=plan,
            review_gate_result=None,
            feedback_body="body",
            em_handle="em",
            regression_test_path="tests/coder/regression/test_fb.py",
        )


def test_notify_em_comments_on_pr_context() -> None:
    """``notify_em`` routes to ``gh pr comment`` when given a PR URL."""
    invocations: List[List[str]] = []

    def runner(args, cwd=None, check=True):
        invocations.append(list(args))
        return _completed()

    out = notify_em(
        pr_url="https://github.com/amd/gaia/pull/456",
        feedback_id="fb-01",
        context_url="https://github.com/amd/gaia/issues/42",
        gh_runner=runner,
    )
    assert out["posted"] is True
    assert invocations[0][:2] == ["issue", "comment"]


def test_notify_em_defers_without_context() -> None:
    """No context URL → no comment posted; deferral marker returned."""
    out = notify_em(
        pr_url="https://github.com/amd/gaia/pull/456",
        feedback_id="fb-02",
        context_url=None,
    )
    assert out["posted"] is False
