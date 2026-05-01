# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
T-4 unit tests — OAuthPkceHandler + Google catalog entry.

Tests cover:
- OAuthPkceHandler.get_credential returns correct token dict
- OAuthPkceHandler.configure: start_flow path (no flow_id)
- OAuthPkceHandler.configure: complete_flow path (flow_id + code)
- OAuthPkceHandler.disconnect deletes token and clears state
- OAuthPkceHandler.test: healthy path returns ok=True
- OAuthPkceHandler.test: AuthRequiredError returns ok=False
- OAuthPkceHandler.test: ConnectorsError returns ok=False
- Catalog registration: google spec is in REGISTRY after import
- Catalog registration: oauth_pkce handler is in _HANDLER_REGISTRY after import
- Handler satisfies ConnectorHandler Protocol
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gaia.connectors.errors import AuthRequiredError, ConnectorsError
from gaia.connectors.handler import _HANDLER_REGISTRY, ConnectorHandler
from gaia.connectors.oauth_pkce import OAuthPkceHandler
from gaia.connectors.registry import ConnectorRegistry
from gaia.connectors.spec import ConnectorSpec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_spec(
    *,
    id: str = "google",
    type: str = "oauth_pkce",
    oauth_provider_ref: str | None = "google",
    default_scopes: tuple = ("openid", "email"),
) -> ConnectorSpec:
    return ConnectorSpec(
        id=id,
        display_name="Google",
        icon="G",
        category="productivity",
        tier=1,
        type=type,
        description="Google connector",
        default_scopes=default_scopes,
        oauth_provider_ref=oauth_provider_ref,
    )


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


class TestProtocolCompliance:
    def test_satisfies_connector_handler_protocol(self):
        assert isinstance(OAuthPkceHandler(), ConnectorHandler)


# ---------------------------------------------------------------------------
# get_credential
# ---------------------------------------------------------------------------


class TestGetCredential:
    @pytest.mark.asyncio
    async def test_returns_token_dict_shape(self):
        spec = _make_spec()
        handler = OAuthPkceHandler()
        with patch(
            "gaia.connectors.oauth_pkce.get_or_refresh",
            new=AsyncMock(return_value=("tok-abc", 9999999999)),
        ):
            result = await handler.get_credential(spec, required_scopes=["openid"])
        assert result["access_token"] == "tok-abc"
        assert result["expires_at"] == 9999999999
        assert result["scopes"] == ["openid"]

    @pytest.mark.asyncio
    async def test_falls_back_to_default_scopes(self):
        spec = _make_spec(default_scopes=("openid", "email"))
        handler = OAuthPkceHandler()
        with patch(
            "gaia.connectors.oauth_pkce.get_or_refresh",
            new=AsyncMock(return_value=("tok", 0)),
        ):
            result = await handler.get_credential(spec)
        assert set(result["scopes"]) == {"openid", "email"}

    @pytest.mark.asyncio
    async def test_uses_oauth_provider_ref_as_provider_id(self):
        spec = _make_spec(id="gmail", oauth_provider_ref="google")
        handler = OAuthPkceHandler()
        mock_refresh = AsyncMock(return_value=("tok", 0))
        with patch("gaia.connectors.oauth_pkce.get_or_refresh", new=mock_refresh):
            await handler.get_credential(spec)
        mock_refresh.assert_called_once_with(
            "google", account_email=mock_refresh.call_args[1]["account_email"]
        )

    @pytest.mark.asyncio
    async def test_falls_back_to_spec_id_when_no_provider_ref(self):
        spec = _make_spec(id="myconnector", oauth_provider_ref=None)
        handler = OAuthPkceHandler()
        mock_refresh = AsyncMock(return_value=("tok", 0))
        with patch("gaia.connectors.oauth_pkce.get_or_refresh", new=mock_refresh):
            await handler.get_credential(spec)
        mock_refresh.assert_called_once_with(
            "myconnector", account_email=mock_refresh.call_args[1]["account_email"]
        )


# ---------------------------------------------------------------------------
# configure
# ---------------------------------------------------------------------------


class TestConfigure:
    @pytest.mark.asyncio
    async def test_start_flow_returns_flow_info(self):
        spec = _make_spec()
        handler = OAuthPkceHandler()
        flow_info = {
            "flow_id": "flow-123",
            "authorization_url": "https://accounts.google.com/o/oauth2/auth?...",
        }
        with patch(
            "gaia.connectors.oauth_pkce.start_authorization",
            new=AsyncMock(return_value=flow_info),
        ):
            result = await handler.configure(spec, {})
        assert result["flow_id"] == "flow-123"
        assert "authorization_url" in result

    @pytest.mark.asyncio
    async def test_complete_flow_calls_complete_authorization(self):
        # state.json writes have moved to flow._exchange_code_for_tokens —
        # the handler is now a thin pass-through.
        spec = _make_spec()
        handler = OAuthPkceHandler()
        completion = {"account_email": "user@example.com", "scopes": ["openid"]}
        with patch(
            "gaia.connectors.oauth_pkce.complete_authorization",
            new=AsyncMock(return_value=completion),
        ) as mock_complete:
            result = await handler.configure(
                spec, {"flow_id": "flow-123", "code": "auth-code"}
            )
        mock_complete.assert_awaited_once_with("flow-123")
        assert result["account_email"] == "user@example.com"

    @pytest.mark.asyncio
    async def test_configure_uses_scopes_from_config(self):
        # The handler hands scopes to start_authorization; state-writes
        # happen inside flow.py, so we assert at the start_authorization
        # boundary instead.
        spec = _make_spec(default_scopes=("openid",))
        handler = OAuthPkceHandler()
        flow_info = {"flow_id": "f", "authorization_url": "https://example/"}
        with patch(
            "gaia.connectors.oauth_pkce.start_authorization",
            new=AsyncMock(return_value=flow_info),
        ) as mock_start:
            await handler.configure(spec, {"scopes": ["openid", "email"]})
        called_scopes = mock_start.call_args.kwargs["scopes"]
        assert "email" in called_scopes

    @pytest.mark.asyncio
    async def test_first_run_persists_client_credentials(self, monkeypatch):
        # First-time setup path: client_id + client_secret in config land
        # in the keyring, the cached provider instance is evicted so the
        # next get_provider() call re-reads from the new credentials, and
        # the OAuth flow then starts as usual. This is what the AgentUI
        # "Save & Connect" form submits.
        spec = _make_spec()
        handler = OAuthPkceHandler()

        from gaia.connectors.providers import _registry as _provider_registry

        # Pre-populate cache to verify eviction.
        _provider_registry["google"] = "STALE-INSTANCE"

        saved: dict = {}

        def fake_save(provider, *, client_id, client_secret):
            saved["provider"] = provider
            saved["client_id"] = client_id
            saved["client_secret"] = client_secret

        monkeypatch.setattr(
            "gaia.connectors.store.save_provider_credentials", fake_save
        )

        with patch(
            "gaia.connectors.oauth_pkce.start_authorization",
            new=AsyncMock(return_value={"flow_id": "f", "authorization_url": "u"}),
        ):
            await handler.configure(
                spec,
                {
                    "client_id": "abc.apps.googleusercontent.com",
                    "client_secret": "GOCSPX-x",
                },
            )

        assert saved == {
            "provider": "google",
            "client_id": "abc.apps.googleusercontent.com",
            "client_secret": "GOCSPX-x",
        }
        # Cache evicted so the next get_provider() picks up new creds.
        assert "google" not in _provider_registry


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_deletes_connection(self):
        # The keyring blob IS the configured-state for OAuth connectors
        # (no separate state.json), so disconnect just needs to delete
        # the keyring entry. peek_connection returning None afterward is
        # what makes the catalog UI flip back to "not configured".
        spec = _make_spec()
        handler = OAuthPkceHandler()
        with patch("gaia.connectors.oauth_pkce.delete_connection") as mock_del:
            await handler.disconnect(spec)
        mock_del.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_uses_provider_ref(self):
        spec = _make_spec(id="gmail", oauth_provider_ref="google")
        handler = OAuthPkceHandler()
        with patch("gaia.connectors.oauth_pkce.delete_connection") as mock_del:
            await handler.disconnect(spec)
        # provider_id passed to delete_connection should be "google", not "gmail"
        args = mock_del.call_args[0]
        assert args[0] == "google"


# ---------------------------------------------------------------------------
# test (health check)
# ---------------------------------------------------------------------------


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy_returns_ok_true(self):
        spec = _make_spec()
        handler = OAuthPkceHandler()
        with patch(
            "gaia.connectors.oauth_pkce.get_or_refresh",
            new=AsyncMock(return_value=("tok", 0)),
        ):
            result = await handler.test(spec)
        assert result == {"ok": True, "detail": "token_valid"}

    @pytest.mark.asyncio
    async def test_auth_required_error_returns_ok_false(self):
        spec = _make_spec()
        handler = OAuthPkceHandler()
        err = AuthRequiredError(
            AuthRequiredError.Reason.NOT_CONNECTED, provider="google"
        )
        with patch(
            "gaia.connectors.oauth_pkce.get_or_refresh",
            new=AsyncMock(side_effect=err),
        ):
            result = await handler.test(spec)
        assert result["ok"] is False
        assert result["detail"]

    @pytest.mark.asyncio
    async def test_connectors_error_returns_ok_false(self):
        spec = _make_spec()
        handler = OAuthPkceHandler()
        with patch(
            "gaia.connectors.oauth_pkce.get_or_refresh",
            new=AsyncMock(side_effect=ConnectorsError("keyring fail")),
        ):
            result = await handler.test(spec)
        assert result["ok"] is False
        assert "keyring fail" in result["detail"]


# ---------------------------------------------------------------------------
# Catalog registration
# ---------------------------------------------------------------------------


class TestCatalogRegistration:
    def test_google_spec_registered_in_registry(self):
        # Import catalog — this triggers REGISTRY.register(GOOGLE_SPEC)
        # Use a fresh registry so we don't depend on singleton state.
        fresh_reg = ConnectorRegistry()
        with patch("gaia.connectors.catalog.google.REGISTRY", fresh_reg):
            # Re-execute the registration call directly
            from gaia.connectors.catalog.google import GOOGLE_SPEC

            fresh_reg.register(GOOGLE_SPEC)
        spec = fresh_reg.get("google")
        assert spec.id == "google"
        assert spec.type == "oauth_pkce"

    def test_google_spec_has_oauth_provider_ref(self):
        from gaia.connectors.catalog.google import GOOGLE_SPEC

        assert GOOGLE_SPEC.oauth_provider_ref == "google"

    def test_google_spec_has_expected_scopes(self):
        from gaia.connectors.catalog.google import GOOGLE_SPEC

        assert "openid" in GOOGLE_SPEC.default_scopes
        assert "email" in GOOGLE_SPEC.default_scopes

    def test_oauth_pkce_handler_registered_after_catalog_import(self):
        # The handler module auto-registers on import; it was already imported
        # in this test session so _HANDLER_REGISTRY should contain oauth_pkce.
        assert "oauth_pkce" in _HANDLER_REGISTRY
