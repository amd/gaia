# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for ``gaia.coder.stores`` — §15.1 persistent store DDL + CRUD.

Each SQLite store has three tests: create, round-trip, and a CHECK-constraint
test that proves the DDL constraint rejects invalid enum values. The non-SQL
stores (``paused_tasks``, ``self_edits_log``, ``learnings_log``) each have a
round-trip test plus a schema-rejection test where applicable.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from gaia.coder.stores import (
    audit,
    ci_history,
    create_all_stores,
    em_inbox,
    feedback,
    learnings_log,
    memory,
    paused_tasks,
    self_edits_log,
    spend,
    tasks,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_ISO = "2026-04-20T12:34:56Z"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    )
    return cur.fetchone() is not None


# ---------------------------------------------------------------------------
# em_inbox
# ---------------------------------------------------------------------------


def test_create_em_inbox(tmp_path: Path) -> None:
    conn = em_inbox.open_store(tmp_path / "em_inbox.db")
    try:
        assert _table_exists(conn, "em_inbox")
        index_names = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='em_inbox'"
            )
        }
        assert {"idx_em_inbox_state", "idx_em_inbox_received"}.issubset(index_names)
    finally:
        conn.close()


def test_round_trip_em_inbox(tmp_path: Path) -> None:
    conn = em_inbox.open_store(tmp_path / "em_inbox.db")
    try:
        row = em_inbox.EmInboxRow(
            id="msg_1",
            received_at=_ISO,
            from_handle="kovtcharov-amd",
            channel="cli",
            severity="question",
            body="is the agent up?",
        )
        em_inbox.insert_row(conn, row)

        fetched = em_inbox.get_row(conn, "msg_1")
        assert fetched is not None
        assert fetched.from_handle == "kovtcharov-amd"
        assert fetched.state == "pending"

        em_inbox.update_row(conn, "msg_1", {"state": "answered", "answer": "yes"})
        updated = em_inbox.get_row(conn, "msg_1")
        assert updated is not None
        assert updated.state == "answered"
        assert updated.answer == "yes"

        rows = em_inbox.list_rows(conn, {"state": "answered"})
        assert len(rows) == 1
        assert rows[0].id == "msg_1"
    finally:
        conn.close()


def test_em_inbox_check_constraints(tmp_path: Path) -> None:
    conn = em_inbox.open_store(tmp_path / "em_inbox.db")
    try:
        # Invalid channel value — CHECK should reject.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO em_inbox (id, received_at, from_handle, channel, severity, body) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("bad_1", _ISO, "kovtcharov-amd", "sms", "info", "body"),
            )
        conn.rollback()

        # Invalid severity value — CHECK should reject.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO em_inbox (id, received_at, from_handle, channel, severity, body) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("bad_2", _ISO, "kovtcharov-amd", "cli", "nuclear", "body"),
            )
        conn.rollback()

        # Invalid state value — CHECK should reject.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO em_inbox (id, received_at, from_handle, channel, severity, body, state) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("bad_3", _ISO, "kovtcharov-amd", "cli", "info", "body", "loitering"),
            )
        conn.rollback()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# tasks
# ---------------------------------------------------------------------------


def test_create_tasks(tmp_path: Path) -> None:
    conn = tasks.open_store(tmp_path / "tasks.db")
    try:
        assert _table_exists(conn, "tasks")
    finally:
        conn.close()


def test_round_trip_tasks(tmp_path: Path) -> None:
    conn = tasks.open_store(tmp_path / "tasks.db")
    try:
        row = tasks.TaskRow(
            id="t_1",
            priority=70,
            created_at=_ISO,
            inputs_json=json.dumps({"prompt": "scaffold weather agent"}),
            loop_version=1,
        )
        tasks.insert_row(conn, row)

        fetched = tasks.get_row(conn, "t_1")
        assert fetched is not None
        assert fetched.priority == 70
        assert fetched.cost_usd == 0.0

        tasks.update_row(conn, "t_1", {"state": "running", "cost_usd": 0.23})
        updated = tasks.get_row(conn, "t_1")
        assert updated is not None
        assert updated.state == "running"
        assert updated.cost_usd == pytest.approx(0.23)

        # list_rows ordering: priority DESC, created_at — insert a second row.
        row2 = tasks.TaskRow(
            id="t_2",
            priority=90,
            created_at=_ISO,
            inputs_json="{}",
            loop_version=1,
        )
        tasks.insert_row(conn, row2)
        rows = tasks.list_rows(conn)
        assert [r.id for r in rows] == ["t_2", "t_1"]
    finally:
        conn.close()


def test_tasks_check_constraints(tmp_path: Path) -> None:
    conn = tasks.open_store(tmp_path / "tasks.db")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO tasks (id, created_at, inputs_json, state, loop_version) "
                "VALUES (?, ?, ?, ?, ?)",
                ("bad_state", _ISO, "{}", "interpretive-dance", 1),
            )
        conn.rollback()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# feedback
# ---------------------------------------------------------------------------


def test_create_feedback(tmp_path: Path) -> None:
    conn = feedback.open_store(tmp_path / "feedback.db")
    try:
        assert _table_exists(conn, "feedback")
    finally:
        conn.close()


def test_round_trip_feedback(tmp_path: Path) -> None:
    conn = feedback.open_store(tmp_path / "feedback.db")
    try:
        # Exercise all 8 fix_class values.
        valid_classes = [
            "prompt",
            "doc",
            "test",
            "tool",
            "policy",
            "architectural",
            "state-machine",
            "out-of-scope",
        ]
        for idx, fc in enumerate(valid_classes):
            row = feedback.FeedbackRow(
                id=f"fb_{idx}",
                received_at=_ISO,
                from_handle="kovtcharov-amd",
                channel="cli",
                severity="high",
                body=f"feedback #{idx}",
                fix_class=fc,
            )
            feedback.insert_row(conn, row)

        assert len(feedback.list_rows(conn)) == len(valid_classes)
        assert len(feedback.list_rows(conn, {"fix_class": "doc"})) == 1

        feedback.update_row(conn, "fb_0", {"state": "triaged"})
        triaged = feedback.get_row(conn, "fb_0")
        assert triaged is not None
        assert triaged.state == "triaged"
    finally:
        conn.close()


def test_feedback_check_constraints(tmp_path: Path) -> None:
    conn = feedback.open_store(tmp_path / "feedback.db")
    try:
        # Invalid fix_class — CHECK should reject.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO feedback (id, received_at, from_handle, channel, severity, body, fix_class) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("bad_1", _ISO, "em", "cli", "high", "body", "vibes"),
            )
        conn.rollback()

        # Invalid severity — CHECK should reject.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO feedback (id, received_at, from_handle, channel, severity, body) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("bad_2", _ISO, "em", "cli", "maybe", "body"),
            )
        conn.rollback()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# spend
# ---------------------------------------------------------------------------


def test_create_spend(tmp_path: Path) -> None:
    conn = spend.open_store(tmp_path / "spend.db")
    try:
        assert _table_exists(conn, "spend")
    finally:
        conn.close()


def test_round_trip_spend(tmp_path: Path) -> None:
    conn = spend.open_store(tmp_path / "spend.db")
    try:
        row = spend.SpendRow(
            id="s_1",
            occurred_at=_ISO,
            task_id="t_1",
            call_site="pass_6_adversarial",
            model="claude-opus-4-7",
            input_tokens=1000,
            cache_read_tokens=800,
            cache_create_tokens=200,
            output_tokens=500,
            usd=0.042,
        )
        spend.insert_row(conn, row)

        fetched = spend.get_row(conn, "s_1")
        assert fetched is not None
        assert fetched.cache_read_tokens == 800
        assert fetched.usd == pytest.approx(0.042)

        rows = spend.list_rows(conn, {"task_id": "t_1"})
        assert len(rows) == 1
    finally:
        conn.close()


def test_spend_check_constraints(tmp_path: Path) -> None:
    """``spend`` has no enum CHECK constraints — verify the NOT NULL columns are enforced."""
    conn = spend.open_store(tmp_path / "spend.db")
    try:
        # Missing required `usd` (NOT NULL) should fail.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO spend (id, occurred_at, call_site, model, input_tokens, output_tokens) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("bad_1", _ISO, "triage", "claude-opus-4-7", 100, 50),
            )
        conn.rollback()

        # Missing required `input_tokens` (NOT NULL) should fail.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO spend (id, occurred_at, call_site, model, output_tokens, usd) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("bad_2", _ISO, "triage", "claude-opus-4-7", 50, 0.001),
            )
        conn.rollback()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------


def test_create_audit(tmp_path: Path) -> None:
    conn = audit.open_store(tmp_path / "audit.log.db")
    try:
        assert _table_exists(conn, "audit")
    finally:
        conn.close()


def test_round_trip_audit(tmp_path: Path) -> None:
    conn = audit.open_store(tmp_path / "audit.log.db")
    try:
        row = audit.AuditRow(
            occurred_at=_ISO,
            task_id="t_1",
            stage="Build",
            state_name="edit",
            tool_name="write_file",
            args_json=json.dumps({"path": "foo.py"}),
            result_json=json.dumps({"bytes": 42}),
            duration_ms=17,
            loop_version=1,
        )
        row_id = audit.insert_row(conn, row)
        assert isinstance(row_id, int) and row_id >= 1

        fetched = audit.get_row(conn, row_id)
        assert fetched is not None
        assert fetched.tool_name == "write_file"
        assert fetched.error is None

        audit.update_row(conn, row_id, {"error": "TimeoutError"})
        err = audit.get_row(conn, row_id)
        assert err is not None and err.error == "TimeoutError"

        rows = audit.list_rows(conn, {"task_id": "t_1"})
        assert len(rows) == 1
    finally:
        conn.close()


def test_audit_check_constraints(tmp_path: Path) -> None:
    """``audit`` has no CHECK enums; verify NOT NULL columns + AUTOINCREMENT semantics."""
    conn = audit.open_store(tmp_path / "audit.log.db")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO audit (occurred_at, tool_name, loop_version) "
                "VALUES (?, ?, ?)",
                (_ISO, "write_file", 1),
            )
        conn.rollback()

        # Two inserts should get strictly-increasing ids (AUTOINCREMENT semantics).
        first = audit.insert_row(
            conn,
            audit.AuditRow(
                occurred_at=_ISO,
                tool_name="read_file",
                args_json="{}",
                loop_version=1,
            ),
        )
        second = audit.insert_row(
            conn,
            audit.AuditRow(
                occurred_at=_ISO,
                tool_name="read_file",
                args_json="{}",
                loop_version=1,
            ),
        )
        assert second > first
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# ci_history
# ---------------------------------------------------------------------------


def test_create_ci_history(tmp_path: Path) -> None:
    conn = ci_history.open_store(tmp_path / "ci_history.db")
    try:
        assert _table_exists(conn, "ci_history")
    finally:
        conn.close()


def test_round_trip_ci_history(tmp_path: Path) -> None:
    conn = ci_history.open_store(tmp_path / "ci_history.db")
    try:
        row = ci_history.CiHistoryRow(
            workflow_name="ci.yml",
            branch="feature/x",
            run_id=42,
            started_at=_ISO,
        )
        ci_history.insert_row(conn, row)

        fetched = ci_history.get_row(conn, "ci.yml", "feature/x", 42)
        assert fetched is not None
        assert fetched.duration_s is None

        ci_history.update_row(
            conn,
            "ci.yml",
            "feature/x",
            42,
            {"completed_at": _ISO, "duration_s": 300, "conclusion": "success"},
        )
        updated = ci_history.get_row(conn, "ci.yml", "feature/x", 42)
        assert updated is not None
        assert updated.duration_s == 300
        assert updated.conclusion == "success"

        rows = ci_history.list_rows(conn, {"workflow_name": "ci.yml"})
        assert len(rows) == 1
    finally:
        conn.close()


def test_ci_history_check_constraints(tmp_path: Path) -> None:
    """``ci_history`` has no CHECK enums; verify compound PK uniqueness is enforced."""
    conn = ci_history.open_store(tmp_path / "ci_history.db")
    try:
        row = ci_history.CiHistoryRow(
            workflow_name="ci.yml",
            branch="main",
            run_id=1,
            started_at=_ISO,
        )
        ci_history.insert_row(conn, row)
        # Duplicate PK — must fail.
        with pytest.raises(sqlite3.IntegrityError):
            ci_history.insert_row(conn, row)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# memory
# ---------------------------------------------------------------------------


def test_create_memory(tmp_path: Path) -> None:
    conn = memory.open_store(tmp_path / "memory.db")
    try:
        assert _table_exists(conn, "memory")
    finally:
        conn.close()


def test_round_trip_memory(tmp_path: Path) -> None:
    conn = memory.open_store(tmp_path / "memory.db")
    try:
        row = memory.MemoryRow(
            id="m_1",
            topic="review_patterns",
            created_at=_ISO,
            source_kind="pr",
            source_id="pr_941",
            payload_json=json.dumps({"pattern": "always cite file:line"}),
            embedding_key="faiss_0001",
            confidence=85,
        )
        memory.insert_row(conn, row)

        fetched = memory.get_row(conn, "m_1")
        assert fetched is not None
        assert fetched.topic == "review_patterns"
        assert fetched.recall_count == 0

        memory.update_row(
            conn,
            "m_1",
            {"last_recalled_at": _ISO, "recall_count": 1},
        )
        updated = memory.get_row(conn, "m_1")
        assert updated is not None
        assert updated.recall_count == 1

        rows = memory.list_rows(conn, {"topic": "review_patterns"})
        assert len(rows) == 1
    finally:
        conn.close()


def test_memory_check_constraints(tmp_path: Path) -> None:
    conn = memory.open_store(tmp_path / "memory.db")
    try:
        # Invalid topic — CHECK should reject.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO memory (id, topic, created_at, source_kind, payload_json, embedding_key) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("bad_1", "launch_codes", _ISO, "pr", "{}", "faiss_x"),
            )
        conn.rollback()

        # confidence out of range — CHECK should reject.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO memory (id, topic, created_at, source_kind, payload_json, embedding_key, confidence) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("bad_2", "review_patterns", _ISO, "pr", "{}", "faiss_x", 101),
            )
        conn.rollback()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# paused_tasks
# ---------------------------------------------------------------------------


def test_round_trip_paused_tasks(tmp_path: Path) -> None:
    root = tmp_path / "paused-tasks"
    data = {
        "task_id": "t_abc",
        "paused_at": _ISO,
        "reason": "self-bug",
        "resume_hint": "re-enter state=edit",
        "cwd": "/tmp/gaia",
        "original_prompt": "scaffold agent",
        "tool_call_history": [],
        "partial_outputs": {},
        "context_hash": "sha256-deadbeef",
        "loop_version": 1,
        "stage_at_pause": "Build",
        "state_at_pause": "edit",
    }
    written = paused_tasks.write_snapshot(root, "t_abc", data)
    assert written.exists()
    assert written.name == "t_abc.json"

    read = paused_tasks.read_snapshot(root, "t_abc")
    assert read == data

    assert paused_tasks.list_snapshots(root) == ["t_abc"]


def test_paused_tasks_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        paused_tasks.write_snapshot(tmp_path, "../escape", {})
    with pytest.raises(ValueError):
        paused_tasks.write_snapshot(tmp_path, "a/b", {})


def test_paused_tasks_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        paused_tasks.read_snapshot(tmp_path, "never_written")


# ---------------------------------------------------------------------------
# self_edits_log
# ---------------------------------------------------------------------------


def test_round_trip_self_edits_log(tmp_path: Path) -> None:
    path = tmp_path / "self-edits.log"
    record = self_edits_log.SelfEditRecord(
        ts=_ISO,
        pr="https://github.com/amd/gaia/pull/941",
        fix_class="prompt",
        files=["src/gaia/coder/GAIA.md"],
        before_sha="abc123",
        after_sha="def456",
        review_passes=self_edits_log.ReviewPasses(
            static="pass",
            functional="pass",
            arch="pass",
            security="pass",
            prose="pass",
            adversarial="pass",
            feedback_binding="pass",
        ),
        confidence=94,
        em_review="approved",
        auto_merged=False,
        feedback_id="fb_01HA",
    )
    self_edits_log.append(path, record)
    self_edits_log.append(path, record)  # append again — log must grow, not overwrite.

    records = self_edits_log.read_all(path)
    assert len(records) == 2
    assert records[0].pr == "https://github.com/amd/gaia/pull/941"
    assert records[0].review_passes.adversarial == "pass"


def test_self_edits_log_rejects_bad_schema(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        self_edits_log.append_dict(
            tmp_path / "self-edits.log",
            {"ts": _ISO, "pr": "x", "fix_class": "prompt"},  # missing required fields
        )


# ---------------------------------------------------------------------------
# learnings_log
# ---------------------------------------------------------------------------


def test_round_trip_learnings_log(tmp_path: Path) -> None:
    path = tmp_path / "learnings.log"
    entry = learnings_log.LearningEntry(
        ts="2026-05-03",
        task_id="t_8f2",
        observation="I added a defensive try/except that the EM rejected",
        promotion_candidate="GAIA.md",
    )
    learnings_log.append(path, entry)
    learnings_log.append(
        path,
        learnings_log.LearningEntry(
            ts="2026-05-04",
            task_id="t_914",
            observation="EM prefers PR bodies that cite the exact CLAUDE.md rule",
            promotion_candidate="skill:em-kalin-preferences",
        ),
    )

    entries = learnings_log.read_all(path)
    assert len(entries) == 2
    assert entries[0].promotion_candidate == "GAIA.md"
    assert entries[1].promotion_candidate == "skill:em-kalin-preferences"

    # Human-readable single-line format is preserved on disk.
    lines = path.read_text().splitlines()
    assert lines[0].startswith("2026-05-03 [task=t_8f2] Observed: ")
    assert lines[0].endswith("→ GAIA.md")


def test_learnings_log_rejects_bad_candidate() -> None:
    with pytest.raises(ValidationError):
        learnings_log.LearningEntry(
            ts=_ISO,
            task_id="t_1",
            observation="x",
            promotion_candidate="promote",  # invalid
        )


def test_learnings_log_rejects_multiline_observation() -> None:
    with pytest.raises(ValidationError):
        learnings_log.LearningEntry(
            ts=_ISO,
            task_id="t_1",
            observation="line one\nline two",
            promotion_candidate="dismissed",
        )


# ---------------------------------------------------------------------------
# create_all_stores
# ---------------------------------------------------------------------------


def test_create_all_stores(tmp_path: Path) -> None:
    paths = create_all_stores(tmp_path)

    expected_keys = {
        "em_inbox",
        "tasks",
        "feedback",
        "spend",
        "audit",
        "ci_history",
        "memory",
        "paused_tasks",
        "self_edits_log",
        "learnings_log",
    }
    assert set(paths.keys()) == expected_keys

    # Every SQLite db exists on disk with its expected table.
    sqlite_stores = {
        "em_inbox": "em_inbox",
        "tasks": "tasks",
        "feedback": "feedback",
        "spend": "spend",
        "audit": "audit",
        "ci_history": "ci_history",
        "memory": "memory",
    }
    for store_name, table in sqlite_stores.items():
        db_path = paths[store_name]
        assert db_path.exists()
        conn = sqlite3.connect(db_path)
        try:
            assert _table_exists(conn, table)
        finally:
            conn.close()

    # paused-tasks is a directory; the log files are not yet materialised but
    # their parents exist.
    assert paths["paused_tasks"].is_dir()
    assert paths["self_edits_log"].parent.exists()
    assert paths["learnings_log"].parent.exists()

    # Idempotent re-run.
    second = create_all_stores(tmp_path)
    assert second == paths
