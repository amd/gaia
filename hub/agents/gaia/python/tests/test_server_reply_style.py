# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/query``'s ``reply_style`` (2.28): a voice turn gets the spoken-reply note,
a typed turn never does, and a retained session can't carry it over."""

from __future__ import annotations

import uuid

import pytest

pytest.importorskip("gaia_agent")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent import caller_auth  # noqa: E402
from gaia_agent import server as server_mod  # noqa: E402
from gaia_agent import session_registry as sr  # noqa: E402
from gaia_agent.chat_agent import ChatAgent  # noqa: E402

from gaia.voice.prompt import VOICE_TURN_NOTE  # noqa: E402

_BASE_URL = "http://127.0.0.1:8141"


class _StyleAgent:
    internet_off = False

    def __init__(self, **_kwargs):
        self.console = None
        self.conversation_history = []
        self._cancel_event = None
        self._tools_registry = {}
        self.styles: list = []

    def process_query(self, query, max_steps=None):
        self.styles.append(getattr(self, "reply_style", None))
        return {"answer": "ok"}

    def close(self):
        pass


@pytest.fixture
def built(monkeypatch):
    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)
    agents: list = []

    def build(**kw):
        agents.append(_StyleAgent(**kw))
        return agents[-1]

    monkeypatch.setattr(server_mod, "build_query_agent", build)
    monkeypatch.setattr(sr, "build_session_agent", build)
    sr.registry.clear()
    server_mod._registry = server_mod._RunRegistry()
    try:
        yield TestClient(server_mod.build_app(), base_url=_BASE_URL), agents
    finally:
        sr.registry.clear()
        server_mod._registry = server_mod._RunRegistry()
        caller_auth.reset()


def _query(client, **fields):
    body = {"query": "hi", "run_id": str(uuid.uuid4()), "context": []}
    body.update(fields)
    return client.post("/v1/gaia/query", json=body)


def test_the_contract_advertises_reply_style():
    major, minor = (int(x) for x in server_mod.API_VERSION.split(".")[:2])
    assert (major, minor) >= (2, 28)
    assert "reply_style" in server_mod.QueryRequest.model_fields


def test_a_retained_session_resets_to_text_each_turn(built):
    client, agents = built
    for style in ("voice", None, "voice", "text"):
        fields = {"session_id": "chat-1"}
        if style is not None:
            fields["reply_style"] = style
        assert _query(client, **fields).status_code == 200
    assert len(agents) == 1
    assert agents[0].styles == ["voice", "text", "voice", "text"]


def test_an_unknown_style_is_refused(built):
    client, _ = built
    assert _query(client, reply_style="sung").status_code == 422


def _context(style):
    agent = ChatAgent.__new__(ChatAgent)
    agent.reply_style = style
    agent._indexed_documents_line = lambda: ""
    return ChatAgent.get_memory_dynamic_context(agent)


def test_only_a_voice_turn_carries_the_note():
    assert VOICE_TURN_NOTE in _context("voice")
    assert VOICE_TURN_NOTE not in _context("text")
    assert "<spoken>" in VOICE_TURN_NOTE and "40 words" in VOICE_TURN_NOTE
