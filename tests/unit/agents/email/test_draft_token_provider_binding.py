# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Draft-token provider binding tests (#1603, M5).

A draft request may carry an optional ``provider`` field ("google" or
"microsoft"). When set, the issued confirmation token is bound to that
provider. When the send echoes the token, it routes to THAT provider's
backend — even when both mailboxes are connected.

Scenarios:
  - draft with provider="microsoft" → token bound to microsoft
  - send echoing that token → routes to Outlook even with both connected
  - send with a provider not connected → 400
  - send with a provider that differs from the token's binding → 403 (gate)
  - one-mailbox draft without provider → unchanged behavior (D2 count logic)
  - draft with provider="google" → token bound to google, routes to Gmail
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("gaia_agent_email")

import gaia_agent_email.api_routes as email_routes
from fastapi.testclient import TestClient

# The FastAPI app the email router is mounted on.
try:
    from gaia.api.openai_server import app
except ImportError:
    app = None


@pytest.fixture
def client(monkeypatch):
    """TestClient with a fake backend seam for each provider."""
    if app is None:
        pytest.skip("gaia.api.openai_server not available")
    pytest.importorskip("gaia_agent_email")
    return TestClient(app)


@pytest.fixture
def fake_backends(monkeypatch):
    """Track which backend was used for the send call."""
    calls = []

    class _FakeGmail:
        def send_message(self, *, to, subject, body, **_kw):
            calls.append("google")
            return {"id": "gmail-sent-id", "to": to, "subject": subject}

    class _FakeOutlook:
        def send_message(self, *, to, subject, body, **_kw):
            calls.append("microsoft")
            return {"id": "", "sent": True, "to": to, "subject": subject}

    def _build_backend(providers):
        if not providers:
            from fastapi import HTTPException

            raise HTTPException(503, "No mailbox connected")
        if len(providers) > 1:
            from fastapi import HTTPException

            raise HTTPException(400, f"Multiple mailboxes: {providers}")
        p = providers[0]
        if p == "google":
            return _FakeGmail()
        if p == "microsoft":
            return _FakeOutlook()
        from fastapi import HTTPException

        raise HTTPException(503, f"Unknown provider: {p}")

    return calls, _FakeGmail, _FakeOutlook, _build_backend


class TestDraftTokenProviderBinding:
    """Provider binding on the draft is echoed through the token and used on send."""

    def test_draft_model_accepts_provider_field(self):
        """EmailDraftRequest must accept an optional provider field."""
        req = email_routes.EmailDraftRequest(
            to=[{"email": "x@example.com"}],
            subject="s",
            body="b",
            provider="microsoft",
        )
        assert req.provider == "microsoft"

    def test_draft_model_provider_defaults_to_none(self):
        req = email_routes.EmailDraftRequest(
            to=[{"email": "x@example.com"}],
            subject="s",
            body="b",
        )
        assert req.provider is None

    def test_send_model_accepts_provider_field(self):
        req = email_routes.EmailSendRequest(
            to=[{"email": "x@example.com"}],
            subject="s",
            body="b",
            provider="google",
        )
        assert req.provider == "google"

    def test_microsoft_draft_routes_to_outlook_even_with_both_connected(
        self, monkeypatch, fake_backends
    ):
        """draft(provider=microsoft) → token → send → Outlook backend."""
        if app is None:
            pytest.skip("gaia.api.openai_server not available")
        calls, _FG, _FO, _build = fake_backends

        # Both mailboxes are "connected" from the provider-count perspective.
        monkeypatch.setattr(
            email_routes,
            "connected_mailbox_providers",
            lambda: ["google", "microsoft"],
        )

        # Backend resolver: when provider binding overrides, it calls with a
        # single-element list. We track via calls.
        def _resolve_with_provider(provider=None):
            if provider == "microsoft":
                return _FO()
            if provider == "google":
                return _FG()
            return _build(["google", "microsoft"])  # would 400 without binding

        monkeypatch.setattr(
            email_routes, "_resolve_backend_for_provider", _resolve_with_provider
        )

        client = TestClient(app)
        draft_resp = client.post(
            "/v1/email/draft",
            json={
                "to": [{"email": "bob@example.com"}],
                "subject": "Hello",
                "body": "World",
                "provider": "microsoft",
            },
        )
        assert draft_resp.status_code == 200, draft_resp.text
        token = draft_resp.json()["confirmation_token"]

        send_resp = client.post(
            "/v1/email/send",
            json={
                "to": [{"email": "bob@example.com"}],
                "subject": "Hello",
                "body": "World",
                "confirmation_token": token,
            },
        )
        assert send_resp.status_code == 200, send_resp.text
        assert calls == ["microsoft"], f"Expected Outlook send, got {calls}"

    def test_send_with_provider_not_connected_returns_400(self, monkeypatch):
        """Sending via microsoft when only google is connected → 400."""
        if app is None:
            pytest.skip("gaia.api.openai_server not available")
        monkeypatch.setattr(
            email_routes,
            "connected_mailbox_providers",
            lambda: ["google"],
        )
        client = TestClient(app)
        draft_resp = client.post(
            "/v1/email/draft",
            json={
                "to": [{"email": "bob@example.com"}],
                "subject": "Hello",
                "body": "World",
                "provider": "microsoft",  # not connected
            },
        )
        assert draft_resp.status_code == 200, draft_resp.text
        token = draft_resp.json()["confirmation_token"]

        send_resp = client.post(
            "/v1/email/send",
            json={
                "to": [{"email": "bob@example.com"}],
                "subject": "Hello",
                "body": "World",
                "confirmation_token": token,
            },
        )
        # Provider 'microsoft' was bound but not connected — must 4xx
        assert 400 <= send_resp.status_code < 500, send_resp.text

    def test_single_mailbox_draft_without_provider_unchanged(self, monkeypatch):
        """One connected mailbox, no provider in draft → D2 count logic (unchanged)."""
        if app is None:
            pytest.skip("gaia.api.openai_server not available")
        calls = []

        class _FakeGmail:
            def send_message(self, *, to, subject, body, **_kw):
                calls.append("google")
                return {"id": "gid-1", "to": to, "subject": subject}

        monkeypatch.setattr(
            email_routes,
            "connected_mailbox_providers",
            lambda: ["google"],
        )
        monkeypatch.setattr(email_routes, "resolve_send_backend", _FakeGmail)

        client = TestClient(app)
        draft_resp = client.post(
            "/v1/email/draft",
            json={
                "to": [{"email": "alice@example.com"}],
                "subject": "Re: x",
                "body": "Got it",
            },
        )
        assert draft_resp.status_code == 200
        token = draft_resp.json()["confirmation_token"]

        send_resp = client.post(
            "/v1/email/send",
            json={
                "to": [{"email": "alice@example.com"}],
                "subject": "Re: x",
                "body": "Got it",
                "confirmation_token": token,
            },
        )
        assert send_resp.status_code == 200, send_resp.text

    def test_confirmation_store_records_provider(self):
        """ConfirmationStore.issue_with_provider / consume_with_provider stores binding."""
        store = email_routes.ConfirmationStore()
        fp = "test-fingerprint"
        token = store.issue(fp, provider="microsoft")
        # Consuming returns the provider alongside the bool.
        ok, bound_provider = store.consume_with_provider(token, fp)
        assert ok is True
        assert bound_provider == "microsoft"

    def test_confirmation_store_none_provider_on_unbound_token(self):
        """Tokens issued without a provider return (True, None) on consume."""
        store = email_routes.ConfirmationStore()
        fp = "fp-no-provider"
        token = store.issue(fp)
        ok, bound_provider = store.consume_with_provider(token, fp)
        assert ok is True
        assert bound_provider is None
