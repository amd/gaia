# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A private chat changes nothing stored in memory, by any tool (#8057).

``remember`` was refused in a private chat, but ``update_memory`` rewrote a
stored fact with the private chat's value, and ``forget`` and recall's use
counts changed rows too. Every tool here runs against a real store and the
knowledge table must read the same afterwards.
"""

import hashlib
import logging
import sqlite3
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from gaia.agents.base.memory import (
    _MEMORY_TOOLS,
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    MemoryMixin,
    _embedding_to_blob,
)
from gaia.agents.base.memory_store import MemoryStore
from tests.unit.faiss_support import require_faiss
from tests.unit.test_memory_mixin import (  # noqa: F401 - pytest fixtures
    FakeAgent,
    mixin_host,
    mixin_with_tools,
    tmp_db_path,
)

#: Memory tools that change what is stored.
WRITES = {"remember", "update_memory", "forget", "forget_everything"}
#: Memory tools that only read it.
READS = {"recall", "search_past_conversations"}


def _rows(store):
    with store._lock:
        return store._conn.execute("SELECT * FROM knowledge ORDER BY id").fetchall()


@pytest.fixture(autouse=True)
def _memory_on(monkeypatch):
    # CI's unit lane sets GAIA_MEMORY_DISABLED; these tests need a real store.
    monkeypatch.delenv("GAIA_MEMORY_DISABLED", raising=False)


@pytest.fixture
def private(mixin_with_tools):  # noqa: F811 - the imported fixture
    store = mixin_with_tools.memory_store
    kid = store.store(category="fact", content="Project budget is 73,904 euros")
    mixin_with_tools._incognito = True
    mixin_with_tools._incognito_reason = "private"
    return mixin_with_tools, store, kid


def _tool(agent, name):
    return agent._registered_tools[name]["function"]


def test_every_memory_tool_is_classified_as_a_read_or_a_write():
    assert set(_MEMORY_TOOLS) == WRITES | READS


@pytest.mark.parametrize(
    "name,kwargs,done",
    [
        ("remember", {"fact": "My favourite tree code is GINKGO-4417"}, "stored"),
        ("update_memory", {"content": "Project budget is 81,000 euros"}, "updated"),
        ("forget", {}, "erased"),
        ("forget_words", {"knowledge_id": "project budget"}, "erased"),
        ("forget_everything", {}, "erased"),
    ],
)
def test_a_private_chat_refuses_every_memory_change(private, name, kwargs, done):
    agent, store, kid = private
    before = _rows(store)
    tool = "forget" if name == "forget_words" else name
    if tool in ("update_memory", "forget") and "knowledge_id" not in kwargs:
        kwargs = {"knowledge_id": kid, **kwargs}

    result = _tool(agent, tool)(**kwargs)

    assert result["status"] == "skipped", result
    assert "private chat" in result["message"]
    assert f"Don't tell the user it was {done}" in result["message"]
    if done != "stored":
        assert f"Nothing was {done}" in result["message"]
        assert "Memory page" in result["message"]
    assert _rows(store) == before


def test_recall_in_a_private_chat_leaves_use_counts_alone(private):
    agent, store, _kid = private
    before = _rows(store)

    result = _tool(agent, "recall")(query="project budget")

    assert "73,904" in str(result)
    assert _rows(store) == before


def test_memory_off_refuses_an_update_with_its_own_reason(private):
    agent, store, kid = private
    agent._incognito_reason = "memory_off"

    result = _tool(agent, "update_memory")(knowledge_id=kid, content="changed")

    assert result["status"] == "skipped"
    assert "turned off in Settings" in result["message"]
    assert store.get_item(kid)["content"] == "Project budget is 73,904 euros"


def test_memory_off_tells_how_to_erase_anyway(private):
    agent, store, _kid = private
    agent._incognito_reason = "memory_off"

    result = _tool(agent, "forget_everything")()

    assert result["status"] == "skipped"
    assert "erase it on the Memory page" in result["message"]
    assert _rows(store)


def test_an_update_still_works_once_the_chat_is_not_private(private):
    agent, store, kid = private
    agent._incognito = False

    result = _tool(agent, "update_memory")(
        knowledge_id=kid, content="Project budget is 81,000 euros"
    )

    assert result["status"] == "updated"
    assert store.get_item(kid)["content"] == "Project budget is 81,000 euros"


# ---------------------------------------------------------------------------
# A chat that starts private: the Agent UI builds the agent with memory off,
# so the embedder and its startup upkeep load on the first recall.
# ---------------------------------------------------------------------------

BUDGET = "Project budget is 73,904 euros"
FERN = "The office plant is a fern"
SYSTEM_FACT = {"content": "Operating system: TestOS 1", "domain": "system:os"}


def _vec(text):
    seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)
    return np.random.default_rng(seed).random(EMBEDDING_DIM).astype(np.float32)


def _days_ago(days):
    return (datetime.now().astimezone() - timedelta(days=days)).isoformat()


def _snapshot(db):
    """Every row of every table, plus the file's bytes."""
    conn = sqlite3.connect(db)
    try:
        dump = list(conn.iterdump())
    finally:
        conn.close()
    return dump, Path(db).read_bytes()


def _seed(db, *, stamp):
    """A 60-day-old fact with a vector, and a fact without one."""
    store = MemoryStore(db_path=db)
    try:
        old_id = store.store(category="fact", content=BUDGET)
        store.store_embedding(old_id, _embedding_to_blob(_vec(BUDGET)))
        bare_id = store.store(category="fact", content=FERN)
        sixty = _days_ago(60)
        with store._lock:
            store._conn.execute(
                "UPDATE knowledge SET confidence = 0.5, last_used = ?, "
                "updated_at = ? WHERE id = ?",
                (sixty, sixty, old_id),
            )
            store._conn.commit()
        if stamp is not None:
            store.set_embedder_id(stamp)
    finally:
        store.close()
    return old_id, bare_id


@pytest.fixture
def cold_agent(tmp_path):
    """Build an agent on a seeded store: ``build(private=..., stamp=...)``."""
    require_faiss()
    from gaia.agents.base.tools import _TOOL_REGISTRY

    db = tmp_path / "memory.db"
    built = []

    class ColdAgent(MemoryMixin, FakeAgent):
        pass

    with ExitStack() as patches:
        patches.enter_context(
            patch.object(MemoryMixin, "_get_embedder", return_value=MagicMock())
        )
        patches.enter_context(
            patch.object(MemoryMixin, "_embed_text", side_effect=_vec)
        )
        patches.enter_context(
            patch.object(
                MemoryMixin, "reconcile_memory", return_value={"pairs_checked": 0}
            )
        )
        patches.enter_context(
            patch.object(
                MemoryMixin,
                "consolidate_old_sessions",
                return_value={"consolidated": 0, "extracted_items": 0},
            )
        )
        patches.enter_context(
            patch(
                "gaia.agents.base.memory._system_context_is_enabled",
                return_value=True,
            )
        )
        patches.enter_context(
            patch(
                "gaia.agents.base.system_context.collect_system_info",
                side_effect=lambda: [dict(SYSTEM_FACT)],
            )
        )

        def build(*, private=True, reason="private", stamp=EMBEDDING_MODEL):
            old_id, bare_id = _seed(db, stamp=stamp)
            before = _snapshot(db)
            agent = ColdAgent()
            if private:
                agent._incognito_reason = reason
            agent.init_memory(db_path=db, incognito=private)
            agent.register_memory_tools()
            for name in _MEMORY_TOOLS:
                agent._registered_tools[name] = _TOOL_REGISTRY[name]
            built.append(agent)
            return agent, db, before, old_id, bare_id

        yield build
        for agent in built:
            if agent.memory_store is not None:
                agent.memory_store.close()


def _knowledge(db, kid):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM knowledge WHERE id = ?", (kid,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def _system_rows(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM knowledge WHERE category = 'system'"
        ).fetchone()[0]
    finally:
        conn.close()


@pytest.mark.parametrize("reason", ["private", "memory_off"])
def test_a_chat_that_starts_private_changes_nothing_on_its_first_recall(
    cold_agent, reason
):
    agent, db, before, old_id, _bare = cold_agent(reason=reason)
    assert _snapshot(db) == before, "starting the agent changed the store"

    result = _tool(agent, "recall")(query="project budget")
    agent.process_query("What is the project budget?")
    _tool(agent, "recall")(query="office plant")

    assert "73,904" in str(result)
    assert _snapshot(db) == before
    old = _knowledge(db, old_id)
    assert old["confidence"] == pytest.approx(0.5)
    assert old["decayed_at"] is None
    assert _system_rows(db) == 0
    # Recall by meaning still works: the index is built in memory.
    assert agent._faiss_index is not None and agent._faiss_index.ntotal == 1


def test_a_private_start_keeps_vectors_from_another_embedder(cold_agent, caplog):
    agent, db, before, old_id, _bare = cold_agent(stamp="old-embedder")

    with caplog.at_level(logging.WARNING):
        result = _tool(agent, "recall")(query="project budget")

    assert "73,904" in str(result)  # keyword recall
    assert _snapshot(db) == before
    assert _knowledge(db, old_id)["embedding"] is not None
    assert agent._faiss_index is None
    assert "came from embedder old-embedder" in caplog.text


def test_turning_private_off_runs_the_held_upkeep_once(cold_agent):
    agent, db, before, old_id, bare_id = cold_agent(stamp="old-embedder")
    _tool(agent, "recall")(query="project budget")
    assert _snapshot(db) == before

    agent._incognito = False
    agent.process_query("hello")

    old = _knowledge(db, old_id)
    assert old["confidence"] == pytest.approx(0.45)
    assert old["decayed_at"] is not None
    assert _knowledge(db, bare_id)["embedding"] is not None
    assert agent.memory_store.get_embedder_id() == EMBEDDING_MODEL
    assert _system_rows(db) == 1
    assert agent._faiss_index.ntotal == 2

    agent.process_query("hello again")
    assert _knowledge(db, old_id)["confidence"] == pytest.approx(0.45)
    assert _system_rows(db) == 1


def test_turning_private_on_mid_session_stops_startup_writes(cold_agent):
    agent, db, _before, old_id, _bare = cold_agent(private=False)
    agent._incognito = True
    agent._incognito_reason = "private"
    sixty = _days_ago(60)
    with agent.memory_store._lock:
        agent.memory_store._conn.execute(
            "UPDATE knowledge SET confidence = 0.5, decayed_at = NULL, "
            "last_used = ?, updated_at = ? WHERE id = ?",
            (sixty, sixty, old_id),
        )
        agent.memory_store._conn.commit()
    snapshot = _snapshot(db)

    agent.reset_memory_session()
    _tool(agent, "recall")(query="project budget")
    agent.process_query("What is the project budget?")

    assert _snapshot(db) == snapshot
    assert _knowledge(db, old_id)["confidence"] == pytest.approx(0.5)


def test_a_chat_that_is_not_private_still_decays_at_startup(cold_agent):
    _agent, db, _before, old_id, bare_id = cold_agent(private=False)

    old = _knowledge(db, old_id)
    assert old["confidence"] == pytest.approx(0.45)
    assert old["decayed_at"] is not None
    assert _knowledge(db, bare_id)["embedding"] is not None
    assert _system_rows(db) == 1
