# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Approving a relayed (sidecar) agent's gated tool from the Agent UI.

A Hub-installed flagship runs as a daemon sidecar. Its ``/query`` parks on a
``needs_confirmation`` (contract >= 2.14) and waits for
``/query/{run_id}/tool_decision``; the relay used to show that as a dead
"Nothing was executed" card with no way to answer. These pin the path from
the UI's own permission prompt to the sidecar's decision route, and that it
stays fail-closed: the answer is bound to the prompt shown, a stale or
duplicate answer is refused, "always" is not offered, and a sidecar-side
timeout denies.
"""

from __future__ import annotations

import threading
import time
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.ui import _chat_helpers as helpers
from gaia.ui.database import ChatDatabase
from gaia.ui.email_sidecar import relay
from gaia.ui.email_sidecar.errors import SidecarError, SidecarHTTPError
from gaia.ui.email_sidecar.profiles import (
    EMAIL_PROFILE,
    GAIA_PROFILE,
    confirmations_supported,
)
from gaia.ui.routers.chat import router as chat_router
from gaia.ui.sse_handler import SSEOutputHandler

pytestmark = pytest.mark.allow_network

_CONFIRM = {
    "type": "needs_confirmation",
    "run_id": "r1",
    "action": "write_file",
    "summary": "Run 'write_file' with file_path=\"notes.md\"?",
    "confirm_id": "c-1",
    "always_scope": "write_file notes.md",
    "risk": "write",
}


class _RecordingProxy:
    agent_id = "gaia"

    def __init__(self, error: Exception | None = None):
        self.decisions: list = []
        self.error = error

    def tool_decision(self, run_id, decision, confirm_id):
        self.decisions.append((run_id, decision, confirm_id))
        if self.error is not None:
            raise self.error
        return {"run_id": run_id, "decision": decision, "delivered": True}


def _relaying_handler(proxy, run_id="r1"):
    handler = SSEOutputHandler()
    handler.active_relay_proxy = proxy
    handler.active_relay_run_id = run_id
    return handler


def _drain(handler):
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    return events


# ── Which sidecars can take an answer ──────────────────────────────────────


@pytest.mark.parametrize(
    "version, expected",
    [("2.15", True), ("2.14", True), ("2.13", False), (None, False), ("x", False)],
)
def test_only_a_flagship_that_has_tool_decision_is_answerable(version, expected):
    """2.13 (the published binary) refuses a gated tool outright."""
    assert confirmations_supported(GAIA_PROFILE, version) is expected


def test_email_confirmations_stay_informational():
    """Email's /query ends the run on a confirmation; there is nothing to answer."""
    assert confirmations_supported(EMAIL_PROFILE, "2.99") is False


# ── Relay event mapping ────────────────────────────────────────────────────


class TestRelayMapsAnswerableConfirmation:
    def test_becomes_the_ui_permission_prompt_without_always(self):
        handler = _relaying_handler(_RecordingProxy())
        terminal = relay._dispatch_one(handler, dict(_CONFIRM), GAIA_PROFILE, True)

        assert terminal is False, "the sidecar run is parked, not over"
        [event] = _drain(handler)
        assert event == {
            "type": "permission_request",
            "tool": "write_file",
            "confirm_id": "c-1",
            "summary": _CONFIRM["summary"],
            "risk": "write",
        }
        # An "always" grant would live in the sidecar where this chat can't
        # list or revoke it, so the choice is never offered.
        assert "always_scope" not in event
        assert handler.relay_confirmation_pending()

    def test_an_unanswerable_sidecar_keeps_the_informational_card(self):
        handler = _relaying_handler(_RecordingProxy())
        relay._dispatch_one(handler, dict(_CONFIRM), GAIA_PROFILE, False)

        [event] = _drain(handler)
        assert event["type"] == "needs_confirmation"
        assert not handler.relay_confirmation_pending()

    def test_a_prompt_without_a_confirm_id_is_not_made_answerable(self):
        """Without the id an answer could resolve a different prompt."""
        handler = _relaying_handler(_RecordingProxy())
        event = {k: v for k, v in _CONFIRM.items() if k != "confirm_id"}
        relay._dispatch_one(handler, event, GAIA_PROFILE, True)

        assert _drain(handler)[0]["type"] == "needs_confirmation"
        assert not handler.relay_confirmation_pending()

    def test_the_next_sidecar_event_retires_the_prompt(self):
        """Any later event means the sidecar stopped waiting (answer or timeout)."""
        handler = _relaying_handler(_RecordingProxy())
        relay._dispatch_one(handler, dict(_CONFIRM), GAIA_PROFILE, True)
        relay._dispatch_one(
            handler, {"type": "status", "message": "timed out"}, GAIA_PROFILE, True
        )
        assert not handler.relay_confirmation_pending()


# ── Handler: binding the answer to the prompt shown ────────────────────────


class TestResolveRelayConfirmation:
    def _pending(self, proxy):
        handler = _relaying_handler(proxy)
        handler.set_relay_confirmation("c-1")
        return handler

    @pytest.mark.parametrize("approved, decision", [(True, "allow"), (False, "deny")])
    def test_delivers_the_decision_for_the_prompt_shown(self, approved, decision):
        proxy = _RecordingProxy()
        handler = self._pending(proxy)

        assert handler.resolve_relay_confirmation(approved, "c-1") is True
        assert proxy.decisions == [("r1", decision, "c-1")]
        assert not handler.relay_confirmation_pending()

    @pytest.mark.parametrize("confirm_id", ["c-0", "", None])
    def test_a_stale_or_missing_confirm_id_reaches_nothing(self, confirm_id):
        proxy = _RecordingProxy()
        handler = self._pending(proxy)

        assert handler.resolve_relay_confirmation(True, confirm_id) is False
        assert proxy.decisions == []
        assert handler.relay_confirmation_pending(), "the live prompt is untouched"

    def test_a_double_click_sends_one_answer(self):
        proxy = _RecordingProxy()
        handler = self._pending(proxy)

        assert handler.resolve_relay_confirmation(True, "c-1") is True
        assert handler.resolve_relay_confirmation(True, "c-1") is False
        assert len(proxy.decisions) == 1

    def test_a_finished_relay_takes_no_answer(self):
        proxy = _RecordingProxy()
        handler = self._pending(proxy)
        handler.active_relay_proxy = None
        handler.active_relay_run_id = None

        assert handler.resolve_relay_confirmation(True, "c-1") is False
        assert proxy.decisions == []

    def test_a_pending_relay_prompt_counts_as_waiting_on_the_user(self):
        handler = self._pending(_RecordingProxy())
        assert handler.awaiting_user_input()


# ── /api/chat/confirm-tool for a relayed run ───────────────────────────────


@pytest.fixture
def client():
    db = ChatDatabase(":memory:")
    app = FastAPI()
    app.include_router(chat_router)
    app.state.db = db
    app.state.session_locks = {}
    yield TestClient(app)
    db.close()


@pytest.fixture
def relayed():
    """A chat whose relayed run is parked on sidecar prompt ``c-1``."""
    session_id = "relayed-session"
    proxy = _RecordingProxy()
    handler = _relaying_handler(proxy)
    handler.set_relay_confirmation("c-1")
    helpers._active_sse_handlers[session_id] = handler
    yield session_id, handler, proxy
    helpers._active_sse_handlers.pop(session_id, None)


def _confirm(client, session_id, **body):
    return client.post(
        "/api/chat/confirm-tool", json={"session_id": session_id, **body}
    )


class TestConfirmToolRoute:
    @pytest.mark.parametrize("approved, decision", [(True, "allow"), (False, "deny")])
    def test_answer_reaches_the_sidecar(self, client, relayed, approved, decision):
        session_id, _, proxy = relayed
        resp = _confirm(client, session_id, approved=approved, confirm_id="c-1")

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"status": "ok", "approved": approved, "granted": None}
        assert proxy.decisions == [("r1", decision, "c-1")]

    def test_always_is_refused_and_sends_nothing(self, client, relayed):
        """Never widen a yes past what the prompt offered."""
        session_id, handler, proxy = relayed
        resp = _confirm(
            client, session_id, approved=True, always=True, confirm_id="c-1"
        )

        assert resp.status_code == 409
        assert "allow once or deny" in resp.json()["detail"]
        assert proxy.decisions == []
        assert handler.relay_confirmation_pending()

    def test_a_stale_click_is_a_409(self, client, relayed):
        session_id, _, proxy = relayed
        resp = _confirm(client, session_id, approved=True, confirm_id="c-old")

        assert resp.status_code == 409
        assert proxy.decisions == []

    def test_a_sidecar_rejection_is_surfaced_not_swallowed(self, client, relayed):
        session_id, handler, _ = relayed
        handler.active_relay_proxy = _RecordingProxy(
            error=SidecarHTTPError(409, "No tool confirmation is pending")
        )
        resp = _confirm(client, session_id, approved=True, confirm_id="c-1")

        assert resp.status_code == 409
        assert "No tool confirmation is pending" in resp.json()["detail"]


# ── End to end against the real flagship sidecar app ───────────────────────


class _GatedAgent:
    """Stands in for GaiaAgent: asks to run one gated tool, records the outcome."""

    def __init__(self, **_kwargs):
        self.console = None
        self.conversation_history = []
        self._cancel_event = None
        self.approved = None

    def process_query(self, query, max_steps=None):
        self.approved = self.console.confirm_tool_execution(
            "write_file", {"file_path": "notes.md", "content": "hi"}
        )
        return {"answer": f"approved={self.approved}"}

    def close(self):
        pass


@pytest.fixture
def live_gaia_sidecar(monkeypatch):
    """The real flagship ``/v1/gaia`` app on a real port, with a gated agent."""
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
        agents.append(_GatedAgent(**kw))
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


def _relay_in_background(base_url):
    from gaia.ui.email_sidecar.proxy import SidecarProxy

    handler = SSEOutputHandler()
    proxy = SidecarProxy(base_url, agent_id="gaia")
    thread = threading.Thread(
        target=relay.relay_query,
        args=(handler, proxy),
        kwargs={
            "query": "write notes.md",
            "context": [],
            "profile": GAIA_PROFILE,
            "session_id": f"s-{uuid.uuid4().hex[:8]}",
            "answers_confirmations": True,
            "read_timeout": 30,
        },
        daemon=True,
    )
    thread.start()
    return handler, thread


def _wait_for_prompt(handler, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with handler._confirm_lock:
            pending = handler._relay_confirm_id
        if pending:
            return pending
        time.sleep(0.02)
    raise AssertionError("the relayed run never surfaced a permission prompt")


def _answers(handler):
    return [e for e in _drain(handler) if e and e.get("type") == "answer"]


@pytest.mark.parametrize("approved", [True, False])
def test_the_ui_answer_runs_or_declines_the_sidecar_tool(live_gaia_sidecar, approved):
    base_url, agents = live_gaia_sidecar
    handler, thread = _relay_in_background(base_url)

    confirm_id = _wait_for_prompt(handler)
    assert handler.resolve_relay_confirmation(approved, confirm_id) is True
    thread.join(timeout=15)

    assert not thread.is_alive()
    assert agents[-1].approved is approved
    assert _answers(handler)[-1]["content"] == f"approved={approved}"


def test_an_unanswered_prompt_is_denied_and_a_late_answer_reaches_nothing(
    live_gaia_sidecar, monkeypatch
):
    """Timeout is a no, and the click that arrives after it changes nothing."""
    base_url, agents = live_gaia_sidecar
    from gaia_agent import stdio

    # The sidecar's own bound for a session prompt (15 minutes in production).
    monkeypatch.setattr(stdio, "ORPHANED_CONFIRM_TIMEOUT_SECONDS", 1.0)
    handler, thread = _relay_in_background(base_url)

    confirm_id = _wait_for_prompt(handler)
    thread.join(timeout=15)

    assert not thread.is_alive()
    assert agents[-1].approved is False
    assert handler.resolve_relay_confirmation(True, confirm_id) is False


def test_a_decision_without_a_confirm_id_is_never_sent():
    """The sidecar would resolve whichever prompt is live; refuse instead."""
    from gaia.ui.email_sidecar.proxy import SidecarProxy

    with pytest.raises(SidecarError, match="confirm_id"):
        SidecarProxy("http://127.0.0.1:9", agent_id="gaia").tool_decision(
            "r1", "allow", ""
        )
