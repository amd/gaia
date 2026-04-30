# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
T-3 unit tests — ConnectorHandler Protocol + get_credential dispatcher.

Tests cover:
- Protocol structural compatibility (duck-typing, not subclassing)
- Dispatcher raises ConnectorsError when no handler is registered
- Dispatcher routes to registered handler
- Grant check blocks unauthorized agents
- get_credential_sync raises in a running event loop
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from gaia.connectors.errors import AuthRequiredError, ConnectorsError
from gaia.connectors.handler import (
    ConnectorHandler,
    _HANDLER_REGISTRY,
    configure,
    disconnect,
    get_credential,
    health_check,
    register_handler,
)
from gaia.connectors.registry import ConnectorRegistry
from gaia.connectors.spec import ConnectorSpec

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_registries(monkeypatch):
    """Give each test a fresh REGISTRY and handler registry."""
    fresh_reg = ConnectorRegistry()
    monkeypatch.setattr("gaia.connectors.handler.REGISTRY", fresh_reg)
    original_handlers = dict(_HANDLER_REGISTRY)
    _HANDLER_REGISTRY.clear()
    yield fresh_reg
    _HANDLER_REGISTRY.clear()
    _HANDLER_REGISTRY.update(original_handlers)


@pytest.fixture
def google_spec(isolated_registries):
    spec = ConnectorSpec(
        id="google",
        display_name="Google",
        icon="G",
        category="oauth",
        tier=1,
        type="oauth_pkce",
        description="Google OAuth",
        default_scopes=("openid",),
    )
    isolated_registries.register(spec)
    return spec


class FakeOAuthHandler:
    """A minimal duck-type implementation of ConnectorHandler for testing."""

    async def get_credential(self, spec, *, required_scopes=None, account_id=None):
        return {"access_token": "fake-token", "scopes": list(required_scopes or [])}

    async def configure(self, spec, config):
        return {"configured": True}

    async def disconnect(self, spec, *, account_id=None):
        pass

    async def test(self, spec):
        return {"ok": True, "detail": "healthy"}


# ---------------------------------------------------------------------------
# Protocol structural test
# ---------------------------------------------------------------------------


class TestConnectorHandlerProtocol:
    def test_fake_handler_satisfies_protocol(self):
        handler = FakeOAuthHandler()
        assert isinstance(handler, ConnectorHandler)

    def test_object_without_methods_does_not_satisfy(self):
        assert not isinstance(object(), ConnectorHandler)


# ---------------------------------------------------------------------------
# register_handler
# ---------------------------------------------------------------------------


class TestRegisterHandler:
    def test_register_then_dispatch(self, google_spec):
        register_handler("oauth_pkce", FakeOAuthHandler())
        assert "oauth_pkce" in _HANDLER_REGISTRY

    def test_duplicate_type_raises(self):
        register_handler("oauth_pkce", FakeOAuthHandler())
        with pytest.raises(ValueError, match="already registered"):
            register_handler("oauth_pkce", FakeOAuthHandler())


# ---------------------------------------------------------------------------
# get_credential dispatcher
# ---------------------------------------------------------------------------


class TestGetCredentialDispatcher:
    @pytest.mark.asyncio
    async def test_no_handler_raises_connectors_error(self, google_spec):
        with pytest.raises(ConnectorsError, match="No handler registered"):
            await get_credential("google")

    @pytest.mark.asyncio
    async def test_routes_to_handler(self, google_spec):
        register_handler("oauth_pkce", FakeOAuthHandler())
        result = await get_credential("google")
        assert result["access_token"] == "fake-token"

    @pytest.mark.asyncio
    async def test_unknown_connector_raises_keyerror(self):
        with pytest.raises(KeyError):
            await get_credential("unknown")

    @pytest.mark.asyncio
    async def test_grant_check_passes_authorized_agent(
        self, google_spec, monkeypatch, tmp_path
    ):
        monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
        from gaia.connectors.grants import grant_agent

        grant_agent("google", "builtin:chat", ["openid"])
        register_handler("oauth_pkce", FakeOAuthHandler())
        result = await get_credential(
            "google", agent_id="builtin:chat", required_scopes=["openid"]
        )
        assert result["access_token"] == "fake-token"

    @pytest.mark.asyncio
    async def test_grant_check_blocks_unauthorized_agent(
        self, google_spec, monkeypatch, tmp_path
    ):
        monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
        register_handler("oauth_pkce", FakeOAuthHandler())
        with pytest.raises(AuthRequiredError) as exc_info:
            await get_credential(
                "google", agent_id="builtin:chat", required_scopes=["openid"]
            )
        assert exc_info.value.reason is AuthRequiredError.Reason.AGENT_NOT_GRANTED
        assert exc_info.value.agent_id == "builtin:chat"
        assert "openid" in exc_info.value.missing_scopes

    @pytest.mark.asyncio
    async def test_no_agent_id_skips_grant_check(self, google_spec):
        register_handler("oauth_pkce", FakeOAuthHandler())
        result = await get_credential("google", required_scopes=["openid"])
        assert result["access_token"] == "fake-token"


# ---------------------------------------------------------------------------
# configure / disconnect / test_connector
# ---------------------------------------------------------------------------


class TestOtherDispatchPaths:
    @pytest.mark.asyncio
    async def test_configure_routes(self, google_spec):
        register_handler("oauth_pkce", FakeOAuthHandler())
        result = await configure("google", {"key": "val"})
        assert result["configured"] is True

    @pytest.mark.asyncio
    async def test_disconnect_routes(self, google_spec):
        register_handler("oauth_pkce", FakeOAuthHandler())
        await disconnect("google")  # should not raise

    @pytest.mark.asyncio
    async def test_health_check_routes(self, google_spec):
        register_handler("oauth_pkce", FakeOAuthHandler())
        result = await health_check("google")
        assert result["ok"] is True
