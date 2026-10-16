# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An agent on the OpenAI-compatible path rides out a real outage.

The model calls go through a real ``LemonadeClient`` to a tiny local server
over real sockets, so the failures are classified from real HTTP responses
and real refused connections. Only the waits run on a fake clock. The
client's own retries are set to one attempt, so every failure reaches the
agent's outage wait.
"""

import socket
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gaia.llm.lemonade_client import LemonadeClient
from tests.unit.agents.test_model_outage_recovery import (
    FakeClock,
    _make_agent,
    _retry_lines,
)
from tests.unit.test_lemonade_retry_fake_server import (
    _GOOD_STREAM,
    COMPLETION,
    MODEL,
    FakeServer,
    _json,
    _rate_limited,
    _stream_text,
)

pytestmark = pytest.mark.allow_network


@pytest.fixture
def clean_registry():
    from gaia.agents.base.tools import _TOOL_REGISTRY

    snapshot = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


@pytest.fixture(autouse=True)
def _one_client_attempt(monkeypatch):
    for name in (
        "GAIA_LLM_RETRY_BASE_DELAY",
        "GAIA_LLM_RETRY_MAX_DELAY",
        "GAIA_LLM_RETRY_DEADLINE",
        "GAIA_AGENT_MODEL_OUTAGE_BUDGET",
        "LEMONADE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GAIA_LLM_RETRY_ATTEMPTS", "1")


def _client(port):
    client = LemonadeClient(host="127.0.0.1", port=port, verbose=False)
    client._ensure_model_loaded = lambda *a, **k: None
    client._model_slot_lease = lambda *a, **k: nullcontext()
    client.cloud_model_provider = lambda *a, **k: None
    client._retry_sleep = lambda _s: None
    return client


def _wire(agent, client):
    """Route the agent's model calls through *client*, as AgentSDK does."""
    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})
    chat.llm_client = SimpleNamespace(base_url=client.base_url)

    def _send(messages, **_):
        result = client.chat_completions(model=MODEL, messages=messages)
        return SimpleNamespace(
            text=result["choices"][0]["message"]["content"], stats={}
        )

    def _stream(messages, **_):
        chunks = client.chat_completions(model=MODEL, messages=messages, stream=True)
        for chunk in chunks:
            text = _stream_text([chunk])
            if text:
                yield SimpleNamespace(text=text, is_complete=False, stats=None)
        yield SimpleNamespace(text="", is_complete=True, stats={})

    chat.send_messages = MagicMock(side_effect=_send)
    chat.send_messages_stream = MagicMock(side_effect=_stream)
    agent.chat = chat


@pytest.mark.parametrize("streaming", [False, True], ids=["non-streaming", "streaming"])
def test_429s_and_a_503_then_success(clean_registry, streaming):
    script = [
        _rate_limited("7"),
        _json(503, {"error": {"message": "Service Unavailable"}}),
        _rate_limited(),
        _GOOD_STREAM if streaming else _json(200, COMPLETION),
    ]
    agent = _make_agent(streaming, FakeClock())
    with FakeServer(script) as server:
        _wire(agent, _client(server.port))
        result = agent.process_query("Say hello", max_steps=3)

    assert "Hello" in result["result"]
    assert len(server.requests) == 4
    assert all(r == server.requests[0] for r in server.requests)
    # Retry-After 7 beats the 5 s floor; then the backoff doubles.
    assert agent.waits == [7.0, 10.0, 20.0]
    lines = _retry_lines(agent)
    assert "(HTTP 429)" in lines[0] and "(HTTP 503)" in lines[1]
    assert result["model_unreachable"] is False


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_a_refused_local_server_is_started_and_the_turn_carries_on(
    clean_registry, monkeypatch
):
    """Nothing listens at first: real connection-refused errors from a
    loopback server. The auto-start path brings it up, with no waiting."""
    from gaia.llm.lemonade_manager import LemonadeManager

    port = _free_port()
    agent = _make_agent(False, FakeClock())
    servers = []

    def ensure_ready(**kwargs):
        assert kwargs["base_url"].startswith(f"http://127.0.0.1:{port}/")
        servers.append(FakeServer([_json(200, COMPLETION)], port=port).__enter__())
        return True

    monkeypatch.setattr(LemonadeManager, "ensure_ready", ensure_ready)
    monkeypatch.setattr(LemonadeManager, "reset", lambda: None)
    _wire(agent, _client(port))
    try:
        result = agent.process_query("Say hello", max_steps=3)
    finally:
        for server in servers:
            server.__exit__(None, None, None)

    assert "Hello" in result["result"]
    assert agent.waits == []
    assert len(servers) == 1
    assert len(servers[0].requests) == 1


def test_a_refused_local_server_that_will_not_start_fails_fast(
    clean_registry, monkeypatch
):
    from gaia.llm.lemonade_manager import LemonadeManager

    port = _free_port()
    agent = _make_agent(False, FakeClock())
    monkeypatch.setattr(LemonadeManager, "ensure_ready", lambda **_: False)
    monkeypatch.setattr(LemonadeManager, "reset", lambda: None)
    _wire(agent, _client(port))
    result = agent.process_query("Say hello", max_steps=3)

    assert agent.waits == []
    assert "is not answering" in result["result"]
    assert "still down after GAIA tried to start it" in result["result"]
    assert result["model_unreachable"] is True


def test_a_permanent_401_from_the_server_is_not_waited_out(clean_registry):
    script = [_json(401, {"error": {"message": "invalid api key"}})]
    agent = _make_agent(False, FakeClock())
    with FakeServer(script) as server:
        _wire(agent, _client(server.port))
        agent.process_query("Say hello", max_steps=3)
    assert len(server.requests) == 1
    assert agent.waits == []
