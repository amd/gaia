# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A cached access token must not outlive the connection that minted it."""

from __future__ import annotations

import pytest

from gaia.connectors import api as api_mod
from gaia.connectors import flow as flow_mod
from gaia.connectors import oauth_pkce as oauth_pkce_mod
from gaia.connectors import providers, tokens
from tests.unit.connectors.test_device_flow import (
    MAIL_READ,
    _FakeResp,
    _id_token,
    _install_responses,
)

pytestmark = pytest.mark.allow_network


def _seed_cached_token(provider_id: str) -> None:
    tokens._cache[tokens._cache_key(provider_id, "default")] = tokens._AccessTokenCache(
        access_token="OLD-ACCOUNT-TOKEN", expires_at=1e12
    )


def _has_cached_token(provider_id: str) -> bool:
    return any(key[0] == provider_id for key in tokens._cache)


@pytest.fixture
def _ms_env(monkeypatch):
    monkeypatch.setenv("GAIA_MICROSOFT_CLIENT_ID", "test-client-id")
    monkeypatch.delenv("GAIA_MICROSOFT_CLIENT_SECRET", raising=False)
    providers._registry.clear()
    yield
    providers._registry.clear()


@pytest.fixture
def _no_remote_revoke(monkeypatch):
    async def no_remote_revoke(_provider, **_kw):
        return {"revoke_supported": False, "revoked_remotely": False}

    monkeypatch.setattr(flow_mod, "revoke_provider_token", no_remote_revoke)
    monkeypatch.setattr(oauth_pkce_mod, "revoke_provider_token", no_remote_revoke)


async def test_revoke_connection_drops_the_cached_token(_no_remote_revoke):
    _seed_cached_token("google")
    _seed_cached_token("microsoft")

    await api_mod.revoke_connection_async("google")

    assert not _has_cached_token("google")
    assert _has_cached_token("microsoft")


async def test_handler_disconnect_drops_the_cached_token(_no_remote_revoke):
    from tests.unit.connectors.test_oauth_pkce import _make_spec

    _seed_cached_token("google")

    await oauth_pkce_mod.OAuthPkceHandler().disconnect(_make_spec())

    assert not _has_cached_token("google")


async def test_a_completed_device_connect_drops_the_previous_token(
    monkeypatch, _ms_env
):
    async def _no_wait(_seconds):
        return None

    monkeypatch.setattr(flow_mod.asyncio, "sleep", _no_wait)
    _install_responses(
        monkeypatch,
        [
            _FakeResp(
                200,
                {
                    "access_token": "NEW",
                    "refresh_token": "RT-new",
                    "expires_in": 3600,
                    "id_token": _id_token({"preferred_username": "b@example.com"}),
                    "scope": MAIL_READ,
                },
            )
        ],
    )
    _seed_cached_token("microsoft")

    await flow_mod.poll_device_flow(
        "microsoft", "DEV", scopes=[MAIL_READ], interval=1, expires_in=900
    )

    assert not _has_cached_token("microsoft")


async def test_a_completed_browser_connect_drops_the_previous_token(
    monkeypatch, _ms_env
):
    import asyncio

    from gaia.connectors.flow import _PendingFlow

    _install_responses(
        monkeypatch,
        [
            _FakeResp(
                200,
                {
                    "access_token": "NEW",
                    "refresh_token": "RT-new",
                    "expires_in": 3600,
                    "id_token": _id_token({"preferred_username": "b@example.com"}),
                    "scope": MAIL_READ,
                },
            )
        ],
    )
    _seed_cached_token("microsoft")
    flow = _PendingFlow(
        flow_id="f1",
        provider_id="microsoft",
        scopes=[MAIL_READ],
        code_verifier="v",
        state="s",
        redirect_uri="http://127.0.0.1/callback",
        runner=None,
        future=asyncio.get_running_loop().create_future(),
    )

    await flow_mod._exchange_code_for_tokens(flow, "code")

    assert not _has_cached_token("microsoft")
