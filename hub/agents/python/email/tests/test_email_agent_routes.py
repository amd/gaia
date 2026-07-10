# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for the stateful agent surface (``/v1/email/agent/*``, #1666 follow-up).

These exercise the sidecar's session-scoped agent host — session lifecycle, the
SSE ``/query`` stream, blocking tool-confirmation over HTTP, and the runtime
memory toggle — WITHOUT Lemonade or Gmail. ``build_session_agent`` is swapped for
a fake agent that drives the real ``SSEOutputHandler`` the routes use, so the
streaming + confirmation machinery is genuinely exercised.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

# parents[0] = tests/, [5] = repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")
pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent_email import agent_routes  # noqa: E402

# ---------------------------------------------------------------------------
# Fake agent (drives the real SSEOutputHandler)
# ---------------------------------------------------------------------------


class _FakeAgent:
    """Stand-in for EmailTriageAgent that exercises the route machinery.

    ``process_query`` drives ``self.console`` (the SSEOutputHandler the route
    assigns) exactly as the real agent loop does, so streaming + confirmation are
    tested for real.
    """

    def __init__(self, *, memory_available: bool = True, confirm_tool=None) -> None:
        self.console = None
        self._memory_available = memory_available
        self._enabled = memory_available
        self.confirm_tool = confirm_tool  # (tool_name, args) to trigger a gate
        self.queries: list[str] = []
        self.closed = False

    def process_query(self, message: str) -> dict:
        self.queries.append(message)
        if self.console is not None:
            self.console.print_thought(f"thinking about: {message}")
        approved = None
        if self.confirm_tool is not None and self.console is not None:
            approved = self.console.confirm_tool_execution(
                self.confirm_tool[0], self.confirm_tool[1], timeout=5
            )
        answer = f"done: {message}" if approved is None else f"approved={approved}"
        return {"answer": answer}

    def set_memory_enabled(self, enabled: bool) -> dict:
        if not self._memory_available:
            return {
                "ok": not enabled,
                "enabled": False,
                "available": False,
                "message": "Memory is unavailable this session.",
            }
        self._enabled = enabled
        return {
            "ok": True,
            "enabled": enabled,
            "available": True,
            "message": "Memory is enabled." if enabled else "Memory is disabled.",
        }

    def memory_status(self) -> dict:
        enabled = self._enabled and self._memory_available
        return {
            "enabled": enabled,
            "available": self._memory_available,
            "message": "status",
        }

    def close_db(self) -> None:
        self.closed = True


@pytest.fixture
def client(monkeypatch):
    """A TestClient over an app mounting only the agent router, with a fresh
    registry and a fake-agent factory."""
    # Fresh registry per test — the registry is module-global.
    monkeypatch.setattr(
        agent_routes, "registry", agent_routes._SessionRegistry(), raising=True
    )

    built: dict = {}

    def _factory(**kwargs):
        agent = built.get("next") or _FakeAgent()
        built["last"] = agent
        built.pop("next", None)
        return agent

    monkeypatch.setattr(agent_routes, "build_session_agent", _factory, raising=True)

    app = FastAPI()
    app.include_router(agent_routes.router)
    tc = TestClient(app)
    tc.built = built  # test hook to preset / inspect the fake agent
    tc.app_ref = app  # so tests can spin a second client for concurrent calls
    return tc


def _sse_events(resp) -> list[dict]:
    """Parse an SSE response body into a list of event dicts."""
    events = []
    for line in resp.iter_lines():
        if not line:
            continue
        text = line if isinstance(line, str) else line.decode()
        if text.startswith("data: "):
            events.append(json.loads(text[6:]))
    return events


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


class TestSessionLifecycle:
    def test_create_session_builds_agent(self, client):
        r = client.post("/v1/email/agent/session", json={"session_id": "s1"})
        assert r.status_code == 200
        body = r.json()
        assert body["session_id"] == "s1"
        assert body["created"] is True
        assert body["memory"]["available"] is True

    def test_create_is_idempotent(self, client):
        client.post("/v1/email/agent/session", json={"session_id": "s1"})
        r = client.post("/v1/email/agent/session", json={"session_id": "s1"})
        assert r.json()["created"] is False

    def test_delete_session(self, client):
        client.post("/v1/email/agent/session", json={"session_id": "s1"})
        r = client.request("DELETE", "/v1/email/agent/session/s1")
        assert r.status_code == 200 and r.json()["deleted"] is True
        # second delete → 404
        r2 = client.request("DELETE", "/v1/email/agent/session/s1")
        assert r2.status_code == 404

    def test_history_404_without_session(self, client):
        r = client.get("/v1/email/agent/session/nope/history")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Query streaming
# ---------------------------------------------------------------------------


class TestQueryStream:
    def test_query_streams_and_records_history(self, client):
        with client.stream(
            "POST",
            "/v1/email/agent/query",
            json={"session_id": "s1", "message": "hi"},
        ) as resp:
            assert resp.status_code == 200
            events = _sse_events(resp)

        types = [e["type"] for e in events]
        assert "thinking" in types
        final = [e for e in events if e["type"] == "run_complete"]
        assert final and final[0]["answer"] == "done: hi"

        # History is now readable on the session.
        h = client.get("/v1/email/agent/session/s1/history").json()
        assert h["turns"] == [{"user": "hi", "assistant": "done: hi"}]

    def test_overlapping_turn_rejected(self, client):
        # Hold the run lock as if a turn were in progress.
        session = client_registry(client).get_or_create("s1")
        session.run_lock.acquire()
        try:
            r = client.post(
                "/v1/email/agent/query",
                json={"session_id": "s1", "message": "hi"},
            )
            assert r.status_code == 409
        finally:
            session.run_lock.release()

    def test_setup_failure_releases_lock(self, client, monkeypatch):
        """If run setup fails (here: handler construction) before the worker
        thread owns the lock, run_lock must be released so the session isn't
        permanently wedged at 409 (PR #1966 review). Patching the handler (not
        threading) keeps TestClient's own threads working."""
        import gaia.ui.sse_handler as sse_mod

        real_handler = sse_mod.SSEOutputHandler

        def _boom(*a, **k):
            raise RuntimeError("cannot build handler")

        monkeypatch.setattr(sse_mod, "SSEOutputHandler", _boom)
        r = client.post(
            "/v1/email/agent/query", json={"session_id": "s1", "message": "hi"}
        )
        assert r.status_code == 500
        # Lock must be free — the session can run again once setup works.
        session = agent_routes.registry.get("s1")
        assert session is not None and not session.is_running()
        # Restore ONLY the handler (monkeypatch.undo would also revert the
        # fixture's build_session_agent/registry patches).
        monkeypatch.setattr(sse_mod, "SSEOutputHandler", real_handler)
        with client.stream(
            "POST", "/v1/email/agent/query", json={"session_id": "s1", "message": "hi"}
        ) as resp:
            assert resp.status_code == 200
            assert any(e["type"] == "run_complete" for e in _sse_events(resp))


# ---------------------------------------------------------------------------
# Tool confirmation over HTTP
# ---------------------------------------------------------------------------


class TestToolConfirmation:
    """The blocking tool-confirmation gate, over HTTP.

    TestClient buffers the SSE body until the stream completes, so a sibling
    thread can't observe ``permission_request`` mid-run. Instead we detect the
    pending gate by polling the registry's live handler (the agent thread is
    blocked in ``confirm_tool_execution`` with ``_confirm_id`` set), then release
    it via ``POST /confirm-tool`` on a SECOND client (avoids sharing one httpx
    client across threads). Events are asserted after the stream completes.
    """

    def _run_query_in_thread(self, client, events_out):
        def _consume():
            with client.stream(
                "POST",
                "/v1/email/agent/query",
                json={"session_id": "s1", "message": "send it"},
            ) as resp:
                for line in resp.iter_lines():
                    text = line if isinstance(line, str) else (line or b"").decode()
                    if text.startswith("data: "):
                        events_out.append(json.loads(text[6:]))

        t = threading.Thread(target=_consume, daemon=True)
        t.start()
        return t

    def _wait_for_pending_gate(self, timeout=5.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            session = agent_routes.registry.get("s1")
            handler = getattr(session, "handler", None) if session else None
            if handler is not None and getattr(handler, "_confirm_id", None):
                return True
            time.sleep(0.02)
        return False

    def test_approve_releases_gated_tool(self, client):
        client.built["next"] = _FakeAgent(confirm_tool=("send_now", {"to": "a@b.com"}))
        events: list[dict] = []
        t = self._run_query_in_thread(client, events)

        assert self._wait_for_pending_gate(), "agent never blocked on confirmation"

        confirm_client = TestClient(client.app_ref)
        r = confirm_client.post(
            "/v1/email/agent/confirm-tool",
            json={"session_id": "s1", "approved": True},
        )
        assert r.status_code == 200 and r.json()["approved"] is True

        t.join(timeout=5)
        assert any(e["type"] == "permission_request" for e in events)
        final = [e for e in events if e["type"] == "run_complete"]
        assert final and final[0]["answer"] == "approved=True"

    def test_deny_blocks_gated_tool(self, client):
        client.built["next"] = _FakeAgent(confirm_tool=("send_now", {"to": "a@b.com"}))
        events: list[dict] = []
        t = self._run_query_in_thread(client, events)

        assert self._wait_for_pending_gate(), "agent never blocked on confirmation"

        confirm_client = TestClient(client.app_ref)
        confirm_client.post(
            "/v1/email/agent/confirm-tool",
            json={"session_id": "s1", "approved": False},
        )
        t.join(timeout=5)
        final = [e for e in events if e["type"] == "run_complete"]
        assert final and final[0]["answer"] == "approved=False"

    def test_confirm_without_active_run_404(self, client):
        client.post("/v1/email/agent/session", json={"session_id": "s1"})
        r = client.post(
            "/v1/email/agent/confirm-tool",
            json={"session_id": "s1", "approved": True},
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Memory toggle over HTTP (#1666)
# ---------------------------------------------------------------------------


class TestMemoryOverHttp:
    def test_toggle_and_status(self, client):
        client.post("/v1/email/agent/session", json={"session_id": "s1"})

        off = client.post(
            "/v1/email/agent/memory", json={"session_id": "s1", "enabled": False}
        )
        assert off.status_code == 200 and off.json()["enabled"] is False

        status = client.get("/v1/email/agent/memory/s1").json()
        assert status["enabled"] is False

        on = client.post(
            "/v1/email/agent/memory", json={"session_id": "s1", "enabled": True}
        )
        assert on.status_code == 200 and on.json()["enabled"] is True

    def test_enable_when_unavailable_conflicts(self, client):
        client.built["next"] = _FakeAgent(memory_available=False)
        client.post("/v1/email/agent/session", json={"session_id": "s1"})
        r = client.post(
            "/v1/email/agent/memory", json={"session_id": "s1", "enabled": True}
        )
        # Cannot enable memory that was never initialized → reported loudly.
        assert r.status_code == 409

    def test_memory_endpoints_404_without_session(self, client):
        assert (
            client.post(
                "/v1/email/agent/memory", json={"session_id": "x", "enabled": True}
            ).status_code
            == 404
        )
        assert client.get("/v1/email/agent/memory/x").status_code == 404


def client_registry(client) -> "agent_routes._SessionRegistry":
    """The registry the client's app is bound to (monkeypatched per test)."""
    return agent_routes.registry
