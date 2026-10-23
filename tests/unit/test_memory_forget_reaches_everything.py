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
    store.store_turn(
        "s-new", "user", "New plan: Lisbon December 10 to 17 with Sam after all"
    )

    out = _tool("search_past_conversations")(query="Lisbon")

    assert out["count"] == 1
    assert "forgotten_hidden" not in out
