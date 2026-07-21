# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Spec for the email sidecar's forwarded-credentials store + intake routes and
the token-resolver seam (issue #2154 / V2-14).

The sidecar operates on a DAEMON-forwarded short-lived access token — it never
reads the keyring/grants store in forwarded mode. These tests assert:
  - the in-memory store: set / get / expiry / scope coverage / withdraw, all
    fail-loud (no silent empty token);
  - the resolver seam: forwarded mode reads the store; standalone mode falls
    back to the live grant-checked path;
  - the intake routes require the caller token (boundary), store the forward,
    return metadata only, and never echo the token or a refresh token.
"""

from __future__ import annotations

import time

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from gaia_agent_email import caller_auth, forwarded_credentials
from gaia_agent_email.api_routes import require_caller_token
from gaia_agent_email.connection_intake_routes import router as intake_router

from gaia.connectors.errors import ConnectorsError

_TOKEN = "s3cret-session-token"
_BASE_URL = "http://127.0.0.1:8131"

_FUTURE = time.time() + 3600
_PAST = time.time() - 10


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Clear the forwarded store + auth policy and forwarded-mode env between
    tests so nothing leaks across cases."""
    monkeypatch.delenv(forwarded_credentials.FORWARDED_MODE_ENV_VAR, raising=False)
    forwarded_credentials.reset()
    caller_auth.reset()
    yield
    forwarded_credentials.reset()
    caller_auth.reset()


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def test_set_then_get_returns_token_when_scopes_covered():
    forwarded_credentials.set_forwarded(
        "google", access_token="tok", scopes=["s1", "s2"], expires_at=_FUTURE
    )
    assert forwarded_credentials.get_forwarded_token("google", ["s1"]) == "tok"
    assert forwarded_credentials.get_forwarded_token("google", ["s1", "s2"]) == "tok"


def test_get_missing_credential_raises_loudly():
    with pytest.raises(ConnectorsError) as exc:
        forwarded_credentials.get_forwarded_token("google", ["s1"])
    assert "google" in str(exc.value)


def test_missing_credential_error_names_the_cli_not_just_the_ui():
    # A headless user must be able to unblock from the message (#2347): it names
    # the connect + grant CLI commands, not only "Settings -> Connections".
    with pytest.raises(ConnectorsError) as exc:
        forwarded_credentials.get_forwarded_token("google", ["s1"])
    msg = str(exc.value)
    assert "gaia connectors connect google" in msg
    assert "gaia connectors grants grant google installed:email" in msg


def test_scope_short_error_names_the_missing_scopes_in_the_cli_command():
    # The reconnect command must carry the ACTUAL missing scopes, not a
    # placeholder, and must not print an unexpanded '{provider}' literal.
    forwarded_credentials.set_forwarded(
        "google", access_token="tok", scopes=["s1"], expires_at=_FUTURE
    )
    with pytest.raises(ConnectorsError) as exc:
        forwarded_credentials.get_forwarded_token("google", ["s1", "s2"])
    msg = str(exc.value)
    assert "gaia connectors connect google --scopes s2" in msg
    assert "{provider}" not in msg  # f-string bug regression guard


def test_get_expired_token_raises_loudly():
    forwarded_credentials.set_forwarded(
        "google", access_token="tok", scopes=["s1"], expires_at=_PAST
    )
    with pytest.raises(ConnectorsError) as exc:
        forwarded_credentials.get_forwarded_token("google", ["s1"])
    assert "expired" in str(exc.value).lower()


def test_get_scope_short_token_raises_and_names_missing():
    forwarded_credentials.set_forwarded(
        "google", access_token="tok", scopes=["s1"], expires_at=_FUTURE
    )
    with pytest.raises(ConnectorsError) as exc:
        forwarded_credentials.get_forwarded_token("google", ["s1", "s2"])
    assert "s2" in str(exc.value)


def test_set_rejects_empty_token():
    with pytest.raises(ConnectorsError):
        forwarded_credentials.set_forwarded(
            "google", access_token="", scopes=["s1"], expires_at=_FUTURE
        )


def test_set_rejects_nonpositive_expiry():
    with pytest.raises(ConnectorsError):
        forwarded_credentials.set_forwarded(
            "google", access_token="tok", scopes=["s1"], expires_at=0
        )


def test_withdraw_removes_credential():
    forwarded_credentials.set_forwarded(
        "google", access_token="tok", scopes=["s1"], expires_at=_FUTURE
    )
    assert forwarded_credentials.withdraw("google") is True
    assert forwarded_credentials.withdraw("google") is False  # idempotent
    with pytest.raises(ConnectorsError):
        forwarded_credentials.get_forwarded_token("google", ["s1"])


def test_list_forwarded_is_metadata_only_never_token():
    forwarded_credentials.set_forwarded(
        "google",
        access_token="super-secret",
        scopes=["s1"],
        expires_at=_FUTURE,
        account_email="u@example.com",
    )
    items = forwarded_credentials.list_forwarded()
    assert items[0]["provider"] == "google"
    assert items[0]["account_email"] == "u@example.com"
    assert "super-secret" not in repr(items)
    assert "access_token" not in items[0]


# ---------------------------------------------------------------------------
# Resolver seam
# ---------------------------------------------------------------------------


def test_resolver_standalone_mode_uses_live_fetch(monkeypatch):
    # Env unset → standalone: the live grant-checked path is used, store ignored.
    called = {"live": False}

    def _live():
        called["live"] = True
        return "live-token"

    assert (
        forwarded_credentials.resolve_access_token("google", ["s1"], live_fetch=_live)
        == "live-token"
    )
    assert called["live"] is True


def test_resolver_forwarded_mode_reads_store_not_live(monkeypatch):
    monkeypatch.setenv(forwarded_credentials.FORWARDED_MODE_ENV_VAR, "1")
    forwarded_credentials.set_forwarded(
        "google", access_token="fwd-token", scopes=["s1"], expires_at=_FUTURE
    )

    def _live():
        raise AssertionError("live_fetch must NOT be called in forwarded mode")

    assert (
        forwarded_credentials.resolve_access_token("google", ["s1"], live_fetch=_live)
        == "fwd-token"
    )


def test_resolver_forwarded_mode_missing_token_raises_never_falls_back(monkeypatch):
    monkeypatch.setenv(forwarded_credentials.FORWARDED_MODE_ENV_VAR, "1")

    def _live():
        raise AssertionError("must never fall back to the keyring in forwarded mode")

    with pytest.raises(ConnectorsError):
        forwarded_credentials.resolve_access_token("google", ["s1"], live_fetch=_live)


# ---------------------------------------------------------------------------
# Intake routes (HTTP boundary)
# ---------------------------------------------------------------------------


def _client(token=_TOKEN) -> TestClient:
    caller_auth.configure(caller_auth.CallerAuthConfig(token=token))
    app = FastAPI()
    app.add_middleware(caller_auth.HostOriginMiddleware)
    app.include_router(intake_router, dependencies=[Depends(require_caller_token)])
    return TestClient(app, base_url=_BASE_URL)


def _auth(token=_TOKEN):
    return {"Authorization": f"Bearer {token}"}


def test_intake_requires_caller_token_401():
    client = _client()
    r = client.post(
        "/v1/connections/google",
        json={"access_token": "tok", "scopes": ["s1"], "expires_at": _FUTURE},
    )
    assert r.status_code == 401


def test_intake_stores_forward_and_returns_metadata_only():
    client = _client()
    r = client.post(
        "/v1/connections/google",
        headers=_auth(),
        json={
            "access_token": "tok",
            "scopes": ["s1", "s2"],
            "expires_at": _FUTURE,
            "account_email": "u@example.com",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "google"
    assert sorted(body["scopes"]) == ["s1", "s2"]
    assert "tok" not in r.text  # token never echoed back
    # The forward is now resolvable from the store.
    assert forwarded_credentials.get_forwarded_token("google", ["s1"]) == "tok"


def test_intake_unknown_provider_404():
    client = _client()
    r = client.post(
        "/v1/connections/dropbox",
        headers=_auth(),
        json={"access_token": "tok", "scopes": [], "expires_at": _FUTURE},
    )
    assert r.status_code == 404


def test_intake_rejects_empty_token_400():
    client = _client()
    r = client.post(
        "/v1/connections/google",
        headers=_auth(),
        json={"access_token": "", "scopes": [], "expires_at": _FUTURE},
    )
    # pydantic min_length=1 → 422; either way it is a loud client error, not 200.
    assert r.status_code in (400, 422)


def test_delete_withdraws_forward():
    client = _client()
    client.post(
        "/v1/connections/google",
        headers=_auth(),
        json={"access_token": "tok", "scopes": ["s1"], "expires_at": _FUTURE},
    )
    r = client.delete("/v1/connections/google", headers=_auth())
    assert r.status_code == 200
    assert r.json()["withdrawn"] is True


def test_list_route_returns_metadata_only():
    client = _client()
    client.post(
        "/v1/connections/google",
        headers=_auth(),
        json={"access_token": "sekret", "scopes": ["s1"], "expires_at": _FUTURE},
    )
    r = client.get("/v1/connections", headers=_auth())
    assert r.status_code == 200
    assert "sekret" not in r.text
    assert r.json()["connections"][0]["provider"] == "google"
