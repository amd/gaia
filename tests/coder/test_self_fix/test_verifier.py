# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for ``gaia.coder.self_fix.verifier``."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gaia.coder.self_fix.verifier import verify_on_merge
from gaia.coder.stores import feedback as feedback_store
from gaia.coder.stores import memory as memory_store


def _seed_feedback_in_fix_state(
    feedback_db_path: Path,
    fid: str = "fb-verified",
    fix_class: str = "tool",
) -> None:
    conn = feedback_store.open_store(feedback_db_path)
    try:
        feedback_store.insert_row(
            conn,
            feedback_store.FeedbackRow(
                id=fid,
                received_at="2026-04-20T00:00:00+00:00",
                from_handle="em",
                channel="cli",
                severity="high",
                body="classify_failure misfires on timestamped errors",
                context_url=None,
                fix_class=fix_class,
                state="fix-pr-open",
                fix_pr_url="https://github.com/amd/gaia/pull/999",
                root_cause="cache key collides on timestamps",
            ),
        )
    finally:
        conn.close()


def test_verify_on_merge_writes_memory_records(
    tmp_git_repo: Path,
    feedback_db_path: Path,
    memory_db_path: Path,
) -> None:
    """After a green regression run, verifier writes failure_patterns + review_patterns."""
    fid = "fb-verified"
    _seed_feedback_in_fix_state(feedback_db_path, fid=fid)

    # Create a passing regression test on the current branch (coder).
    test_dir = tmp_git_repo / "tests" / "coder" / "regression"
    test_dir.mkdir(parents=True, exist_ok=True)
    test_path = test_dir / f"test_{fid.replace('-', '_')}.py"
    test_path.write_text("def test_green():\n    assert True\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", str(test_path)],
        cwd=str(tmp_git_repo),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", "regression test landed"],
        cwd=str(tmp_git_repo),
        check=True,
        capture_output=True,
    )
    merged_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(tmp_git_repo),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    result = verify_on_merge(
        merged_sha=merged_sha,
        feedback_id=fid,
        regression_test_path=str(test_path.relative_to(tmp_git_repo).as_posix()),
        repo_root=tmp_git_repo,
        feedback_db_path=feedback_db_path,
        memory_db_path=memory_db_path,
        checkout_merged=False,  # we are already on the right ref
    )
    assert result.verified is True
    assert result.failure_pattern_id is not None
    assert result.review_pattern_id is not None

    # Feedback row transitioned to 'verified'.
    conn = feedback_store.open_store(feedback_db_path)
    try:
        row = feedback_store.get_row(conn, fid)
    finally:
        conn.close()
    assert row is not None
    assert row.state == "verified"
    assert row.regression_test_path == str(
        test_path.relative_to(tmp_git_repo).as_posix()
    )

    # Memory rows exist under the expected topics.
    mem_conn = memory_store.open_store(memory_db_path)
    try:
        failure_rows = memory_store.list_rows(mem_conn, {"topic": "failure_patterns"})
        review_rows = memory_store.list_rows(mem_conn, {"topic": "review_patterns"})
    finally:
        mem_conn.close()
    assert len(failure_rows) == 1
    assert len(review_rows) == 1
    payload = json.loads(failure_rows[0].payload_json)
    assert payload["fix_class"] == "tool"
    assert payload["merged_sha"] == merged_sha


def test_verify_on_merge_raises_on_failing_test(
    tmp_git_repo: Path,
    feedback_db_path: Path,
    memory_db_path: Path,
) -> None:
    """A regression test that fails post-merge must raise RuntimeError."""
    fid = "fb-red-post-merge"
    _seed_feedback_in_fix_state(feedback_db_path, fid=fid)
    test_dir = tmp_git_repo / "tests" / "coder" / "regression"
    test_dir.mkdir(parents=True, exist_ok=True)
    test_path = test_dir / "test_red.py"
    test_path.write_text(
        "def test_red():\n    assert False, 'regression still live'\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", str(test_path)],
        cwd=str(tmp_git_repo),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", "red test"],
        cwd=str(tmp_git_repo),
        check=True,
        capture_output=True,
    )
    merged_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(tmp_git_repo),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    with pytest.raises(RuntimeError, match="regression test"):
        verify_on_merge(
            merged_sha=merged_sha,
            feedback_id=fid,
            regression_test_path=str(test_path.relative_to(tmp_git_repo).as_posix()),
            repo_root=tmp_git_repo,
            feedback_db_path=feedback_db_path,
            memory_db_path=memory_db_path,
            checkout_merged=False,
        )
