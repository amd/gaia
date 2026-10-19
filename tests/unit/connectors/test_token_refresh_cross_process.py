# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Token refresh must be safe across GAIA processes, not just across tasks.

The daemon, the Agent UI and the CLI each run their own token cache over one
shared keyring. Guarded only by an in-process ``asyncio.Lock``, two processes
refreshed with the same refresh token; once the provider rotated it, the
loser got ``invalid_grant`` and deleted the connection the winner had just
saved. Here a child Python process stands in for the other GAIA process; the
token endpoint is respx and the keyring is the in-memory test backend.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

import httpx
import pytest
import respx

from gaia.connectors import _refresh_lock, tokens
from gaia.connectors.errors import ConnectionRevokedError, ConnectorsError
from gaia.connectors.providers import _registry
from gaia.connectors.store import load_connection, save_connection
from gaia.connectors.tokens import get_or_refresh

TOKEN_URL = "https://oauth2.googleapis.com/token"
SEED = "seed-refresh-token"

# Takes the refresh lock the way another GAIA process would, then holds it
# until its stdin closes.
_HOLDER = r"""
import os, sys
from gaia.daemon.lock import try_lock
path = sys.argv[1]
os.makedirs(os.path.dirname(path), exist_ok=True)
fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
assert try_lock(fd), "lock already held"
print("held", flush=True)
sys.stdin.read()
"""


@pytest.fixture
def google(monkeypatch):
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "test.apps.example")
    _registry.clear()
    from gaia.connectors.providers import get as get_provider

    provider = get_provider("google")
    _save(provider, SEED)
    return provider


def _save(provider, refresh_token):
    save_connection(
        provider="google",
        account_email="alice@example.com",
        refresh_token=refresh_token,
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        client_id_hash=provider.client_id_hash,
    )


def _stored_refresh_token(provider):
    stored = load_connection("google", current_client_id_hash=provider.client_id_hash)
    return stored and stored["refresh_token"]


def _sent_refresh_token(request) -> str:
    return urllib.parse.parse_qs(request.content.decode())["refresh_token"][0]


def _lock_path() -> Path:
    # Path.home() is a per-test tmp dir (tests/unit/connectors/conftest.py).
    return Path.home() / ".gaia" / "connectors" / "google.refresh.lock"


@pytest.fixture
def other_process():
    """A separate OS process holding the google refresh lock."""
    src = Path(tokens.__file__).resolve().parents[2]
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(_lock_path())],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(src)},
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        yield holder
    finally:
        if holder.poll() is None:
            holder.stdin.close()
            holder.wait(timeout=10)


def _release(holder):
    holder.stdin.close()
    holder.wait(timeout=10)


def _rotating_endpoint(valid: str, access: str):
    """Accepts only ``valid``; any other refresh token was already rotated."""

    def handler(request):
        if _sent_refresh_token(request) == valid:
            return httpx.Response(
                200, json={"access_token": access, "expires_in": 3600}
            )
        return httpx.Response(400, json={"error": "invalid_grant"})

    return handler


@respx.mock
async def test_waits_for_the_other_process_and_uses_its_rotated_token(
    google, other_process
):
    respx.post(TOKEN_URL).mock(side_effect=_rotating_endpoint("rotated", "fresh"))

    refresh = asyncio.create_task(get_or_refresh("google"))
    await asyncio.sleep(0.5)
    assert not refresh.done(), "refreshed while another process held the lock"

    # The other process finishes its refresh: the provider rotated the token.
    _save(google, "rotated")
    _release(other_process)

    assert await asyncio.wait_for(refresh, 10) == "fresh"
    assert _stored_refresh_token(google) == "rotated"


@respx.mock
async def test_lock_held_too_long_fails_loudly(monkeypatch, google, other_process):
    monkeypatch.setattr(_refresh_lock, "LOCK_TIMEOUT_SECONDS", 0.3)
    route = respx.post(TOKEN_URL).mock(side_effect=_rotating_endpoint(SEED, "x"))

    with pytest.raises(ConnectorsError, match="has been busy"):
        await get_or_refresh("google")

    assert route.call_count == 0
    assert _stored_refresh_token(google) == SEED


@respx.mock
async def test_invalid_grant_after_a_reconnect_keeps_the_new_connection(google):
    def reconnect_mid_refresh(request):
        # The user reconnects (a new refresh token is saved) while this
        # refresh, using the old token, is still in flight.
        _save(google, "reconnected")
        return httpx.Response(400, json={"error": "invalid_grant"})

    respx.post(TOKEN_URL).mock(side_effect=reconnect_mid_refresh)

    with pytest.raises(ConnectorsError, match="replaced") as exc:
        await get_or_refresh("google")

    assert not isinstance(exc.value, ConnectionRevokedError)
    assert _stored_refresh_token(google) == "reconnected"


@respx.mock
async def test_genuine_invalid_grant_still_deletes_the_connection(google):
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"})
    )

    with pytest.raises(ConnectionRevokedError):
        await get_or_refresh("google")

    assert _stored_refresh_token(google) is None


@respx.mock
async def test_keyring_calls_run_off_the_event_loop(monkeypatch, google):
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": "a", "expires_in": 3600, "refresh_token": "r2"}
        )
    )
    loop_thread = threading.get_ident()
    seen = {}

    def record(name, real):
        def wrapper(*args, **kwargs):
            seen[name] = threading.get_ident()
            return real(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(
        tokens, "load_connection", record("load", tokens.load_connection)
    )
    monkeypatch.setattr(
        tokens, "save_connection", record("save", tokens.save_connection)
    )

    await get_or_refresh("google")

    assert set(seen) == {"load", "save"}
    assert loop_thread not in seen.values(), "keyring call blocked the event loop"


def _rotated():
    return httpx.Response(
        200, json={"access_token": "a", "expires_in": 3600, "refresh_token": "r2"}
    )


@respx.mock
async def test_rotation_after_a_reconnect_does_not_overwrite_it(google):
    def reconnect_then_rotate(request):
        _save(google, "reconnected")
        return _rotated()

    respx.post(TOKEN_URL).mock(side_effect=reconnect_then_rotate)

    with pytest.raises(ConnectorsError, match="replaced"):
        await get_or_refresh("google")

    assert _stored_refresh_token(google) == "reconnected"


@respx.mock
async def test_rotation_after_a_disconnect_does_not_resurrect_it(google):
    from gaia.connectors.store import delete_connection

    def disconnect_then_rotate(request):
        delete_connection("google")
        return _rotated()

    respx.post(TOKEN_URL).mock(side_effect=disconnect_then_rotate)

    with pytest.raises(ConnectorsError, match="replaced"):
        await get_or_refresh("google")

    assert _stored_refresh_token(google) is None


@respx.mock
async def test_invalid_grant_during_a_torn_read_deletes_nothing(monkeypatch, google):
    # A chunked blob being rewritten by a reconnect reads back as None.
    monkeypatch.setattr(tokens, "peek_connection", lambda *_a, **_k: None)
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"})
    )

    with pytest.raises(ConnectorsError, match="replaced"):
        await get_or_refresh("google")

    monkeypatch.undo()
    assert _stored_refresh_token(google) == SEED


def _lock_is_free() -> bool:
    from gaia.daemon.lock import try_lock, unlock

    fd = os.open(str(_lock_path()), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if try_lock(fd):
            unlock(fd)
            return True
        return False
    finally:
        os.close(fd)


@respx.mock
async def test_cancel_during_a_write_keeps_the_lock_until_it_finishes(
    monkeypatch, google
):
    respx.post(TOKEN_URL).mock(return_value=_rotated())
    started, finish = threading.Event(), threading.Event()
    real_save = tokens.save_connection

    def slow_save(**kwargs):
        started.set()
        assert finish.wait(5), "test never released the write"
        real_save(**kwargs)

    monkeypatch.setattr(tokens, "save_connection", slow_save)

    refresh = asyncio.create_task(get_or_refresh("google"))
    for _ in range(100):
        if started.is_set():
            break
        await asyncio.sleep(0.05)
    assert started.is_set()

    refresh.cancel()
    await asyncio.sleep(0.2)
    assert not _lock_is_free(), "lock released while the keyring write ran"

    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(refresh, 5)
    assert _lock_is_free()
    assert _stored_refresh_token(google) == "r2"


async def test_a_slow_token_endpoint_fails_inside_the_lock_cap(monkeypatch, google):
    monkeypatch.setattr(tokens, "_TOKEN_POST_DEADLINE_SECONDS", 0.2)

    async def slow_post(self, url, **kwargs):
        await asyncio.sleep(1.0)
        return _rotated()

    monkeypatch.setattr(httpx.AsyncClient, "post", slow_post)

    with pytest.raises(httpx.TimeoutException, match="did not answer"):
        await get_or_refresh("google")


async def test_a_hung_peer_task_in_this_process_is_bounded(monkeypatch, google):
    from gaia.connectors.tokens import _AccessTokenCache, _cache, _cache_key

    monkeypatch.setattr(_refresh_lock, "LOCK_TIMEOUT_SECONDS", 0.3)
    entry = _AccessTokenCache()
    _cache[_cache_key("google", "default")] = entry
    await entry.lock.acquire()  # a peer task stuck in a keyring write
    try:
        with pytest.raises(ConnectorsError, match="has been busy"):
            await asyncio.wait_for(get_or_refresh("google"), 5)
    finally:
        entry.lock.release()


@respx.mock
async def test_a_forward_while_waiting_does_not_clear_the_new_connection(
    monkeypatch, google, other_process
):
    """The refresh resolved the provider before waiting. A forwarded import
    that ran meanwhile replaced the client, and the stale client hash made the
    store clear the connection the import had just saved."""
    from contextlib import nullcontext

    from gaia.connectors import api as api_mod
    from tests.unit.connectors.test_forwarded_import import FULL_SCOPES

    respx.post(TOKEN_URL).mock(
        side_effect=_rotating_endpoint("forwarded-refresh", "fresh")
    )
    refresh = asyncio.create_task(get_or_refresh("google"))
    await asyncio.sleep(0.5)
    assert not refresh.done()

    # The forward itself, as if it ran just before the other process let go.
    monkeypatch.setattr(api_mod, "held_sync", lambda _provider: nullcontext())
    api_mod.import_forwarded_connection(
        provider="google",
        client_id="forwarded.apps.googleusercontent.com",
        client_secret="forwarded-secret",
        refresh_token="forwarded-refresh",
        scopes=FULL_SCOPES,
    )
    _release(other_process)

    assert await asyncio.wait_for(refresh, 10) == "fresh"
    assert _stored_refresh_token_any_client() == "forwarded-refresh"


def _stored_refresh_token_any_client():
    from gaia.connectors.store import peek_connection

    stored = peek_connection("google")
    return stored and stored["refresh_token"]


async def test_the_slot_and_file_lock_waits_share_one_budget(
    monkeypatch, google, other_process
):
    from gaia.connectors.tokens import _AccessTokenCache, _cache, _cache_key

    monkeypatch.setattr(_refresh_lock, "LOCK_TIMEOUT_SECONDS", 1.0)
    entry = _AccessTokenCache()
    _cache[_cache_key("google", "default")] = entry
    await entry.lock.acquire()

    async def peer_finishes_after(seconds):
        await asyncio.sleep(seconds)
        entry.lock.release()

    peer = asyncio.create_task(peer_finishes_after(0.6))
    started = time.monotonic()
    with pytest.raises(ConnectorsError, match="has been busy"):
        await asyncio.wait_for(get_or_refresh("google"), 10)
    elapsed = time.monotonic() - started
    await peer

    assert elapsed < 1.4, f"waited {elapsed:.2f}s on a 1.0 s budget"
