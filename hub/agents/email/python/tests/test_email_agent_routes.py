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

from gaia.connectors.errors import (  # noqa: E402
    ConnectionRevokedError,
    ConnectorsError,
    ScopeMismatchError,
)

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

    # -- Autonomy surface --------------------------------------------------
    _autonomy_level = "off"

    def autonomy_status(self) -> dict:
        return {
            "level": self._autonomy_level,
            "enabled": self._autonomy_level != "off",
            "trust_min_samples": 5,
            "trust_threshold": 0.85,
            "trusted_scope_count": 0,
            "scopes": [],
        }

    def set_autonomy_level(self, level: str) -> dict:
        if level not in ("off", "suggest", "earn_trust", "full"):
            raise ValueError(f"bad level {level!r}")
        self._autonomy_level = level
        return {"level": level, "enabled": level != "off"}

    def run_autonomy_cycle(self, context=None) -> dict:
        return {
            "level": self._autonomy_level,
            "executed": [],
            "proposals": [],
            "skipped": 0,
        }

    def undo_autonomy_action(self, action_id: str) -> dict:
        """Routing-only fake — no real ledger logic, matching the style of
        run_autonomy_cycle/autonomy_status above. Two sentinel ids let route
        tests drive the RuntimeError -> 409 / ValueError -> 400 mapping
        without a real trust ledger."""
        if action_id == "raise-runtime":
            raise RuntimeError("undo window has expired")
        if action_id == "raise-value":
            raise ValueError("bad action_id")
        if action_id == "raise-connectors":
            raise ConnectorsError(
                "no forwarded 'microsoft' credential is available to the "
                "email sidecar. The connection may not be granted to this "
                "agent, or it was revoked/withdrawn. Connect and grant it "
                "in one command — no Agent UI required: `gaia connectors "
                "connect microsoft --scopes <scopes> --grant-agent "
                "installed:email`, or use Settings -> Connections in the "
                "Agent UI."
            )
        if action_id == "raise-revoked":
            raise ConnectionRevokedError("microsoft")
        if action_id == "raise-scope-mismatch":
            raise ScopeMismatchError(
                required=["mail.read"], granted=[], provider="microsoft"
            )
        return {
            "action_id": action_id,
            "action_type": "archive",
            "message_id": "m1",
            "undone": True,
            "correction_captured": True,
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


# ---------------------------------------------------------------------------
# Autonomy control surface (#1483 / #1115)
# ---------------------------------------------------------------------------


class TestAutonomyRoutes:
    def _mk(self, client, sid="s1"):
        client.post("/v1/email/agent/session", json={"session_id": sid})

    def test_status_reports_level(self, client):
        self._mk(client)
        r = client.get("/v1/email/agent/autonomy/s1")
        assert r.status_code == 200
        body = r.json()
        assert body["level"] == "off"
        assert body["enabled"] is False
        assert "scopes" in body

    def test_status_404_without_session(self, client):
        r = client.get("/v1/email/agent/autonomy/nope")
        assert r.status_code == 404

    def test_set_level_resume_then_kill(self, client):
        self._mk(client)
        r = client.post(
            "/v1/email/agent/autonomy",
            json={"session_id": "s1", "level": "earn_trust"},
        )
        assert r.status_code == 200 and r.json()["level"] == "earn_trust"
        # Kill switch.
        r2 = client.post(
            "/v1/email/agent/autonomy", json={"session_id": "s1", "level": "off"}
        )
        assert r2.json()["enabled"] is False

    def test_set_level_rejects_bad_value(self, client):
        self._mk(client)
        r = client.post(
            "/v1/email/agent/autonomy", json={"session_id": "s1", "level": "turbo"}
        )
        assert r.status_code == 400

    def test_run_cycle_returns_report_when_enabled(self, client):
        self._mk(client)
        client.post(
            "/v1/email/agent/autonomy",
            json={"session_id": "s1", "level": "earn_trust"},
        )
        r = client.post("/v1/email/agent/autonomy/run", json={"session_id": "s1"})
        assert r.status_code == 200
        body = r.json()
        assert "executed" in body and "proposals" in body

    def test_run_cycle_404_without_session(self, client):
        r = client.post("/v1/email/agent/autonomy/run", json={"session_id": "nope"})
        assert r.status_code == 404

    def test_run_cycle_refused_while_off(self, client):
        """#2528: a session's default level is 'off' — /run must refuse with an
        actionable error naming the current level, not silently return the
        same 200 shape a real (found-nothing) run would."""
        self._mk(client)
        r = client.post("/v1/email/agent/autonomy/run", json={"session_id": "s1"})
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert "off" in detail
        assert "/v1/email/agent/autonomy" in detail

    def test_run_cycle_refused_while_off_after_explicit_kill(self, client):
        """Same refusal after the level was explicitly killed mid-session, not
        just at the untouched default."""
        self._mk(client)
        client.post(
            "/v1/email/agent/autonomy",
            json={"session_id": "s1", "level": "earn_trust"},
        )
        client.post(
            "/v1/email/agent/autonomy", json={"session_id": "s1", "level": "off"}
        )
        r = client.post("/v1/email/agent/autonomy/run", json={"session_id": "s1"})
        assert r.status_code == 409

    def test_undo_returns_report(self, client):
        self._mk(client)
        r = client.post(
            "/v1/email/agent/autonomy/undo",
            json={"session_id": "s1", "action_id": "a1"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "undone" in body
        assert "correction_captured" in body

    def test_undo_404_without_session(self, client):
        r = client.post(
            "/v1/email/agent/autonomy/undo",
            json={"session_id": "nope", "action_id": "a1"},
        )
        # An unmatched path 404s too — pin the actual detail text so this
        # only goes green once the route exists AND does its own session
        # lookup (matching the "No such session." convention used by every
        # other route in this file), not by accident of the path missing.
        assert r.status_code == 404
        assert r.json()["detail"] == "No such session."

    def test_undo_409_on_runtime_error(self, client):
        self._mk(client)
        r = client.post(
            "/v1/email/agent/autonomy/undo",
            json={"session_id": "s1", "action_id": "raise-runtime"},
        )
        assert r.status_code == 409

    def test_undo_400_on_value_error(self, client):
        self._mk(client)
        r = client.post(
            "/v1/email/agent/autonomy/undo",
            json={"session_id": "s1", "action_id": "raise-value"},
        )
        assert r.status_code == 400

    def test_run_cycle_500_with_connectors_error_detail(self, client):
        """#2617: a bare ``ConnectorsError`` escaping ``run_autonomy_cycle``
        must not become a textless HTTP 500 — the route must catch it and
        re-raise as an ``HTTPException`` carrying the actionable connectors
        message (the base ``ConnectorsError`` maps to 500 per the table in
        ``src/gaia/ui/routers/connectors.py``, but with a real body).

        Uses a second TestClient with ``raise_server_exceptions=False``:
        with the default client, an exception unhandled by the route
        propagates as a raw Python exception through TestClient rather than
        becoming an HTTP response at all, which would defeat a status-code
        assertion entirely.
        """

        class _ConnectorsErrorAgent(_FakeAgent):
            def run_autonomy_cycle(self, context=None):
                raise ConnectorsError(
                    "no forwarded 'microsoft' credential is available to "
                    "the email sidecar. The connection may not be granted "
                    "to this agent, or it was revoked/withdrawn. Connect "
                    "and grant it in one command — no Agent UI required: "
                    "`gaia connectors connect microsoft --scopes <scopes> "
                    "--grant-agent installed:email`, or use Settings -> "
                    "Connections in the Agent UI."
                )

        client.built["next"] = _ConnectorsErrorAgent()
        self._mk(client)
        client.post(
            "/v1/email/agent/autonomy",
            json={"session_id": "s1", "level": "earn_trust"},
        )
        insecure = TestClient(client.app_ref, raise_server_exceptions=False)
        r = insecure.post("/v1/email/agent/autonomy/run", json={"session_id": "s1"})
        assert r.status_code == 500
        detail = r.json()["detail"]
        assert "gaia connectors connect" in detail
        assert "microsoft" in detail

    def test_undo_500_with_connectors_error_detail(self, client):
        """Same #2617 contract as the /run test above, for /undo."""
        self._mk(client)
        insecure = TestClient(client.app_ref, raise_server_exceptions=False)
        r = insecure.post(
            "/v1/email/agent/autonomy/undo",
            json={"session_id": "s1", "action_id": "raise-connectors"},
        )
        assert r.status_code == 500
        detail = r.json()["detail"]
        assert "gaia connectors connect" in detail
        assert "microsoft" in detail

    def test_run_cycle_401_on_connection_revoked_error(self, client):
        """#2617: ``ConnectionRevokedError`` is a ``ConnectorsError`` SIBLING
        of ``AuthRequiredError`` (errors.py:159), not a subclass — it must
        still map to 401 per the canonical table, not fall through to 500.
        A revoked mailbox grant is the headline scenario for this issue."""

        class _RevokedAgent(_FakeAgent):
            def run_autonomy_cycle(self, context=None):
                raise ConnectionRevokedError("microsoft")

        client.built["next"] = _RevokedAgent()
        self._mk(client)
        client.post(
            "/v1/email/agent/autonomy",
            json={"session_id": "s1", "level": "earn_trust"},
        )
        insecure = TestClient(client.app_ref, raise_server_exceptions=False)
        r = insecure.post("/v1/email/agent/autonomy/run", json={"session_id": "s1"})
        assert r.status_code == 401
        assert "gaia connectors connect" in r.json()["detail"]

    def test_run_cycle_403_on_scope_mismatch_error(self, client):
        """Same sibling-not-subclass gap as above, for ``ScopeMismatchError``
        (errors.py:175) — must map to 403, not fall through to 500."""

        class _ScopeMismatchAgent(_FakeAgent):
            def run_autonomy_cycle(self, context=None):
                raise ScopeMismatchError(
                    required=["mail.read"],
                    granted=[],
                    provider="microsoft",
                )

        client.built["next"] = _ScopeMismatchAgent()
        self._mk(client)
        client.post(
            "/v1/email/agent/autonomy",
            json={"session_id": "s1", "level": "earn_trust"},
        )
        insecure = TestClient(client.app_ref, raise_server_exceptions=False)
        r = insecure.post("/v1/email/agent/autonomy/run", json={"session_id": "s1"})
        assert r.status_code == 403
        assert "mail.read" in r.json()["detail"]

    def test_undo_401_on_connection_revoked_error(self, client):
        """Same #2617 sibling-mapping contract as the /run test, for /undo."""
        self._mk(client)
        insecure = TestClient(client.app_ref, raise_server_exceptions=False)
        r = insecure.post(
            "/v1/email/agent/autonomy/undo",
            json={"session_id": "s1", "action_id": "raise-revoked"},
        )
        assert r.status_code == 401

    def test_undo_403_on_scope_mismatch_error(self, client):
        """Same #2617 sibling-mapping contract as the /run test, for /undo."""
        self._mk(client)
        insecure = TestClient(client.app_ref, raise_server_exceptions=False)
        r = insecure.post(
            "/v1/email/agent/autonomy/undo",
            json={"session_id": "s1", "action_id": "raise-scope-mismatch"},
        )
        assert r.status_code == 403

    def test_run_cycle_503_on_agent_local_configuration_error_cold_start(
        self, client, monkeypatch
    ):
        """#2617 follow-up: ``gaia_agent_email.config.ConfigurationError`` is a
        SEPARATE class from ``gaia.connectors.errors.ConfigurationError`` --
        a bare ``ValueError`` subclass sharing nothing in its MRO with
        ``ConnectorsError``. ``EmailAgentConfig.resolve_mail_backends()``
        raises it for real in the actual cold-start state (no mailbox
        connected yet), and it escaped the route's ``except ConnectorsError``
        as a textless 500 -- byte-identical to the bug #2617 exists to fix.

        Drives the REAL ``resolve_mail_backends()`` (no injected exception):
        every other test in this class injects a hand-raised
        ``ConnectorsError``/sibling from a fake agent, which is exactly why
        this shipped -- an injected-exception test can't catch a wrong
        exception CLASS. The null keyring backend (reset via
        ``keyring.core._keyring_backend = None`` so the new env var actually
        takes effect) makes "no mailbox connected" the real, hermetic state
        instead of depending on this machine's OS credential store.
        """
        import keyring
        from gaia_agent_email.config import EmailAgentConfig

        monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "null")
        monkeypatch.setattr(keyring.core, "_keyring_backend", None, raising=False)

        class _ColdStartAgent(_FakeAgent):
            def run_autonomy_cycle(self, context=None):
                # No mocking: no mailbox connected under the null keyring
                # backend, so this raises the real agent-local
                # ConfigurationError, not a stand-in.
                return EmailAgentConfig().resolve_mail_backends()

        client.built["next"] = _ColdStartAgent()
        self._mk(client)
        client.post(
            "/v1/email/agent/autonomy",
            json={"session_id": "s1", "level": "earn_trust"},
        )
        insecure = TestClient(client.app_ref, raise_server_exceptions=False)
        r = insecure.post("/v1/email/agent/autonomy/run", json={"session_id": "s1"})
        assert r.status_code == 503
        assert "No mailbox connected" in r.json()["detail"]

    def test_run_cycle_returns_200_with_partial_report_on_per_message_error(
        self, client
    ):
        """#2625: a per-message failure inside the cycle must not surface as
        a bare 500 at the HTTP boundary. ``/autonomy/run`` declares no
        ``response_model``, so a report carrying the new ``errors``/
        ``stopped`` keys passes through verbatim with a 200."""
        self._mk(client)
        client.post(
            "/v1/email/agent/autonomy",
            json={"session_id": "s1", "level": "full"},
        )
        partial_report = {
            "level": "full",
            "executed": [{"message_id": "m1", "action": "archive"}],
            "proposals": [],
            "decisions": [],
            "skipped": 0,
            "already_proposed": 0,
            "errors": [
                {
                    "message_id": "m2",
                    "error_type": "ConnectionError",
                    "error": "gmail: 502 Bad Gateway",
                }
            ],
            "stopped": None,
        }
        agent = client.built["last"]
        agent.run_autonomy_cycle = lambda context=None: partial_report
        r = client.post("/v1/email/agent/autonomy/run", json={"session_id": "s1"})
        assert r.status_code == 200
        body = r.json()
        assert body["executed"] == partial_report["executed"]
        assert body["errors"] == partial_report["errors"]
        assert body["stopped"] is None

    def test_kill_broadcasts_to_every_other_session(self, client):
        """#2624/adversarial-C4: the kill can hit a different agent object
        than the one actually running a cycle — both routes resolve against
        the module-level registry by session_id, and nothing reconciles a
        CLI kill fired at the default 'cli' id against a cycle running
        under a different (e.g. Agent-UI) session. Killing one session must
        stop every OTHER live session too, not just the one named."""
        self._mk(client, "s1")
        self._mk(client, "s2")
        client.post(
            "/v1/email/agent/autonomy",
            json={"session_id": "s1", "level": "earn_trust"},
        )
        client.post(
            "/v1/email/agent/autonomy", json={"session_id": "s2", "level": "full"}
        )

        r = client.post(
            "/v1/email/agent/autonomy", json={"session_id": "s1", "level": "off"}
        )
        assert r.json()["enabled"] is False

        # s2 was never explicitly killed — the broadcast must have stopped
        # it anyway.
        r2 = client.get("/v1/email/agent/autonomy/s2")
        assert r2.json()["level"] == "off"
        assert r2.json()["enabled"] is False


class TestWorkerDiesWithoutTerminalEvent:
    """The stream must report a dead worker, not close on a blank answer.

    ``_run_agent`` catches ``Exception`` and always emits a terminal event, so
    the only way to reach the drain loop's fallback is a ``BaseException``
    escaping the worker (the thread dies, ``finally`` still frees the lock, no
    event is ever queued). That path used to synthesize
    ``{"type": "run_complete", "answer": ""}`` — a well-formed *successful*
    completion carrying an empty reply, indistinguishable to any client from
    the agent genuinely having nothing to say.
    """

    def test_dead_worker_streams_an_error_before_completing(self, client):
        class _Killed:
            console = None

            def process_query(self, *a, **k):
                raise KeyboardInterrupt("worker killed outside the error path")

        client.built["next"] = _Killed()
        with client.stream(
            "POST", "/v1/email/agent/query", json={"session_id": "s1", "message": "hi"}
        ) as resp:
            assert resp.status_code == 200
            events = _sse_events(resp)

        types = [e["type"] for e in events]
        assert "error" in types, (
            "the stream closed without an error event — a client cannot tell a "
            f"dead worker from an empty reply. Got: {types}"
        )
        assert types.index("error") < types.index("run_complete")

        message = next(e["message"] for e in events if e["type"] == "error")
        # Actionable: names what happened and where to look next.
        assert "without producing an answer" in message
        assert "gaia-agent-email serve" in message

    def test_dead_worker_still_frees_the_session_lock(self, client):
        class _Killed:
            console = None

            def process_query(self, *a, **k):
                raise KeyboardInterrupt("boom")

        client.built["next"] = _Killed()
        with client.stream(
            "POST", "/v1/email/agent/query", json={"session_id": "s1", "message": "hi"}
        ) as resp:
            _sse_events(resp)

        session = agent_routes.registry.get("s1")
        assert session is not None and not session.is_running()


# ---------------------------------------------------------------------------
# Idle-TTL reaper + LRU cap (#2829)
#
# session_id turned a per-turn local variable (refcount-freed) into a
# permanent root reference in _sessions — each retained EmailTriageAgent
# holds a WAL sqlite connection, a memory-store DB + embedder, and connector
# backends. These operate on standalone _SessionRegistry() instances (no
# HTTP, no shared module-global) so the clock can be controlled deterministically.
# ---------------------------------------------------------------------------


class TestSessionReaper:
    def _registry(self, monkeypatch, **kwargs):
        reg = agent_routes._SessionRegistry(**kwargs)
        monkeypatch.setattr(
            agent_routes, "build_session_agent", lambda **k: _FakeAgent(), raising=True
        )
        return reg

    def test_idle_session_past_ttl_is_reaped(self, monkeypatch):
        reg = self._registry(monkeypatch, idle_ttl_seconds=10)
        clock = [1000.0]
        monkeypatch.setattr(agent_routes.time, "monotonic", lambda: clock[0])

        session = reg.get_or_create("s1")
        clock[0] += 11
        evicted = reg.reap()

        assert evicted == ["s1"]
        assert reg.get("s1") is None
        assert session.agent.closed is True

    def test_session_within_ttl_survives_a_reap_sweep(self, monkeypatch):
        reg = self._registry(monkeypatch, idle_ttl_seconds=100)
        clock = [1000.0]
        monkeypatch.setattr(agent_routes.time, "monotonic", lambda: clock[0])

        session = reg.get_or_create("s1")
        clock[0] += 10
        reg.reap()

        assert reg.get("s1") is session

    def test_locked_session_is_never_reaped_even_past_ttl(self, monkeypatch):
        reg = self._registry(monkeypatch, idle_ttl_seconds=5)
        clock = [1000.0]
        monkeypatch.setattr(agent_routes.time, "monotonic", lambda: clock[0])

        session = reg.get_or_create("s1")
        session.run_lock.acquire()
        try:
            clock[0] += 1000
            reg.reap()
            assert reg.get("s1") is session
        finally:
            session.run_lock.release()

    def test_get_or_create_touches_last_used_so_active_sessions_never_expire(
        self, monkeypatch
    ):
        reg = self._registry(monkeypatch, idle_ttl_seconds=10)
        clock = [1000.0]
        monkeypatch.setattr(agent_routes.time, "monotonic", lambda: clock[0])

        session = reg.get_or_create("s1")
        for _ in range(3):
            clock[0] += 9  # always under the TTL between touches
            reg.get_or_create("s1")  # a "turn" — touches last_used
        reg.reap()

        assert reg.get("s1") is session

    def test_lru_cap_evicts_the_oldest_unlocked_session(self, monkeypatch):
        reg = self._registry(monkeypatch, idle_ttl_seconds=10_000, max_sessions=2)
        clock = [1000.0]
        monkeypatch.setattr(agent_routes.time, "monotonic", lambda: clock[0])

        s1 = reg.get_or_create("s1")
        clock[0] += 1
        reg.get_or_create("s2")
        clock[0] += 1
        reg.get_or_create("s3")  # over cap -> evicts s1, the oldest unlocked

        assert reg.get("s1") is None
        assert s1.agent.closed is True
        assert reg.get("s2") is not None
        assert reg.get("s3") is not None

    def test_lru_cap_raises_an_actionable_error_when_everything_is_locked(
        self, monkeypatch
    ):
        reg = self._registry(monkeypatch, idle_ttl_seconds=10_000, max_sessions=1)
        monkeypatch.setattr(agent_routes.time, "monotonic", lambda: 1000.0)

        s1 = reg.get_or_create("s1")
        s1.run_lock.acquire()
        try:
            with pytest.raises(RuntimeError, match="session"):
                reg.get_or_create("s2")
        finally:
            s1.run_lock.release()
        # Refused, not silently created past the cap.
        assert reg.get("s2") is None

    def test_delete_clears_last_used_so_a_reused_id_is_not_reaped_prematurely(
        self, monkeypatch
    ):
        reg = self._registry(monkeypatch, idle_ttl_seconds=10)
        clock = [1000.0]
        monkeypatch.setattr(agent_routes.time, "monotonic", lambda: clock[0])

        reg.get_or_create("s1")
        reg.delete("s1")
        clock[0] += 1
        session = reg.get_or_create("s1")  # a brand new session under the same id
        clock[0] += 5  # well under the TTL from the NEW session's creation
        reg.reap()

        assert reg.get("s1") is session
