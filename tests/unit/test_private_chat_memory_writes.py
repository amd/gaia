# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A private chat changes nothing stored in memory, by any tool (#8057).

``remember`` was refused in a private chat, but ``update_memory`` rewrote a
stored fact with the private chat's value, and ``forget`` and recall's use
counts changed rows too. Every tool here runs against a real store and the
knowledge table must read the same afterwards.
"""

import pytest

from gaia.agents.base.memory import _MEMORY_TOOLS
from tests.unit.test_memory_mixin import (  # noqa: F401 - pytest fixtures
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
