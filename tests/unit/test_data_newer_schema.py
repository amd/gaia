# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Going back to an earlier GAIA reopens data a newer version already changed.

The desktop app and the TUI can go back to the previous release after a bad
update. That release's stores then open databases the newer one migrated, so
each store must keep working on a schema newer than its own: a higher version
number, extra columns, extra tables. This code is the "earlier release" the
next update goes back to.
"""

import sqlite3

from gaia.agents.base import goal_store, memory_store
from gaia.agents.base.goal_store import GoalStore
from gaia.agents.base.memory_store import MemoryStore
from gaia.ui.database import ChatDatabase


def _as_if_newer(path, table, version_table=True, bump_to=None):
    """Change a database the way a later release's migration might."""
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN added_later TEXT NOT NULL DEFAULT ''"
        )
        conn.execute("CREATE TABLE added_later (id INTEGER PRIMARY KEY, note TEXT)")
        if version_table:
            conn.execute("UPDATE schema_version SET version = ?", (bump_to,))
        conn.commit()
    finally:
        conn.close()


def _schema_version(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    finally:
        conn.close()


def test_chat_history_opens_after_a_newer_version_changed_it(tmp_path):
    path = tmp_path / "chat.db"
    db = ChatDatabase(str(path))
    session = db.create_session(title="before going back")
    db.add_message(session["id"], "user", "hello")
    db.close()
    _as_if_newer(path, "messages", version_table=False)

    db = ChatDatabase(str(path))
    try:
        assert [m["content"] for m in db.get_messages(session["id"])] == ["hello"]
        db.add_message(session["id"], "assistant", "still works", ends_turn=True)
        later = db.create_session(title="after going back")
        assert {s["title"] for s in db.list_sessions()} == {
            "before going back",
            "after going back",
        }
        assert later["id"]
    finally:
        db.close()


def test_memory_opens_after_a_newer_version_changed_it(tmp_path):
    path = tmp_path / "memory.db"
    store = MemoryStore(path)
    store.store("fact", "The user likes tea", source="user")
    store.close()
    newer = memory_store._SCHEMA_VERSION + 1
    _as_if_newer(path, "knowledge", bump_to=newer)

    store = MemoryStore(path)
    try:
        assert len(store.search("tea")) == 1
        store.store("fact", "The user also likes coffee", source="user")
        assert len(store.search("coffee")) == 1
    finally:
        store.close()
    assert _schema_version(path) == newer


def test_goals_open_after_a_newer_version_changed_them(tmp_path):
    path = tmp_path / "goals.db"
    store = GoalStore(path)
    store.create_goal("before going back", "a goal")
    store.close()
    newer = goal_store._SCHEMA_VERSION + 1
    _as_if_newer(path, "goals", bump_to=newer)

    store = GoalStore(path)
    try:
        assert [g.title for g in store.list_goals()] == ["before going back"]
        store.create_goal("after going back", "another goal")
        assert len(store.list_goals()) == 2
    finally:
        store.close()
    assert _schema_version(path) == newer
