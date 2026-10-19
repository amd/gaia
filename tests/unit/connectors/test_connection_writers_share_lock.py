# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every keyring write of a connection holds the per-provider connection lock.

A token refresh compares the stored refresh token and then saves under that
lock. A connect or disconnect that wrote without it could land between the
compare and the write. Each writer is driven with its keyring call replaced
by a probe that tries to take the lock from a fresh handle: the probe must
find it held, and it must be free again afterwards.
"""

from __future__ import annotations

import os

import pytest

from gaia.connectors import _refresh_lock
from gaia.connectors import api as api_mod
from gaia.connectors import flow as flow_mod
from gaia.connectors import oauth_pkce as oauth_pkce_mod
from gaia.connectors.store import delete_connection, save_connection
from gaia.daemon.lock import try_lock, unlock
from tests.unit.connectors.test_device_flow import (
    MAIL_READ,
    _FakeResp,
    _id_token,
    _install_responses,
)
from tests.unit.connectors.test_forwarded_import import FULL_SCOPES as FORWARDED_SCOPES


def _lock_is_free(provider_id: str) -> bool:
    path = _refresh_lock.lock_path(provider_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if try_lock(fd):
            unlock(fd)
            return True
        return False
    finally:
        os.close(fd)


def _probe(provider_id, real, seen):
    def wrapper(*args, **kwargs):
        seen.append(_lock_is_free(provider_id))
        return real(*args, **kwargs)

    return wrapper


async def test_revoke_connection_deletes_under_the_lock(monkeypatch):
    seen = []

    async def no_remote_revoke(_provider, **_kw):
        return {"revoke_supported": False, "revoked_remotely": False}

    monkeypatch.setattr(flow_mod, "revoke_provider_token", no_remote_revoke)
    monkeypatch.setattr(
        api_mod, "delete_connection", _probe("google", delete_connection, seen)
    )

    await api_mod.revoke_connection_async("google")

    assert seen == [False]
    assert _lock_is_free("google")


async def test_oauth_disconnect_deletes_under_the_lock(monkeypatch):
    from tests.unit.connectors.test_oauth_pkce import _make_spec

    seen = []

    async def no_remote_revoke(_provider, **_kw):
        return {"revoke_supported": False, "revoked_remotely": False}

    monkeypatch.setattr(oauth_pkce_mod, "revoke_provider_token", no_remote_revoke)
    monkeypatch.setattr(
        oauth_pkce_mod, "delete_connection", _probe("google", delete_connection, seen)
    )

    await oauth_pkce_mod.OAuthPkceHandler().disconnect(_make_spec())

    assert seen == [False]
    assert _lock_is_free("google")


def test_forwarded_import_saves_under_the_lock(monkeypatch):
    seen = []
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "env.apps.googleusercontent.com")
    # import_forwarded_connection imports it from the store at call time.
    monkeypatch.setattr(
        "gaia.connectors.store.save_connection",
        _probe("google", save_connection, seen),
    )

    api_mod.import_forwarded_connection(
        provider="google",
        client_id="fwd.apps.googleusercontent.com",
        client_secret="fwd-secret",
        refresh_token="fwd-refresh",
        scopes=FORWARDED_SCOPES,
        account_email="alice@example.com",
    )

    assert seen == [False]
    assert _lock_is_free("google")


@pytest.fixture
def microsoft(monkeypatch):
    monkeypatch.setenv("GAIA_MICROSOFT_CLIENT_ID", "test-client-id")
    monkeypatch.delenv("GAIA_MICROSOFT_CLIENT_SECRET", raising=False)
    from gaia.connectors import providers

    providers._registry.clear()  # type: ignore[attr-defined]
    yield
    providers._registry.clear()  # type: ignore[attr-defined]


def _token_payload():
    return {
        "access_token": "AT",
        "refresh_token": "RT",
        "expires_in": 3600,
        "id_token": _id_token({"preferred_username": "user@example.com"}),
        "scope": MAIL_READ,
    }


async def test_device_flow_connect_saves_under_the_lock(monkeypatch, microsoft):
    seen = []
    _install_responses(monkeypatch, [_FakeResp(200, _token_payload())])
    monkeypatch.setattr(
        flow_mod, "save_connection", _probe("microsoft", save_connection, seen)
    )

    await flow_mod.poll_device_flow(
        "microsoft", "DEV", scopes=[MAIL_READ], interval=1, expires_in=900
    )

    assert seen == [False]
    assert _lock_is_free("microsoft")


async def test_browser_connect_saves_under_the_lock(monkeypatch, microsoft):
    import asyncio

    from gaia.connectors.flow import _PendingFlow

    seen = []
    _install_responses(monkeypatch, [_FakeResp(200, _token_payload())])
    monkeypatch.setattr(
        flow_mod, "save_connection", _probe("microsoft", save_connection, seen)
    )
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

    assert seen == [False]
    assert _lock_is_free("microsoft")


async def test_forward_route_waits_for_a_holder_on_its_own_loop(monkeypatch):
    """The UI route ran the import on the loop: a refresh holding the lock on
    that loop could never release it, so every such import timed out."""
    import asyncio

    from gaia.ui.routers import connectors as router

    monkeypatch.setattr(_refresh_lock, "LOCK_TIMEOUT_SECONDS", 2.0)
    monkeypatch.setattr(router, "_resolve_grant_scopes", lambda *_a, **_k: {})

    async def emit(*_a, **_k):
        return None

    monkeypatch.setattr(router._emitter, "emit", emit)
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "env.apps.googleusercontent.com")

    async def refresh_holding_the_lock():
        async with _refresh_lock.held("google"):
            await asyncio.sleep(0.5)

    holder = asyncio.create_task(refresh_holding_the_lock())
    while _lock_is_free("google"):
        await asyncio.sleep(0.01)

    body = router.ForwardConnectionRequest(
        client_id="fwd.apps.googleusercontent.com",
        client_secret="fwd-secret",
        refresh_token="fwd-refresh",
        scopes=FORWARDED_SCOPES,
        account_email="alice@example.com",
    )
    summary = await router.forward_connection(None, "google", body)

    await holder
    assert summary["provider"] == "google"
