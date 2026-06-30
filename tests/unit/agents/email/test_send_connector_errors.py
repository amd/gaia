# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for connector-error translation in POST /v1/email/send (#1877).

Fix 1: the send handler must translate connector-layer errors into actionable
HTTP responses rather than letting FastAPI return a bare 500.

  - AuthRequiredError  → 403 (agent has no grant)
  - ScopeMismatchError → 403 (grant exists but lacks send scope)
  - ConnectionRevokedError → 403 (tokens revoked remotely)
  - ConfigurationError → 503 (backend misconfigured)
  - ConnectorsError (base) → 502 (transient backend failure)

HTTPExceptions raised by _resolve_backend_for_provider (0 providers → 503,
2+ providers → 400) must pass through unchanged — they are already HTTPExceptions
and are NOT ConnectorsError subclasses, so the except chain leaves them alone.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("gaia_agent_email")

import gaia_agent_email.api_routes as email_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.connectors.errors import (
    AuthRequiredError,
    ConfigurationError,
    ConnectionRevokedError,
    ConnectorsError,
    ScopeMismatchError,
)


def _build_client(monkeypatch, send_backend_factory):
    """Return a TestClient where the send backend raises on send_message."""

    class _FakeBackend:
        def send_message(self, *, to, subject, body, **_kw):
            return send_backend_factory()

    # Patch the module-level indirection so _resolve_backend_for_provider returns
    # our fake (which will call send_backend_factory() on send_message).
    actual_backend = _FakeBackend()
    monkeypatch.setattr(
        email_routes,
        "_resolve_backend_for_provider",
        lambda provider=None: actual_backend,
    )

    app = FastAPI()
    app.include_router(email_routes.router)
    return TestClient(app, raise_server_exceptions=False)


def _draft_and_send(client, *, raise_on_send):
    """Issue draft then send with a factory that raises on send_message."""
    # Draft
    draft_resp = client.post(
        "/v1/email/draft",
        json={"to": [{"email": "bob@example.com"}], "subject": "Hi", "body": "Hello"},
    )
    assert draft_resp.status_code == 200, draft_resp.text
    token = draft_resp.json()["confirmation_token"]

    # Send
    return client.post(
        "/v1/email/send",
        json={
            "to": [{"email": "bob@example.com"}],
            "subject": "Hi",
            "body": "Hello",
            "confirmation_token": token,
        },
    )


class TestSendConnectorErrorTranslation:
    """Connector errors raised during send must become actionable HTTP responses."""

    def _client_raising(self, monkeypatch, exc):
        def _factory():
            raise exc

        class _FakeBackend:
            def send_message(self, *, to, subject, body, **_kw):
                _factory()

        monkeypatch.setattr(
            email_routes,
            "_resolve_backend_for_provider",
            lambda provider=None: _FakeBackend(),
        )
        app = FastAPI()
        app.include_router(email_routes.router)
        return TestClient(app, raise_server_exceptions=False)

    def test_auth_required_error_becomes_403(self, monkeypatch):
        exc = AuthRequiredError(
            AuthRequiredError.Reason.AGENT_NOT_GRANTED,
            provider="google",
            agent_id="installed:email",
        )
        client = self._client_raising(monkeypatch, exc)
        resp = _draft_and_send(client, raise_on_send=exc)
        assert resp.status_code == 403, resp.text
        assert "installed:email" in resp.json()["detail"]

    def test_scope_mismatch_error_becomes_403(self, monkeypatch):
        exc = ScopeMismatchError(
            required=["https://www.googleapis.com/auth/gmail.send"],
            granted=["https://www.googleapis.com/auth/gmail.modify"],
            provider="google",
        )
        client = self._client_raising(monkeypatch, exc)
        resp = _draft_and_send(client, raise_on_send=exc)
        assert resp.status_code == 403, resp.text

    def test_connection_revoked_error_becomes_403(self, monkeypatch):
        exc = ConnectionRevokedError("google")
        client = self._client_raising(monkeypatch, exc)
        resp = _draft_and_send(client, raise_on_send=exc)
        assert resp.status_code == 403, resp.text

    def test_configuration_error_becomes_503(self, monkeypatch):
        exc = ConfigurationError("Backend config is missing")
        client = self._client_raising(monkeypatch, exc)
        resp = _draft_and_send(client, raise_on_send=exc)
        assert resp.status_code == 503, resp.text

    def test_base_connectors_error_becomes_502(self, monkeypatch):
        exc = ConnectorsError("Transient Gmail API failure")
        client = self._client_raising(monkeypatch, exc)
        resp = _draft_and_send(client, raise_on_send=exc)
        assert resp.status_code == 502, resp.text

    def test_resolution_503_still_propagates(self, monkeypatch):
        """0 providers → 503 from _resolve_backend_for_provider must survive unchanged."""
        from fastapi import HTTPException

        monkeypatch.setattr(
            email_routes,
            "_resolve_backend_for_provider",
            lambda provider=None: (_ for _ in ()).throw(
                HTTPException(503, "No mailbox connected")
            ),
        )
        app = FastAPI()
        app.include_router(email_routes.router)
        client = TestClient(app, raise_server_exceptions=False)
        resp = _draft_and_send(client, raise_on_send=None)
        assert resp.status_code == 503, resp.text

    def test_resolution_400_still_propagates(self, monkeypatch):
        """2+ providers → 400 from _resolve_backend_for_provider must survive unchanged."""
        from fastapi import HTTPException

        monkeypatch.setattr(
            email_routes,
            "_resolve_backend_for_provider",
            lambda provider=None: (_ for _ in ()).throw(
                HTTPException(400, "Multiple mailboxes connected")
            ),
        )
        app = FastAPI()
        app.include_router(email_routes.router)
        client = TestClient(app, raise_server_exceptions=False)
        resp = _draft_and_send(client, raise_on_send=None)
        assert resp.status_code == 400, resp.text

    def test_gate_rejects_before_backend(self, monkeypatch):
        """Confirmation gate must still fire BEFORE any backend resolution."""
        backend_calls = []

        class _FakeBackend:
            def send_message(self, *, to, subject, body, **_kw):
                backend_calls.append(True)
                return {"id": "ok"}

        monkeypatch.setattr(
            email_routes,
            "_resolve_backend_for_provider",
            lambda provider=None: _FakeBackend(),
        )
        app = FastAPI()
        app.include_router(email_routes.router)
        client = TestClient(app, raise_server_exceptions=False)

        # Send with no token → gate must reject with 403 before backend is touched.
        resp = client.post(
            "/v1/email/send",
            json={
                "to": [{"email": "bob@example.com"}],
                "subject": "Hi",
                "body": "Hello",
            },
        )
        assert resp.status_code == 403, resp.text
        assert backend_calls == [], "Backend was called before gate check"
