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
        self.external_marks = 0

    def mark_external_content(self):
        self.external_marks += 1

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


class _BigToolAgent(_ToolAgent):
    """Every turn reads a long file, so a few turns outgrow a small window."""

    def process_query(self, query, max_steps=None):
        self.seen.append([dict(m) for m in self.conversation_history])
        call = {**_CALL, "id": f"call_{len(self.seen)}"}
        answer = f"read it ({query})"
        trace = [
            {"role": "user", "content": query},
            {"role": "assistant", "content": "", "tool_calls": [call]},
            {
                "role": "tool",
                "tool_call_id": call["id"],
                "name": "read_file",
                "content": f"{query}: " + "lorem ipsum " * 400,
            },
            {"role": "assistant", "content": answer},
        ]
        return {"answer": answer, "model_messages": trace}


@pytest.fixture
def live_sidecar(monkeypatch, request):
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

    agent_class = getattr(request, "param", _ToolAgent)

    def build(**kw):
        agents.append(agent_class(**kw))
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
    [("2.25", True), ("3.0", True), ("2.24", False), (None, False)],
)
def test_only_a_flagship_at_2_25_takes_turns(version, expected):
    assert turns_supported(GAIA_PROFILE, version) is expected


def test_email_never_takes_turns():
    assert turns_supported(EMAIL_PROFILE, "9.9") is False


def test_the_next_turn_sees_the_refused_tool_call(monkeypatch, live_sidecar, chat):
    base_url, agents, api_version = live_sidecar
    _point_ui_at(monkeypatch, base_url, api_version)

    first_events = chat.send("list the PRs")
    assert _answer(first_events) == "The command was refused."
    assert _answer(chat.send("why did that fail?")) == "second"
    # The record is the model's, kept server-side: the browser never gets it.
    assert _REFUSAL not in json.dumps(first_events)
    assert _REFUSAL not in json.dumps(chat.db.get_messages(chat.session_id))

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
    # Claims 2.24, so the relay must not send a field that version refuses.
    _point_ui_at(monkeypatch, base_url, "2.24")

    chat.send("list the PRs")
    assert _answer(chat.send("why did that fail?")) == "second"

    [agent] = agents
    assert agent.seen[1] == [
        {"role": "user", "content": "list the PRs"},
        {"role": "assistant", "content": "The command was refused."},
    ]


def _turn_questions(history):
    return [m["content"] for m in history if m.get("role") == "user"]


@pytest.mark.parametrize("live_sidecar", [_BigToolAgent], indirect=True)
def test_turns_are_fitted_to_the_window_and_the_cut_is_shown(
    monkeypatch, live_sidecar, chat
):
    base_url, agents, api_version = live_sidecar
    _point_ui_at(monkeypatch, base_url, api_version)
    import gaia.agents.base.history as history

    monkeypatch.setattr(history, "relayed_history_budget", lambda *a, **k: 300)

    events = [chat.send(f"q{i}") for i in range(5)]

    [agent] = agents
    last = agent.seen[-1]
    # Whole turns only, oldest dropped first: each kept question has its answer.
    questions = _turn_questions(last)
    assert questions and "q0" not in questions
    assert questions == [f"q{i}" for i in range(4)][-len(questions) :]
    for q in questions:
        assert {"role": "assistant", "content": f"read it ({q})"} in last
    # A call is never sent without its result.
    assert len([m for m in last if m.get("tool_calls")]) == len(
        [m for m in last if m.get("role") == "tool"]
    )
    # The long results went as #5864 stubs, not in full.
    assert all(
        m["content"].startswith("[Left out of the") and len(m["content"]) < 500
        for m in last
        if m.get("role") == "tool"
    )
    cuts = [e for turn in events for e in turn if e.get("status") == "history_cut"]
    assert cuts and cuts[0]["turns"] >= 1


def test_a_window_with_room_sends_every_turn_whole(monkeypatch, live_sidecar, chat):
    base_url, agents, api_version = live_sidecar
    _point_ui_at(monkeypatch, base_url, api_version)

    chat.send("list the PRs")
    events = chat.send("why did that fail?")

    [agent] = agents
    assert agent.seen[1][2] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": _REFUSAL,
    }
    assert not [e for e in events if e.get("status") == "history_cut"]


def test_turns_are_sized_off_the_event_loop(monkeypatch, live_sidecar, chat):
    base_url, agents, api_version = live_sidecar
    _point_ui_at(monkeypatch, base_url, api_version)
    import gaia.agents.base.history as history

    real = history.history_budget
    on_loop: list = []

    def spy(agent, query):
        if isinstance(agent, _ToolAgent):
            try:
                asyncio.get_running_loop()
                on_loop.append(True)
            except RuntimeError:
                on_loop.append(False)
        return real(agent, query)

    monkeypatch.setattr(history, "history_budget", spy)

    chat.send("list the PRs")
    chat.send("why did that fail?")

    assert on_loop and not any(on_loop)


def _final(base_url, body):
    import uuid

    import requests

    body = {"run_id": uuid.uuid4().hex, "context": [], **body}
    with requests.post(
        f"{base_url}/v1/gaia/query", json=body, stream=True, timeout=30
    ) as resp:
        assert resp.status_code == 200, resp.text
        events = [
            json.loads(line[len("data: ") :])
            for line in resp.iter_lines(decode_unicode=True)
            if line and line.startswith("data: ")
        ]
    [final] = [e for e in events if e.get("type") == "final"]
    return final


def test_only_a_caller_that_sends_turns_gets_the_record(live_sidecar):
    base_url, _agents, _ = live_sidecar

    # A text-only caller (the TUI) never asked for it, and a turn's full tool
    # results can outgrow its SSE frame limit.
    assert "model_messages" not in _final(base_url, {"query": "list the PRs"})
    record = _final(base_url, {"query": "list the PRs", "turns": []})
    assert record["model_messages"][1]["tool_calls"] == [_CALL]


def test_turns_and_context_together_are_refused(live_sidecar):
    import uuid

    import requests

    base_url, agents, _ = live_sidecar
    resp = requests.post(
        f"{base_url}/v1/gaia/query",
        json={
            "run_id": uuid.uuid4().hex,
            "query": "q",
            "context": [{"role": "user", "content": "dropped?"}],
            "turns": [],
        },
        timeout=30,
    )

    assert resp.status_code == 422
    assert "not both" in resp.text
    assert agents == []


class _TextToolAgent(_ToolAgent):
    def _uses_native_tool_calls(self):
        return False


@pytest.mark.parametrize("live_sidecar", [_TextToolAgent], indirect=True)
def test_a_model_without_native_tools_reads_the_calls_as_text(
    monkeypatch, live_sidecar, chat
):
    base_url, agents, api_version = live_sidecar
    _point_ui_at(monkeypatch, base_url, api_version)

    chat.send("list the PRs")
    chat.send("why did that fail?")

    [agent] = agents
    second = agent.seen[1]
    assert not [m for m in second if m.get("role") == "tool" or "tool_calls" in m]
    text = json.dumps(second)
    assert "[Recorded tool calls]" in text and "gh pr list" in text
    assert "[Recorded tool result: " in text and _REFUSAL in text


def test_empty_turns_replace_a_retained_sessions_history(live_sidecar):
    base_url, agents, _ = live_sidecar
    body = {"query": "q", "session_id": "s1"}

    _final(base_url, {**body, "context": [{"role": "user", "content": "stale"}]})
    _final(base_url, {**body, "turns": []})

    [agent] = agents
    assert agent.seen[1] == []


@pytest.mark.parametrize("live_sidecar", [_BigToolAgent], indirect=True)
def test_the_sidecar_refits_turns_to_its_own_window(monkeypatch, live_sidecar, chat):
    base_url, agents, api_version = live_sidecar
    _point_ui_at(monkeypatch, base_url, api_version)
    import gaia.agents.base.history as history

    # The UI budgets generously; only the sidecar's own window is tight.
    monkeypatch.setattr(history, "relayed_history_budget", lambda *a, **k: 100_000)
    real = history.history_budget
    monkeypatch.setattr(
        history,
        "history_budget",
        lambda agent, query: (
            300 if isinstance(agent, _ToolAgent) else real(agent, query)
        ),
    )

    for i in range(5):
        chat.send(f"q{i}")

    [agent] = agents
    questions = _turn_questions(agent.seen[-1])
    assert questions and "q0" not in questions
    assert all(
        m["content"].startswith("[Left out of the")
        for m in agent.seen[-1]
        if m.get("role") == "tool"
    )


def test_replayed_turns_count_as_external_content(monkeypatch, live_sidecar, chat):
    base_url, agents, api_version = live_sidecar
    _point_ui_at(monkeypatch, base_url, api_version)

    chat.send("list the PRs")
    [agent] = agents
    # The first turn sends `turns: []`: nothing pushed, nothing to mark.
    assert agent.external_marks == 0

    chat.send("why did that fail?")
    # Replayed tool results are not the user speaking (memory provenance).
    assert agent.external_marks == 1
