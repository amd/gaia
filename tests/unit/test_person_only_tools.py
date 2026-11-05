# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Person-only tools (smart-home writes) are never approved by pre-approval.

A connector marks its state-changing MCP tools ``needs_person``. Full access,
auto-approve, background runs and messaging turns must not answer them; a
console that asks past pre-approval gets the call marked instead.
"""

from types import SimpleNamespace

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.call_risk import NEEDS_PERSON_ARG
from gaia.agents.base.tool_grants import grant_scope
from gaia.mcp.client.mcp_client import MCPClient, MCPTool
from gaia.mcp.mixin import MCPClientMixin

TOOL = "mcp_home_assistant_intent__HassTurnOff"


def _check(console, monkeypatch, env=None):
    from gaia.agents.base import console as console_mod

    env = dict(env or {})
    monkeypatch.delenv("GAIA_MESSAGING_CHANNEL", raising=False)
    # Read from a startup snapshot, so patched rather than set.
    auto = env.pop("GAIA_AUTO_APPROVE_TOOLS", None) == "1"
    monkeypatch.setattr(console_mod, "auto_approve_env_enabled", lambda: auto)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Agent._person_only_check(SimpleNamespace(console=console), TOOL)


def _console(**attrs):
    base = dict(
        asks_past_preapproval=False,
        answers_confirmations=False,
        background_mode=False,
        full_access=False,
        auto_approve_gated_tools=False,
        auto_approve_confirmations_enabled=lambda: False,
    )
    base.update(attrs)
    return SimpleNamespace(**base)


@pytest.mark.parametrize(
    "console",
    [
        _console(),
        # The TUI's stdio pipe: asks past pre-approval, never says who answers.
        _console(asks_past_preapproval=True),
        _console(asks_past_preapproval=True, answers_confirmations=True),
    ],
)
def test_without_preapproval_the_ordinary_prompt_asks(console, monkeypatch):
    assert _check(console, monkeypatch) == (None, False)


def test_preapproval_with_a_watching_person_still_asks(monkeypatch):
    console = _console(
        asks_past_preapproval=True, answers_confirmations=True, full_access=True
    )
    assert _check(console, monkeypatch) == (None, True)


@pytest.mark.parametrize(
    "console, env, reason",
    [
        (_console(full_access=True), None, "full access is on"),
        (_console(auto_approve_gated_tools=True), None, "full access is on"),
        (
            _console(auto_approve_confirmations_enabled=lambda: True),
            None,
            "full access is on",
        ),
        (_console(), {"GAIA_AUTO_APPROVE_TOOLS": "1"}, "full access is on"),
        # Pre-approved on a pipe that never says a person answers.
        (
            _console(asks_past_preapproval=True, full_access=True),
            None,
            "full access is on",
        ),
        (_console(background_mode=True), None, "nobody can answer"),
        (None, None, "nobody can answer"),
        (
            _console(asks_past_preapproval=True, answers_confirmations=True),
            {"GAIA_MESSAGING_CHANNEL": "1"},
            "messaging app",
        ),
    ],
)
def test_preapproval_never_answers_a_person_only_call(
    console, env, reason, monkeypatch
):
    from gaia.agents.base.verification import NOT_EXECUTED

    refusal, mark = _check(console, monkeypatch, env)
    assert refusal is not None and refusal["status"] == "error" and mark is False
    assert reason in refusal["error"]
    # Refused before the tool body: never counted as a call that ran.
    assert NOT_EXECUTED.items() <= refusal.items()


def test_no_always_is_offered_for_home_assistant_tools():
    assert grant_scope(TOOL, {"name": "Porch Light"}) is None
    assert grant_scope(TOOL, {"name": "Porch Light", NEEDS_PERSON_ARG: True}) is None


def _client(writes_need_person):
    client = MCPClient.__new__(MCPClient)
    client.name = "mcp-home-assistant"
    client.prefix = "home_assistant"
    client.writes_need_person = writes_need_person
    client.tool_note = "Read the live context first."
    client.list_tools = lambda: [
        MCPTool(
            "intent__HassTurnOff",
            "Turns off",
            {"properties": {}},
            {"readOnlyHint": False},
            "Turn off",
        ),
        MCPTool(
            "homeassistant__GetLiveContext",
            "State",
            {"properties": {}},
            {"readOnlyHint": True},
        ),
    ]
    client.create_tool_wrapper = lambda tool: (lambda **kw: {"ok": True})
    return client


@pytest.mark.parametrize("flag", [True, False])
def test_only_writes_of_a_flagged_server_become_person_only(flag, monkeypatch):
    from gaia.agents.base import tools as tools_mod

    registry = {}
    monkeypatch.setattr(tools_mod, "_TOOL_REGISTRY", registry)
    import gaia.mcp.mixin as mixin_mod

    monkeypatch.setattr(mixin_mod, "_TOOL_REGISTRY", registry, raising=False)
    host = MCPClientMixin.__new__(MCPClientMixin)
    MCPClientMixin._register_mcp_tools(host, _client(flag))
    turn_off = next(v for k, v in registry.items() if k.endswith("HassTurnOff"))
    live = next(v for k, v in registry.items() if k.endswith("GetLiveContext"))
    assert turn_off.get("needs_person", False) is flag
    assert "needs_person" not in live
    # The catalog's note guides the writes; reads keep the server's wording.
    assert turn_off["description"].endswith("Read the live context first.")
    assert "Read the live context first." not in live["description"]
