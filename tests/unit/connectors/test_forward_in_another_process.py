# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A connection forwarded by another GAIA process must survive this one.

Each process caches its OAuth provider, client hash included. When another
process forwards a connection under a new client, this process's cached hash
is stale, and the store's client-hash tripwire used to clear the connection
that had just been saved.

Both processes share one file-backed keyring and one temp home. A child
Python process does the forward where the process boundary is the point;
elsewhere the test writes the shared store directly, which is all the other
process does, while this process's provider cache stays stale.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

import httpx
import keyring
import pytest
import respx

from gaia.connectors import _refresh_lock, tokens
from gaia.connectors.errors import (
    AuthRequiredError,
    ConnectionBusyError,
    ConnectorsError,
)
from gaia.connectors.providers import _registry
from gaia.connectors.providers import get as get_provider
from gaia.connectors.providers.google import GoogleOAuthProvider
from gaia.connectors.providers.microsoft import MicrosoftOAuthProvider
from gaia.connectors.store import (
    peek_connection,
    peek_provider_credentials,
    save_connection,
    save_provider_credentials,
)
from tests.unit.connectors._file_keyring import FileKeyring

TOKEN_URL = "https://oauth2.googleapis.com/token"
OLD_CLIENT = "old.apps.example"
NEW_CLIENT = "forwarded.apps.example"
GMAIL = "https://www.googleapis.com/auth/gmail.modify"
# A child imports GAIA from scratch, which is slow on a loaded machine.
_CHILD_TIMEOUT = 180

_FORWARD = r"""
import sys
sys.path.insert(0, sys.argv[1])
import keyring
from _file_keyring import FileKeyring
keyring.set_keyring(FileKeyring(sys.argv[2]))
from gaia.connectors.api import import_forwarded_connection
import_forwarded_connection(
    provider="google",
    client_id=sys.argv[3],
    client_secret="forwarded-secret",
    refresh_token="forwarded-refresh",
    scopes=[sys.argv[4]],
    required_scopes=[],
)
print("forwarded", flush=True)
"""

# Holds a connection lock the way another GAIA process would, until stdin closes.
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


def _child_env() -> dict:
    src = Path(tokens.__file__).resolve().parents[2]
    return {**os.environ, "PYTHONPATH": str(src)}


@pytest.fixture
def shared_keyring(mock_home, monkeypatch):
    """One keyring file and one home, shared with any child process."""
    monkeypatch.setenv("GAIA_HOME", str(mock_home / ".gaia"))
    for name in (
        "GAIA_GOOGLE_CLIENT_ID",
        "GAIA_GOOGLE_CLIENT_SECRET",
        "PYTHON_KEYRING_BACKEND",
    ):
        monkeypatch.delenv(name, raising=False)
    path = mock_home / "keyring.json"
    previous = keyring.get_keyring()
    keyring.set_keyring(FileKeyring(path))
    _registry.clear()
    try:
        yield path
    finally:
        _registry.clear()
        keyring.set_keyring(previous)


@pytest.fixture
def connected_under_old_client(shared_keyring):
    """This process configured a client, connected, and cached the provider."""
    save_provider_credentials("google", client_id=OLD_CLIENT, client_secret="old")
    cached = get_provider("google")
    save_connection(
        provider="google",
        account_email="alice@example.com",
        refresh_token="old-refresh",
        scopes=[GMAIL],
        client_id_hash=cached.client_id_hash,
    )
    return cached


@pytest.fixture
def lock_held_elsewhere(shared_keyring):
    """Another OS process holding the google connection lock."""
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(_refresh_lock.lock_path("google"))],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=_child_env(),
    )
    # A child hung on import fails the test instead of hanging it.
    watchdog = threading.Timer(_CHILD_TIMEOUT, holder.kill)
    watchdog.start()
    try:
        ready = holder.stdout.readline().strip()
        watchdog.cancel()
        assert ready == "held"
        yield holder
    finally:
        watchdog.cancel()
        if holder.poll() is None:
            holder.stdin.close()
            holder.wait(timeout=_CHILD_TIMEOUT)


def _release(holder) -> None:
    holder.stdin.close()
    holder.wait(timeout=_CHILD_TIMEOUT)


def _forward_in_another_process(keyring_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _FORWARD,
            str(Path(__file__).parent),
            str(keyring_path),
            NEW_CLIENT,
            GMAIL,
        ],
        env=_child_env(),
        capture_output=True,
        text=True,
        timeout=_CHILD_TIMEOUT,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "forwarded"


def _written_by_another_process(client_id: str = NEW_CLIENT) -> None:
    """The keyring writes a forward makes, leaving this process's cache alone."""
    save_provider_credentials("google", client_id=client_id, client_secret="new")
    save_connection(
        provider="google",
        account_email="alice@example.com",
        refresh_token="forwarded-refresh",
        scopes=[GMAIL],
        client_id_hash=GoogleOAuthProvider(client_id, "new").client_id_hash,
        forwarded=True,
    )


def _accepts_only_the_forwarded_grant(request):
    body = urllib.parse.parse_qs(request.content.decode())
    if body["client_id"] == [NEW_CLIENT] and body["refresh_token"] == [
        "forwarded-refresh"
    ]:
        return httpx.Response(200, json={"access_token": "fresh", "expires_in": 3600})
    return httpx.Response(400, json={"error": "invalid_grant"})


def _stored_refresh_token(provider_id: str = "google"):
    stored = peek_connection(provider_id)
    return stored and stored["refresh_token"]


@respx.mock
async def test_refresh_keeps_a_connection_another_process_forwarded(
    shared_keyring, connected_under_old_client
):
    _forward_in_another_process(shared_keyring)
    respx.post(TOKEN_URL).mock(side_effect=_accepts_only_the_forwarded_grant)

    assert await tokens.get_or_refresh("google") == "fresh"
    assert _stored_refresh_token() == "forwarded-refresh"


@respx.mock
async def test_get_access_token_keeps_a_connection_another_process_forwarded(
    connected_under_old_client,
):
    from gaia.connectors.api import get_access_token

    _written_by_another_process()
    respx.post(TOKEN_URL).mock(side_effect=_accepts_only_the_forwarded_grant)

    assert await get_access_token(provider="google", scopes=[GMAIL]) == "fresh"
    assert _stored_refresh_token() == "forwarded-refresh"


def test_listing_keeps_a_connection_another_process_forwarded(
    connected_under_old_client,
):
    from gaia.connectors.api import list_connections

    _written_by_another_process()

    assert [c["provider"] for c in list_connections()] == ["google"]
    assert _stored_refresh_token() == "forwarded-refresh"


def test_a_forward_landing_right_after_the_read_is_not_deleted(
    monkeypatch, connected_under_old_client
):
    """The connection is judged on the one read, so a forward that lands
    straight after it is never compared against the stale client."""
    from gaia.connectors.api import list_connections

    real_peek = tokens.peek_connection

    def peek_then_forward(*args, **kwargs):
        blob = real_peek(*args, **kwargs)
        monkeypatch.setattr(tokens, "peek_connection", real_peek)
        _written_by_another_process()
        return blob

    monkeypatch.setattr(tokens, "peek_connection", peek_then_forward)

    assert [c["provider"] for c in list_connections()] == ["google"]
    assert _stored_refresh_token() == "forwarded-refresh"


def test_a_forward_landing_after_an_empty_read_is_not_deleted(
    monkeypatch, connected_under_old_client
):
    """An unreadable first read (absent, torn, corrupt) defers to the store,
    which must re-read under the lock, not judge the forward unlocked."""
    from gaia.connectors.api import list_connections

    real_peek = tokens.peek_connection

    def empty_then_forward(*args, **kwargs):
        monkeypatch.setattr(tokens, "peek_connection", real_peek)
        _written_by_another_process()
        return None

    monkeypatch.setattr(tokens, "peek_connection", empty_then_forward)

    assert [c["provider"] for c in list_connections()] == ["google"]
    assert _stored_refresh_token() == "forwarded-refresh"


@pytest.fixture
def busy_mismatch(connected_under_old_client, lock_held_elsewhere):
    """A connection that mismatches the configured client, while another
    process holds its lock — mid-save, so not known to be gone."""
    _written_by_another_process()
    save_provider_credentials("google", client_id="third.apps.example")
    return lock_held_elsewhere


def _busy_error():
    from gaia.connectors.api import _authorize_access

    with pytest.raises(ConnectionBusyError) as exc:
        _authorize_access(
            provider="google", scopes=[GMAIL], agent_id=None, account_email="default"
        )
    return exc.value


def test_a_busy_connection_is_not_reported_as_disconnected(busy_mismatch):
    from gaia.connectors.formatting import format_connector_error
    from gaia.ui.routers.connectors import _raise_http_for

    err = _busy_error()

    # The TUI keys on this phrase (preflight/check.go connectionBeingSaved).
    assert "being saved by another GAIA operation" in str(err)
    tool_text = format_connector_error(err)
    assert tool_text.startswith("CONNECTION_BUSY:")
    assert "Retry" in tool_text and "click Connect" not in tool_text
    http = _raise_http_for(err)
    assert http.status_code == 503
    assert http.detail["error"] == "connection_busy"
    assert http.headers["Retry-After"] == "1"
    assert _stored_refresh_token() == "forwarded-refresh"


def test_the_listing_keeps_a_busy_connection_then_clears_it_once_free(
    busy_mismatch,
):
    from gaia.connectors.api import get_connection

    row = get_connection("google")
    assert row["error"] == "busy"
    assert row["scopes"] == [], "scopes mid-save can't be trusted for a decision"
    assert row["account_email"] == "alice@example.com"
    assert "refresh_token" not in row
    assert _stored_refresh_token() == "forwarded-refresh"

    _release(busy_mismatch)
    assert get_connection("google") is None
    assert _stored_refresh_token() is None


def test_the_daemon_keeps_its_forward_while_the_connection_is_busy(busy_mismatch):
    from gaia.connectors.api import _authorize_access
    from tests.unit.test_daemon_forward import _forwarder

    def mint(*, provider, scopes, agent_id):
        _authorize_access(
            provider=provider, scopes=scopes, agent_id=None, account_email="default"
        )
        return ("token", 1_900_000_000.0)

    fwd, http = _forwarder(grants={"google": {"installed:email": [GMAIL]}}, mint=mint)
    with pytest.raises(ConnectionBusyError):
        fwd.forward_provider(
            "email", "google", base_url="http://127.0.0.1:9", bearer="b"
        )

    assert http.deletes == [], "withdrew a forward that is still valid"
    assert http.posts == []


def test_an_unreadable_connection_is_busy_while_another_process_writes_it(
    monkeypatch, connected_under_old_client, lock_held_elsewhere
):
    from gaia.connectors.api import list_connections

    monkeypatch.setattr(tokens, "peek_connection", lambda *_a, **_k: None)

    rows = list_connections()

    assert [(r["provider"], r["error"]) for r in rows] == [("google", "busy")]
    monkeypatch.undo()
    assert _stored_refresh_token() == "old-refresh"


def test_a_forward_saves_its_client_only_under_the_lock(
    connected_under_old_client, lock_held_elsewhere
):
    """A refresh holding the lock must never see the new client next to the
    old connection — that pairing reads as a dead connection and is cleared."""
    from gaia.connectors.api import import_forwarded_connection

    errors = []

    def forward():
        try:
            import_forwarded_connection(
                provider="google",
                client_id=NEW_CLIENT,
                client_secret="forwarded-secret",
                refresh_token="forwarded-refresh",
                scopes=[GMAIL],
                required_scopes=[],
            )
        except Exception as e:  # noqa: BLE001 - re-raised on the test thread
            errors.append(e)

    thread = threading.Thread(target=forward)
    thread.start()
    time.sleep(0.5)
    assert peek_provider_credentials("google")["client_id"] == OLD_CLIENT

    _release(lock_held_elsewhere)
    thread.join(timeout=_refresh_lock.LOCK_TIMEOUT_SECONDS)
    assert not thread.is_alive()
    assert not errors, errors
    assert peek_provider_credentials("google")["client_id"] == NEW_CLIENT
    assert _stored_refresh_token() == "forwarded-refresh"


@respx.mock
async def test_a_connection_whose_client_is_gone_is_still_cleared(
    connected_under_old_client,
):
    # Another process forwarded under one client, then configured a third.
    _written_by_another_process()
    save_provider_credentials("google", client_id="third.apps.example")
    route = respx.post(TOKEN_URL).mock(side_effect=_accepts_only_the_forwarded_grant)

    with pytest.raises(AuthRequiredError) as exc:
        await tokens.get_or_refresh("google")

    assert exc.value.reason == AuthRequiredError.Reason.REAUTH_REQUIRED
    assert route.call_count == 0
    assert _stored_refresh_token() is None


def _work_connection_under_tenant(tenant: str) -> None:
    """What another process writes when it sets ``tenant`` and reconnects."""
    save_provider_credentials("microsoft_work", client_id="ms-client", tenant=tenant)
    built = MicrosoftOAuthProvider(
        client_id="ms-client",
        client_secret="",
        tenant=tenant,
        provider_id="microsoft_work",
    )
    save_connection(
        provider="microsoft_work",
        account_email="bob@contoso.example",
        refresh_token="ms-refresh",
        scopes=["https://graph.microsoft.com/Mail.Read"],
        client_id_hash=built.client_id_hash,
        tenant=tenant,
    )


@pytest.fixture
def cached_work_provider(shared_keyring):
    save_provider_credentials("microsoft_work", client_id="ms-client", tenant="t-old")
    cached = get_provider("microsoft_work")
    assert cached.tenant == "t-old"
    return cached


def test_a_tenant_changed_in_another_process_keeps_the_connection(
    cached_work_provider,
):
    from gaia.connectors.api import list_connections

    _work_connection_under_tenant("t-new")

    assert [c["provider"] for c in list_connections()] == ["microsoft_work"]
    assert _stored_refresh_token("microsoft_work") == "ms-refresh"


async def test_a_connection_whose_tenant_is_gone_is_still_cleared(
    cached_work_provider,
):
    _work_connection_under_tenant("t-new")
    save_provider_credentials("microsoft_work", client_id="ms-client", tenant="t-3")

    with pytest.raises(AuthRequiredError) as exc:
        await tokens.get_or_refresh("microsoft_work")

    assert exc.value.reason == AuthRequiredError.Reason.TENANT_MISMATCH
    assert _stored_refresh_token("microsoft_work") is None


def test_the_startup_sweep_keeps_a_connection_another_process_forwarded(
    connected_under_old_client,
):
    from gaia.connectors.api import tripwire_check

    _written_by_another_process()

    tripwire_check()
    assert _stored_refresh_token() == "forwarded-refresh"


def test_the_startup_sweep_still_clears_a_connection_whose_client_is_gone(
    connected_under_old_client,
):
    from gaia.connectors.api import tripwire_check

    _written_by_another_process()
    save_provider_credentials("google", client_id="third.apps.example")

    tripwire_check()
    assert _stored_refresh_token() is None


@respx.mock
async def test_a_disconnect_in_another_process_stays_disconnected(
    connected_under_old_client,
):
    """A stale cache must not read as a reason to keep, or rewrite, a
    connection the user removed from another process."""
    from gaia.connectors.api import list_connections
    from gaia.connectors.store import delete_connection

    _written_by_another_process()
    delete_connection("google")
    route = respx.post(TOKEN_URL).mock(side_effect=_accepts_only_the_forwarded_grant)

    assert list_connections() == []
    with pytest.raises(AuthRequiredError) as exc:
        await tokens.get_or_refresh("google")

    assert exc.value.reason == AuthRequiredError.Reason.NOT_CONNECTED
    assert route.call_count == 0
    assert peek_connection("google") is None


def test_a_mismatch_is_not_cleared_while_another_thread_here_holds_the_lock(
    connected_under_old_client,
):
    """The lock is per open file, so a writer on another thread of this same
    process blocks the clear exactly as another process would."""
    from gaia.connectors.api import _authorize_access

    _written_by_another_process()
    save_provider_credentials("google", client_id="third.apps.example")
    holding, release = threading.Event(), threading.Event()

    def hold():
        with _refresh_lock.held_sync("google"):
            holding.set()
            release.wait(_CHILD_TIMEOUT)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert holding.wait(_CHILD_TIMEOUT)
        with pytest.raises(ConnectionBusyError, match="this process or another"):
            _authorize_access(
                provider="google",
                scopes=[GMAIL],
                agent_id=None,
                account_email="default",
            )
        assert _stored_refresh_token() == "forwarded-refresh"
    finally:
        release.set()
        holder.join(timeout=_CHILD_TIMEOUT)


@respx.mock
async def test_reloading_a_stale_client_never_logs_a_secret(
    caplog, connected_under_old_client
):
    _written_by_another_process()
    respx.post(TOKEN_URL).mock(side_effect=_accepts_only_the_forwarded_grant)

    with caplog.at_level("DEBUG", logger="gaia.connectors"):
        assert await tokens.get_or_refresh("google") == "fresh"

    assert "reloading" in caplog.text
    for secret in ("forwarded-refresh", "old-refresh"):
        assert secret not in caplog.text


def _forward_with_failing_connection_write(monkeypatch, error=None):
    from gaia.connectors import store
    from gaia.connectors.api import import_forwarded_connection

    def locked_keyring(**_kwargs):
        raise error or ConnectorsError("keyring locked (simulated)")

    monkeypatch.setattr(store, "save_connection", locked_keyring)
    try:
        import_forwarded_connection(
            provider="google",
            client_id=NEW_CLIENT,
            client_secret="forwarded-secret",
            refresh_token="forwarded-refresh",
            scopes=[GMAIL],
            required_scopes=[],
        )
    finally:
        monkeypatch.setattr(store, "save_connection", save_connection)


@respx.mock
async def test_a_forward_that_fails_halfway_keeps_the_existing_connection(
    monkeypatch, connected_under_old_client
):
    from gaia.connectors.api import list_connections

    with pytest.raises(ConnectorsError, match="keyring locked"):
        _forward_with_failing_connection_write(monkeypatch)

    assert peek_provider_credentials("google")["client_id"] == OLD_CLIENT
    # This process must not keep the forwarded client it was halfway to using.
    cached = _registry.get("google")
    assert cached is None or cached.client_id == OLD_CLIENT
    _registry.clear()  # any process, starting fresh
    assert [c["provider"] for c in list_connections()] == ["google"]
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "a", "expires_in": 60})
    )
    assert await tokens.get_or_refresh("google") == "a"
    assert _stored_refresh_token() == "old-refresh"


def test_a_failed_undo_of_a_halfway_forward_says_so(
    monkeypatch, connected_under_old_client
):
    from gaia.connectors import store

    def also_fails(_snapshot):
        raise ConnectorsError("keyring gone (simulated)")

    monkeypatch.setattr(store, "restore_connection_slots", also_fails)

    with pytest.raises(ConnectorsError, match="putting back the previous") as exc:
        _forward_with_failing_connection_write(monkeypatch)

    assert "keyring locked" in str(exc.value)
    assert "keyring gone" in str(exc.value)


def test_the_startup_sweep_clears_a_dead_connection_on_a_cold_start(
    connected_under_old_client,
):
    from gaia.connectors.api import tripwire_check

    save_provider_credentials("google", client_id="third.apps.example")
    _registry.clear()  # the sweep runs at startup, before anything is cached

    tripwire_check()

    assert _stored_refresh_token() is None


def test_the_startup_sweep_leaves_a_busy_connection_in_place(busy_mismatch, caplog):
    from gaia.connectors.api import tripwire_check

    with caplog.at_level("INFO", logger="gaia.connectors.api"):
        tripwire_check()

    assert _stored_refresh_token() == "forwarded-refresh"
    # Reported as a busy connection, not as a failed check or a cleared one.
    assert "being saved by another GAIA operation; left in place" in caplog.text
    assert "check failed" not in caplog.text
    assert "cleared by tripwire" not in caplog.text


def test_an_interrupted_forward_whose_undo_fails_still_raises_the_interrupt(
    monkeypatch, connected_under_old_client
):
    from gaia.connectors import store

    def also_fails(_snapshot):
        raise ConnectorsError("keyring gone (simulated)")

    monkeypatch.setattr(store, "restore_connection_slots", also_fails)

    with pytest.raises(KeyboardInterrupt) as exc:
        _forward_with_failing_connection_write(monkeypatch, KeyboardInterrupt())

    assert "keyring gone" in str(exc.value.__cause__), "the failed undo was not kept"


def test_an_interrupted_forward_still_puts_back_the_old_connection(
    monkeypatch, connected_under_old_client
):
    with pytest.raises(KeyboardInterrupt):
        _forward_with_failing_connection_write(monkeypatch, KeyboardInterrupt())

    assert peek_provider_credentials("google")["client_id"] == OLD_CLIENT
    assert _stored_refresh_token() == "old-refresh"
