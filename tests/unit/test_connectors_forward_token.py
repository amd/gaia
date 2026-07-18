# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit spec for the grant-gated token+expiry accessor (issue #2154).

``get_access_token_with_expiry`` is the accessor the daemon's OAuth forward-out
path uses: it must apply the SAME per-agent grant + OAuth-scope gate as
``get_access_token`` (no bypass) and additionally return the token's wall-clock
expiry so the daemon knows when to re-forward. These tests mock the grant/store
seams and the refresh engine so they assert the authorization contract without a
live keyring or a real token endpoint.
"""

from __future__ import annotations

import asyncio

import pytest

from gaia.connectors import api
from gaia.connectors.errors import AuthRequiredError


class _FakeProvider:
    client_id_hash = "hash-abc"


def _patch_common(monkeypatch, *, granted=True, connection_scopes=None):
    monkeypatch.setattr(api, "get_provider", lambda provider: _FakeProvider())
    monkeypatch.setattr(
        api, "check_agent_grant", lambda provider, agent, scopes: granted
    )
    if connection_scopes is None:
        stored = None
    else:
        stored = {"scopes": list(connection_scopes), "account_email": "u@example.com"}
    monkeypatch.setattr(api, "load_connection", lambda *a, **k: stored)


def test_returns_token_and_expiry_when_granted_and_covered(monkeypatch):
    scopes = ["s1", "s2"]
    _patch_common(monkeypatch, granted=True, connection_scopes=scopes)

    async def _fake_get_token_with_expiry(provider, *, account_email):
        return ("live-access-token", 1_900_000_000.0)

    monkeypatch.setattr(
        "gaia.connectors.tokens.get_token_with_expiry", _fake_get_token_with_expiry
    )

    token, expires_at = asyncio.run(
        api.get_access_token_with_expiry(
            provider="google", scopes=scopes, agent_id="installed:email"
        )
    )
    assert token == "live-access-token"
    assert expires_at == 1_900_000_000.0


def test_not_granted_raises_agent_not_granted_before_any_refresh(monkeypatch):
    _patch_common(monkeypatch, granted=False, connection_scopes=["s1"])

    called = {"refresh": False}

    async def _should_not_run(provider, *, account_email):
        called["refresh"] = True
        return ("x", 0.0)

    monkeypatch.setattr("gaia.connectors.tokens.get_token_with_expiry", _should_not_run)

    with pytest.raises(AuthRequiredError) as exc:
        asyncio.run(
            api.get_access_token_with_expiry(
                provider="google", scopes=["s1"], agent_id="installed:email"
            )
        )
    assert exc.value.reason == AuthRequiredError.Reason.AGENT_NOT_GRANTED
    assert called["refresh"] is False  # gate fires before any network round-trip


def test_connection_missing_scopes_raises_and_names_missing(monkeypatch):
    # Granted, but the underlying OAuth connection only carries a subset — the
    # daemon must never forward a token that cannot cover what the agent needs.
    _patch_common(monkeypatch, granted=True, connection_scopes=["s1"])

    async def _fake(provider, *, account_email):
        return ("x", 0.0)

    monkeypatch.setattr("gaia.connectors.tokens.get_token_with_expiry", _fake)

    with pytest.raises(AuthRequiredError) as exc:
        asyncio.run(
            api.get_access_token_with_expiry(
                provider="google", scopes=["s1", "s2"], agent_id="installed:email"
            )
        )
    assert exc.value.reason == AuthRequiredError.Reason.CONNECTION_MISSING_SCOPES
    assert "s2" in (exc.value.missing_scopes or [])


def test_not_connected_raises_not_connected(monkeypatch):
    _patch_common(monkeypatch, granted=True, connection_scopes=None)

    async def _fake(provider, *, account_email):
        return ("x", 0.0)

    monkeypatch.setattr("gaia.connectors.tokens.get_token_with_expiry", _fake)

    with pytest.raises(AuthRequiredError) as exc:
        asyncio.run(
            api.get_access_token_with_expiry(
                provider="google", scopes=["s1"], agent_id="installed:email"
            )
        )
    assert exc.value.reason == AuthRequiredError.Reason.NOT_CONNECTED
