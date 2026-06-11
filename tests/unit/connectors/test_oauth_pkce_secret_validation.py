# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for connect-time client_secret validation in OAuthPkceHandler.configure.
Root cause 4 of #1592 (AC5): connecting must fail loudly when the provider
requires client_secret but none is present, rather than storing a "connected"
entry that will 401 on first use.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from gaia.connectors.errors import ConfigurationError, ConnectorsError
from gaia.connectors.oauth_pkce import OAuthPkceHandler
from gaia.connectors.spec import ConnectorSpec


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


class TestConnectTimeSecretValidation:
    @pytest.mark.asyncio
    async def test_configure_raises_when_no_secret_and_provider_requires_it(
        self, monkeypatch
    ):
        """When Google requires client_secret but none is found in env/keyring,
        configure() must raise ConfigurationError before starting an OAuth flow
        that would produce a token which later fails on first refresh."""
        monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "test.apps.example")
        monkeypatch.delenv("GAIA_GOOGLE_CLIENT_SECRET", raising=False)

        from gaia.connectors.providers import _registry

        _registry.clear()

        spec = _make_spec()
        handler = OAuthPkceHandler()

        with pytest.raises((ConfigurationError, ConnectorsError)) as exc_info:
            await handler.configure(spec, {})
        msg = str(exc_info.value)
        assert any(
            kw in msg.lower()
            for kw in [
                "client_secret",
                "secret",
                "gaia_google_client_secret",
                "settings",
            ]
        )

    @pytest.mark.asyncio
    async def test_configure_proceeds_when_secret_present_in_env(self, monkeypatch):
        """When client_secret IS present, configure() proceeds normally."""
        monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "test.apps.example")
        monkeypatch.setenv("GAIA_GOOGLE_CLIENT_SECRET", "GOCSPX-secret")

        from gaia.connectors.providers import _registry

        _registry.clear()

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

    @pytest.mark.asyncio
    async def test_configure_proceeds_when_secret_in_keyring(self, monkeypatch):
        """When client_secret IS present in keyring (Save & Connect path),
        configure() proceeds normally."""
        monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "test.apps.example")
        monkeypatch.delenv("GAIA_GOOGLE_CLIENT_SECRET", raising=False)

        from gaia.connectors.providers import _registry

        _registry.clear()

        spec = _make_spec()
        handler = OAuthPkceHandler()
        flow_info = {
            "flow_id": "f",
            "authorization_url": "https://accounts.google.com/",
        }
        with patch(
            "gaia.connectors.oauth_pkce.start_authorization",
            new=AsyncMock(return_value=flow_info),
        ):
            result = await handler.configure(
                spec,
                {
                    "client_id": "test.apps.example",
                    "client_secret": "GOCSPX-provided-directly",
                },
            )
        assert result["flow_id"] == "f"
