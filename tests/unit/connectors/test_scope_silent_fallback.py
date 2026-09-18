# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
D0 (#2730): an empty scope list on a (re)connect must never silently become
the provider's identity-only ``default_scopes``.

``list(scopes) or list(provider.default_scopes)`` at four sites
(``flow.start_authorization``, ``flow.start_device_flow``,
``flow.poll_device_flow``, ``oauth_pkce.configure``) meant a bare
``gaia connectors connect google`` — the EXACT command GAIA's own error text
told a user to run — silently overwrote a working mail+calendar connection
with three identity-only scopes (``openid email profile``), because the
daemon's grant ledger was never touched by the same call. This regression-
locks the fix: an empty request against a provider with prior state (an
existing connection OR an agent grant) now raises loudly and never reaches
``save_connection``; an empty request with no prior state (a genuine
first-time connect) still falls back to ``default_scopes``.

Pure ``src/gaia/connectors/**`` — no ``gaia_agent_email`` import — so a
connectors-only contributor's CI run catches a D0 regression without the
email wheel installed.
"""

from __future__ import annotations

import pytest

from gaia.connectors.errors import ConnectorsError
from gaia.connectors.providers import _registry as _provider_registry


@pytest.fixture(autouse=True)
def _google_env(monkeypatch):
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "test.apps.example")
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_SECRET", "test-secret")
    _provider_registry.clear()
    yield
    _provider_registry.clear()


def _seed_connection(scopes):
    from gaia.connectors.providers import get as get_provider
    from gaia.connectors.store import save_connection

    save_connection(
        provider="google",
        account_email="alice@example.com",
        refresh_token="seed-refresh",
        scopes=list(scopes),
        client_id_hash=get_provider("google").client_id_hash,
    )


def _seed_grant_only():
    from gaia.connectors.grants import grant_agent

    grant_agent(
        "google", "installed:email", ["https://www.googleapis.com/auth/gmail.modify"]
    )


@pytest.fixture(autouse=True)
def _isolated_grants(tmp_path, monkeypatch):
    monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)


# ---------------------------------------------------------------------------
# Site 7 — flow.start_authorization
# ---------------------------------------------------------------------------


class TestStartAuthorizationEmptyScopes:
    @pytest.mark.asyncio
    async def test_empty_scopes_with_existing_connection_raises(self, monkeypatch):
        _seed_connection(["https://www.googleapis.com/auth/gmail.modify", "openid"])
        from gaia.connectors import store as store_mod
        from gaia.connectors.flow import start_authorization

        calls = []
        monkeypatch.setattr(
            store_mod,
            "save_connection",
            lambda **kw: calls.append(kw)
            or pytest.fail("save_connection must not run"),
        )
        with pytest.raises(ConnectorsError) as exc:
            await start_authorization("google", scopes=[])
        message = str(exc.value)
        assert "no scopes" in message.lower()
        assert "gaia connectors connect google --scopes" in message
        # The remedy must be copy-pasteable — the real scopes, never a
        # placeholder (that is the exact defect this issue removes).
        assert "<scope" not in message
        assert "https://www.googleapis.com/auth/gmail.modify" in message
        assert "openid" in message
        assert calls == []

    @pytest.mark.asyncio
    async def test_empty_scopes_with_grant_only_raises(self, monkeypatch):
        _seed_grant_only()
        from gaia.connectors import store as store_mod
        from gaia.connectors.flow import start_authorization

        monkeypatch.setattr(
            store_mod,
            "save_connection",
            lambda **kw: pytest.fail("save_connection must not run"),
        )
        with pytest.raises(ConnectorsError) as exc:
            await start_authorization("google", scopes=[])
        message = str(exc.value)
        assert "<scope" not in message
        assert "https://www.googleapis.com/auth/gmail.modify" in message
        assert "--grant-agent installed:email" in message

    @pytest.mark.asyncio
    async def test_empty_scopes_with_no_prior_state_falls_back_to_defaults(
        self, monkeypatch
    ):
        """Genuine first-time connect — no connection, no grant. The
        pre-#2730 fallback still applies here; only the reconnect case is
        now rejected."""
        from gaia.connectors.flow import start_authorization

        monkeypatch.setattr("webbrowser.open", lambda *_a, **_k: True)
        info = await start_authorization("google", scopes=[])
        assert info["flow_id"]
        from gaia.connectors.flow import cancel_flow

        await cancel_flow(info["flow_id"])


# ---------------------------------------------------------------------------
# Site 8 / 9 — flow.start_device_flow / poll_device_flow (Microsoft: the only
# provider with device_code_url configured in the catalog)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _ms_env(monkeypatch):
    monkeypatch.setenv("GAIA_MICROSOFT_CLIENT_ID", "test-ms-client")
    monkeypatch.delenv("GAIA_MICROSOFT_CLIENT_SECRET", raising=False)


def _seed_ms_connection(scopes):
    from gaia.connectors.providers import get as get_provider
    from gaia.connectors.store import save_connection

    save_connection(
        provider="microsoft",
        account_email="alice@example.com",
        refresh_token="seed-refresh",
        scopes=list(scopes),
        client_id_hash=get_provider("microsoft").client_id_hash,
    )


class TestStartDeviceFlowEmptyScopes:
    @pytest.mark.asyncio
    async def test_empty_scopes_with_existing_connection_raises(self, monkeypatch):
        _seed_ms_connection(["https://graph.microsoft.com/Mail.ReadWrite"])
        import httpx

        async def _boom_post(self, url, data=None, **kw):
            pytest.fail("no HTTP call should happen before the D0 guard raises")

        monkeypatch.setattr(httpx.AsyncClient, "post", _boom_post)
        from gaia.connectors.flow import start_device_flow

        with pytest.raises(ConnectorsError) as exc:
            await start_device_flow("microsoft", scopes=[])
        message = str(exc.value)
        assert "no scopes" in message.lower()
        assert "<scope" not in message
        assert "https://graph.microsoft.com/Mail.ReadWrite" in message


class TestPollDeviceFlowEmptyScopes:
    @pytest.mark.asyncio
    async def test_empty_scopes_with_existing_connection_raises(self, monkeypatch):
        _seed_ms_connection(["https://graph.microsoft.com/Mail.ReadWrite"])
        import httpx

        async def _boom_post(self, url, data=None, **kw):
            pytest.fail("no HTTP call should happen before the D0 guard raises")

        monkeypatch.setattr(httpx.AsyncClient, "post", _boom_post)
        from gaia.connectors.flow import poll_device_flow

        with pytest.raises(ConnectorsError) as exc:
            await poll_device_flow("microsoft", "dc-1", scopes=[])
        message = str(exc.value)
        assert "no scopes" in message.lower()
        assert "<scope" not in message
        assert "https://graph.microsoft.com/Mail.ReadWrite" in message
