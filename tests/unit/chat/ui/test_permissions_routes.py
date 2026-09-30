# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Per-chat permission mode and "always allow" grants in the Agent UI.

Covers :mod:`gaia.ui.permissions`, the confirm-tool / permissions routes in
``gaia.ui.routers.chat``, and the wiring that attaches a chat's permissions to
each turn's handler and forgets them when the chat is deleted.
"""

import json
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia import config as config_mod
from gaia.ui import _chat_helpers as helpers
from gaia.ui import permissions
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest
from gaia.ui.routers.chat import router as chat_router
from gaia.ui.sse_handler import SSEOutputHandler

SCOPED_CALL = ("run_shell_command", {"command": "gh issue list --limit 5"})
SCOPED_KEY = "run_shell_command:gh issue list"
UNSCOPED_CALL = ("run_shell_command", {"command": "bash -c 'rm -rf x'"})


@pytest.fixture(autouse=True)
def _fresh_registry():
    permissions.reset_all()
    yield
    permissions.reset_all()


def _write_config(data):
    config_mod.GAIA_CONFIG_FILE.write_text(json.dumps(data), encoding="utf-8")


# ── SessionPermissions ─────────────────────────────────────────────────────


class TestSessionPermissions:
    def test_new_chat_asks(self):
        assert permissions.SessionPermissions().mode == permissions.MODE_ASK

    def test_set_mode_round_trips(self):
        perms = permissions.SessionPermissions()
        perms.set_mode(permissions.MODE_FULL_ACCESS)
        assert perms.mode == permissions.MODE_FULL_ACCESS
        perms.set_mode(permissions.MODE_ASK)
        assert perms.mode == permissions.MODE_ASK

    def test_unknown_mode_is_refused_and_leaves_mode_alone(self):
        perms = permissions.SessionPermissions()
        with pytest.raises(ValueError, match="Unknown permission mode"):
            perms.set_mode("yolo")
        assert perms.mode == permissions.MODE_ASK

    def test_grant_uses_the_invocation_scope(self):
        perms = permissions.SessionPermissions()
        assert perms.grant(*SCOPED_CALL) == "gh issue list"
        assert perms.grants() == [{"key": SCOPED_KEY, "label": "gh issue list"}]

    def test_unscoped_call_grants_nothing(self):
        perms = permissions.SessionPermissions()
        assert perms.grant(*UNSCOPED_CALL) is None
        assert perms.grant("read_file", {"path": "a.txt"}) is None
        assert perms.grants() == []

    def test_revoke_one_and_all(self):
        perms = permissions.SessionPermissions()
        perms.grant(*SCOPED_CALL)
        perms.grant("write_file", {"file_path": "notes.md"})
        assert perms.revoke("no-such-key") == 0
        assert perms.revoke(SCOPED_KEY) == 1
        assert [g["key"] for g in perms.grants()] == ["write_file:notes.md"]
        assert perms.revoke() == 1
        assert perms.grants() == []

    def test_attach_hands_the_turn_mode_grants_and_timeout(self):
        perms = permissions.SessionPermissions(full_access=True)
        perms.grant(*SCOPED_CALL)
        handler = SSEOutputHandler()
        perms.attach(handler)
        assert handler.auto_approve_gated_tools is True
        assert handler.full_access is True
        assert handler.confirm_timeout_seconds == permissions.CONFIRM_TIMEOUT_SECONDS
        assert permissions.CONFIRM_TIMEOUT_SECONDS == 600
        assert SCOPED_KEY in handler.session_grants()

    def test_mode_change_reaches_the_attached_turn(self):
        perms = permissions.SessionPermissions()
        handler = SSEOutputHandler()
        perms.attach(handler)
        assert handler.full_access is False
        perms.set_mode(permissions.MODE_FULL_ACCESS)
        assert handler.auto_approve_gated_tools is True
        assert handler.full_access is True

    def test_revoke_reaches_the_attached_turn(self):
        perms = permissions.SessionPermissions()
        perms.grant(*SCOPED_CALL)
        handler = SSEOutputHandler()
        perms.attach(handler)
        perms.revoke(SCOPED_KEY)
        assert SCOPED_KEY not in handler.session_grants()
        assert not handler.call_is_granted(*SCOPED_CALL)

    def test_revoke_all_reaches_the_attached_turn(self):
        perms = permissions.SessionPermissions()
        perms.grant(*SCOPED_CALL)
        handler = SSEOutputHandler()
        perms.attach(handler)
        perms.revoke()
        assert handler.session_grants() == set()

    def test_detach_stops_updates(self):
        perms = permissions.SessionPermissions()
        handler = SSEOutputHandler()
        perms.attach(handler)
        perms.detach(handler)
        perms.set_mode(permissions.MODE_FULL_ACCESS)
        assert handler.full_access is False

    def test_detach_of_a_stale_handler_keeps_the_live_one(self):
        perms = permissions.SessionPermissions()
        old, live = SSEOutputHandler(), SSEOutputHandler()
        perms.attach(old)
        perms.attach(live)
        perms.detach(old)
        perms.set_mode(permissions.MODE_FULL_ACCESS)
        assert live.full_access is True

    def test_full_access_runs_a_gated_tool_without_a_prompt(self):
        perms = permissions.SessionPermissions(full_access=True)
        handler = SSEOutputHandler()
        perms.attach(handler)
        assert handler.confirm_tool_execution(*UNSCOPED_CALL, timeout=0.2) is True
        events = []
        while not handler.event_queue.empty():
            events.append(handler.event_queue.get_nowait())
        assert not any(e.get("type") == "permission_request" for e in events)

    def test_a_carried_grant_approves_the_same_call_in_a_later_turn(self):
        perms = permissions.SessionPermissions()
        perms.grant(*SCOPED_CALL)
        next_turn = SSEOutputHandler()
        perms.attach(next_turn)
        assert next_turn.confirm_tool_execution(*SCOPED_CALL, timeout=0.2) is True


class TestRegistry:
    def test_default_is_ask_without_config(self):
        assert permissions.for_session("s1").mode == permissions.MODE_ASK

    def test_default_follows_config_full_access(self):
        _write_config({"full_access": True})
        assert permissions.for_session("s1").mode == permissions.MODE_FULL_ACCESS

    def test_same_session_same_state(self):
        assert permissions.for_session("s1") is permissions.for_session("s1")
        assert permissions.for_session("s1") is not permissions.for_session("s2")

    def test_all_sessions_is_a_snapshot(self):
        permissions.for_session("s1")
        snapshot = permissions.all_sessions()
        permissions.for_session("s2")
        assert set(snapshot) == {"s1"}

    def test_forget_session_drops_grants(self):
        permissions.for_session("s1").grant(*SCOPED_CALL)
        permissions.forget_session("s1")
        permissions.forget_session("never-existed")
        assert "s1" not in permissions.all_sessions()
        assert permissions.for_session("s1").grants() == []


# ── Routes ─────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(chat_router)
    app.state.session_locks = {}
    app.state.chat_semaphore = None
    return TestClient(app)


@pytest.fixture
def live_prompt():
    """A handler blocked on a real permission prompt for a scoped call."""
    handler = SSEOutputHandler()
    session_id = "perm-session"
    helpers._active_sse_handlers[session_id] = handler
    permissions.for_session(session_id).attach(handler)
    state = {}

    def _ask(call=SCOPED_CALL):
        def run():
            state["result"] = handler.confirm_tool_execution(*call, timeout=5)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        deadline = time.time() + 2
        while handler._confirm_id is None and time.time() < deadline:
            time.sleep(0.01)
        assert handler._confirm_id is not None
        state["thread"] = thread
        return handler._confirm_id

    yield SimpleNamespace(handler=handler, session_id=session_id, ask=_ask, state=state)
    handler.cancelled.set()
    if "thread" in state:
        state["thread"].join(timeout=3)
    helpers._active_sse_handlers.pop(session_id, None)


def _answer(live, state_key="result"):
    live.state["thread"].join(timeout=3)
    assert not live.state["thread"].is_alive()
    return live.state[state_key]


class TestConfirmTool:
    def test_allow_once(self, client, live_prompt):
        confirm_id = live_prompt.ask()
        resp = client.post(
            "/api/chat/confirm-tool",
            json={
                "session_id": live_prompt.session_id,
                "approved": True,
                "confirm_id": confirm_id,
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "approved": True, "granted": None}
        assert _answer(live_prompt) is True
        assert permissions.for_session(live_prompt.session_id).grants() == []

    def test_always_grants_the_scope_for_the_rest_of_the_chat(
        self, client, live_prompt
    ):
        confirm_id = live_prompt.ask()
        resp = client.post(
            "/api/chat/confirm-tool",
            json={
                "session_id": live_prompt.session_id,
                "approved": True,
                "always": True,
                "confirm_id": confirm_id,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["granted"] == "gh issue list"
        assert _answer(live_prompt) is True
        perms = permissions.for_session(live_prompt.session_id)
        assert perms.grants() == [{"key": SCOPED_KEY, "label": "gh issue list"}]
        # The next turn's handler inherits it.
        next_turn = SSEOutputHandler()
        perms.attach(next_turn)
        assert next_turn.call_is_granted(*SCOPED_CALL)

    def test_always_for_an_unscoped_call_is_refused_and_the_prompt_stays(
        self, client, live_prompt
    ):
        confirm_id = live_prompt.ask(UNSCOPED_CALL)
        resp = client.post(
            "/api/chat/confirm-tool",
            json={
                "session_id": live_prompt.session_id,
                "approved": True,
                "always": True,
                "confirm_id": confirm_id,
            },
        )
        assert resp.status_code == 409
        assert "allow once or deny" in resp.json()["detail"]
        assert not live_prompt.handler._confirm_event.is_set()
        assert permissions.for_session(live_prompt.session_id).grants() == []

    def test_deny_with_always_grants_nothing(self, client, live_prompt):
        confirm_id = live_prompt.ask()
        resp = client.post(
            "/api/chat/confirm-tool",
            json={
                "session_id": live_prompt.session_id,
                "approved": False,
                "always": True,
                "confirm_id": confirm_id,
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "approved": False, "granted": None}
        assert _answer(live_prompt) is False
        assert permissions.for_session(live_prompt.session_id).grants() == []

    def test_stale_confirm_id_is_refused(self, client, live_prompt):
        live_prompt.ask()
        resp = client.post(
            "/api/chat/confirm-tool",
            json={
                "session_id": live_prompt.session_id,
                "approved": True,
                "confirm_id": "an-older-prompt",
            },
        )
        assert resp.status_code == 409
        assert "no longer waiting" in resp.json()["detail"]
        assert not live_prompt.handler._confirm_event.is_set()

    def test_stale_always_does_not_grant_the_newer_prompt(self, client, live_prompt):
        """A late "always" click must not grant whatever prompt replaced it."""
        live_prompt.ask()
        resp = client.post(
            "/api/chat/confirm-tool",
            json={
                "session_id": live_prompt.session_id,
                "approved": True,
                "always": True,
                "confirm_id": "an-older-prompt",
            },
        )
        assert resp.status_code == 409
        assert permissions.for_session(live_prompt.session_id).grants() == []
        assert SCOPED_KEY not in live_prompt.handler.session_grants()
        assert not live_prompt.handler._confirm_event.is_set()

    def test_always_on_a_prompt_that_expired_meanwhile_grants_nothing(
        self, client, live_prompt, monkeypatch
    ):
        confirm_id = live_prompt.ask()
        # The prompt times out between the scope check and the answer.
        monkeypatch.setattr(
            live_prompt.handler, "resolve_tool_confirmation", lambda *a, **k: False
        )
        resp = client.post(
            "/api/chat/confirm-tool",
            json={
                "session_id": live_prompt.session_id,
                "approved": True,
                "always": True,
                "confirm_id": confirm_id,
            },
        )
        assert resp.status_code == 409
        assert permissions.for_session(live_prompt.session_id).grants() == []

    def test_unknown_session_is_404(self, client):
        resp = client.post(
            "/api/chat/confirm-tool",
            json={"session_id": "nope", "approved": True},
        )
        assert resp.status_code == 404


class TestPermissionRoutes:
    def test_get_defaults_to_ask(self, client):
        resp = client.get("/api/chat/permissions/s1")
        assert resp.status_code == 200
        assert resp.json() == {"session_id": "s1", "mode": "ask", "grants": []}

    def test_put_switches_mode(self, client):
        resp = client.put("/api/chat/permissions/s1", json={"mode": "full_access"})
        assert resp.status_code == 200
        assert resp.json()["mode"] == "full_access"
        assert permissions.for_session("s1").mode == "full_access"

    def test_put_unknown_mode_is_422(self, client):
        resp = client.put("/api/chat/permissions/s1", json={"mode": "everything"})
        assert resp.status_code == 422
        assert "Unknown permission mode" in resp.json()["detail"]
        assert permissions.for_session("s1").mode == "ask"

    def test_list_shows_only_chats_with_something_to_show(self, client):
        permissions.for_session("plain")
        permissions.for_session("granted").grant(*SCOPED_CALL)
        permissions.for_session("open").set_mode("full_access")
        resp = client.get("/api/chat/permissions")
        assert resp.status_code == 200
        listed = {s["session_id"]: s for s in resp.json()["sessions"]}
        assert set(listed) == {"granted", "open"}
        assert listed["granted"]["grants"][0]["key"] == SCOPED_KEY
        assert listed["open"]["mode"] == "full_access"

    def test_delete_one_grant(self, client):
        perms = permissions.for_session("s1")
        perms.grant(*SCOPED_CALL)
        perms.grant("write_file", {"file_path": "notes.md"})
        resp = client.delete(
            "/api/chat/permissions/s1/grants", params={"key": SCOPED_KEY}
        )
        assert resp.status_code == 200
        assert [g["key"] for g in resp.json()["grants"]] == ["write_file:notes.md"]

    def test_delete_unknown_grant_is_404(self, client):
        resp = client.delete("/api/chat/permissions/s1/grants", params={"key": "x"})
        assert resp.status_code == 404

    def test_delete_all_grants(self, client):
        permissions.for_session("s1").grant(*SCOPED_CALL)
        resp = client.delete("/api/chat/permissions/s1/grants")
        assert resp.status_code == 200
        assert resp.json()["grants"] == []


# ── Wiring ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_each_turn_handler_is_attached_then_detached():
    db = ChatDatabase(":memory:")
    try:
        session = db.create_session()
        sid = session["id"]
        perms = permissions.for_session(sid)
        perms.grant(*SCOPED_CALL)
        run = SimpleNamespace(handler=None)
        gen = helpers._stream_chat_impl(
            run, db, session, ChatRequest(session_id=sid, message="hi", stream=True)
        )
        await anext(gen)
        handler = helpers._active_sse_handlers[sid]
        assert perms._handler is handler
        assert handler.confirm_timeout_seconds == 600
        assert SCOPED_KEY in handler.session_grants()
        await gen.aclose()
        assert perms._handler is None
        assert sid not in helpers._active_sse_handlers
    finally:
        db.close()


@pytest.mark.allow_network
def test_deleting_a_chat_forgets_its_permissions():
    from gaia.ui.server import create_app

    app = create_app(db_path=":memory:")
    with TestClient(app) as test_client:
        sid = test_client.post(
            "/api/sessions", json={}, headers={"X-Gaia-UI": "1"}
        ).json()["id"]
        permissions.for_session(sid).grant(*SCOPED_CALL)
        resp = test_client.delete(f"/api/sessions/{sid}", headers={"X-Gaia-UI": "1"})
        assert resp.status_code == 200
    assert sid not in permissions.all_sessions()
