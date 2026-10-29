# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Cache eviction preserves recently used chats and releases idle agents."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import gaia.ui._chat_helpers as helpers


@pytest.fixture
def full_cache(monkeypatch):
    monkeypatch.setattr(helpers, "_agent_cache", {})
    agents = {}
    for index in range(helpers._MAX_CACHED_AGENTS):
        session_id = f"session-{index}"
        agent = SimpleNamespace(close=Mock(), _mcp_manager=Mock())
        agents[session_id] = agent
        helpers._store_agent(session_id, "model", [], agent)
    return agents


@pytest.mark.parametrize("accesses", [[], [0], [0, 1, 0]])
def test_cache_evicts_least_recently_used_agent(full_cache, accesses):
    order = list(full_cache)
    for index in accesses:
        session_id = f"session-{index}"
        assert helpers._get_cached_agent(session_id, "model") is full_cache[session_id]
        order.remove(session_id)
        order.append(session_id)

    new_agent = SimpleNamespace(close=Mock(), _mcp_manager=Mock())
    helpers._store_agent("new-session", "model", [], new_agent)

    evicted = order.pop(0)
    assert list(helpers._agent_cache) == [*order, "new-session"]
    assert helpers._agent_cache["new-session"]["agent"] is new_agent
    full_cache[evicted].close.assert_called_once_with()
    full_cache[evicted]._mcp_manager.disconnect_all.assert_called_once_with()
    for session_id in order:
        full_cache[session_id].close.assert_not_called()
        full_cache[session_id]._mcp_manager.disconnect_all.assert_not_called()


def test_cache_miss_does_not_change_eviction_order(full_cache):
    assert helpers._get_cached_agent("missing", "model") is None
    assert list(helpers._agent_cache) == list(full_cache)


@pytest.mark.parametrize("model,agent_type", [("other", "chat"), ("model", "email")])
def test_mismatched_entry_is_disconnected_not_promoted(full_cache, model, agent_type):
    assert helpers._get_cached_agent("session-0", model, agent_type) is None
    assert list(helpers._agent_cache) == list(full_cache)[1:]
    full_cache["session-0"].close.assert_called_once_with()
    full_cache["session-0"]._mcp_manager.disconnect_all.assert_called_once_with()
