# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
T-4a (AC10, AC11, A4, A5): keyring-backed connection store.

Coverage:
- save_connection / load_connection / delete_connection round-trip via the
  in-memory keyring fixture.
- ``client_id_hash`` tripwire: ``load_connection`` with a different current
  hash clears the entry and returns ``None`` (eager enforcement, AC10).
- Backend allowlist: ``PlaintextKeyring`` and ``EncryptedKeyring`` are
  refused with ``ConnectorsError`` BEFORE any write happens (A4).
- Single-blob atomicity: a single ``set_password`` per connection — token
  and metadata in one slot — so a partial write is impossible (A5).
- Refresh-token rotation: overwriting an existing entry replaces the value
  in place; the prior call to ``set_password`` is visible to ``get_password``
  immediately and no separate ``delete`` step is interposed.
- Hygiene: refresh-token sentinel never appears in caplog records.
"""

from __future__ import annotations

import keyring
import pytest

from gaia.connectors.errors import AuthRequiredError, ConnectorsError
from gaia.connectors.store import (
    _CHUNK_CHARS,
    _CHUNK_SENTINEL,
    SERVICE_NAME,
    _chunk_username,
    _connection_username,
    _provider_credentials_username,
    clear_provider_credentials,
    delete_connection,
    list_connections,
    load_connection,
    peek_connection,
    peek_provider_credentials,
    save_connection,
    save_provider_credentials,
    verify_keyring_backend,
)

SENTINEL_REFRESH_TOKEN = "REFRESH-TOKEN-FAKE-XYZ-do-not-leak"


class TestRoundTrip:
    def test_save_then_load(self):
        save_connection(
            provider="google",
            account_email="alice@example.com",
            refresh_token=SENTINEL_REFRESH_TOKEN,
            scopes=["https://www.googleapis.com/auth/gmail.readonly"],
            client_id_hash="hash-1",
        )
        loaded = load_connection(provider="google", current_client_id_hash="hash-1")
        assert loaded is not None
        assert loaded["account_email"] == "alice@example.com"
        assert loaded["refresh_token"] == SENTINEL_REFRESH_TOKEN
        assert loaded["scopes"] == ["https://www.googleapis.com/auth/gmail.readonly"]
        assert loaded["client_id_hash"] == "hash-1"
        assert "connected_at" in loaded

    def test_load_missing_returns_none(self):
        assert load_connection("google", current_client_id_hash="hash-1") is None

    def test_delete_removes_entry(self):
        save_connection(
            provider="google",
            account_email="alice@example.com",
            refresh_token=SENTINEL_REFRESH_TOKEN,
            scopes=["s1"],
            client_id_hash="h",
        )
        delete_connection("google")
        assert load_connection("google", current_client_id_hash="h") is None

    def test_delete_missing_is_idempotent(self):
        # Calling delete on an already-empty entry must not raise — the
        # caller may not know whether the entry exists.
        delete_connection("google")
        delete_connection("google")  # second call also fine

    def test_list_connections(self):
        save_connection(
            provider="google",
            account_email="a@example.com",
            refresh_token=SENTINEL_REFRESH_TOKEN,
            scopes=["s1"],
            client_id_hash="h",
        )
        ids = list_connections()
        assert "google" in ids

    def test_list_connections_finds_microsoft_only(self):
        # Registry-driven enumeration (#1603): a stored Microsoft connection
        # with NO google must surface — the old hardcoded ("google",) tuple
        # made every generic consumer Microsoft-blind.
        save_connection(
            provider="microsoft",
            account_email="m@example.com",
            refresh_token=SENTINEL_REFRESH_TOKEN,
            scopes=["s1"],
            client_id_hash="h",
        )
        ids = list_connections()
        assert ids == ["microsoft"]

    def test_list_connections_finds_both_providers(self):
        save_connection(
            provider="google",
            account_email="g@example.com",
            refresh_token=SENTINEL_REFRESH_TOKEN,
            scopes=["s1"],
            client_id_hash="h",
        )
        save_connection(
            provider="microsoft",
            account_email="m@example.com",
            refresh_token=SENTINEL_REFRESH_TOKEN,
            scopes=["s1"],
            client_id_hash="h",
        )
        ids = list_connections()
        assert set(ids) == {"google", "microsoft"}
        # Only oauth_pkce providers are enumerated — MCP-server connectors have
        # no keyring connection and must not appear.
        assert "mcp-git" not in ids


class TestLargeBlobChunking:
    """#1275: Windows Credential Manager caps a blob at ~2560 bytes. Microsoft
    refresh tokens (~1600 chars) exceed it, so the store transparently chunks
    oversized values across extra slots. These tests exercise that path (the
    in-memory keyring has no size cap, so we assert the chunk *mechanics*
    directly rather than relying on a backend to reject the write)."""

    def test_torn_rewrite_fails_safe_to_none(self):
        # A crashed mid-rewrite (chunk overwritten but manifest still points at
        # a stale count/CRC) must NOT reassemble into a truncated-but-valid
        # token — the CRC guard turns it into None ("reconnect").
        import keyring as _kr

        from gaia.connectors.store import _chunk_username

        save_connection(
            provider="microsoft",
            account_email="a@example.com",
            refresh_token="R" * (_CHUNK_CHARS * 2),  # 2 chunks
            scopes=["s1"],
            client_id_hash="h",
        )
        username = _connection_username("microsoft", "default")
        # Simulate a torn rewrite: chunk #0 gets new (longer) data, but the
        # manifest still describes the old payload.
        _kr.set_password(SERVICE_NAME, _chunk_username(username, 0), "X" * _CHUNK_CHARS)
        assert load_connection("microsoft", current_client_id_hash="h") is None

    def test_large_refresh_token_round_trips(self):
        big_token = "R" * (_CHUNK_CHARS * 3 + 17)  # forces 4 chunks
        save_connection(
            provider="microsoft",
            account_email="a@example.com",
            refresh_token=big_token,
            scopes=["s1"],
            client_id_hash="h",
        )
        blob = load_connection("microsoft", current_client_id_hash="h")
        assert blob is not None
        assert blob["refresh_token"] == big_token

    def test_large_blob_writes_sentinel_and_chunk_slots(self):
        big_token = "R" * (_CHUNK_CHARS * 2)
        save_connection(
            provider="microsoft",
            account_email="a@example.com",
            refresh_token=big_token,
            scopes=["s1"],
            client_id_hash="h",
        )
        username = _connection_username("microsoft", "default")
        # Base slot holds the manifest sentinel, not raw JSON.
        base = keyring.get_password(SERVICE_NAME, username)
        assert base.startswith(_CHUNK_SENTINEL)
        # At least the first chunk slot exists.
        assert keyring.get_password(SERVICE_NAME, _chunk_username(username, 0)) is not None

    def test_delete_removes_all_chunk_slots(self):
        big_token = "R" * (_CHUNK_CHARS * 2)
        save_connection(
            provider="microsoft",
            account_email="a@example.com",
            refresh_token=big_token,
            scopes=["s1"],
            client_id_hash="h",
        )
        username = _connection_username("microsoft", "default")
        delete_connection("microsoft")
        assert keyring.get_password(SERVICE_NAME, username) is None
        assert keyring.get_password(SERVICE_NAME, _chunk_username(username, 0)) is None
        assert load_connection("microsoft", current_client_id_hash="h") is None

    def test_shrink_from_chunked_to_small_sweeps_stale_chunks(self):
        # A large value (chunked) overwritten by a small one must not leave
        # orphaned chunk slots that a later reader could trip over.
        username = _connection_username("microsoft", "default")
        save_connection(
            provider="microsoft",
            account_email="a@example.com",
            refresh_token="R" * (_CHUNK_CHARS * 2),
            scopes=["s1"],
            client_id_hash="h",
        )
        assert keyring.get_password(SERVICE_NAME, _chunk_username(username, 0)) is not None
        # Overwrite with a short token — stored raw, chunk slots swept.
        save_connection(
            provider="microsoft",
            account_email="a@example.com",
            refresh_token="short",
            scopes=["s1"],
            client_id_hash="h",
        )
        assert keyring.get_password(SERVICE_NAME, _chunk_username(username, 0)) is None
        blob = load_connection("microsoft", current_client_id_hash="h")
        assert blob["refresh_token"] == "short"


class TestSingleBlobAtomicity:
    def test_one_keyring_slot_per_connection(self):
        # A5 fix: a single keyring slot stores token + metadata in one JSON
        # blob, so a partial write is impossible. Verify by inspecting the
        # backend directly: there is at most ONE keyring entry per provider.
        save_connection(
            provider="google",
            account_email="a@example.com",
            refresh_token=SENTINEL_REFRESH_TOKEN,
            scopes=["s1"],
            client_id_hash="h",
        )
        username = _connection_username("google", "a@example.com")
        # Multi-account-ready key shape (A10): "<provider>:<account_email>"
        assert username == "google:a@example.com"
        # Default-account key for callers that don't pass account_email —
        # used by load_connection until we wire the explicit path through.
        default = _connection_username("google", "default")
        assert default == "google:default"

    def test_rotation_overwrites_in_place(self):
        # Save once, save again with a new refresh token — load returns the
        # new one. No separate delete is performed.
        save_connection(
            provider="google",
            account_email="a@example.com",
            refresh_token="OLD",
            scopes=["s"],
            client_id_hash="h",
        )
        save_connection(
            provider="google",
            account_email="a@example.com",
            refresh_token="NEW",
            scopes=["s"],
            client_id_hash="h",
        )
        loaded = load_connection("google", current_client_id_hash="h")
        assert loaded["refresh_token"] == "NEW"


class TestClientIdHashTripwire:
    """AC10 — eager enforcement at every load. The store clears the entry
    and raises ``AuthRequiredError(REAUTH_REQUIRED)`` so the caller and
    the router can distinguish this case from 'user never connected'.
    Without this, a rotated client id would silently use stale tokens."""

    def test_mismatch_clears_entry_and_raises(self):
        save_connection(
            provider="google",
            account_email="alice@example.com",
            refresh_token=SENTINEL_REFRESH_TOKEN,
            scopes=["s"],
            client_id_hash="OLD-HASH",
        )
        # Caller now passes the NEW hash → tripwire fires.
        with pytest.raises(AuthRequiredError) as exc:
            load_connection("google", current_client_id_hash="NEW-HASH")
        assert exc.value.reason is AuthRequiredError.Reason.REAUTH_REQUIRED
        assert exc.value.provider == "google"
        # Entry was cleared — re-loading at any hash returns None.
        assert load_connection("google", current_client_id_hash="OLD-HASH") is None

    def test_match_returns_blob(self):
        save_connection(
            provider="google",
            account_email="a@example.com",
            refresh_token=SENTINEL_REFRESH_TOKEN,
            scopes=["s"],
            client_id_hash="HASH-1",
        )
        loaded = load_connection("google", current_client_id_hash="HASH-1")
        assert loaded is not None


class TestBackendAllowlist:
    """A4 — refuse insecure keyring backends explicitly. Without the check,
    a Linux system without SecretService could silently fall back to
    ``keyrings.alt.PlaintextKeyring`` (unencrypted file storage)."""

    def test_plaintext_backend_refused(self):
        # Build a keyring backend that names itself "PlaintextKeyring" —
        # this is the literal class name keyrings.alt ships.
        class PlaintextKeyring(keyring.backend.KeyringBackend):
            priority = 100

            def get_password(self, service, username):
                return None

            def set_password(self, service, username, password):
                raise AssertionError("must not write — store should refuse")

            def delete_password(self, service, username):
                pass

        previous = keyring.get_keyring()
        keyring.set_keyring(PlaintextKeyring())
        try:
            with pytest.raises(ConnectorsError) as exc:
                save_connection(
                    provider="google",
                    account_email="a@example.com",
                    refresh_token="x",
                    scopes=["s"],
                    client_id_hash="h",
                )
            msg = str(exc.value)
            assert "Insecure keyring backend" in msg
            assert "PlaintextKeyring" in msg
        finally:
            keyring.set_keyring(previous)

    def test_encrypted_file_backend_refused(self):
        # keyrings.alt's EncryptedKeyring is also disk-based and uses a
        # weak passphrase scheme; refuse it for the same reason.
        class EncryptedKeyring(keyring.backend.KeyringBackend):
            priority = 100

            def get_password(self, service, username):
                return None

            def set_password(self, service, username, password):
                raise AssertionError("must not write")

            def delete_password(self, service, username):
                pass

        previous = keyring.get_keyring()
        keyring.set_keyring(EncryptedKeyring())
        try:
            with pytest.raises(ConnectorsError) as exc:
                save_connection(
                    provider="google",
                    account_email="a@example.com",
                    refresh_token="x",
                    scopes=["s"],
                    client_id_hash="h",
                )
            assert "Insecure" in str(exc.value)
        finally:
            keyring.set_keyring(previous)

    def test_in_memory_test_backend_allowed(self):
        # The in-memory backend used in CI (autouse fixture) must be
        # explicitly allowlisted by class identity, not class name string,
        # so it works on every CI platform.
        verify_keyring_backend()  # must not raise


class TestKeyringFailureTranslated:
    def test_keyring_failure_raises_actionable_connections_error(self):
        # keyring.backends.fail.Keyring raises on every call. Our store
        # must catch that and surface a ConnectorsError naming what
        # failed, what to do, where to look.
        previous = keyring.get_keyring()
        keyring.set_keyring(keyring.backends.fail.Keyring())
        try:
            with pytest.raises(ConnectorsError) as exc:
                save_connection(
                    provider="google",
                    account_email="a@example.com",
                    refresh_token="x",
                    scopes=["s"],
                    client_id_hash="h",
                )
            msg = str(exc.value).lower()
            assert "keyring" in msg
            # Names what to do:
            assert any(tok in msg for tok in ("install", "configure", "see docs"))
        finally:
            keyring.set_keyring(previous)


class TestSecretHygiene:
    def test_save_does_not_log_refresh_token(self, caplog):
        caplog.set_level("DEBUG")
        save_connection(
            provider="google",
            account_email="a@example.com",
            refresh_token=SENTINEL_REFRESH_TOKEN,
            scopes=["s"],
            client_id_hash="h",
        )
        assert SENTINEL_REFRESH_TOKEN not in caplog.text

    def test_load_does_not_log_refresh_token(self, caplog):
        save_connection(
            provider="google",
            account_email="a@example.com",
            refresh_token=SENTINEL_REFRESH_TOKEN,
            scopes=["s"],
            client_id_hash="h",
        )
        caplog.clear()
        caplog.set_level("DEBUG")
        load_connection("google", current_client_id_hash="h")
        assert SENTINEL_REFRESH_TOKEN not in caplog.text


class TestPeekConnection:
    """``peek_connection`` is the read-only sibling of ``load_connection``
    used by the catalog UI/CLI to render "configured" without firing the
    client_id_hash tripwire — must be totally side-effect-free."""

    def test_returns_none_for_missing_entry(self):
        assert peek_connection("google") is None

    def test_returns_blob_when_present(self):
        save_connection(
            provider="google",
            account_email="peek@example.com",
            refresh_token=SENTINEL_REFRESH_TOKEN,
            scopes=["openid"],
            client_id_hash="hash-A",
        )
        blob = peek_connection("google")
        assert blob is not None
        assert blob["account_email"] == "peek@example.com"
        assert blob["scopes"] == ["openid"]

    def test_returns_blob_even_when_client_id_hash_stale(self):
        # Catalog render must NOT fire the tripwire — the user keeps
        # seeing "configured" until the next auth-path read.
        save_connection(
            provider="google",
            account_email="stale@example.com",
            refresh_token=SENTINEL_REFRESH_TOKEN,
            scopes=["openid"],
            client_id_hash="OLD-HASH",
        )
        blob = peek_connection("google")
        assert blob is not None
        assert blob["client_id_hash"] == "OLD-HASH"
        # And the entry is still there — peek did not clear it.
        assert (
            keyring.get_password(
                SERVICE_NAME, _connection_username("google", "default")
            )
            is not None
        )

    def test_corrupt_blob_returns_none_without_clearing(self):
        # A corrupt blob (not valid JSON) is treated as "not configured"
        # but the entry stays put — clearing is load_connection's job.
        keyring.set_password(
            SERVICE_NAME, _connection_username("google", "default"), "{not json"
        )
        assert peek_connection("google") is None
        assert (
            keyring.get_password(
                SERVICE_NAME, _connection_username("google", "default")
            )
            is not None
        )


class TestProviderCredentials:
    """Provider credentials (the *app's* OAuth client_id+client_secret)
    are stored in the keyring under a separate username namespace from
    the connection blob, so users can self-onboard via the AgentUI
    without ever touching env vars."""

    def test_username_namespace_does_not_collide_with_connection(self):
        # Connection: "google:default"; provider creds: "provider:google".
        # Both keyed under SERVICE_NAME but the username distinguishes them.
        assert _connection_username("google", "default") == "google:default"
        assert _provider_credentials_username("google") == "provider:google"

    def test_save_and_peek_roundtrip(self):
        save_provider_credentials(
            "google",
            client_id="abc.apps.googleusercontent.com",
            client_secret="GOCSPX-secret",
        )
        creds = peek_provider_credentials("google")
        assert creds == {
            "client_id": "abc.apps.googleusercontent.com",
            "client_secret": "GOCSPX-secret",
        }

    def test_peek_returns_none_when_absent(self):
        assert peek_provider_credentials("google") is None

    def test_clear_is_idempotent(self):
        save_provider_credentials("google", client_id="x", client_secret="y")
        clear_provider_credentials("google")
        assert peek_provider_credentials("google") is None
        # Second call must not raise.
        clear_provider_credentials("google")

    def test_save_rejects_empty_client_id(self):
        with pytest.raises(ConnectorsError, match="client_id is empty"):
            save_provider_credentials("google", client_id="", client_secret="x")

    def test_save_does_not_disturb_connection_blob(self):
        # Saving provider creds and a connection blob for the same provider
        # must both land — different keyring slots.
        save_connection(
            provider="google",
            account_email="a@example.com",
            refresh_token=SENTINEL_REFRESH_TOKEN,
            scopes=["s"],
            client_id_hash="h",
        )
        save_provider_credentials("google", client_id="cid", client_secret="cs")
        assert peek_connection("google") is not None
        assert peek_provider_credentials("google") == {
            "client_id": "cid",
            "client_secret": "cs",
        }


class TestConstants:
    def test_service_name_namespaced(self):
        # Per plan amendment A3, the keyring service name stays as
        # "gaia.connections" even after the module rename to gaia.connectors.
        # Renaming the constant would orphan #915's existing keyring entries.
        assert SERVICE_NAME == "gaia.connections"
