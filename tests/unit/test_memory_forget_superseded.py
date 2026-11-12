# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Forgetting a memory also erases the older versions it superseded (#7266)."""

from __future__ import annotations

import sqlite3

import pytest

from gaia.agents.base.memory_store import MemoryStore


@pytest.fixture
def store(tmp_path):
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield db
    db.close()


def _raw_rows(db_path, sql):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _chain(store, *contents, sensitive=False):
    ids = [
        store.store("fact", c, context="global", sensitive=sensitive) for c in contents
    ]
    for old, new in zip(ids, ids[1:]):
        store.update(old, superseded_by=new)
    return ids


def test_forget_removes_every_superseded_version(store, tmp_path):
    ids = _chain(
        store,
        "User lives in Lisbon",
        "User moved to Porto last spring",
        "User now lives in Berlin near the river",
    )
    unrelated = store.store("fact", "User plays the cello", context="global")

    assert store.forget(ids[-1]) is True

    db = tmp_path / "memory.db"
    remaining = {r[0] for r in _raw_rows(db, "SELECT id FROM knowledge")}
    assert remaining == {unrelated}
    fts = " ".join(r[0] for r in _raw_rows(db, "SELECT content FROM knowledge_fts"))
    for word in ("Lisbon", "Porto", "Berlin"):
        assert word not in fts
    assert store.is_forgotten("User lives in Lisbon")


def test_forget_keeps_only_stems_of_a_sensitive_superseded_version(store, tmp_path):
    ids = _chain(
        store,
        "User's bank PIN is 4417 for checking",
        "User changed the bank PIN to 9902 yesterday",
        sensitive=True,
    )

    assert store.forget(ids[-1]) is True

    db = tmp_path / "memory.db"
    assert _raw_rows(db, "SELECT id FROM knowledge") == []
    forgotten = [r[0] for r in _raw_rows(db, "SELECT content FROM forgotten")]
    assert len(forgotten) == 2
    assert "User's bank PIN is 4417 for checking" not in forgotten


def test_forgetting_an_old_version_leaves_the_newer_one(store, tmp_path):
    ids = _chain(store, "User drives a red bicycle", "User drives a green scooter")

    assert store.forget(ids[0]) is True

    remaining = {
        r[0] for r in _raw_rows(tmp_path / "memory.db", "SELECT id FROM knowledge")
    }
    assert remaining == {ids[1]}


def test_forget_unknown_id_returns_false(store):
    assert store.forget("no-such-id") is False
