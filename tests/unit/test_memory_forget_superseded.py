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


def test_a_sensitive_newer_version_keeps_older_ones_as_stems(store, tmp_path):
    old = store.store("fact", "User's locker code is 5521 at the gym", context="global")
    new = store.store(
        "fact", "User's locker code changed to 8830", context="global", sensitive=True
    )
    store.update(old, superseded_by=new)

    assert store.forget(new) is True

    rows = _raw_rows(tmp_path / "memory.db", "SELECT content, sensitive FROM forgotten")
    assert len(rows) == 2
    assert all(flag == 1 for _, flag in rows)
    assert "User's locker code is 5521 at the gym" not in [c for c, _ in rows]


def test_forget_terminates_on_a_supersede_cycle(store, tmp_path):
    a = store.store("fact", "User likes green tea", context="global")
    b = store.store("fact", "User prefers oolong tea", context="global")
    store.update(a, superseded_by=b)
    store.update(b, superseded_by=a)

    assert store.forget(a) is True
    assert _raw_rows(tmp_path / "memory.db", "SELECT id FROM knowledge") == []


def test_forget_with_copies_also_erases_a_copys_older_versions(store, tmp_path):
    old = store.store("fact", "User lives in Lisbon", context="global")
    copy = store.store("fact", "User lives in Madrid", context="global")
    store.update(old, superseded_by=copy)
    primary = store.store("fact", "Lives in Madrid.", context="global")

    erased = store.forget_with_copies(primary)

    assert set(erased) == {primary, copy}
    assert _raw_rows(tmp_path / "memory.db", "SELECT id FROM knowledge") == []
