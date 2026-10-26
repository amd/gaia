# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A relayed flagship sees its own earlier tool calls on the next turn (#5701).

The Agent UI used to push only user/assistant text to the sidecar and never
saved the turn's model record, so "why did that fail?" got "I have no record
of the tool calls". These drive the real chat stream against the real
flagship app over HTTP, so a field the sidecar refuses or drops fails here.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest

import gaia.ui._chat_helpers as chat_helpers_module
from gaia.ui import permissions as session_permissions
from gaia.ui._chat_helpers import _stream_chat_impl
from gaia.ui.database import ChatDatabase
from gaia.ui.email_sidecar.profiles import (
    EMAIL_PROFILE,
    GAIA_PROFILE,
    turns_supported,
)
from gaia.ui.models import ChatRequest
from tests.unit.test_relay_flagship_documents import (  # noqa: F401  (fixture)
    _answer,
    _point_ui_at,
    sidecar_flagship_registry,
)

pytestmark = pytest.mark.allow_network

_CALL = {
    "id": "call_1",
    "type": "function",
    "function": {"name": "run_shell_command", "arguments": '{"command": "gh pr list"}'},
}
_REFUSAL = "Not run: gh pr list needs your approval"


class _ToolAgent:
    """Stands in for GaiaAgent: its first turn runs a tool that is refused."""

    def __init__(self, **_kwargs):
        self.console = None
        self.conversation_history: list = []
        self._cancel_event = None
        self.seen: list = []

    def _uses_native_tool_calls(self):
        return True

    def process_query(self, query, max_steps=None):
        self.seen.append([dict(m) for m in self.conversation_history])
        if len(self.seen) == 1:
            answer = "The command was refused."
            trace = [
                {"role": "user", "content": query},
                {"role": "assistant", "content": "", "tool_calls": [_CALL]},
                {"role": "tool", "tool_call_id": "call_1", "content": _REFUSAL},
                {"role": "assistant", "content": answer},
            ]
            return {"answer": answer, "model_messages": trace}
        return {"answer": "second", "model_messages": None}

    def close(self):
        pass


@pytest.fixture
def live_sidecar(monkeypatch):
    uvicorn = pytest.importorskip("uvicorn")
    pytest.importorskip("gaia_agent")
    import requests
    from gaia_agent import caller_auth
    from gaia_agent import server as server_mod
    from gaia_agent import session_registry as sr

    from gaia.daemon.sidecars.manager import find_free_port

    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)
    agents: list = []

    def build(**kw):
        agents.append(_ToolAgent(**kw))
        return agents[-1]

    monkeypatch.setattr(server_mod, "build_query_agent", build)
    monkeypatch.setattr(sr, "build_session_agent", build)
    monkeypatch.setattr(server_mod, "_warmup_blocking", lambda: None)
    monkeypatch.setattr(
        server_mod,
        "_probe_lemonade",
        lambda: {
            "base_url": "http://lemonade.invalid/api/v1",
            "reachable": True,
            "version": "99.0.0",
            "present": True,
            "ctx_size": None,
            "model_id": "m",
        },
    )
    sr.registry.clear()
    server_mod._registry = server_mod._RunRegistry()

    port = find_free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            server_mod.build_app(), host="127.0.0.1", port=port, log_level="warning"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            if requests.get(f"{base_url}/health", timeout=0.5).status_code == 200:
                break
        except requests.exceptions.RequestException:
            time.sleep(0.05)
    else:
        server.should_exit = True
        raise RuntimeError("flagship sidecar test server never became reachable")
    try:
        yield base_url, agents, server_mod.API_VERSION
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sr.registry.clear()
        caller_auth.reset()


@pytest.fixture
def chat(monkeypatch, sidecar_flagship_registry):  # noqa: F811
    """A flagship chat that saves each question first, as the chat router does."""
    session_permissions.reset_all()

    async def _noop_retitle(**_kwargs):
        return None

    monkeypatch.setattr(
        chat_helpers_module, "_agent_registry", sidecar_flagship_registry
    )
    monkeypatch.setattr(
        chat_helpers_module, "_maybe_update_session_title", _noop_retitle
    )
    db = ChatDatabase(":memory:")
    session_id = db.create_session(agent_type="gaia")["id"]

    def send(message):
        db.begin_turn(session_id, message)
        request = ChatRequest(
            session_id=session_id, message=message, stream=True, agent_type="gaia"
        )
        events: list = []

        async def _drive():
            async for chunk in _stream_chat_impl(
                SimpleNamespace(handler=None), db, db.get_session(session_id), request
            ):
                for line in str(chunk).splitlines():
                    if line.startswith("data: "):
                        events.append(json.loads(line[len("data: ") :]))

        asyncio.run(_drive())
        return events

    yield SimpleNamespace(send=send, db=db, session_id=session_id)
    session_permissions.reset_all()


@pytest.mark.parametrize(
    "version, expected",
    [("2.21", True), ("3.0", True), ("2.20", False), (None, False)],
)
def test_only_a_flagship_at_2_21_takes_turns(version, expected):
    assert turns_supported(GAIA_PROFILE, version) is expected


def test_email_never_takes_turns():
    assert turns_supported(EMAIL_PROFILE, "9.9") is False


def test_the_next_turn_sees_the_refused_tool_call(monkeypatch, live_sidecar, chat):
    base_url, agents, api_version = live_sidecar
    _point_ui_at(monkeypatch, base_url, api_version)

    assert _answer(chat.send("list the PRs")) == "The command was refused."
    assert _answer(chat.send("why did that fail?")) == "second"

    [agent] = agents
    first, second = agent.seen
    assert first == []
    calls = [m for m in second if m.get("tool_calls")]
    assert calls and calls[0]["tool_calls"] == [_CALL]
    assert {"role": "tool", "tool_call_id": "call_1", "content": _REFUSAL} in second
    # Saved with the reply, so a restarted sidecar is sent it too.
    [reply] = [
        m
        for m in chat.db.get_context_messages(chat.session_id)
        if m["role"] == "assistant" and m["content"] == "The command was refused."
    ]
    assert reply["model_messages"][1]["tool_calls"] == [_CALL]


def test_an_older_flagship_still_gets_text_context(monkeypatch, live_sidecar, chat):
    base_url, agents, _ = live_sidecar
    # Claims 2.20, so the relay must not send a field that version refuses.
    _point_ui_at(monkeypatch, base_url, "2.20")

    chat.send("list the PRs")
    assert _answer(chat.send("why did that fail?")) == "second"

    [agent] = agents
    assert agent.seen[1] == [
        {"role": "user", "content": "list the PRs"},
        {"role": "assistant", "content": "The command was refused."},
    ]
