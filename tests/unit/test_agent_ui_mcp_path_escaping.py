# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Ids the MCP tools receive stay inside their own URL path segment."""

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from gaia.mcp.servers.agent_ui_mcp import create_agent_ui_mcp

NASTY = "a?x=1#frag"
ESCAPED = "a%3Fx%3D1%23frag"


class _RecordingServer:
    """Stands in for ``mcp.server.MCPServer``, which the unit job doesn't install."""

    def __init__(self, name):
        self.tools = {}

    def tool(self):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn

        return register


@pytest.fixture
def tools(monkeypatch):
    monkeypatch.setenv("GAIA_MEMORY_MCP_ALWAYS", "1")
    fake = types.ModuleType("mcp.server")
    fake.MCPServer = _RecordingServer
    monkeypatch.setitem(sys.modules, "mcp", types.ModuleType("mcp"))
    monkeypatch.setitem(sys.modules, "mcp.server", fake)
    return create_agent_ui_mcp("http://backend").tools


def _called_url(verb, tool, *args):
    response = MagicMock()
    response.json.return_value = {}
    with patch(f"requests.{verb}", return_value=response) as call:
        tool(*args)
    return call.call_args.args[0]


@pytest.mark.parametrize(
    "tool_name, verb",
    [
        ("get_session", "get"),
        ("delete_session", "delete"),
        ("get_messages", "get"),
        ("memory_get", "get"),
        ("memory_get_by_entity", "get"),
        ("memory_get_conversation_turns", "get"),
    ],
)
def test_id_with_query_and_fragment_is_escaped(tools, tool_name, verb):
    url = _called_url(verb, tools[tool_name], NASTY)

    assert ESCAPED in url
    assert NASTY not in url


def test_dot_segment_id_is_refused(tools):
    with pytest.raises(ValueError, match="not a valid id"):
        tools["get_session"]("..")


@pytest.mark.parametrize(
    "tool_name",
    [
        "get_session",
        "delete_session",
        "get_messages",
        "memory_get",
        "memory_get_by_entity",
        "memory_get_conversation_turns",
    ],
)
def test_id_with_slash_is_refused(tools, tool_name):
    with patch("requests.get") as get, patch("requests.delete") as delete:
        with pytest.raises(ValueError, match="not a valid id"):
            tools[tool_name]("S/messages/M")
    get.assert_not_called()
    delete.assert_not_called()
