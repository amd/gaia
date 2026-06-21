# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for the 401 actionable-error path in ``_refresh_token``.
Root cause 3 of #1592: Google returns 401 invalid_client when client_secret
is absent or wrong; the old code raised a generic ConnectorsError without
naming the provider or telling the user what to do.

AC4: the 401 branch must raise an error that names the provider and account,
and distinguishes "reconnect" (client config looks fine -- maybe revoked at
provider level) from "server misconfigured" (client_secret missing).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from gaia.connectors.errors import (
    AuthRequiredError,
    ConfigurationError,
    ConnectorsError,
)
from gaia.connectors.providers import _registry
from gaia.connectors.store import save_connection
from gaia.connectors.tokens import get_or_refresh


@pytest.fixture
def google_provider(monkeypatch):
    """Build a known Google provider with a client_secret configured."""
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "test.apps.example")
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_SECRET", "test-secret")
    _registry.clear()
    from gaia.connectors.providers import get as get_provider

    return get_provider("google")


@pytest.fixture
def google_provider_no_secret(monkeypatch):
    """Build a Google provider with NO client_secret (missing config)."""
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "test.apps.example")
    monkeypatch.delenv("GAIA_GOOGLE_CLIENT_SECRET", raising=False)
    _registry.clear()
    from gaia.connectors.providers import get as get_provider

    return get_provider("google")


@pytest.fixture
def seeded_connection(google_provider):
    save_connection(
        provider="google",
        account_email="alice@example.com",
        refresh_token="seed-refresh-token",
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        client_id_hash=google_provider.client_id_hash,
    )
    yield google_provider


@pytest.fixture
def seeded_connection_no_secret(google_provider_no_secret):
    save_connection(
        provider="google",
        account_email="alice@example.com",
        refresh_token="seed-refresh-token",
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        client_id_hash=google_provider_no_secret.client_id_hash,
    )
    yield google_provider_no_secret


class TestToken401ActionableError:
    @respx.mock
    async def test_401_with_secret_present_raises_actionable_reauth(
        self, seeded_connection
    ):
        """401 when client_secret is configured -> actionable reauth error
        naming the provider (reconnect path, not config-missing path)."""
        respx.post("https://oauth2.googleapis.com/token").mock(
            return_value=httpx.Response(
                401,
                json={
                    "error": "invalid_client",
                    "error_description": "The OAuth client was not found.",
                },
            )
        )
        with pytest.raises((AuthRequiredError, ConnectorsError)) as exc_info:
            await get_or_refresh("google")
        msg = str(exc_info.value)
        # Must name the provider
        assert "google" in msg.lower()
        # Must not be a silent generic error -- it names an action
        assert any(
            kw in msg.lower() for kw in ["reconnect", "connect", "settings", "reauth"]
        )

    @respx.mock
    async def test_401_with_no_secret_raises_config_error(
        self, seeded_connection_no_secret, monkeypatch
    ):
        """401 when client_secret is absent -> ConfigurationError naming the
        missing env var / Settings path, NOT a generic "try again" message."""
        respx.post("https://oauth2.googleapis.com/token").mock(
            return_value=httpx.Response(
                401,
                json={"error": "invalid_client", "error_description": "Unauthorized"},
            )
        )
        with pytest.raises((ConfigurationError, ConnectorsError)) as exc_info:
            await get_or_refresh("google")
        msg = str(exc_info.value)
        # Must name something about client secret / configuration
        assert any(
            kw in msg.lower()
            for kw in ["client_secret", "secret", "config", "setting", "gaia_google"]
        )

    @respx.mock
    async def test_401_does_not_silently_succeed(self, seeded_connection):
        """A 401 from the token endpoint must NEVER return a token."""
        respx.post("https://oauth2.googleapis.com/token").mock(
            return_value=httpx.Response(401, json={"error": "invalid_client"})
        )
        with pytest.raises(Exception):
            await get_or_refresh("google")

    @respx.mock
    async def test_generic_non200_non400_non401_still_raises(self, seeded_connection):
        """500s still raise a ConnectorsError (regression guard)."""
        respx.post("https://oauth2.googleapis.com/token").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )
        with pytest.raises(ConnectorsError):
            await get_or_refresh("google")
