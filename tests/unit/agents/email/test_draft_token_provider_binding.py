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

# The email agent is no longer mounted in-process on the core API server
# (#2176); build its standalone REST app directly — the same app the OpenAPI
# exporter and the sidecar mount, so these binding tests exercise the real
# email surface, not the (removed) `gaia api` mount.
try:
    from gaia_agent_email.export_openapi import build_app as _build_email_app

    app = _build_email_app()
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


class TestSendRequestProviderFallback:
    """``EmailSendRequest.provider`` is the unbound-token fallback.

    Precedence: the token's bound provider always wins; ``request.provider`` is
    consulted ONLY when the token carries no binding. Both None + multiple
    connected → still 400 ambiguous.
    """

    def _draft_token(self, client, *, provider=None):
        body = {
            "to": [{"email": "bob@example.com"}],
            "subject": "Hello",
            "body": "World",
        }
        if provider is not None:
            body["provider"] = provider
        resp = client.post("/v1/email/draft", json=body)
        assert resp.status_code == 200, resp.text
        return resp.json()["confirmation_token"]

    def _spy_resolver(self, monkeypatch, calls):
        """Patch _resolve_backend_for_provider to record the provider it sees."""

        class _Fake:
            def __init__(self, name):
                self.name = name

            def send_message(self, *, to, subject, body, **_kw):
                calls.append(self.name)
                if self.name == "microsoft":
                    return {"id": "", "sent": True, "to": to, "subject": subject}
                return {"id": f"{self.name}-sent", "to": to, "subject": subject}

        def _resolve(provider=None):
            # Mirror production: None + 2 connected → 400 ambiguous.
            if provider is None:
                from fastapi import HTTPException

                connected = email_routes.connected_mailbox_providers()
                if len(connected) != 1:
                    raise HTTPException(400, f"ambiguous: {connected}")
                provider = connected[0]
            return _Fake(provider)

        monkeypatch.setattr(email_routes, "_resolve_backend_for_provider", _resolve)
        return calls

    def test_unbound_token_uses_request_provider(self, monkeypatch):
        """Unbound token + request.provider='microsoft' + both connected → Outlook."""
        if app is None:
            pytest.skip("gaia.api.openai_server not available")
        monkeypatch.setattr(
            email_routes,
            "connected_mailbox_providers",
            lambda: ["google", "microsoft"],
        )
        calls = self._spy_resolver(monkeypatch, [])
        client = TestClient(app)
        token = self._draft_token(client)  # no binding
        resp = client.post(
            "/v1/email/send",
            json={
                "to": [{"email": "bob@example.com"}],
                "subject": "Hello",
                "body": "World",
                "confirmation_token": token,
                "provider": "microsoft",
            },
        )
        assert resp.status_code == 200, resp.text
        assert calls == ["microsoft"], f"request.provider ignored: {calls}"

    def test_token_binding_wins_over_request_provider(self, monkeypatch):
        """Token bound to google + request.provider='microsoft' → google wins."""
        if app is None:
            pytest.skip("gaia.api.openai_server not available")
        monkeypatch.setattr(
            email_routes,
            "connected_mailbox_providers",
            lambda: ["google", "microsoft"],
        )
        calls = self._spy_resolver(monkeypatch, [])
        client = TestClient(app)
        token = self._draft_token(client, provider="google")  # bound to google
        resp = client.post(
            "/v1/email/send",
            json={
                "to": [{"email": "bob@example.com"}],
                "subject": "Hello",
                "body": "World",
                "confirmation_token": token,
                "provider": "microsoft",  # must be ignored
            },
        )
        assert resp.status_code == 200, resp.text
        assert calls == ["google"], f"token binding did not win: {calls}"

    def test_unbound_token_no_provider_both_connected_still_400(self, monkeypatch):
        """Unbound token + no request.provider + both connected → 400 ambiguous."""
        if app is None:
            pytest.skip("gaia.api.openai_server not available")
        monkeypatch.setattr(
            email_routes,
            "connected_mailbox_providers",
            lambda: ["google", "microsoft"],
        )
        calls = self._spy_resolver(monkeypatch, [])
        client = TestClient(app)
        token = self._draft_token(client)  # no binding, no provider on send
        resp = client.post(
            "/v1/email/send",
            json={
                "to": [{"email": "bob@example.com"}],
                "subject": "Hello",
                "body": "World",
                "confirmation_token": token,
            },
        )
        assert resp.status_code == 400, resp.text
        assert calls == [], f"a send leaked through the ambiguity guard: {calls}"
