# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Structured OAuth failures + their classification (#2590).

Before this, ``flow.py`` raised ``ConnectorsError`` with the ENTIRE unbounded
``response.text`` interpolated into the message, with no JSON parse — no
exception in the codebase exposed ``error`` / ``error_description`` as
fields a caller could inspect without risking that raw text (which could,
in principle, carry anything the provider chose to send back) reaching
model context via ``str(exc)``.

Every exception used here is built the SAME WAY ``flow.py`` builds it — via
the real ``start_device_flow`` / ``poll_device_flow`` coroutines against a
mocked HTTP layer — never hand-fed as a bare tuple. A classifier tested only
against synthetic tuples would be unit-green and production-blind to a real
response shape it never saw.
"""

from __future__ import annotations

import asyncio

import pytest

from gaia.connectors import flow as flow_mod
from gaia.connectors.errors import (
    ConnectorsError,
    ConsentDeniedError,
    FlowTimeoutError,
    OAuthProviderError,
)
from gaia.connectors.flow import classify_oauth_exception

MAIL_READ = "https://graph.microsoft.com/Mail.Read"


@pytest.fixture(autouse=True)
def _ms_env(monkeypatch):
    monkeypatch.setenv("GAIA_MICROSOFT_CLIENT_ID", "test-client-id")
    monkeypatch.delenv("GAIA_MICROSOFT_CLIENT_SECRET", raising=False)
    from gaia.connectors import providers

    providers._registry.clear()
    yield
    providers._registry.clear()


@pytest.fixture(autouse=True)
def _instant_sleep(monkeypatch):
    async def _no_wait(_seconds):
        return None

    monkeypatch.setattr(flow_mod.asyncio, "sleep", _no_wait)


class _FakeResp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class _FakeAsyncClient:
    _queue: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, data=None, **kwargs):
        return _FakeAsyncClient._queue.pop(0)


def _install_responses(monkeypatch, responses):
    _FakeAsyncClient._queue = list(responses)
    monkeypatch.setattr(flow_mod.httpx, "AsyncClient", _FakeAsyncClient)


# ---------------------------------------------------------------------------
# Real exceptions, built the way flow.py builds them.
# ---------------------------------------------------------------------------


def test_declined_device_signin_classifies_as_authorization_declined(monkeypatch):
    _install_responses(
        monkeypatch, [_FakeResp(400, {"error": "authorization_declined"})]
    )
    with pytest.raises(ConsentDeniedError) as exc:
        asyncio.run(flow_mod.poll_device_flow("microsoft", "DEV", scopes=[MAIL_READ]))

    category, guidance = classify_oauth_exception(exc.value)
    assert category == "authorization_declined"
    assert "sign-in" in guidance.lower() or "consent" in guidance.lower()


def test_expired_device_code_classifies_as_expired_token(monkeypatch):
    _install_responses(monkeypatch, [_FakeResp(400, {"error": "expired_token"})])
    with pytest.raises(FlowTimeoutError) as exc:
        asyncio.run(flow_mod.poll_device_flow("microsoft", "DEV", scopes=[MAIL_READ]))

    category, guidance = classify_oauth_exception(exc.value)
    assert category == "expired_token"
    assert "expired" in guidance.lower()


def test_unrecognized_device_poll_error_raises_a_structured_exception(monkeypatch):
    """The bug this replaces: a bare ConnectorsError with the full response
    body interpolated — no structured error/error_description a caller could
    inspect without str(exc)."""
    _install_responses(
        monkeypatch,
        [
            _FakeResp(
                400,
                {
                    "error": "invalid_grant",
                    "error_description": "AADSTS65001: the user or administrator "
                    "has not consented to use the application",
                },
            )
        ],
    )
    with pytest.raises(OAuthProviderError) as exc:
        asyncio.run(flow_mod.poll_device_flow("microsoft", "DEV", scopes=[MAIL_READ]))

    assert exc.value.error == "invalid_grant"
    assert "AADSTS65001" in exc.value.error_description
    # Still a ConnectorsError — existing `except ConnectorsError` callers
    # (onboarding_tools, the CLI) keep working unchanged.
    assert isinstance(exc.value, ConnectorsError)


def test_admin_consent_required_names_the_permissions_step(monkeypatch):
    _install_responses(
        monkeypatch,
        [
            _FakeResp(
                400,
                {
                    "error": "invalid_grant",
                    "error_description": "AADSTS65001: admin consent required",
                },
            )
        ],
    )
    with pytest.raises(OAuthProviderError) as exc:
        asyncio.run(flow_mod.poll_device_flow("microsoft", "DEV", scopes=[MAIL_READ]))

    category, guidance = classify_oauth_exception(exc.value)
    assert category == "admin_consent_required"
    assert "permission" in guidance.lower() or "consent" in guidance.lower()


def test_unrecognized_reason_reports_only_bounded_fields_never_str_exc(monkeypatch):
    """A provider error the classifier does not recognise must still never
    leak the exception's raw, unbounded message text — only the structured
    (and length-bounded) fields."""
    huge_body = "x" * 5000
    _install_responses(
        monkeypatch,
        [_FakeResp(400, {"error": "server_error", "error_description": huge_body})],
    )
    with pytest.raises(OAuthProviderError) as exc:
        asyncio.run(flow_mod.poll_device_flow("microsoft", "DEV", scopes=[MAIL_READ]))

    category, guidance = classify_oauth_exception(exc.value)
    assert category == "unrecognized"
    # The exception's OWN fields are bounded...
    assert len(exc.value.error_description) < 1000
    # ...and the guidance text is built from those bounded fields, never the
    # raw 5000-char body.
    assert len(guidance) < 1000


def test_loopback_token_exchange_failure_is_structured_not_raw_text(monkeypatch):
    """flow.py's OTHER raise site (the browser/loopback exchange) gets the
    same treatment — this was the literally-named bug location."""
    from gaia.connectors.flow import _PendingFlow

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, data=None, **kwargs):
            return _FakeResp(
                400,
                {"error": "invalid_grant", "error_description": "code expired"},
                text="raw unbounded body should never be interpolated here",
            )

    monkeypatch.setattr(flow_mod.httpx, "AsyncClient", _FakeClient)

    class _Prov:
        provider_id = "microsoft"
        token_url = "https://example/token"
        client_id_hash = "abc"

        def token_request_body(self, **kw):
            return {}

    monkeypatch.setattr(flow_mod, "get_provider", lambda pid: _Prov())

    async def _run():
        flow = _PendingFlow(
            flow_id="f1",
            provider_id="microsoft",
            scopes=[MAIL_READ],
            code_verifier="v",
            state="s",
            redirect_uri="http://127.0.0.1/callback",
            runner=None,
            future=asyncio.get_event_loop().create_future(),
        )
        await flow_mod._exchange_code_for_tokens(flow, "code")

    with pytest.raises(OAuthProviderError) as exc:
        asyncio.run(_run())

    assert exc.value.error == "invalid_grant"
    assert exc.value.error_description == "code expired"
