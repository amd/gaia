# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
""" "Always allow" on a relayed (sidecar) flagship's permission prompt.

The relay used to drop ``always_scope`` because an "always" grant would live
in the sidecar, where the chat could neither list nor revoke it. From contract
2.19 the sidecar lists and revokes its grants, so the chat offers "always",
relays it, and keeps those grants in its own permission list. These pin that
the offer, the decision, the list and the revoke all line up, and that "always"
is never offered where the user could not see or undo it.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import gaia.ui._chat_helpers as chat_helpers_module
from gaia.agents.registry import AgentRegistry
from gaia.hub.installer import register_installed_sidecars
from gaia.ui import _chat_helpers as helpers
from gaia.ui import permissions as session_permissions
from gaia.ui.database import ChatDatabase
from gaia.ui.email_sidecar import daemon_client as daemon_client_module
from gaia.ui.email_sidecar import grants as relayed_grants
from gaia.ui.email_sidecar import relay
from gaia.ui.email_sidecar.errors import SidecarError, SidecarHTTPError
from gaia.ui.email_sidecar.profiles import (
    EMAIL_PROFILE,
    GAIA_PROFILE,
    grants_supported,
)
from gaia.ui.email_sidecar.proxy import SidecarProxy
from gaia.ui.models import ChatRequest
from gaia.ui.routers.chat import router as chat_router
from gaia.ui.sse_handler import SSEOutputHandler

pytestmark = pytest.mark.allow_network

_CONFIRM = {
    "type": "needs_confirmation",
    "run_id": "r1",
    "action": "write_file",
    "summary": "Write notes.md?",
    "confirm_id": "c-1",
    "always_scope": "write_file on notes.md",
    "always_label": "editing notes.md",
}
_GRANT = {"key": "write_file:notes.md", "label": "write_file on notes.md"}


@pytest.fixture(autouse=True)
def _clean_permissions():
    session_permissions.reset_all()
    yield
    session_permissions.reset_all()


@pytest.mark.parametrize(
    "version, expected",
    [("2.19", True), ("3.0", True), ("2.18", False), (None, False), ("x", False)],
)
def test_only_a_flagship_at_2_19_lists_its_grants(version, expected):
    assert grants_supported(GAIA_PROFILE, version) is expected


def test_email_never_offers_always():
    assert grants_supported(EMAIL_PROFILE, "9.9") is False


class _Proxy:
    """Records what the chat asks the sidecar; holds its grant list."""

    agent_id = "gaia"

    def __init__(self, grants=None, error=None):
        self.decisions: list = []
        self.revoked: list = []
        self.grants = list(grants or [])
        self.error = error

    def tool_decision(self, run_id, decision, confirm_id):
        self.decisions.append((run_id, decision, confirm_id))
        if decision == "always":
            self.grants.append(dict(_GRANT))
        return {"run_id": run_id, "decision": decision, "delivered": True}

    def list_grants(self, session_id):
        if self.error is not None:
            raise self.error
        return {"session_id": session_id, "grants": list(self.grants)}

    def revoke_grant(self, session_id, key=None):
        if self.error is not None:
            raise self.error
        self.revoked.append((session_id, key))
        self.grants = [g for g in self.grants if key is not None and g["key"] != key]
        return {"session_id": session_id, "grants": list(self.grants)}


def _drain(handler):
    out = []
    while not handler.event_queue.empty():
        out.append(handler.event_queue.get_nowait())
    return out


# ── The prompt the chat shows ──────────────────────────────────────────────


class TestRelayOffersAlways:
    def test_a_scoped_prompt_offers_always_with_its_wording(self):
        handler = SSEOutputHandler()
        relay._dispatch_one(handler, dict(_CONFIRM), GAIA_PROFILE, True, True)

        [event] = _drain(handler)
        assert event["always_scope"] == "write_file on notes.md"
        assert event["always_label"] == "editing notes.md"
        assert handler.relay_always_scope("c-1") == "write_file on notes.md"

    def test_not_offered_when_the_sidecar_cannot_list_grants(self):
        handler = SSEOutputHandler()
        relay._dispatch_one(handler, dict(_CONFIRM), GAIA_PROFILE, True, False)

        [event] = _drain(handler)
        assert "always_scope" not in event and "always_label" not in event
        assert handler.relay_always_scope("c-1") is None

    @pytest.mark.parametrize(
        "tool",
        [
            "allow_path_access",
            "share_engineering_context",
            "append_engineering_context",
            "approve_engineering_code",
            "capture_skill",
        ],
    )
    def test_never_offered_for_a_call_that_needs_fresh_consent(self, tool):
        handler = SSEOutputHandler()
        event = {**_CONFIRM, "action": tool}
        relay._dispatch_one(handler, event, GAIA_PROFILE, True, True)

        assert "always_scope" not in _drain(handler)[0]
        assert handler.relay_always_scope("c-1") is None

    def test_not_offered_for_a_prompt_without_a_scope(self):
        handler = SSEOutputHandler()
        event = {k: v for k, v in _CONFIRM.items() if k != "always_scope"}
        relay._dispatch_one(handler, event, GAIA_PROFILE, True, True)

        assert "always_scope" not in _drain(handler)[0]


# ── /api/chat/confirm-tool and the permission routes ───────────────────────


@pytest.fixture
def app_client():
    db = ChatDatabase(":memory:")
    app = FastAPI()
    app.include_router(chat_router)
    app.state.db = db
    app.state.session_locks = {}
    session_id = db.create_session(agent_type="gaia")["id"]
    yield TestClient(app), db, session_id
    helpers._active_sse_handlers.pop(session_id, None)
    db.close()


def _parked(session_id, proxy, scope="write_file on notes.md"):
    handler = SSEOutputHandler()
    handler.active_relay_proxy = proxy
    handler.active_relay_run_id = "r1"
    handler.set_relay_confirmation("c-1", scope)
    helpers._active_sse_handlers[session_id] = handler
    return handler


class TestAlwaysThroughTheChat:
    def test_always_reaches_the_sidecar_and_is_listed(self, app_client):
        client, _, sid = app_client
        proxy = _Proxy()
        _parked(sid, proxy)

        r = client.post(
            "/api/chat/confirm-tool",
            json={
                "session_id": sid,
                "approved": True,
                "always": True,
                "confirm_id": "c-1",
            },
        )

        assert r.status_code == 200, r.text
        assert r.json() == {
            "status": "ok",
            "approved": True,
            "granted": "write_file on notes.md",
        }
        assert proxy.decisions == [("r1", "always", "c-1")]
        view = client.get(f"/api/chat/permissions/{sid}").json()
        assert view["grants"] == [_GRANT]
        listed = client.get("/api/chat/permissions").json()["sessions"]
        assert [s["grants"] for s in listed if s["session_id"] == sid] == [[_GRANT]]

    def test_always_on_a_prompt_that_offered_none_is_refused(self, app_client):
        client, _, sid = app_client
        proxy = _Proxy()
        handler = _parked(sid, proxy, scope=None)

        r = client.post(
            "/api/chat/confirm-tool",
            json={
                "session_id": sid,
                "approved": True,
                "always": True,
                "confirm_id": "c-1",
            },
        )

        assert r.status_code == 409
        assert "allow once or deny" in r.json()["detail"]
        assert proxy.decisions == []
        assert handler.relay_confirmation_pending()

    def test_always_for_a_stale_prompt_is_stale(self, app_client):
        client, _, sid = app_client
        proxy = _Proxy()
        _parked(sid, proxy)

        r = client.post(
            "/api/chat/confirm-tool",
            json={
                "session_id": sid,
                "approved": True,
                "always": True,
                "confirm_id": "old",
            },
        )

        assert r.status_code == 409
        assert "no longer waiting" in r.json()["detail"]
        assert proxy.decisions == []

    def test_revoking_a_relayed_grant_goes_through_the_sidecar(self, app_client):
        client, _, sid = app_client
        proxy = _Proxy(grants=[_GRANT])
        _parked(sid, proxy)
        relayed_grants.refresh(proxy, sid)

        r = client.delete(
            f"/api/chat/permissions/{sid}/grants", params={"key": _GRANT["key"]}
        )

        assert r.status_code == 200, r.text
        assert proxy.revoked == [(sid, _GRANT["key"])]
        assert r.json()["grants"] == []

    def test_a_revoke_the_sidecar_did_not_take_stays_listed(self, app_client):
        client, _, sid = app_client
        proxy = _Proxy(grants=[_GRANT])
        _parked(sid, proxy)
        relayed_grants.refresh(proxy, sid)
        proxy.error = SidecarError("connection refused")

        r = client.delete(f"/api/chat/permissions/{sid}/grants")

        assert r.status_code == 502
        assert "still allowed" in r.json()["detail"]
        assert client.get(f"/api/chat/permissions/{sid}").json()["grants"] == [_GRANT]

    def test_a_grant_the_sidecar_no_longer_holds_is_dropped(self, app_client):
        client, _, sid = app_client
        proxy = _Proxy(grants=[_GRANT])
        _parked(sid, proxy)
        relayed_grants.refresh(proxy, sid)
        proxy.error = SidecarHTTPError(404, "No session")

        r = client.delete(
            f"/api/chat/permissions/{sid}/grants", params={"key": _GRANT["key"]}
        )

        assert r.status_code == 200, r.text
        assert r.json()["grants"] == []

    def test_with_no_turn_running_the_revoke_reaches_the_sidecar_via_the_daemon(
        self, app_client, monkeypatch
    ):
        client, _, sid = app_client
        proxy = _Proxy(grants=[_GRANT])
        relayed_grants.refresh(proxy, sid)
        monkeypatch.setattr(
            daemon_client_module,
            "acquire_handle",
            lambda agent_id: SimpleNamespace(proxy=lambda: proxy),
        )

        r = client.delete(
            f"/api/chat/permissions/{sid}/grants", params={"key": _GRANT["key"]}
        )

        assert r.status_code == 200, r.text
        assert proxy.revoked == [(sid, _GRANT["key"])]


# ── Keeping the list true before each relayed turn ─────────────────────────


class TestReconcile:
    def test_a_grant_the_chat_does_not_list_is_revoked(self):
        other = {"key": "run_shell_command:pytest", "label": "pytest"}
        proxy = _Proxy(grants=[_GRANT, other])
        session_permissions.for_session("s1").set_relayed_grants("gaia", [_GRANT])

        relayed_grants.reconcile(proxy, "s1")

        assert proxy.revoked == [("s1", other["key"])]
        assert session_permissions.for_session("s1").grants() == [_GRANT]

    def test_a_grant_the_sidecar_lost_is_dropped(self):
        proxy = _Proxy(error=SidecarHTTPError(404, "No session"))
        session_permissions.for_session("s1").set_relayed_grants("gaia", [_GRANT])

        relayed_grants.reconcile(proxy, "s1")

        assert session_permissions.for_session("s1").grants() == []

    def test_an_unreadable_sidecar_raises(self):
        proxy = _Proxy(error=SidecarHTTPError(500, "boom"))
        with pytest.raises(SidecarError):
            relayed_grants.reconcile(proxy, "s1")


# ── End to end against the real flagship sidecar app ───────────────────────


class _WritingAgent:
    """Asks to write notes.md each turn and answers with whether it may."""

    def __init__(self, **_kwargs):
        self.console = None
        self.conversation_history = []
        self._cancel_event = None
        self._followup_queue = None

    def process_query(self, query, max_steps=None):
        approved = self.console.confirm_tool_execution(
            "write_file", {"file_path": "notes.md", "content": "hi"}
        )
        return {"answer": f"approved={approved}"}

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
    monkeypatch.setattr(server_mod, "build_query_agent", _WritingAgent)
    monkeypatch.setattr(sr, "build_session_agent", _WritingAgent)
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
        yield base_url, server_mod.API_VERSION
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sr.registry.clear()
        caller_auth.reset()


@pytest.fixture
def relayed_chat(monkeypatch, tmp_path, live_gaia_sidecar):
    base_url, api_version = live_gaia_sidecar
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

    async def _noop_retitle(**_kwargs):
        return None

    monkeypatch.setattr(chat_helpers_module, "_agent_registry", registry)
    monkeypatch.setattr(
        chat_helpers_module, "_maybe_update_session_title", _noop_retitle
    )
    monkeypatch.setattr(
        daemon_client_module,
        "acquire_handle",
        lambda _id: SimpleNamespace(
            api_version=api_version,
            proxy=lambda: SidecarProxy(base_url, agent_id="gaia"),
        ),
    )
    db = ChatDatabase(":memory:")
    session_id = db.create_session(agent_type="gaia")["id"]
    app = FastAPI()
    app.include_router(chat_router)
    app.state.db = db
    app.state.session_locks = {}
    client = TestClient(app)

    def send(message, answer=None):
        """Run one turn; answer its prompt (allow / always / deny) if it asks."""
        db.add_message(session_id, "user", message)
        session = db.get_session(session_id)
        request = ChatRequest(
            session_id=session_id, message=message, stream=True, agent_type="gaia"
        )
        events: list = []

        async def _drive():
            async for chunk in chat_helpers_module._stream_chat_impl(
                SimpleNamespace(handler=None), db, session, request
            ):
                for line in str(chunk).splitlines():
                    if line.startswith("data: "):
                        events.append(json.loads(line[len("data: ") :]))

        turn = threading.Thread(target=lambda: asyncio.run(_drive()), daemon=True)
        turn.start()
        if answer is not None:
            deadline = time.monotonic() + 10
            prompt = None
            while prompt is None and time.monotonic() < deadline:
                prompt = next(
                    (e for e in list(events) if e.get("type") == "permission_request"),
                    None,
                )
                time.sleep(0.02)
            assert prompt is not None, f"the turn never asked: {events}"
            r = client.post(
                "/api/chat/confirm-tool",
                json={
                    "session_id": session_id,
                    "approved": answer != "deny",
                    "always": answer == "always",
                    "confirm_id": prompt["confirm_id"],
                },
            )
            assert r.status_code == 200, r.text
        turn.join(timeout=20)
        assert not turn.is_alive()
        return events

    yield SimpleNamespace(send=send, client=client, session_id=session_id)
    db.close()


def _answer(events):
    return [e["content"] for e in events if e.get("type") == "answer"][-1]


def test_always_allow_on_the_relayed_flagship_is_listed_honoured_and_revocable(
    relayed_chat,
):
    chat = relayed_chat
    first = chat.send("write notes", answer="always")
    [prompt] = [e for e in first if e["type"] == "permission_request"]
    assert prompt["always_scope"] == "write_file on notes.md"
    assert _answer(first) == "approved=True"

    grants = chat.client.get(f"/api/chat/permissions/{chat.session_id}").json()[
        "grants"
    ]
    assert [g["label"] for g in grants] == ["write_file on notes.md"]

    second = chat.send("write it again")
    assert not [e for e in second if e["type"] == "permission_request"]
    assert _answer(second) == "approved=True"

    r = chat.client.delete(
        f"/api/chat/permissions/{chat.session_id}/grants",
        params={"key": grants[0]["key"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["grants"] == []

    third = chat.send("and again", answer="deny")
    assert [e for e in third if e["type"] == "permission_request"]
    assert _answer(third) == "approved=False"


def test_a_grant_from_before_the_backend_restarted_is_revoked_before_the_turn(
    relayed_chat,
):
    chat = relayed_chat
    chat.send("write notes", answer="always")
    # A restarted backend has no record of the grant the sidecar still holds.
    session_permissions.reset_all()

    events = chat.send("write it again", answer="deny")

    assert [
        e for e in events if e["type"] == "permission_request"
    ], "an unlisted grant must not approve silently"
    assert _answer(events) == "approved=False"
