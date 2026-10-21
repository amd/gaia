# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
""" "Add to this reply" reaches a relayed (sidecar) flagship turn.

The Agent UI used to deliver a mid-turn message only to an in-process agent,
so for the flagship sidecar every click came back 409 and silently re-queued.
These pin the relay of that message to the sidecar's
``/query/{run_id}/followup`` (contract >= 2.15) and that the chat records only
what the sidecar accepted.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import gaia.ui._chat_helpers as chat_helpers_module
from gaia.agents.base.agent import Agent
from gaia.agents.registry import AgentRegistry
from gaia.hub.installer import register_installed_sidecars
from gaia.ui import followups
from gaia.ui import permissions as session_permissions
from gaia.ui._chat_helpers import _stream_chat_impl
from gaia.ui.database import ChatDatabase
from gaia.ui.email_sidecar import daemon_client as daemon_client_module
from gaia.ui.email_sidecar.errors import SidecarError, SidecarHTTPError
from gaia.ui.email_sidecar.profiles import (
    EMAIL_PROFILE,
    GAIA_PROFILE,
    followups_supported,
)
from gaia.ui.email_sidecar.proxy import SidecarProxy
from gaia.ui.models import ChatRequest
from gaia.ui.routers import chat as chat_router

pytestmark = pytest.mark.allow_network


@pytest.mark.parametrize(
    "version, expected",
    [("2.15", True), ("2.18", True), ("2.14", False), (None, False), ("x", False)],
)
def test_only_a_flagship_at_2_15_takes_followups(version, expected):
    assert followups_supported(GAIA_PROFILE, version) is expected


def test_email_never_takes_followups():
    assert followups_supported(EMAIL_PROFILE, "9.9") is False


# ── Proxy: the request the sidecar receives ────────────────────────────────


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


class _Session:
    def __init__(self, resp):
        self.resp = resp
        self.headers = {}
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        return self.resp


def test_followup_posts_the_text_to_the_runs_followup_route():
    session = _Session(_Resp(payload={"run_id": "r1", "delivered": True}))
    proxy = SidecarProxy("http://127.0.0.1:9", agent_id="gaia", session=session)

    assert proxy.followup("r1", "also the totals") == {
        "run_id": "r1",
        "delivered": True,
    }
    assert session.calls == [
        ("http://127.0.0.1:9/v1/gaia/query/r1/followup", {"text": "also the totals"})
    ]


def test_a_refused_followup_raises_with_the_sidecars_detail():
    session = _Session(_Resp(404, {"detail": "No run 'r1' is in flight"}))
    proxy = SidecarProxy("http://127.0.0.1:9", agent_id="gaia", session=session)

    with pytest.raises(SidecarHTTPError) as exc:
        proxy.followup("r1", "hi")
    assert exc.value.status_code == 404
    assert "No run 'r1' is in flight" in str(exc.value)


# ── The relayed turn's inbox ───────────────────────────────────────────────


class TestRelayInbox:
    def setup_method(self):
        self.sent: list = []
        self.taken: list = []

    def _open(self, session_id="s-relay", error=None):
        def send(text):
            if error is not None:
                raise error
            self.sent.append(text)

        return followups.open_relay_inbox(session_id, send, self.taken.append)

    def test_a_delivered_message_is_sent_recorded_and_reported_taken(self):
        inbox = self._open()
        assert followups.deliver("s-relay", "and the totals") is True
        followups.close_relay_inbox("s-relay", inbox)

        assert self.sent == ["and the totals"]
        assert self.taken == ["and the totals"]
        assert followups.last_outcome("s-relay") == {
            "taken": ["and the totals"],
            "returned": [],
        }

    def test_a_refused_message_is_neither_recorded_nor_reported(self):
        inbox = self._open(error=SidecarHTTPError(409, "not accepting"))
        with pytest.raises(SidecarError):
            followups.deliver("s-relay", "and the totals")
        followups.close_relay_inbox("s-relay", inbox)

        assert self.taken == []
        assert followups.last_outcome("s-relay") == {"taken": [], "returned": []}

    def test_nothing_reaches_a_turn_that_has_ended(self):
        inbox = self._open()
        followups.close_relay_inbox("s-relay", inbox)

        assert followups.deliver("s-relay", "too late") is False
        assert inbox.relay("too late") is False
        assert self.sent == []

    def test_closing_leaves_a_newer_turns_inbox_alone(self):
        old = self._open()
        newer = self._open()
        followups.close_relay_inbox("s-relay", old)

        assert followups.deliver("s-relay", "for the new turn") is True
        followups.close_relay_inbox("s-relay", newer)


# ── POST /api/chat/followup for a relayed turn ─────────────────────────────


@pytest.mark.asyncio
async def test_route_delivers_to_the_relayed_turn():
    sent: list = []
    inbox = followups.open_relay_inbox("s-route", sent.append, lambda _t: None)
    try:
        with patch.object(chat_router.run_manager, "is_running", return_value=True):
            result = await chat_router.send_followup(
                chat_router.FollowUpRequest(session_id="s-route", text=" more ")
            )
    finally:
        followups.close_relay_inbox("s-route", inbox)
    assert result == {"delivered": True}
    assert sent == ["more"]


@pytest.mark.asyncio
async def test_route_reports_the_sidecars_refusal_as_a_409():
    def refuse(_text):
        raise SidecarHTTPError(409, "Run 'r' cannot take a follow-up")

    inbox = followups.open_relay_inbox("s-route", refuse, lambda _t: None)
    try:
        with patch.object(chat_router.run_manager, "is_running", return_value=True):
            with pytest.raises(HTTPException) as exc:
                await chat_router.send_followup(
                    chat_router.FollowUpRequest(session_id="s-route", text="more")
                )
    finally:
        followups.close_relay_inbox("s-route", inbox)
    assert exc.value.status_code == 409
    assert "cannot take a follow-up" in exc.value.detail
    assert "sent as the next message" in exc.value.detail


# ── End to end against the real flagship sidecar app ───────────────────────


class _FollowUpAgent:
    """Stands in for GaiaAgent: waits for a follow-up, then answers with it."""

    queue_followup = Agent.queue_followup

    def __init__(self, **_kwargs):
        self.console = None
        self.conversation_history = []
        self._cancel_event = None
        self._followup_queue = None

    def process_query(self, query, max_steps=None):
        deadline = time.monotonic() + 10
        while self._followup_queue.empty() and time.monotonic() < deadline:
            time.sleep(0.02)
        took = []
        while not self._followup_queue.empty():
            took.append(self._followup_queue.get_nowait())
        return {"answer": f"took: {took}"}

    def close(self):
        pass


@pytest.fixture
def live_gaia_sidecar(monkeypatch):
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
    monkeypatch.setattr(server_mod, "build_query_agent", _FollowUpAgent)
    monkeypatch.setattr(sr, "build_session_agent", _FollowUpAgent)
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
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sr.registry.clear()
        caller_auth.reset()


@pytest.fixture
def chat(monkeypatch, tmp_path):
    """A flagship chat relayed to the sidecar, and a way to send it a turn."""
    root = tmp_path / "agents"
    (root / "gaia").mkdir(parents=True)
    (root / "gaia" / ".installed").write_text(
        json.dumps(
            {
                "id": "gaia",
                "version": "0.2.0",
                "language": "python",
                "installed_at": "2026-09-23T00:00:00Z",
                "artifact_sha256": "0" * 64,
                "artifact_kind": "binary",
                "executable": "gaia-agent-gaia",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "gaia.hub.installer.default_install_root", lambda: root, raising=False
    )
    registry = AgentRegistry()
    registry._agents.clear()
    register_installed_sidecars(registry)
    session_permissions.reset_all()

    async def _noop_retitle(**_kwargs):
        return None

    monkeypatch.setattr(chat_helpers_module, "_agent_registry", registry)
    monkeypatch.setattr(
        chat_helpers_module, "_maybe_update_session_title", _noop_retitle
    )
    db = ChatDatabase(":memory:")
    session_id = db.create_session(agent_type="gaia")["id"]

    def send(message):
        db.add_message(session_id, "user", message)
        session = db.get_session(session_id)
        request = ChatRequest(
            session_id=session_id, message=message, stream=True, agent_type="gaia"
        )
        events: list = []

        async def _drive():
            async for chunk in _stream_chat_impl(
                SimpleNamespace(handler=None), db, session, request
            ):
                for line in str(chunk).splitlines():
                    if line.startswith("data: "):
                        events.append(json.loads(line[len("data: ") :]))

        asyncio.run(_drive())
        return events

    yield SimpleNamespace(send=send, session_id=session_id, db=db)
    session_permissions.reset_all()
    db.close()


def _point_ui_at(monkeypatch, base_url, api_version):
    class _Handle:
        def __init__(self):
            self.api_version = api_version

        def proxy(self):
            return SidecarProxy(base_url, agent_id="gaia")

    monkeypatch.setattr(daemon_client_module, "acquire_handle", lambda _id: _Handle())


def _deliver_once_running(session_id, text, timeout=10):
    """Deliver as soon as the sidecar run exists; before that it answers 404."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if followups.deliver(session_id, text):
                return True
        except SidecarHTTPError as exc:
            if exc.status_code != 404:
                raise
        time.sleep(0.02)
    return False


def test_a_followup_reaches_the_running_sidecar_turn(
    monkeypatch, live_gaia_sidecar, chat
):
    from gaia_agent.server import API_VERSION

    _point_ui_at(monkeypatch, live_gaia_sidecar, API_VERSION)
    result: dict = {}
    turn = threading.Thread(
        target=lambda: result.setdefault("events", chat.send("summarize it")),
        daemon=True,
    )
    turn.start()

    assert _deliver_once_running(chat.session_id, "also the totals")
    turn.join(timeout=20)
    assert not turn.is_alive()

    events = result["events"]
    assert {"type": "followup_taken", "text": "also the totals"} in events
    assert not [e for e in events if e["type"] == "followup_unavailable"]
    answers = [e["content"] for e in events if e["type"] == "answer"]
    assert answers[-1] == "took: ['also the totals']"
    saved = [(m["role"], m["content"]) for m in chat.db.get_messages(chat.session_id)]
    assert saved == [
        ("user", "summarize it"),
        ("user", "also the totals"),
        ("assistant", "took: ['also the totals']"),
    ]
    assert followups.last_outcome(chat.session_id) == {
        "taken": ["also the totals"],
        "returned": [],
    }


def test_a_sidecar_without_followups_says_so_up_front(
    monkeypatch, live_gaia_sidecar, chat
):
    """2.14 has no /followup: the chat is told, and nothing is offered to it."""
    _point_ui_at(monkeypatch, live_gaia_sidecar, "2.14")
    seen: list = []

    def answer_at_once(self, query, max_steps=None):
        seen.append(followups.deliver(chat.session_id, "hi"))
        return {"answer": "done"}

    monkeypatch.setattr(_FollowUpAgent, "process_query", answer_at_once)
    events = chat.send("hello")

    assert {"type": "followup_unavailable"} in events
    assert seen == [False]
