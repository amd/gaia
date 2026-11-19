# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An erase on another connection still voids a write that began before it.

The Memory page, another chat, or a sidecar each open their own connection to
memory.db. While that connection's "forget everything" transaction is still
open, its new erase count is invisible to everyone else, so a write that
checked the count then would land right after the erase commits.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager

import pytest

from gaia.agents.base.memory_store import MemoryStore

_GOAL = "a goal the user asked to forget"


@pytest.fixture
def store(tmp_path):
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield db
    db.close()


@contextmanager
def _open_write_on_other_connection(store: MemoryStore, *, erase: bool):
    """Hold a write transaction on a second connection; commit on exit."""
    other = sqlite3.connect(str(store._db_path), timeout=5)
    try:
        other.execute("BEGIN IMMEDIATE")
        if erase:
            other.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (MemoryStore._ERASE_COUNT_KEY, str(store.erase_count() + 1)),
            )
        else:
            other.execute(
                "INSERT INTO meta (key, value) VALUES ('unrelated', '1') "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
            )
        yield
        other.commit()
    finally:
        other.close()


def _race(store: MemoryStore, write, *, erase: bool):
    """Start ``write`` while the other connection's transaction is open."""
    outcome = {}

    def _run():
        try:
            outcome["value"] = write(store.erase_count())
        except Exception as e:  # surfaced by the assertion below
            outcome["error"] = e

    with _open_write_on_other_connection(store, erase=erase):
        thread = threading.Thread(target=_run)
        thread.start()
        time.sleep(0.3)  # the write is now waiting on the other connection
    thread.join(timeout=10)
    assert not thread.is_alive(), "the write never finished"
    assert "error" not in outcome, outcome.get("error")
    return outcome.get("value")


def _count(store: MemoryStore, table: str) -> int:
    return store._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _store_turn(store):
    return lambda count: store.store_turn("s1", "user", _GOAL, erase_count=count)


def _log_tool_call(store):
    return lambda count: store.log_tool_call(
        "s1", "read_file", {"path": "a"}, "ok", True, erase_count=count
    )


def _put_skill(store):
    return lambda count: store.put_skill(
        name="triage",
        when_to_use="Triage a ticket.",
        markdown_body="1. Triage.",
        erase_count=count,
    )


def _mark(store):
    return lambda count: store.mark_sessions_synthesized(
        ["s1"], "distilled", goal=_GOAL, erase_count=count
    )


_WRITES = [
    pytest.param(_store_turn, "conversations", id="store_turn"),
    pytest.param(_log_tool_call, "tool_history", id="log_tool_call"),
    pytest.param(_put_skill, "procedures", id="put_skill"),
    pytest.param(_mark, "synthesis_marks", id="mark_sessions_synthesized"),
]


@pytest.mark.parametrize("make_write,table", _WRITES)
def test_erase_still_open_on_another_connection_drops_the_write(
    store, make_write, table
):
    _race(store, make_write(store), erase=True)

    assert _count(store, table) == 0


@pytest.mark.parametrize("make_write,table", _WRITES)
def test_an_unrelated_write_on_another_connection_keeps_the_write(
    store, make_write, table
):
    _race(store, make_write(store), erase=False)

    assert _count(store, table) == 1


@pytest.mark.parametrize("make_write,table", _WRITES)
def test_a_dropped_write_leaves_no_transaction_open(store, make_write, table):
    stale = store.erase_count()
    store.clear_all()

    make_write(store)(stale)

    assert not store._conn.in_transaction
    assert _count(store, table) == 0
    store.store_turn("s2", "user", "a later turn")
    assert _count(store, "conversations") == 1
