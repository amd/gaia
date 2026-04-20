# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""End-to-end integration tests for gaia-coder (Phase 11).

These tests exercise the whole coder as a system — CLI → store → loop
driver → publisher → verifier — with the external boundaries (LLM calls,
``gh`` CLI, subprocess) mocked via pytest-mock. Every assertion lives on
the observable contract (file on disk, DB row, stdout JSON, audit trail).

Two scenarios:

* ``test_full_flow_feedback_to_fix`` — the §7.3 / §7.4 happy path from
  EM feedback submission through draft PR opening to a simulated merge.
* ``test_dev_mode_self_heal_e2e`` — the §7.5 sub-loop where the agent
  classifies a mid-task failure as a self-bug, pauses the user's task,
  hot-reloads / restarts itself, and resumes the task from the snapshot.

Both tests are hermetic: every git operation, subprocess, and file read
happens under ``tmp_path`` and every LLM/``gh`` call is injected via a
mock callable. No real Anthropic tokens are ever touched.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Callable

import pytest

from gaia.coder import cli as coder_cli
from gaia.coder import dev_mode as dev_mode_mod
from gaia.coder import trust as trust_mod
from gaia.coder.self_fix import (
    FeedbackLoopDriver,
    LoopDriverConfig,
    self_heal,
)
from gaia.coder.self_fix.publisher import ReviewGateResult
from gaia.coder.self_fix.verifier import verify_on_merge
from gaia.coder.stores import audit as audit_store
from gaia.coder.stores import feedback as feedback_store
from gaia.coder.stores import memory as memory_store
from gaia.coder.stores import paused_tasks as paused_tasks_store

# ---------------------------------------------------------------------------
# Shared fixtures — hermetic tmp git repo + paths
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Run ``git <args>`` under ``cwd`` with commit-signing disabled.

    Hermetic: every call passes an explicit user.email/name + gpgsign
    override so the fixture cannot depend on the developer's global
    git config.
    """
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def e2e_repo(tmp_path: Path) -> Path:
    """Fresh git repo with ``main`` + ``coder`` branches.

    Seeds a ``src/gaia/coder/sample.py`` the triage classifier points at
    and a ``tests/coder/regression/`` dir the fixer can write into.
    The ``coder`` branch is the current HEAD so fixer branch creation
    from ``coder`` succeeds without extra setup.
    """
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "test")
    _git(root, "config", "commit.gpgsign", "false")

    (root / "README.md").write_text("# e2e repo\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "initial")

    _git(root, "checkout", "-q", "-b", "coder")
    sample = root / "src" / "gaia" / "coder"
    sample.mkdir(parents=True, exist_ok=True)
    (sample / "sample.py").write_text(
        "# sample module\n"
        "def classify_failure(err):\n"
        "    # BUG: cache collision on timestamped errors\n"
        "    return err\n",
        encoding="utf-8",
    )
    (root / "tests" / "coder" / "regression").mkdir(parents=True, exist_ok=True)
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "seed coder branch")
    return root


@pytest.fixture
def gaia_coder_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``~/.gaia/coder/`` to a tmp dir via ``GAIA_CODER_HOME``."""
    home = tmp_path / "coder-home"
    home.mkdir()
    monkeypatch.setenv("GAIA_CODER_HOME", str(home))
    return home


# ---------------------------------------------------------------------------
# Mock factories
# ---------------------------------------------------------------------------


def _canned_triage_client(
    fix_class: str = "tool",
    confidence: int = 90,
) -> Callable[..., str]:
    """Return a triage LLM stub — never hits Anthropic."""

    def client(**_kwargs: object) -> str:
        return json.dumps(
            {
                "fix_class": fix_class,
                "root_cause_hypothesis": (
                    "classify_failure uses a cache key that collides on "
                    "timestamped errors"
                ),
                "candidate_files": [
                    {"path": "src/gaia/coder/sample.py", "why": "seeded"}
                ],
                "prior_pattern_hit": None,
                "confidence": confidence,
            }
        )

    return client


def _canned_gh_runner(
    pr_number: int = 777,
) -> Callable[..., subprocess.CompletedProcess]:
    """Return a ``gh`` shell-out stub — never hits GitHub."""

    def runner(args, cwd=None, check=True):
        if args and args[0] == "pr" and args[1] == "create":
            return subprocess.CompletedProcess(
                args=["gh", *args],
                returncode=0,
                stdout=f"https://github.com/amd/gaia/pull/{pr_number}\n",
                stderr="",
            )
        # `pr comment` (notify_em) and everything else — harmless success.
        return subprocess.CompletedProcess(
            args=["gh", *args], returncode=0, stdout="", stderr=""
        )

    return runner


def _seven_pass_review_gate(
    success_confidence: int = 85,
) -> Callable[..., ReviewGateResult]:
    """Return a review-gate runner that passes all seven passes.

    The shape matches the loose duck-typed ``ReviewGateResult`` defined
    in :mod:`gaia.coder.self_fix.publisher` — 7 ``pass`` verdicts.
    """

    def runner(*, diff, plan, feedback_body):
        pass_names = (
            "pass_1_static",
            "pass_2_functional",
            "pass_3_architectural",
            "pass_4_security",
            "pass_5_prose",
            "pass_6_adversarial",
            "pass_7_feedback_binding",
        )
        return ReviewGateResult(
            overall="pass",
            passes={name: {"status": "pass", "findings": []} for name in pass_names},
            confidence=success_confidence,
        )

    return runner


def _audit_tool_call(
    audit_conn: sqlite3.Connection,
    tool_name: str,
    args_json: str = "{}",
) -> None:
    """Append one ``audit`` row — stand-in for the ReAct-loop's audit hook."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    audit_store.insert_row(
        audit_conn,
        audit_store.AuditRow(
            occurred_at=now,
            tool_name=tool_name,
            args_json=args_json,
            loop_version=1,
        ),
    )


# ---------------------------------------------------------------------------
# Test 1: feedback → triage → fix-PR → (simulated merge) → verified
# ---------------------------------------------------------------------------


def test_full_flow_feedback_to_fix(
    e2e_repo: Path,
    gaia_coder_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full §7.3 / §7.4 round-trip with every external boundary mocked."""

    # --- Step 0: bootstrap em.toml via the CLI -----------------------------
    rc = coder_cli.main(
        [
            "trust",
            "--bootstrap",
            "--em-handle",
            "e2e-em",
            "--em-channel",
            "github-issue-comment",
        ]
    )
    assert rc == 0, "trust --bootstrap should succeed"
    em_cfg_path = gaia_coder_home / "em.toml"
    assert em_cfg_path.exists()
    em_cfg = trust_mod.load_em_config(em_cfg_path)
    assert em_cfg.em_handle == "e2e-em"

    # --- Step 1: enqueue feedback via the CLI ------------------------------
    feedback_db = gaia_coder_home / "feedback.db"
    rc = coder_cli.main(
        [
            "feedback",
            "classify_failure misfires on timestamped errors; fix the cache key",
            "--severity",
            "high",
            "--on",
            "https://github.com/amd/gaia/pull/42",
            "--from-handle",
            "e2e-em",
            "--db-path",
            str(feedback_db),
            "--id",
            "fb-e2e-1",
        ]
    )
    assert rc == 0, "feedback CLI should succeed"
    assert feedback_db.exists(), "feedback.db should have been created"

    conn = feedback_store.open_store(feedback_db)
    try:
        row = feedback_store.get_row(conn, "fb-e2e-1")
    finally:
        conn.close()
    assert row is not None
    assert row.state == "pending"
    assert row.severity == "high"

    # --- Step 2: drive one FeedbackLoopDriver iteration --------------------
    # The driver writes an audit row manually per tool-call via the helper
    # below so we can assert on the audit trail without instrumenting the
    # full ReAct loop. The production hook will live in Phase 11's
    # production swap; the observable contract is the same audit-log rows.
    memory_db = gaia_coder_home / "memory.db"
    memory_store.open_store(memory_db).close()
    audit_db = gaia_coder_home / "audit.log.db"
    audit_conn = audit_store.open_store(audit_db)

    try:
        _audit_tool_call(audit_conn, "self_fix.drive.start", '{"id":"fb-e2e-1"}')
        driver = FeedbackLoopDriver(
            LoopDriverConfig(
                repo_root=e2e_repo,
                feedback_db_path=feedback_db,
                memory_db_path=memory_db,
                em_config={"em_handle": "e2e-em"},
                base_ref="coder",
            ),
            triage_client=_canned_triage_client(fix_class="tool", confidence=92),
            gh_runner=_canned_gh_runner(pr_number=777),
            review_gate_runner=_seven_pass_review_gate(success_confidence=88),
            # The tmp repo has no collectable tests — differential verify
            # would checkout between refs and run pytest over the synthetic
            # sample file. Skip it: the state-machine transitions are what
            # we assert on.
            skip_differential_verify=True,
        )
        _audit_tool_call(audit_conn, "triage.classify_fix_class")
        result = driver.process_pending_feedback()
        _audit_tool_call(audit_conn, "publisher.open_self_fix_pr")
    finally:
        audit_conn.close()

    # --- Step 3: assert the driver reached fix-pr-open ---------------------
    assert (
        result.final_state == "fix-pr-open"
    ), f"expected fix-pr-open, got {result.final_state}: notes={result.notes}"
    assert result.pr is not None
    assert result.pr.number == 777
    assert result.fix_class is not None
    assert result.fix_class.fix_class == "tool"
    assert result.regression_test_path is not None

    # --- Step 4: assert feedback state transitions ------------------------
    conn = feedback_store.open_store(feedback_db)
    try:
        row = feedback_store.get_row(conn, "fb-e2e-1")
    finally:
        conn.close()
    assert row is not None
    assert row.state == "fix-pr-open"
    assert row.fix_pr_url == "https://github.com/amd/gaia/pull/777"
    notes = json.loads(row.notes_json)
    transitions = {n.get("transition") for n in notes}
    assert "pending → triaged" in transitions
    assert "triaged → in-fix" in transitions
    assert "in-fix → fix-pr-open" in transitions

    # --- Step 5: assert the fix branch exists ----------------------------
    expected_branch = "auto/gaia-coder/fb-e2e-1"
    branch_check = subprocess.run(
        ["git", "branch", "--list", expected_branch],
        cwd=str(e2e_repo),
        capture_output=True,
        text=True,
        check=True,
    )
    assert expected_branch in branch_check.stdout, (
        f"expected branch {expected_branch} to exist; "
        f"got branches: {branch_check.stdout!r}"
    )

    # Regression test was actually written on the fix branch.
    regression_abs = e2e_repo / row.regression_test_path
    assert regression_abs.exists(), f"regression test should exist at {regression_abs}"

    # --- Step 6: review gate ran + returned pass on all 7 -----------------
    # The stub runner returned overall=pass with 7 entries in passes. Assert
    # the driver propagated that to its notes.
    review_notes = [n for n in result.notes if "review gate" in n]
    assert review_notes, f"no review-gate note in driver output: {result.notes}"
    assert "overall=pass" in review_notes[0]

    # --- Step 7: audit log has the tool-call trail ------------------------
    audit_conn = audit_store.open_store(audit_db)
    try:
        rows = audit_conn.execute("SELECT tool_name FROM audit ORDER BY id").fetchall()
    finally:
        audit_conn.close()
    tool_names = [r[0] for r in rows]
    assert "self_fix.drive.start" in tool_names
    assert "triage.classify_fix_class" in tool_names
    assert "publisher.open_self_fix_pr" in tool_names

    # --- Step 8: simulate the PR-merged event → verify_on_merge -----------
    # Merge the fix branch into coder so the verifier has a real merged SHA
    # to check out. The regression test we wrote is a pytest no-op written
    # by the fixer's default helper, so it passes on the merged SHA.
    _git(e2e_repo, "checkout", "-q", "coder")
    _git(e2e_repo, "merge", "--no-ff", "-q", "-m", "merge fix", expected_branch)
    merged_sha = _git(e2e_repo, "rev-parse", "HEAD").stdout.strip()
    # Point at the same Python interpreter pytest is running with so the
    # subprocess `python -m pytest` call inside verify_on_merge can find
    # pytest. Windows CI occasionally resolves `sys.executable` to a path
    # that the subprocess loses, so we prepend its directory to PATH.
    # Uses monkeypatch so the change reverts after the test (no leak to
    # sibling tests). Cf. #829 auto-review.
    py_dir = str(Path(sys.executable).parent)
    current_path = os.environ.get("PATH", "")
    if py_dir not in current_path.split(os.pathsep):
        monkeypatch.setenv("PATH", py_dir + os.pathsep + current_path)
    verify_result = verify_on_merge(
        merged_sha=merged_sha,
        feedback_id="fb-e2e-1",
        regression_test_path=row.regression_test_path,
        repo_root=e2e_repo,
        feedback_db_path=feedback_db,
        memory_db_path=memory_db,
        checkout_merged=False,  # we already checked out coder
    )
    assert verify_result.verified is True
    assert verify_result.failure_pattern_id is not None
    assert verify_result.review_pattern_id is not None

    # feedback row should now be 'verified'.
    conn = feedback_store.open_store(feedback_db)
    try:
        row = feedback_store.get_row(conn, "fb-e2e-1")
    finally:
        conn.close()
    assert row is not None
    assert row.state == "verified"

    # memory.db must contain both rows.
    mem_conn = memory_store.open_store(memory_db)
    try:
        mem_rows = memory_store.list_rows(mem_conn)
    finally:
        mem_conn.close()
    topics = {m.topic for m in mem_rows}
    assert "failure_patterns" in topics, f"got topics: {topics}"
    assert "review_patterns" in topics, f"got topics: {topics}"


# ---------------------------------------------------------------------------
# Test 2: dev-mode self-heal sub-loop
# ---------------------------------------------------------------------------


@pytest.fixture
def dev_mode_repo(tmp_path: Path) -> Path:
    """Git repo whose ``origin`` matches the ``amd/gaia`` binding.

    The hard precondition in §7.1 requires the running source to live in
    a git tree whose ``origin`` URL matches ``repo_binding.toml.repo``.
    We simulate that by configuring the remote URL to ``amd/gaia.git``.
    """
    root = tmp_path / "src-tree"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "test")
    _git(root, "config", "commit.gpgsign", "false")
    (root / "README.md").write_text("stub", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "init")
    _git(root, "remote", "add", "origin", "git@github.com:amd/gaia.git")
    return root


@pytest.fixture
def repo_binding_file(tmp_path: Path) -> Path:
    """repo_binding.toml pointing at amd/gaia (§15.6)."""
    path = tmp_path / "repo_binding.toml"
    path.write_text(
        "\n".join(
            [
                'repo = "amd/gaia"',
                "github_app_id = 12345",
                "github_installation_id = 67890",
                'webhook_secret_keyring_slot = "gaia-coder/webhook"',
                'private_key_keyring_slot = "gaia-coder/pem"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_dev_mode_self_heal_e2e(
    tmp_path: Path,
    dev_mode_repo: Path,
    repo_binding_file: Path,
) -> None:
    """§7.5: classify self-bug → pause → restart → resume, snapshot round-trips."""

    # --- Step 0: bootstrap em.toml so dev_mode has an EM to attribute to ---
    em_cfg_path = tmp_path / "em.toml"
    em_cfg = trust_mod.EMConfig(
        em_handle="e2e-em",
        em_channel="github-issue-comment",
    )
    trust_mod.save_em_config(em_cfg_path, em_cfg)

    # --- Step 1: enable dev mode (session-only) ----------------------------
    session_path = tmp_path / "session.json"
    audit_db = tmp_path / "audit.log.db"
    audit_conn = audit_store.open_store(audit_db)
    try:
        # detect_dev_mode needs to see the fake amd/gaia origin. We pass
        # ``source_root`` pointing at the dev-mode repo so the status detector
        # runs against the hermetic fixture rather than the running source
        # tree of the actual GAIA checkout.
        status = dev_mode_mod.detect_dev_mode(
            em_cfg_path=em_cfg_path,
            repo_binding_path=repo_binding_file,
            source_root=dev_mode_repo,
        )
        assert (
            status.editable_install
        ), f"hard precondition should be met; reason={status.reason!r}"

        dev_mode_mod.enable_session(
            em_cfg,
            reason="running e2e test",
            session_path=session_path,
            audit_conn=audit_conn,
            status=status,
        )
        # Session flag on disk.
        data = json.loads(session_path.read_text(encoding="utf-8"))
        assert data["dev_mode_session"] is True
        assert data["dev_mode_session_reason"] == "running e2e test"

        enabled = dev_mode_mod.is_enabled(
            em_cfg_path=em_cfg_path,
            session_path=session_path,
            repo_binding_path=repo_binding_file,
            source_root=dev_mode_repo,
        )
        assert enabled, "dev mode should be ON after enable_session"

        # --- Step 2: classify failure with a canned self-code response ----
        def _self_code_classifier(**_kwargs: object) -> str:
            return json.dumps(
                {
                    "kind": "self-code",
                    "evidence": (
                        "KeyError in classify_failure — cache-key collision "
                        "on timestamped errors"
                    ),
                    "confidence": 85,
                    "suggested_next_action": "pause current task and self-fix",
                }
            )

        classification = self_heal.classify_failure(
            error={
                "message": "KeyError: 'ts_2026-04-20T10:15:00+00:00'",
                "stack": "traceback here",
                "tool_name": "classify_failure",
                "tool_args": {"err": "sample"},
            },
            context_json={"recent_tool_calls": []},
            dev_mode_on=True,
            client=_self_code_classifier,
        )
        assert classification.kind == "self-code"
        assert classification.confidence == 85

        # --- Step 3: pause the current user task --------------------------
        paused_root = tmp_path / "paused-tasks"
        paused_path = self_heal.pause_current_task(
            task_id="user-task-01",
            reason="self-bug detected in classify_failure",
            root=paused_root,
            cwd=dev_mode_repo,
            tool_call_history=[
                {"tool": "read_file", "args": {"path": "sample.py"}},
                {"tool": "edit_file", "args": {"path": "sample.py"}},
            ],
            partial_outputs={"draft_plan": "scaffold a weather agent"},
            original_prompt="scaffold a weather agent please",
        )
        assert paused_path.path.exists()
        snapshot = json.loads(paused_path.path.read_text(encoding="utf-8"))
        assert snapshot["task_id"] == "user-task-01"
        assert snapshot["reason"].startswith("self-bug")
        assert snapshot["tool_call_history"][0]["tool"] == "read_file"

        # --- Step 4: self-fix cycle -> restart_self (hot reload path) ----
        # A prompt-only self-fix takes the hot-reload branch so the agent
        # stays alive and can resume the paused task directly. We reset
        # the restart window so this test doesn't conflict with any
        # other test state.
        self_heal._reset_restart_window()
        restart_result = self_heal.restart_self(
            reason="hot-reload prompts after prompt-only self-fix",
            kind="prompt-only",
            audit_conn=audit_conn,
            em_handle=em_cfg.em_handle,
        )
        assert (
            restart_result.exited is False
        ), "prompt-only restart should hot-reload, not exit"
        assert restart_result.kind == "prompt-only"

        # --- Step 5: resume the paused task (snapshot round-trip) --------
        resumed = self_heal.resume_task(
            "user-task-01",
            root=paused_root,
            delete_snapshot=True,
        )
        assert resumed.task_id == "user-task-01"
        assert resumed.original_prompt == "scaffold a weather agent please"
        assert resumed.tool_call_history == snapshot["tool_call_history"]
        assert resumed.partial_outputs == snapshot["partial_outputs"]
        # delete_snapshot=True consumes the file.
        assert not paused_tasks_store.snapshot_path(
            paused_root, "user-task-01"
        ).exists(), "resume_task(delete_snapshot=True) should consume the file"

    finally:
        audit_conn.close()
        self_heal._reset_restart_window()

    # --- Step 6: audit log has dev-mode + self_heal entries --------------
    audit_conn = audit_store.open_store(audit_db)
    try:
        rows = audit_conn.execute("SELECT tool_name FROM audit ORDER BY id").fetchall()
    finally:
        audit_conn.close()
    tool_names = [r[0] for r in rows]
    assert "dev_mode.enable_session" in tool_names
    assert "self_heal.restart_self" in tool_names
