# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the single-master-key encrypted credential vault."""

from __future__ import annotations

import os
import stat
import threading

import keyring
import pytest
from keyring.backends.null import Keyring as NullKeyring

from gaia.browser import session as browser_session
from gaia.connectors import store, vault
from gaia.connectors.errors import ConnectorsError
from gaia.messaging.discord import credentials as discord_credentials
from gaia.messaging.slack import credentials as slack_credentials


def _record_writes(monkeypatch, backend) -> list:
    """Usernames the OS keyring is asked to write or delete from now on."""
    writes = []
    real_set = backend.set_password
    real_delete = backend.delete_password

    def recording_set(service, username, value):
        writes.append(("set", username))
        return real_set(service, username, value)

    def recording_delete(service, username):
        writes.append(("delete", username))
        return real_delete(service, username)

    monkeypatch.setattr(backend, "set_password", recording_set)
    monkeypatch.setattr(backend, "delete_password", recording_delete)
    return writes


def _record_reads(monkeypatch, backend) -> list:
    """Usernames the OS keyring is asked to read from now on."""
    reads = []
    real_get = backend.get_password

    def recording_get(service, username):
        reads.append(username)
        return real_get(service, username)

    monkeypatch.setattr(backend, "get_password", recording_get)
    return reads


class TestSingleKeychainEntry:
    """Python-owned gaia.connections secrets are read through ONE Keychain entry."""

    def test_reads_touch_only_the_master_key(
        self, _autouse_in_memory_keyring, monkeypatch
    ):
        store.save_connection(
            provider="google",
            account_email="alice@example.com",
            refresh_token="rt-google",
            scopes=["openid"],
            client_id_hash="h1",
        )
        store.save_connection(
            provider="microsoft",
            account_email="bob@example.com",
            refresh_token="R" * 3000,
            scopes=["Mail.Read"],
            client_id_hash="h2",
        )
        store.save_provider_credentials(
            "google", client_id="cid-1", client_secret="csec-1"
        )
        store.save_secret("fireworks_api_key", "fw-key-123")
        store.save_secret("gateway:amd", "gw-tok-456")
        slack_credentials.save("xoxb-bot-tok", "xapp-app-tok")
        discord_credentials.save("discord-bot-tok")
        browser_key = browser_session._load_key()

        # Every stored secret round-trips through its normal API, and the only
        # OS keyring item read is the vault master key.
        reads = _record_reads(monkeypatch, _autouse_in_memory_keyring)
        assert (
            store.load_connection("google", current_client_id_hash="h1")[
                "refresh_token"
            ]
            == "rt-google"
        )
        assert (
            store.load_connection("microsoft", current_client_id_hash="h2")[
                "refresh_token"
            ]
            == "R" * 3000
        )
        assert store.peek_provider_credentials("google")["client_secret"] == "csec-1"
        assert store.peek_secret("fireworks_api_key") == "fw-key-123"
        assert store.peek_secret("gateway:amd") == "gw-tok-456"
        assert slack_credentials.load().bot_token == "xoxb-bot-tok"
        assert slack_credentials.load().app_token == "xapp-app-tok"
        assert discord_credentials.load() == "discord-bot-tok"
        assert browser_session._load_key() == browser_key
        assert set(reads) == {vault.MASTER_KEY_USERNAME}

        # On-disk vault is ciphertext, not plaintext, and 0600 on POSIX.
        raw_disk = vault.vault_path().read_bytes()
        assert raw_disk.startswith(vault.VAULT_MAGIC)
        for secret in (
            b"rt-google",
            b"csec-1",
            b"fw-key-123",
            b"gw-tok-456",
            b"xoxb-bot-tok",
            b"discord-bot-tok",
        ):
            assert secret not in raw_disk
        if os.name != "nt":
            mode = stat.S_IMODE(vault.vault_path().stat().st_mode)
            assert mode == 0o600

    def test_deleting_last_entry_removes_vault_and_master_key(
        self, _autouse_in_memory_keyring
    ):
        store.save_secret("one", "val-1")
        store.save_secret("two", "val-2")
        assert vault.vault_path().exists()

        store.delete_secret("one")
        assert vault.vault_path().exists()
        assert (
            vault.SERVICE_NAME,
            vault.MASTER_KEY_USERNAME,
        ) in _autouse_in_memory_keyring._store
        assert store.peek_secret("two") == "val-2"

        store.delete_secret("two")
        assert not vault.vault_path().exists()
        assert _autouse_in_memory_keyring._store == {}


class TestEnvelopeContract:
    """Language-neutral AES-256-GCM envelope format and tamper checks (#1899)."""

    def test_deterministic_test_vector(self):
        key = bytes.fromhex(
            "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"
        )
        nonce = bytes.fromhex("a0a1a2a3a4a5a6a7a8a9aaab")
        entries = {"secret:demo": "hello-vault"}

        blob = vault._encrypt_envelope_with_nonce(key, entries, nonce)

        assert blob[:6] == b"GAIAV1"
        assert blob[6] == 1
        assert blob[7:19] == nonce
        assert blob.hex() == (
            "474149415631"
            "01"
            "a0a1a2a3a4a5a6a7a8a9aaab"
            "9d3a194331b96bda1147bda82509a5bd02c92d2af6d22f03be3404ee1ac719"
            "6eff00268ac356714073be72ea334bfe2515f07959e0e10d4697c7c859aa1499"
        )
        assert vault.decrypt_envelope(key, blob) == entries

    def test_public_encrypt_takes_no_nonce_and_never_repeats_one(self):
        import inspect

        assert list(inspect.signature(vault.encrypt_envelope).parameters) == [
            "key",
            "entries",
        ]
        with pytest.raises(TypeError):
            vault.encrypt_envelope(  # pylint: disable=unexpected-keyword-arg
                os.urandom(32), {"k": "v"}, nonce=bytes(12)
            )
        key = os.urandom(32)
        nonces = {vault.encrypt_envelope(key, {"k": "v"})[7:19] for _ in range(50)}
        assert len(nonces) == 50

    def test_tampered_ciphertext_fails_loudly(self):
        key = os.urandom(32)
        blob = bytearray(vault.encrypt_envelope(key, {"k": "v"}))
        blob[-1] ^= 0xFF
        with pytest.raises(ConnectorsError, match="Could not decrypt"):
            vault.decrypt_envelope(key, bytes(blob))

    def test_wrong_key_fails_loudly(self):
        blob = vault.encrypt_envelope(os.urandom(32), {"k": "v"})
        with pytest.raises(ConnectorsError, match="Could not decrypt"):
            vault.decrypt_envelope(os.urandom(32), blob)

    def test_truncated_or_bad_magic_fails_loudly(self):
        key = os.urandom(32)
        with pytest.raises(ConnectorsError, match="corrupt or truncated"):
            vault.decrypt_envelope(key, b"short")
        with pytest.raises(ConnectorsError, match="corrupt or truncated"):
            vault.decrypt_envelope(key, b"BADMGC" + b"\x01" + b"\x00" * 40)

    def test_unsupported_version_fails_loudly(self):
        key = os.urandom(32)
        blob = bytearray(vault.encrypt_envelope(key, {"k": "v"}))
        blob[6] = 99
        with pytest.raises(
            ConnectorsError, match="Unsupported credential vault version"
        ):
            vault.decrypt_envelope(key, bytes(blob))

    def test_missing_master_key_with_existing_vault_fails_loudly(
        self, _autouse_in_memory_keyring
    ):
        store.save_secret("demo", "value")
        _autouse_in_memory_keyring._store.clear()
        with pytest.raises(ConnectorsError, match="master key.*missing"):
            store.peek_secret("demo")


class TestLazyLegacyMigration:
    """Per-slot Keychain items are copied into vault.enc on first read and kept
    for one release, so a downgrade or an older agent build still finds them."""

    def test_plain_legacy_slot_is_copied_and_kept(
        self, _autouse_in_memory_keyring, monkeypatch
    ):
        username = store._secret_username("legacy_key")
        keyring.set_password(vault.SERVICE_NAME, username, "legacy-secret-val")

        assert store.peek_secret("legacy_key") == "legacy-secret-val"
        assert vault.vault_path().exists()
        assert keyring.get_password(vault.SERVICE_NAME, username) == "legacy-secret-val"
        # Second read comes straight from vault.enc, not the legacy item.
        reads = _record_reads(monkeypatch, _autouse_in_memory_keyring)
        assert store.peek_secret("legacy_key") == "legacy-secret-val"
        assert username not in reads

    def test_writes_keep_the_legacy_copy_current_for_a_downgrade(
        self, _autouse_in_memory_keyring
    ):
        username = store._secret_username("rotating")
        keyring.set_password(vault.SERVICE_NAME, username, "v1")
        assert store.peek_secret("rotating") == "v1"

        store.save_secret("rotating", "v2")
        # An older build reads the per-slot item directly: never stale.
        assert store._kr_legacy_get(username) == "v2"
        assert store.peek_secret("rotating") == "v2"

        big = "B" * (store._CHUNK_CHARS * 2 + 7)
        store.save_secret("rotating", big)
        assert store._kr_legacy_get(username) == big
        store.save_secret("rotating", "short")
        assert store._kr_legacy_get(username) == "short"
        assert (
            keyring.get_password(vault.SERVICE_NAME, store._chunk_username(username, 0))
            is None
        )

        store.delete_secret("rotating")
        assert store._kr_legacy_get(username) is None
        assert store.peek_secret("rotating") is None

    def test_a_failed_legacy_mirror_write_fails_a_user_save(
        self, _autouse_in_memory_keyring, monkeypatch
    ):
        username = store._secret_username("mirrored")
        keyring.set_password(vault.SERVICE_NAME, username, "old")
        assert store.peek_secret("mirrored") == "old"

        def refuse(_username, _value):
            raise ConnectorsError("Keyring set_password failed: denied")

        monkeypatch.setattr(store, "_kr_legacy_set", refuse)
        with pytest.raises(ConnectorsError, match="set_password failed.*save again"):
            store.save_secret("mirrored", "new")

        # The retry mirrors even though the vault already holds the value.
        monkeypatch.undo()
        store.save_secret("mirrored", "new")
        assert keyring.get_password(vault.SERVICE_NAME, username) == "new"

    def test_chunked_legacy_slot_is_copied_and_kept(self, _autouse_in_memory_keyring):
        import json

        big_token = "M" * (store._CHUNK_CHARS * 2 + 50)
        payload = json.dumps(
            {
                "account_email": "a@example.com",
                "client_id_hash": "h",
                "connected_at": 1,
                "refresh_token": big_token,
                "scopes": ["s1"],
            }
        )
        username = store._connection_username("microsoft", "default")
        chunks = [
            payload[i : i + store._CHUNK_CHARS]
            for i in range(0, len(payload), store._CHUNK_CHARS)
        ]
        for idx, part in enumerate(chunks):
            keyring.set_password(
                vault.SERVICE_NAME, store._chunk_username(username, idx), part
            )
        keyring.set_password(
            vault.SERVICE_NAME,
            username,
            f"{store._CHUNK_SENTINEL}{len(chunks)}:{store._payload_crc(payload)}",
        )

        loaded = store.load_connection("microsoft", current_client_id_hash="h")
        assert loaded is not None
        assert loaded["refresh_token"] == big_token
        assert username in vault._read_vault_entries(
            vault.vault_path(), for_delete=False
        )
        # The chunked legacy copy is left intact for an older build.
        assert store._kr_legacy_get(username) == payload

    def test_legacy_browser_session_key_is_copied_without_breaking_sessions(
        self, _autouse_in_memory_keyring, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(browser_session, "sessions_dir", lambda: tmp_path)
        legacy_key = os.urandom(32)
        keyring.set_password(
            browser_session.KEYRING_SERVICE,
            browser_session.KEYRING_USERNAME,
            legacy_key.hex(),
        )

        assert browser_session._load_key() == legacy_key
        assert vault.get(browser_session.KEYRING_USERNAME) == legacy_key.hex()
        assert (
            keyring.get_password(
                browser_session.KEYRING_SERVICE, browser_session.KEYRING_USERNAME
            )
            == legacy_key.hex()
        )

    def test_null_keyring_save_fails_loudly_without_orphan_vault(self, monkeypatch):
        monkeypatch.setattr(keyring.core, "_keyring_backend", NullKeyring())
        with pytest.raises(ConnectorsError, match="null backend"):
            store.save_secret("discarded", "val")
        assert not vault.vault_path().exists()
        assert store.peek_secret("discarded") is None

    def test_null_keyring_fails_every_vault_backed_save(self, monkeypatch):
        monkeypatch.setattr(keyring.core, "_keyring_backend", NullKeyring())
        with pytest.raises(ConnectorsError, match="null backend"):
            vault.put("secret:any", "val")
        with pytest.raises(ConnectorsError, match="null backend"):
            slack_credentials.save("xoxb-bot", "xapp-app")
        with pytest.raises(discord_credentials.DiscordCredentialsError):
            discord_credentials.save("discord-bot-tok")
        assert not vault.vault_path().exists()


class TestLegacyMirrorScope:
    """Only slots migrated from a legacy item are mirrored, and only on a
    user save whose value changed, so unsigned macOS builds do not raise a
    Keychain prompt on every background token refresh."""

    def _migrate_ms_connection(self, token: str) -> str:
        import json

        username = store._connection_username("microsoft", "default")
        payload = json.dumps(
            {
                "account_email": "a@example.com",
                "client_id_hash": "h",
                "connected_at": 1,
                "refresh_token": token,
                "scopes": ["Mail.Read"],
            },
            sort_keys=True,
        )
        keyring.set_password(vault.SERVICE_NAME, username, payload)
        assert store.load_connection("microsoft", current_client_id_hash="h")
        return username

    def _save_ms(self, token: str, **kwargs) -> None:
        store.save_connection(
            provider="microsoft",
            account_email="a@example.com",
            refresh_token=token,
            scopes=["Mail.Read"],
            client_id_hash="h",
            connected_at=1,
            **kwargs,
        )

    def test_fresh_install_creates_no_legacy_items(
        self, _autouse_in_memory_keyring, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(browser_session, "sessions_dir", lambda: tmp_path)
        self._save_ms("rt-1")
        self._save_ms("rt-2")
        self._save_ms("rt-3", background_refresh=True)
        store.save_provider_credentials("google", client_id="cid", client_secret="s")
        store.save_secret("fireworks_api_key", "fw-1")
        store.save_secret("fireworks_api_key", "fw-2")
        slack_credentials.save("xoxb-bot", "xapp-app")
        discord_credentials.save("discord-bot")
        browser_session._load_key()

        assert set(_autouse_in_memory_keyring._store) == {
            (vault.SERVICE_NAME, vault.MASTER_KEY_USERNAME)
        }

    def test_migrated_slot_is_mirrored_on_a_user_save(self, _autouse_in_memory_keyring):
        username = self._migrate_ms_connection("legacy-rt")
        self._save_ms("signed-in-rt")
        assert '"signed-in-rt"' in store._kr_legacy_get(username)

    def test_token_refresh_does_not_touch_the_keyring(
        self, _autouse_in_memory_keyring, monkeypatch
    ):
        username = self._migrate_ms_connection("legacy-rt")
        writes = _record_writes(monkeypatch, _autouse_in_memory_keyring)
        self._save_ms("rotated-rt", background_refresh=True)

        assert writes == []
        assert '"legacy-rt"' in store._kr_legacy_get(username)
        loaded = store.load_connection("microsoft", current_client_id_hash="h")
        assert loaded["refresh_token"] == "rotated-rt"

    def test_unchanged_value_does_not_write_the_legacy_item(
        self, _autouse_in_memory_keyring, monkeypatch
    ):
        username = store._secret_username("api_key")
        keyring.set_password(vault.SERVICE_NAME, username, "same")
        assert store.peek_secret("api_key") == "same"

        writes = _record_writes(monkeypatch, _autouse_in_memory_keyring)
        store.save_secret("api_key", "same")
        assert writes == []

        store.save_secret("api_key", "changed")
        assert ("set", username) in writes
        writes.clear()
        store.save_secret("api_key", "changed")
        assert writes == []

    def test_background_mirror_failure_warns_and_keeps_the_vault_update(
        self, _autouse_in_memory_keyring, monkeypatch, caplog
    ):
        username = store._secret_username("bg")
        keyring.set_password(vault.SERVICE_NAME, username, "old-value")
        assert store.peek_secret("bg") == "old-value"

        def refuse(_username, value):
            raise ConnectorsError(f"backend-detail-text {value}")

        monkeypatch.setattr(store, "_kr_legacy_set", refuse)
        with caplog.at_level("WARNING", logger="gaia.connectors.vault"):
            vault.put(username, "new-value", mirror=vault.MIRROR_BACKGROUND)

        assert store.peek_secret("bg") == "new-value"
        assert "could not update" in caplog.text
        assert "ConnectorsError" in caplog.text
        # Neither the secret nor the exception message reaches the log.
        assert "new-value" not in caplog.text
        assert "old-value" not in caplog.text
        assert "backend-detail-text" not in caplog.text
        assert keyring.get_password(vault.SERVICE_NAME, username) == "old-value"

        # Still stale, so the next save retries even with the same value.
        monkeypatch.undo()
        vault.put(username, "new-value", mirror=vault.MIRROR_BACKGROUND)
        assert keyring.get_password(vault.SERVICE_NAME, username) == "new-value"

    def test_vault_stores_no_digest_of_a_secret(self, _autouse_in_memory_keyring):
        username = store._secret_username("flag")
        keyring.set_password(vault.SERVICE_NAME, username, "legacy-val")
        assert store.peek_secret("flag") == "legacy-val"
        store.save_secret("flag", "next-val")

        key = bytes.fromhex(
            keyring.get_password(vault.SERVICE_NAME, vault.MASTER_KEY_USERNAME)
        )
        _, legacy_mirror = vault._decrypt_payload(key, vault.vault_path().read_bytes())
        assert legacy_mirror == {username: "synced"}

    def test_refresh_then_unchanged_user_save_brings_legacy_current(
        self, _autouse_in_memory_keyring
    ):
        username = self._migrate_ms_connection("legacy-rt")
        self._save_ms("rotated-rt", background_refresh=True)
        assert '"legacy-rt"' in store._kr_legacy_get(username)
        # Same value as the vault, but the legacy item is behind it.
        self._save_ms("rotated-rt")
        assert '"rotated-rt"' in store._kr_legacy_get(username)

    def test_delete_forgets_the_migration_so_a_re_save_creates_nothing(
        self, _autouse_in_memory_keyring
    ):
        username = store._secret_username("gone")
        keyring.set_password(vault.SERVICE_NAME, username, "old")
        store.save_secret("keep", "k")
        assert store.peek_secret("gone") == "old"

        store.delete_secret("gone")
        store.save_secret("gone", "again")
        assert keyring.get_password(vault.SERVICE_NAME, username) is None

    def test_unknown_mirror_mode_is_rejected(self, _autouse_in_memory_keyring):
        with pytest.raises(ConnectorsError, match="mirror mode"):
            vault.put("secret:x", "v", mirror="sometimes")
        assert not vault.vault_path().exists()


class TestConcurrency:
    """Concurrent writers racing on first mint and updates keep all entries."""

    def test_concurrent_first_writes_preserve_every_slot(
        self, _autouse_in_memory_keyring
    ):
        n_threads = 12
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker(idx: int) -> None:
            try:
                barrier.wait()
                store.save_secret(f"slot-{idx}", f"val-{idx}")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert errors == []
        assert (
            vault.SERVICE_NAME,
            vault.MASTER_KEY_USERNAME,
        ) in _autouse_in_memory_keyring._store
        entries = vault._read_vault_entries(vault.vault_path(), for_delete=False)
        assert len(entries) == n_threads
        for i in range(n_threads):
            assert store.peek_secret(f"slot-{i}") == f"val-{i}"

    def test_put_after_concurrent_delete_of_last_slot_mints_fresh_key(
        self, _autouse_in_memory_keyring, monkeypatch
    ):
        # Seed a vault so put() pre-reads K1 outside the lock, then simulate
        # another process deleting the last slot (unlinking vault.enc and
        # removing K1) before put() enters _vault_locked().
        store.save_secret("first", "v1")
        real_lock = vault._vault_locked
        deleted = False

        def delete_before_lock():
            nonlocal deleted
            if not deleted:
                deleted = True
                vault.vault_path().unlink()
                _autouse_in_memory_keyring._store.pop(
                    (vault.SERVICE_NAME, vault.MASTER_KEY_USERNAME), None
                )
            return real_lock()

        monkeypatch.setattr(vault, "_vault_locked", delete_before_lock)
        store.save_secret("second", "v2")

        monkeypatch.undo()
        assert (
            vault.SERVICE_NAME,
            vault.MASTER_KEY_USERNAME,
        ) in _autouse_in_memory_keyring._store
        assert store.peek_secret("second") == "v2"

    def test_lazy_migration_does_not_overwrite_newer_value(
        self, _autouse_in_memory_keyring, monkeypatch
    ):
        # A slow legacy Keychain read must not overwrite a fresher token saved
        # into vault.enc by another process while the prompt was open.
        slot = store._secret_username("rotated")
        keyring.set_password(vault.SERVICE_NAME, slot, "stale-legacy-token")

        real_read_legacy = vault._read_legacy_slot

        def interleave_newer_put(username, *, for_delete, keyring_mod=None):
            value = real_read_legacy(
                username, for_delete=for_delete, keyring_mod=keyring_mod
            )
            if username == slot and value is not None:
                vault.put(slot, "fresh-rotated-token")
            return value

        monkeypatch.setattr(vault, "_read_legacy_slot", interleave_newer_put)
        assert store.peek_secret("rotated") == "fresh-rotated-token"

        monkeypatch.undo()
        assert store.peek_secret("rotated") == "fresh-rotated-token"
        # The slot is still recorded as migrated, so the next user save
        # brings the legacy copy up to date for a downgrade.
        store.save_secret("rotated", "fresh-rotated-token")
        assert keyring.get_password(vault.SERVICE_NAME, slot) == "fresh-rotated-token"

    def test_read_recovers_from_concurrent_vault_remint(
        self, _autouse_in_memory_keyring, monkeypatch
    ):
        # If the vault is deleted and re-minted under a new key between the
        # pre-lock key read and the locked file read, get() retries once with
        # the fresh key instead of raising a destructive corruption error.
        store.save_secret("demo", "old-val")
        real_lock = vault._vault_locked
        reminted = False

        def remint_before_lock():
            nonlocal reminted
            if not reminted:
                reminted = True
                new_key = os.urandom(32)
                _autouse_in_memory_keyring._store[
                    (vault.SERVICE_NAME, vault.MASTER_KEY_USERNAME)
                ] = new_key.hex()
                vault.vault_path().write_bytes(
                    vault.encrypt_envelope(
                        new_key, {store._secret_username("demo"): "new-val"}
                    )
                )
            return real_lock()

        monkeypatch.setattr(vault, "_vault_locked", remint_before_lock)
        assert store.peek_secret("demo") == "new-val"
