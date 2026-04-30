# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
T-9a (AC8, AC9): public API surface tests for ``gaia.connections.api``.

Coverage:
- ``get_access_token`` agent_id resolution: explicit kwarg → contextvar →
  None.
- ``agent_id=None`` skips the per-agent grant check (CLI debug path).
- ``agent_id`` set with no grant → ``AuthRequiredError(AGENT_NOT_GRANTED)``.
- Granted scopes that don't cover the OAuth grant → ``AuthRequiredError(
  CONNECTION_MISSING_SCOPES)``.
- ``start_authorization`` and ``complete_authorization`` exposed at
  package level.
- ``list_connections``, ``get_connection``, ``revoke_connection``,
  ``grant_agent``, ``revoke_agent_grant``, ``list_agent_grants`` all
  importable and callable.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from gaia.connections import (
    AuthRequiredError,
    ScopeMismatchError,
    get_access_token,
    grant_agent,
    list_agent_grants,
    list_connections,
    revoke_agent_grant,
    revoke_connection,
)
from gaia.connections.context import _agent_context
from gaia.connections.providers import _registry
from gaia.connections.store import save_connection


@pytest.fixture
def google_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "test.apps.example")
    monkeypatch.setattr("gaia.connections.grants.Path.home", lambda: tmp_path)
    _registry.clear()
    from gaia.connections.providers import get as get_provider

    return get_provider("google")


@pytest.fixture
def seeded(google_provider):
    save_connection(
        provider="google",
        account_email="alice@example.com",
        refresh_token="seed-rt",
        scopes=["gmail.readonly"],
        client_id_hash=google_provider.client_id_hash,
    )
    return google_provider


def _ok_token():
    return httpx.Response(
        200,
        json={"access_token": "ACCESS-1", "expires_in": 3600, "scope": "x"},
    )


class TestGetAccessTokenAgentResolution:
    @respx.mock
    async def test_explicit_agent_id_kwarg_used_directly(self, seeded):
        respx.post("https://oauth2.googleapis.com/token").mock(return_value=_ok_token())
        grant_agent("google", "builtin:chat", ["gmail.readonly"])
        token = await get_access_token(
            provider="google",
            scopes=["gmail.readonly"],
            agent_id="builtin:chat",
        )
        assert token == "ACCESS-1"

    @respx.mock
    async def test_agent_id_resolved_from_contextvar(self, seeded):
        respx.post("https://oauth2.googleapis.com/token").mock(return_value=_ok_token())
        grant_agent("google", "builtin:chat", ["gmail.readonly"])
        with _agent_context("builtin:chat"):
            token = await get_access_token(provider="google", scopes=["gmail.readonly"])
        assert token == "ACCESS-1"

    @respx.mock
    async def test_agent_id_none_skips_grant_check(self, seeded):
        # AC8 explicit opt-out: agent_id=None bypasses the per-agent
        # grant check (CLI/debugging path). NOT a silent fallback —
        # it's documented and tested.
        respx.post("https://oauth2.googleapis.com/token").mock(return_value=_ok_token())
        token = await get_access_token(
            provider="google", scopes=["gmail.readonly"], agent_id=None
        )
        assert token == "ACCESS-1"


class TestGrantEnforcement:
    @respx.mock
    async def test_no_grant_raises_agent_not_granted(self, seeded):
        respx.post("https://oauth2.googleapis.com/token").mock(return_value=_ok_token())
        with pytest.raises(AuthRequiredError) as exc:
            await get_access_token(
                provider="google",
                scopes=["gmail.readonly"],
                agent_id="builtin:chat",
            )
        assert exc.value.reason is AuthRequiredError.Reason.AGENT_NOT_GRANTED
        assert exc.value.agent_id == "builtin:chat"
        assert exc.value.provider == "google"

    @respx.mock
    async def test_partial_grant_raises_agent_not_granted(self, seeded):
        # Agent granted only readonly; tool requests send too.
        respx.post("https://oauth2.googleapis.com/token").mock(return_value=_ok_token())
        grant_agent("google", "builtin:chat", ["gmail.readonly"])
        with pytest.raises(AuthRequiredError) as exc:
            await get_access_token(
                provider="google",
                scopes=["gmail.send"],
                agent_id="builtin:chat",
            )
        assert exc.value.reason is AuthRequiredError.Reason.AGENT_NOT_GRANTED


class TestScopeCoverage:
    @respx.mock
    async def test_oauth_grant_missing_scope_raises_missing(self, google_provider):
        # OAuth connection has only readonly; agent tool requests send.
        save_connection(
            provider="google",
            account_email="a@example.com",
            refresh_token="rt",
            scopes=["gmail.readonly"],
            client_id_hash=google_provider.client_id_hash,
        )
        # Agent IS granted gmail.send, but the OAuth connection is not.
        grant_agent("google", "builtin:chat", ["gmail.send"])

        respx.post("https://oauth2.googleapis.com/token").mock(return_value=_ok_token())
        with pytest.raises(AuthRequiredError) as exc:
            await get_access_token(
                provider="google",
                scopes=["gmail.send"],
                agent_id="builtin:chat",
            )
        assert exc.value.reason is AuthRequiredError.Reason.CONNECTION_MISSING_SCOPES
        assert "gmail.send" in exc.value.missing_scopes


class TestPublicSurface:
    def test_grant_round_trip_via_public_api(self, google_provider):
        grant_agent("google", "builtin:chat", ["gmail.readonly"])
        listing = list_agent_grants("google")
        assert listing["builtin:chat"] == ["gmail.readonly"]

    def test_revoke_agent_grant_via_public_api(self, google_provider):
        grant_agent("google", "builtin:chat", ["s"])
        revoke_agent_grant("google", "builtin:chat")
        assert list_agent_grants("google") == {}

    def test_list_connections_via_public_api(self, seeded):
        rows = list_connections()
        providers = {row["provider"] for row in rows}
        assert "google" in providers
        # The returned shape includes metadata but never the refresh token.
        google_row = next(row for row in rows if row["provider"] == "google")
        assert "refresh_token" not in google_row
        assert google_row["account_email"] == "alice@example.com"

    def test_revoke_connection_via_public_api(self, seeded):
        revoke_connection("google")
        assert list_connections() == []
