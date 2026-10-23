# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A tool can name the arguments its repeat-call guard compares (#3746).

A free-text argument such as ``reason`` made every call look new, so the guard
never fired. ``@tool(dedup_key=[...])`` narrows the comparison; tools that
don't declare one keep comparing every argument, unchanged.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.mcp.client.mcp_client import MCPClient, MCPTool
from gaia.mcp.mixin import MCP_DEDUP_KEY_META, MCPClientMixin

_KEYED = "dedup_probe_keyed"
_PLAIN = "dedup_probe_plain"
_CORRECTION = "Do not repeat it"


class _ProbeAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        agent = self
        agent.calls = 0

        @tool(dedup_key=["path"])
        def dedup_probe_keyed(path: str, reason: str = "") -> dict:
            """Probe tool that dedups on path only."""
            del path, reason
            agent.calls += 1
            return {"status": "error", "error": "not found"}

        @tool
        def dedup_probe_plain(path: str, reason: str = "") -> dict:
            """Probe tool that dedups on every argument."""
            del path, reason
            agent.calls += 1
            return {"status": "error", "error": "not found"}

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def agent():
    with patch("gaia.agents.base.agent.AgentSDK"):
        a = _ProbeAgent(silent_mode=True, skip_lemonade=True)
        a.streaming = False
        a.max_consecutive_repeats = 3
        return a


def _stub_chat(agent_, responses):
    queue = list(responses)
    sent: list = []

    def _send(messages, *_, **__):
        sent.append([dict(m) for m in messages])
        if not queue:
            raise AssertionError("chat stub ran out of scripted responses")
        resp = MagicMock()
        resp.text = queue.pop(0)
        resp.stats = {}
        return resp

    agent_.chat = MagicMock()
    agent_.chat.send_messages = MagicMock(side_effect=_send)
    return sent


def _legacy(name: str, args: dict) -> str:
    return json.dumps({"thought": "try", "tool": name, "tool_args": args})


def _native(name: str, args: dict, call_id: str) -> str:
    return json.dumps(
        {
            "__tool_calls__": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            ],
            "finish_reason": "tool_calls",
        }
    )


def _script(path_name: str, name: str, args_list: list) -> list:
    if path_name == "legacy":
        return [_legacy(name, a) for a in args_list]
    return [_native(name, a, f"c{i}") for i, a in enumerate(args_list)]


def _corrections(sent: list) -> list:
    return [
        m
        for m in sent[-1]
        if m.get("role") == "tool" and _CORRECTION in str(m.get("content"))
    ]


_ANSWER = json.dumps({"thought": "done", "answer": "gave up"})
_REASONS = [{"path": "a.txt", "reason": f"attempt {i}"} for i in range(3)]


@pytest.mark.parametrize("path_name", ["legacy", "native"])
def test_varying_only_a_non_key_argument_trips_the_guard(agent, path_name):
    sent = _stub_chat(agent, _script(path_name, _KEYED, _REASONS) + [_ANSWER])

    agent.process_query("read it", max_steps=10)

    assert agent.calls == 2  # the third call was replaced by the correction
    assert len(_corrections(sent)) == 1


@pytest.mark.parametrize("path_name", ["legacy", "native"])
def test_without_dedup_key_a_varying_argument_is_a_new_call(agent, path_name):
    sent = _stub_chat(agent, _script(path_name, _PLAIN, _REASONS) + [_ANSWER])

    agent.process_query("read it", max_steps=10)

    assert agent.calls == 3
    assert not _corrections(sent)


@pytest.mark.parametrize("path_name", ["legacy", "native"])
def test_varying_a_key_argument_is_a_new_call(agent, path_name):
    calls = [{"path": f"{i}.txt", "reason": "same"} for i in range(3)]
    sent = _stub_chat(agent, _script(path_name, _KEYED, calls) + [_ANSWER])

    agent.process_query("read them", max_steps=10)

    assert agent.calls == 3
    assert not _corrections(sent)


# ---------------------------------------------------------------------------
# The key itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,args",
    [
        (_PLAIN, {"path": "a.txt", "reason": "r"}),
        (_PLAIN, {}),
        ("not_a_registered_tool", {"x": [1, 2]}),
        (_KEYED, None),
        (_KEYED, "raw string payload"),
    ],
)
def test_default_key_is_unchanged(agent, name, args):
    assert agent._loop_guard_key(name, args) == (name, str(args))


def test_dedup_key_ignores_argument_order_and_missing_values(agent):
    a = agent._loop_guard_key(_KEYED, {"path": "a.txt", "reason": "x"})
    b = agent._loop_guard_key(_KEYED, {"reason": "y", "path": "a.txt"})
    assert a == b == (_KEYED, json.dumps({"path": "a.txt"}))
    assert agent._loop_guard_key(_KEYED, {"reason": "y"}) == (
        _KEYED,
        json.dumps({"path": None}),
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_registry_records_the_key():
    registry: dict = {}

    @tool(dedup_key=["path", "query"], registry=registry)
    def search(path: str, query: str, reason: str = "") -> dict:
        """Search."""
        return {}

    @tool(registry=registry)
    def plain(path: str) -> dict:
        """Plain."""
        return {}

    assert registry["search"]["dedup_key"] == ("path", "query")
    assert registry["plain"]["dedup_key"] is None


def test_unknown_argument_name_is_refused_naming_the_tool():
    with pytest.raises(ValueError, match=r"'misnamed_tool'.*'pth'.*path, reason"):

        @tool(dedup_key=["pth"], registry={})
        def misnamed_tool(path: str, reason: str = "") -> dict:
            """Bad key."""
            return {}


@pytest.mark.parametrize("bad", ["path", [], ["path", 3], {"path": 1}])
def test_malformed_dedup_key_is_refused(bad):
    with pytest.raises(TypeError, match=r"'bad_key_tool'.*non-empty list"):

        @tool(dedup_key=bad, registry={})
        def bad_key_tool(path: str) -> dict:
            """Bad key."""
            return {}


# ---------------------------------------------------------------------------
# MCP: the same list, declared in the tool's ``_meta``
# ---------------------------------------------------------------------------


def _mcp_tool(name: str, meta: dict) -> MCPTool:
    return MCPTool(
        name=name,
        description="d",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}, "reason": {"type": "string"}},
        },
        meta=meta,
    )


@pytest.fixture
def restore_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


def _register(*tools: MCPTool) -> None:
    client = SimpleNamespace(
        name="catalyst",
        prefix="catalyst",
        list_tools=lambda: list(tools),
        create_tool_wrapper=lambda tool: lambda **kw: {"ok": True},
    )
    MCPClientMixin._register_mcp_tools(object.__new__(MCPClientMixin), client)


def test_mcp_tool_declares_its_key_through_meta(restore_registry):
    _register(
        _mcp_tool("read", {MCP_DEDUP_KEY_META: ["path"]}),
        _mcp_tool("write", {}),
    )
    assert _TOOL_REGISTRY["mcp_catalyst_read"]["dedup_key"] == ("path",)
    assert "dedup_key" not in _TOOL_REGISTRY["mcp_catalyst_write"]


def test_mcp_bad_key_registers_none_of_the_servers_tools(restore_registry):
    with pytest.raises(ValueError, match=r"read \(MCP server 'catalyst'\).*'pth'"):
        _register(
            _mcp_tool("write", {}),
            _mcp_tool("read", {MCP_DEDUP_KEY_META: ["pth"]}),
        )
    assert "mcp_catalyst_write" not in _TOOL_REGISTRY


def test_list_tools_keeps_the_meta_object():
    transport = Mock()
    transport.is_connected.return_value = True
    transport.send_request.return_value = {
        "result": {
            "tools": [
                {"name": "a", "_meta": {MCP_DEDUP_KEY_META: ["path"]}},
                {"name": "b", "_meta": "not an object"},
                {"name": "c"},
            ]
        }
    }
    tools = MCPClient("test", transport).list_tools()
    assert [t.meta for t in tools] == [{MCP_DEDUP_KEY_META: ["path"]}, {}, {}]
