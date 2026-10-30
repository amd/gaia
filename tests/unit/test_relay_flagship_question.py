# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Answering a relayed flagship's mid-run question from the Agent UI.

The Agent UI relays the answer with the spec §5.1 body ``{request_id, value}``
— the same one the email sidecar takes. The flagship only took ``response``,
so every answer 422'd and the run sat on the question until it timed out.
These drive the real Agent UI relay and proxy against the real flagship app
over HTTP, because a stubbed proxy cannot catch a field-name mismatch.
"""

from __future__ import annotations

import threading
import time
import uuid

import pytest

from gaia.ui.email_sidecar import relay
from gaia.ui.email_sidecar.profiles import GAIA_PROFILE
from gaia.ui.email_sidecar.proxy import SidecarProxy
from gaia.ui.sse_handler import SSEOutputHandler

pytestmark = pytest.mark.allow_network


class _AskingAgent:
    """Stands in for GaiaAgent: asks one question, answers with what it got."""

    def __init__(self, **_kwargs):
        self.console = None
        self.conversation_history = []
        self._cancel_event = None
        self.answer = None

    def process_query(self, query, max_steps=None):
        self.answer = self.console.request_user_input_blocking(
            "Which colour?", choices=["red", "blue"], timeout_seconds=30
        )
        return {"answer": f"picked={self.answer}"}

    def close(self):
        pass


@pytest.fixture
def live_gaia_sidecar(monkeypatch):
    """The real flagship ``/v1/gaia`` app on a real port, with an asking agent."""
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
        agents.append(_AskingAgent(**kw))
        return agents[-1]

    monkeypatch.setattr(server_mod, "build_query_agent", build)
    monkeypatch.setattr(sr, "build_session_agent", build)
    monkeypatch.setattr(server_mod, "_warmup_blocking", lambda: None)
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
        yield base_url, agents
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sr.registry.clear()
        caller_auth.reset()


def _wait_for_question(handler, seen, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while not handler.event_queue.empty():
            event = handler.event_queue.get_nowait()
            seen.append(event)
            if event and event.get("type") == "needs_input":
                return event["request_id"]
        time.sleep(0.02)
    raise AssertionError("the relayed run never surfaced a question")


def test_the_ui_answer_reaches_the_flagship_question(live_gaia_sidecar):
    base_url, agents = live_gaia_sidecar
    handler = SSEOutputHandler()
    thread = threading.Thread(
        target=relay.relay_query,
        args=(handler, SidecarProxy(base_url, agent_id="gaia")),
        kwargs={
            "query": "pick a colour",
            "context": [],
            "profile": GAIA_PROFILE,
            "session_id": f"s-{uuid.uuid4().hex[:8]}",
            "read_timeout": 30,
        },
        daemon=True,
    )
    thread.start()

    seen: list = []
    request_id = _wait_for_question(handler, seen)
    # The path POST /api/chat/user-input takes for a relayed run.
    assert handler.resolve_relay_input(request_id, "blue") is True
    thread.join(timeout=15)

    assert not thread.is_alive()
    assert agents[-1].answer == "blue"
    while not handler.event_queue.empty():
        seen.append(handler.event_queue.get_nowait())
    answers = [e for e in seen if e and e.get("type") == "answer"]
    assert answers and answers[-1]["content"] == "picked=blue"


def test_both_relayed_agents_take_the_same_answer_body():
    """One relay body for both sidecars: the spec §5.1 ``value``."""
    pytest.importorskip("gaia_agent")
    pytest.importorskip("gaia_agent_email")
    from gaia_agent.server import QueryRespondRequest as GaiaRespond
    from gaia_agent_email.query_routes import QueryRespondRequest as EmailRespond

    body = {"request_id": "q-1", "value": "blue"}
    assert GaiaRespond(**body).answer == "blue"
    assert EmailRespond(**body).value == "blue"
