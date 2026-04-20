# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""End-to-end tests for ``gaia.coder.self_fix.loop_driver.FeedbackLoopDriver``."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable

from gaia.coder.self_fix import (
    FeedbackLoopDriver,
    LoopDriverConfig,
)
from gaia.coder.stores import feedback as feedback_store


def _triage_client(fix_class: str = "tool", confidence: int = 85) -> Callable[..., str]:
    """Factory: triage LLM stub returning a canned classification."""

    def client(**_kwargs):
        return json.dumps(
            {
                "fix_class": fix_class,
                "root_cause_hypothesis": "mock root cause",
                "candidate_files": [
                    {"path": "src/gaia/coder/sample.py", "why": "seeded"}
                ],
                "prior_pattern_hit": None,
                "confidence": confidence,
            }
        )

    return client


def _gh_runner_factory(
    pr_number: int = 123,
) -> Callable[..., subprocess.CompletedProcess]:
    """Factory: gh CLI stub that returns a canned PR URL."""

    def runner(args, cwd=None, check=True):
        if args and args[0] == "pr" and args[1] == "create":
            return subprocess.CompletedProcess(
                args=["gh", *args],
                returncode=0,
                stdout=f"https://github.com/amd/gaia/pull/{pr_number}\n",
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=["gh", *args], returncode=0, stdout="", stderr=""
        )

    return runner


def test_loop_driver_end_to_end(
    tmp_git_repo: Path,
    feedback_db_path: Path,
    memory_db_path: Path,
    seed_feedback,
) -> None:
    """Seed a pending feedback row, run the driver, assert fix-pr-open."""
    fid = seed_feedback(
        body="classify_failure misfires on timestamped errors; please fix cache key",
    )
    driver = FeedbackLoopDriver(
        LoopDriverConfig(
            repo_root=tmp_git_repo,
            feedback_db_path=feedback_db_path,
            memory_db_path=memory_db_path,
            em_config={"em_handle": "test-em"},
            base_ref="coder",
        ),
        triage_client=_triage_client(),
        gh_runner=_gh_runner_factory(pr_number=777),
        # The synthetic tmp repo has no pytest collector under it; skipping
        # differential verify keeps the test focussed on state transitions.
        skip_differential_verify=True,
    )
    result = driver.process_pending_feedback()
    assert result.final_state == "fix-pr-open"
    assert result.pr is not None
    assert result.pr.number == 777
    assert result.regression_test_path is not None
    assert result.plan is not None
    assert result.fix_class is not None
    assert result.fix_class.fix_class == "tool"
    # Feedback row should now show fix-pr-open with the PR URL recorded.
    conn = feedback_store.open_store(feedback_db_path)
    try:
        row = feedback_store.get_row(conn, fid)
    finally:
        conn.close()
    assert row is not None
    assert row.state == "fix-pr-open"
    assert row.fix_pr_url == "https://github.com/amd/gaia/pull/777"
    assert row.regression_test_path == result.regression_test_path
    notes = json.loads(row.notes_json)
    transitions = {n.get("transition") for n in notes}
    assert "pending → triaged" in transitions
    assert "triaged → in-fix" in transitions
    assert "in-fix → fix-pr-open" in transitions


def test_loop_driver_rejects_out_of_scope(
    tmp_git_repo: Path,
    feedback_db_path: Path,
    memory_db_path: Path,
    seed_feedback,
) -> None:
    """A low-confidence triage transitions the row to 'rejected' and stops."""
    fid = seed_feedback(body="vague complaint — can't tell what's broken")
    driver = FeedbackLoopDriver(
        LoopDriverConfig(
            repo_root=tmp_git_repo,
            feedback_db_path=feedback_db_path,
            memory_db_path=memory_db_path,
            em_config={"em_handle": "test-em"},
        ),
        # confidence=20 → out-of-scope escalation.
        triage_client=_triage_client(fix_class="tool", confidence=20),
        gh_runner=_gh_runner_factory(),
        skip_differential_verify=True,
    )
    result = driver.process_pending_feedback()
    assert result.final_state == "rejected"
    assert result.pr is None
    conn = feedback_store.open_store(feedback_db_path)
    try:
        row = feedback_store.get_row(conn, fid)
    finally:
        conn.close()
    assert row is not None
    assert row.state == "rejected"


def test_loop_driver_no_pending(
    tmp_git_repo: Path,
    feedback_db_path: Path,
    memory_db_path: Path,
) -> None:
    """Empty feedback queue returns 'no-pending' cleanly (no side effects)."""
    driver = FeedbackLoopDriver(
        LoopDriverConfig(
            repo_root=tmp_git_repo,
            feedback_db_path=feedback_db_path,
            memory_db_path=memory_db_path,
        ),
        triage_client=_triage_client(),
        gh_runner=_gh_runner_factory(),
    )
    result = driver.process_pending_feedback()
    assert result.final_state == "no-pending"
    assert result.feedback_id is None
