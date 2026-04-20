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
