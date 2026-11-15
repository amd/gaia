# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Closing an agent stops the MCP servers it started.

Each MCP server is a process tree (``uvx`` → ``uv`` → the server). The Agent UI
builds agents it never caches — the new-chat warm-up, scheduled runs, goal
ticks — and closes them; their servers kept running, about ten processes per
new chat.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("gaia_agent")

from gaia_agent.chat_agent import ChatAgent  # noqa: E402


def _agent(manager):
    agent = ChatAgent.__new__(ChatAgent)
    agent.observers = []
    agent._mcp_manager = manager
    return agent


def test_close_disconnects_every_mcp_server():
    manager = MagicMock()
    agent = _agent(manager)
    ChatAgent.__del__(agent)
    manager.disconnect_all.assert_called_once_with()


def test_an_agent_without_mcp_closes_cleanly():
    ChatAgent.__del__(_agent(None))


def test_a_failed_disconnect_does_not_stop_the_rest_of_the_teardown():
    manager = MagicMock()
    manager.disconnect_all.side_effect = RuntimeError("server already gone")
    agent = _agent(manager)
    agent._fs_index = MagicMock()
    ChatAgent.__del__(agent)
    agent._fs_index.close_db.assert_called_once_with()
