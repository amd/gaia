# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Forgetting reaches what the user cannot see: sensitive rows and old turns.

"Forget everything" deleted each row a filterless recall listed, but that
listing holds back sensitive rows, so a stored PIN survived while the agent
said memory was empty. The next session then rebuilt the whole profile, the
forgotten trip included, from past conversations. Runs against a real
``MemoryStore``; only the model is stubbed.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from gaia.agents.base.agent import TOOLS_REQUIRING_CONFIRMATION
from gaia.agents.base.memory import EMBEDDING_DIM
from gaia.agents.base.memory_store import MemoryStore
from tests.unit.test_memory_forget_sticks import _Host as _BaseHost
from tests.unit.test_memory_forget_sticks import _tool


class _Host(_BaseHost):
    _embedding_dim = EMBEDDING_DIM


@pytest.fixture
def store(tmp_path):
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield db
    db.close()


def test_a_filterless_recall_says_what_it_held_back(store):
    _Host(store, [])
    store.store(category="fact", content="User's name is Jordan")
    store.store(category="fact", content="User's bank PIN is 4821", sensitive=True)

    out = _tool("recall")()

    assert out["count"] == 1
    assert out["sensitive_withheld"] == 1
    assert "category" in out["note"]
    assert _tool("recall")(category="fact")["count"] == 2
    assert _tool("recall")(time_from="2000-01-01")["count"] == 2


def test_forget_everything_takes_sensitive_rows_and_past_conversations(store):
    _Host(store, [])
    store.store(category="fact", content="User's name is Jordan")
    store.store(category="fact", content="User's bank PIN is 4821", sensitive=True)
    store.store_turn("s-old", "user", "I'm Jordan and my trip to Lisbon is in December")

    out = _tool("forget_everything")()

    assert out["status"] == "cleared"
    assert store._conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0] == 0
    assert store._conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 0
    assert _tool("search_past_conversations")(query="Lisbon")["count"] == 0


def test_forget_everything_needs_the_users_approval():
    assert "forget_everything" in TOOLS_REQUIRING_CONFIRMATION


def test_a_forgotten_memory_does_not_come_back_from_old_turns(store):
    _Host(store, [])
    store.store_turn("s-old", "user", "My trip to Lisbon is December 10 to 17 with Sam")
    store.forget(
        store.store(
            category="fact", content="Trip to Lisbon December 10 to 17 with Sam"
        )
    )

    out = _tool("search_past_conversations")(query="Lisbon")

    assert out["count"] == 0
    assert out["forgotten_hidden"] == 1


def test_something_said_again_after_forgetting_is_found(store):
    _Host(store, [])
    store.forget(
        store.store(
            category="fact", content="Trip to Lisbon December 10 to 17 with Sam"
        )
    )
    # A forget in the turn's own clock tick still applies, so the user says it later.
    earlier = (datetime.now().astimezone() - timedelta(seconds=1)).isoformat()
    store._conn.execute("UPDATE forgotten SET forgotten_at = ?", (earlier,))
    store.store_turn(
        "s-new", "user", "New plan: Lisbon December 10 to 17 with Sam after all"
    )

    out = _tool("search_past_conversations")(query="Lisbon")

    assert out["count"] == 1
    assert "forgotten_hidden" not in out


def test_learning_queued_before_forget_everything_does_not_refill_memory(store):
    host = _Host(store, [])
    said = datetime.now().astimezone().isoformat()
    _tool("forget_everything")()

    applied = host._execute_extraction_operations(
        [{"op": "add", "category": "fact", "content": "User's name is Jordan"}],
        existing_items=[],
        said_at=said,
    )

    assert applied == 0
    assert store._conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0] == 0


def test_something_said_after_forget_everything_is_learned(store):
    host = _Host(store, [])
    _tool("forget_everything")()
    said = (datetime.now().astimezone() + timedelta(seconds=1)).isoformat()

    applied = host._execute_extraction_operations(
        [{"op": "add", "category": "fact", "content": "User's name is Jordan"}],
        existing_items=[],
        said_at=said,
    )

    assert applied == 1


def _on_disk(tmp_path) -> bytes:
    return b"".join(p.read_bytes().lower() for p in tmp_path.glob("memory.db*"))


def test_forget_everything_leaves_nothing_readable_on_disk(store, tmp_path):
    _Host(store, [])
    other = MemoryStore(db_path=tmp_path / "memory.db")  # the UI's own connection
    for i in range(50):
        store.store(
            category="fact", content=f"Trip to Lisbon number {i} at QQZHOTEL{i}"
        )
        store.store_turn("s-old", "user", f"My trip to Lisbon {i} is in December " * 5)
    store.store(category="fact", content="User's bank PIN is 4821", sensitive=True)
    other.get_all_knowledge(limit=5)
    assert b"lisbon" in _on_disk(tmp_path)

    _tool("forget_everything")()

    raw = _on_disk(tmp_path)
    for gone in (b"lisbon", b"qqzhotel", b"4821"):
        assert gone not in raw, f"{gone!r} still readable in the database files"
    assert other.get_all_knowledge(limit=5)["total"] == 0
    other.close()


def test_forget_everything_says_so_when_another_reader_blocks_the_scrub(
    store, tmp_path
):
    _Host(store, [])
    store.store(category="fact", content="User's bank PIN is 4821", sensitive=True)
    reader = MemoryStore(db_path=tmp_path / "memory.db")
    reader._conn.execute("PRAGMA busy_timeout=0")
    store._conn.execute("PRAGMA busy_timeout=0")
    reader._conn.execute("BEGIN")
    reader._conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()
    # Debian's SQLite defaults secure_delete on, so compare to what it was.
    secure_delete = store._conn.execute("PRAGMA secure_delete").fetchone()[0]

    with pytest.raises(RuntimeError, match="was erased, but"):
        store.clear_all()

    reader._conn.rollback()
    reader.close()
    assert store._conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0] == 0
    assert store._conn.execute("PRAGMA secure_delete").fetchone()[0] == secure_delete


def test_a_blocked_scrub_still_drops_the_erased_facts_from_the_agent(
    store, tmp_path, monkeypatch
):
    host = _Host(store, [])
    refreshed = []
    monkeypatch.setattr(
        host, "_refresh_stable_memory_prompt", lambda: refreshed.append("prompt")
    )
    monkeypatch.setattr(host, "_rebuild_faiss_index", lambda: refreshed.append("index"))
    store.store(category="fact", content="User's bank PIN is 4821", sensitive=True)
    reader = MemoryStore(db_path=tmp_path / "memory.db")
    reader._conn.execute("PRAGMA busy_timeout=0")
    store._conn.execute("PRAGMA busy_timeout=0")
    reader._conn.execute("BEGIN")
    reader._conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()

    out = _tool("forget_everything")()

    reader._conn.rollback()
    reader.close()
    assert out["status"] == "error"
    assert "Close other GAIA windows and erase again" in out["message"]
    assert sorted(refreshed) == ["index", "prompt"]


def test_erasing_again_after_a_blocked_scrub_clears_the_file(store, tmp_path):
    _Host(store, [])
    for i in range(20):
        store.store(category="fact", content=f"Trip to Lisbon number {i}")
        store.store_turn("s-old", "user", f"My trip to Lisbon {i} is in December")
    store.store(category="fact", content="User's bank PIN is 4821", sensitive=True)
    reader = MemoryStore(db_path=tmp_path / "memory.db")
    reader._conn.execute("PRAGMA busy_timeout=0")
    store._conn.execute("PRAGMA busy_timeout=0")
    reader._conn.execute("BEGIN")
    reader._conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()

    assert _tool("forget_everything")()["status"] == "error"
    reader._conn.rollback()
    reader.close()
    retry = _tool("forget_everything")()

    assert retry["status"] == "cleared"
    raw = _on_disk(tmp_path)
    for gone in (b"lisbon", b"4821"):
        assert gone not in raw, f"{gone!r} still readable after erasing again"
