# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Single-master-key encrypted vault for ``gaia.connections`` secrets.

macOS ties Keychain "Always Allow" approvals on ad-hoc builds to the asking
binary's exact hash. Storing each OAuth token, API key, messaging token, and
browser key in its own Keychain item meant every unsigned release-candidate
update opened a stack of password dialogs.

This module keeps one 32-byte AES-256-GCM master key in the OS credential
store (service ``gaia.connections``, username ``vault-master-key``) and stores
all Python-owned secret slots inside one ``0600`` encrypted file at
``~/.gaia/connectors/vault.enc`` (or ``$GAIA_VAULT_FILE``). Existing legacy
Keychain items are migrated lazily on first read and removed from the OS
Keychain once persisted in the vault.

MCP server slots (``mcp-*``) remain directly in the OS Keychain because the
C++ runtime (``cpp/src/keyring.cpp``) resolves ``$keyring`` references there.

Envelope wire format (version 1, language-neutral for C++ portability):

    bytes[0:6]   = b"GAIAV1"                       (magic)
    bytes[6:7]   = b"\\x01"                         (format version)
    bytes[7:19]  = 12-byte random AES-GCM nonce
    bytes[19:]   = AES-256-GCM ciphertext + 16-byte tag
    AAD          = b"gaia.connections:vault:v1"
    plaintext    = UTF-8 JSON ``{"entries": {"<slot>": "<value>"}, "v": 1}``
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, Optional

from gaia import config as gaia_config
from gaia.connectors import _refresh_lock
from gaia.connectors.errors import ConnectorsError
from gaia.daemon.lock import try_lock, unlock

logger = logging.getLogger(__name__)

SERVICE_NAME = "gaia.connections"
MASTER_KEY_USERNAME = "vault-master-key"
VAULT_FILE_ENV_VAR = "GAIA_VAULT_FILE"

VAULT_MAGIC = b"GAIAV1"
VAULT_VERSION = 1
VAULT_AAD = b"gaia.connections:vault:v1"

_KEY_BYTES = 32
_NONCE_BYTES = 12
_TAG_BYTES = 16
_HEADER_BYTES = len(VAULT_MAGIC) + 1
_MIN_ENVELOPE_BYTES = _HEADER_BYTES + _NONCE_BYTES + _TAG_BYTES
_LOCK_POLL_SECONDS = 0.02

_thread_lock = threading.Lock()


def vault_path() -> Path:
    """Path to the encrypted vault file.

    Scoped per OS user alongside ``accounts.json`` and the refresh locks,
    matching the OS credential store's scope. ``GAIA_VAULT_FILE`` overrides it
    for tests and explicit sandboxes.
    """
    override = (os.environ.get(VAULT_FILE_ENV_VAR) or "").strip()
    if override:
        return Path(os.path.expandvars(os.path.expanduser(override)))
    return gaia_config.default_gaia_home() / "connectors" / "vault.enc"


def lock_path() -> Path:
    """Cross-process advisory lock file guarding ``vault.enc``."""
    return vault_path().with_name("vault.lock")


def _aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as e:
        raise ConnectorsError(
            "Credential vault encryption requires the 'cryptography' package, "
            "which ships with GAIA. Reinstall with: pip install amd-gaia"
        ) from e
    return AESGCM


def encrypt_envelope(
    key: bytes, entries: Dict[str, str], *, nonce: Optional[bytes] = None
) -> bytes:
    """Serialize and encrypt ``entries`` into the version-1 vault envelope."""
    if len(key) != _KEY_BYTES:
        raise ConnectorsError(
            f"Vault master key must be {_KEY_BYTES} bytes, got {len(key)}."
        )
    iv = os.urandom(_NONCE_BYTES) if nonce is None else nonce
    if len(iv) != _NONCE_BYTES:
        raise ConnectorsError(
            f"Vault nonce must be {_NONCE_BYTES} bytes, got {len(iv)}."
        )
    plaintext = json.dumps(
        {"v": VAULT_VERSION, "entries": entries},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    ciphertext = _aesgcm()(key).encrypt(iv, plaintext, VAULT_AAD)
    return VAULT_MAGIC + bytes([VAULT_VERSION]) + iv + ciphertext


def decrypt_envelope(key: bytes, blob: bytes) -> Dict[str, str]:
    """Decrypt and validate a version-1 vault envelope into ``{slot: value}``."""
    if len(key) != _KEY_BYTES:
        raise ConnectorsError(
            f"Vault master key must be {_KEY_BYTES} bytes, got {len(key)}."
        )
    if len(blob) < _MIN_ENVELOPE_BYTES or not blob.startswith(VAULT_MAGIC):
        raise ConnectorsError(
            f"The encrypted credential vault at {vault_path()} is corrupt or "
            "truncated. Remove the file and reconnect your accounts."
        )
    version = blob[len(VAULT_MAGIC)]
    if version != VAULT_VERSION:
        raise ConnectorsError(
            f"Unsupported credential vault version {version} at {vault_path()}. "
            "Update GAIA to a version that supports this vault format."
        )
    nonce = blob[_HEADER_BYTES : _HEADER_BYTES + _NONCE_BYTES]
    ciphertext = blob[_HEADER_BYTES + _NONCE_BYTES :]
    try:
        plaintext = _aesgcm()(key).decrypt(nonce, ciphertext, VAULT_AAD)
    except Exception as e:  # noqa: BLE001 - re-raised with recovery steps
        raise ConnectorsError(
            f"Could not decrypt the credential vault at {vault_path()}: the "
            "master key changed or the file was tampered with. Delete "
            f"`{MASTER_KEY_USERNAME}` from your OS credential store and "
            f"remove {vault_path()}, then reconnect your accounts."
        ) from e
    try:
        parsed = json.loads(plaintext.decode("utf-8"))
        raw_entries = parsed.get("entries") if isinstance(parsed, dict) else None
        if parsed.get("v") != VAULT_VERSION or not isinstance(raw_entries, dict):
            raise ValueError("unexpected vault payload shape")
        if not all(
            isinstance(k, str) and isinstance(v, str) for k, v in raw_entries.items()
        ):
            raise ValueError("vault entries must map strings to strings")
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, ValueError) as e:
        raise ConnectorsError(
            f"The credential vault payload at {vault_path()} is malformed ({e}). "
            "Remove the file and reconnect your accounts."
        ) from e
    return dict(raw_entries)


def _parse_master_key(raw: str) -> bytes:
    try:
        key = bytes.fromhex(raw.strip())
    except ValueError as e:
        raise ConnectorsError(
            f"The stored vault master key `{MASTER_KEY_USERNAME}` in service "
            f"`{SERVICE_NAME}` is corrupt. Delete it from your OS credential "
            "store and reconnect your accounts."
        ) from e
    if len(key) != _KEY_BYTES:
        raise ConnectorsError(
            f"The stored vault master key `{MASTER_KEY_USERNAME}` in service "
            f"`{SERVICE_NAME}` has the wrong length. Delete it from your OS "
            "credential store and reconnect your accounts."
        )
    return key


def _ensure_private_dir(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            directory.chmod(0o700)
        except OSError as e:
            logger.debug("Could not tighten vault dir permissions: %s", e)


def _write_private(path: Path, data: bytes) -> None:
    """Atomic 0600 write with fsync so a crash never leaves a torn vault."""
    _ensure_private_dir(path.parent)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".vault-tmp-")
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _lock_timeout_error(path: Path) -> ConnectorsError:
    return ConnectorsError(
        f"The credential vault at {vault_path()} has been locked by another "
        f"GAIA process for over {_refresh_lock.LOCK_TIMEOUT_SECONDS:.0f}s "
        f"(lock file {path}). Retry; if it keeps happening, look for a stuck "
        "`gaia daemon` or Agent UI process."
    )


@contextmanager
def _vault_locked() -> Iterator[None]:
    """Serialize vault read-modify-write across threads and GAIA processes.

    Lock ordering: always acquired INSIDE any provider/account/index lock
    (``_refresh_lock``, ``accounts._index_locked``), never before them.
    Both the intra-process thread lock and the OS file lock share one bounded
    deadline so callers never hang indefinitely.
    """
    path = lock_path()
    _ensure_private_dir(path.parent)
    deadline = time.monotonic() + _refresh_lock.LOCK_TIMEOUT_SECONDS
    if not _thread_lock.acquire(timeout=_refresh_lock.LOCK_TIMEOUT_SECONDS):
        raise _lock_timeout_error(path)
    try:
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            while not try_lock(fd):
                if time.monotonic() >= deadline:
                    raise _lock_timeout_error(path)
                time.sleep(_LOCK_POLL_SECONDS)
            try:
                yield
            finally:
                unlock(fd)
        finally:
            os.close(fd)
    finally:
        _thread_lock.release()


def _raw_get(username: str, *, for_delete: bool, keyring_mod=None) -> Optional[str]:
    if keyring_mod is not None:
        return keyring_mod.read(SERVICE_NAME, username)
    from gaia.connectors import store

    return store._kr_raw_get(username, for_delete=for_delete)


def _raw_set(username: str, value: str, *, keyring_mod=None) -> None:
    if keyring_mod is not None:
        keyring_mod.write(SERVICE_NAME, username, value)
        return
    from gaia.connectors import store

    store._kr_raw_set(username, value)


def _raw_delete(username: str, *, keyring_mod=None) -> None:
    if keyring_mod is not None:
        try:
            keyring_mod.delete(SERVICE_NAME, username)
        except keyring_mod.errors.PasswordDeleteError:
            pass
        return
    from gaia.connectors import store

    store._kr_raw_delete(username)


def _sweep_legacy_slot_no_read(username: str, *, keyring_mod=None) -> None:
    """Remove any legacy Keychain entry and chunk slots without calling get_password.

    Calling get_password on an old macOS Keychain item during a write would
    trigger an extra password dialog. Deleting directly avoids reading the
    secret and stops on the first absent chunk index.
    """
    from gaia.connectors import store

    if keyring_mod is not None:
        try:
            keyring_mod.delete(SERVICE_NAME, username)
        except keyring_mod.errors.PasswordDeleteError:
            pass
        return

    from gaia.connectors import keyring_access

    try:
        keyring_access.delete(SERVICE_NAME, username)
    except keyring_access.errors.PasswordDeleteError:
        pass
    except keyring_access.errors.KeyringError as e:
        logger.debug("Could not sweep legacy slot %s: %s", username, e)
        return

    idx = 0
    while True:
        chunk_name = store._chunk_username(username, idx)
        try:
            keyring_access.delete(SERVICE_NAME, chunk_name)
        except keyring_access.errors.PasswordDeleteError:
            break
        except keyring_access.errors.KeyringError as e:
            logger.debug("Could not sweep legacy chunk %s: %s", chunk_name, e)
            break
        idx += 1


def _read_legacy_slot(
    username: str, *, for_delete: bool, keyring_mod=None
) -> Optional[str]:
    if keyring_mod is not None:
        return _raw_get(username, for_delete=for_delete, keyring_mod=keyring_mod)
    from gaia.connectors import store

    return store._kr_legacy_get(username, for_delete=for_delete)


def _delete_legacy_slot(username: str, *, keyring_mod=None) -> None:
    if keyring_mod is not None:
        _raw_delete(username, keyring_mod=keyring_mod)
        return
    from gaia.connectors import store

    store._kr_legacy_delete(username)


def _load_or_mint_master_key(
    *, allow_mint: bool, preloaded_key: Optional[bytes] = None, keyring_mod=None
) -> Optional[bytes]:
    """Return the master key from Keychain, minting and verifying it if allowed.

    Returns ``None`` only when ``allow_mint`` is True and the active backend
    silently discards writes (such as ``NullKeyring``), or when ``allow_mint``
    is False and no key is stored.
    """
    if preloaded_key is not None:
        return preloaded_key
    raw = _raw_get(MASTER_KEY_USERNAME, for_delete=False, keyring_mod=keyring_mod)
    if raw:
        return _parse_master_key(raw)
    if not allow_mint:
        return None
    key = os.urandom(_KEY_BYTES)
    key_hex = key.hex()
    _raw_set(MASTER_KEY_USERNAME, key_hex, keyring_mod=keyring_mod)
    readback = _raw_get(MASTER_KEY_USERNAME, for_delete=False, keyring_mod=keyring_mod)
    if readback is None:
        # A null/no-op backend silently discarded the write; do not create a
        # vault file whose master key cannot be read back.
        return None
    if readback.strip() != key_hex:
        raise ConnectorsError(
            "The OS credential store returned a different value than the vault "
            "master key that was just written."
        )
    return key


def _missing_master_key_error(path: Path) -> ConnectorsError:
    return ConnectorsError(
        f"The credential vault at {path} exists, but its master key "
        f"`{MASTER_KEY_USERNAME}` is missing from your OS credential store. "
        f"Remove {path} and reconnect your accounts."
    )


def _write_entries_locked(
    path: Path,
    username: str,
    value: str,
    *,
    preloaded_key: Optional[bytes] = None,
    keyring_mod=None,
) -> bool:
    """Persist ``username -> value`` into ``path`` while holding ``_vault_locked``.

    Returns True if written to ``vault.enc``, False if the keyring backend
    silently discarded the newly minted master key (``NullKeyring``).
    """
    if path.exists():
        key = _load_or_mint_master_key(
            allow_mint=False, preloaded_key=preloaded_key, keyring_mod=keyring_mod
        )
        if key is None:
            raise _missing_master_key_error(path)
        entries = decrypt_envelope(key, path.read_bytes())
    else:
        key = _load_or_mint_master_key(
            allow_mint=True, preloaded_key=preloaded_key, keyring_mod=keyring_mod
        )
        if key is None:
            return False
        entries = {}

    entries[username] = value
    _write_private(path, encrypt_envelope(key, entries))
    return True


def _read_vault_entries(
    path: Path, *, for_delete: bool, keyring_mod=None
) -> Optional[Dict[str, str]]:
    """Read and decrypt ``path`` without holding ``_vault_locked`` during Keychain I/O.

    Returns ``None`` when ``path`` does not exist (or when ``for_delete`` sees
    no master key so legacy Keychain cleanup can still proceed).
    """
    if not path.exists():
        return None
    # Fetch the master key outside the vault lock so an interactive OS Keychain
    # prompt never stalls other threads or processes waiting on vault.lock.
    raw_key = _raw_get(
        MASTER_KEY_USERNAME, for_delete=for_delete, keyring_mod=keyring_mod
    )
    with _vault_locked():
        if not path.exists():
            return None
        if not raw_key:
            if for_delete:
                return None
            raise _missing_master_key_error(path)
        blob = path.read_bytes()
    return decrypt_envelope(_parse_master_key(raw_key), blob)


def put(username: str, value: str, *, keyring_mod=None) -> None:
    """Store ``value`` under ``username`` in the encrypted vault."""
    if not username or username == MASTER_KEY_USERNAME:
        raise ConnectorsError(f"Invalid credential slot name: {username!r}")
    from gaia.connectors import store

    if keyring_mod is None:
        store.verify_keyring_backend()
        store.retry_keyring()

    path = vault_path()
    # Steady-state writes read the existing master key outside the vault lock so
    # an interactive OS Keychain dialog cannot stall concurrent processes.
    preloaded_key: Optional[bytes] = None
    if path.exists():
        raw_key = _raw_get(
            MASTER_KEY_USERNAME, for_delete=False, keyring_mod=keyring_mod
        )
        if raw_key:
            preloaded_key = _parse_master_key(raw_key)

    with _vault_locked():
        written = _write_entries_locked(
            path,
            username,
            value,
            preloaded_key=preloaded_key,
            keyring_mod=keyring_mod,
        )
        if written:
            _sweep_legacy_slot_no_read(username, keyring_mod=keyring_mod)


def get(
    username: str,
    *,
    migrate: bool = True,
    keyring_mod=None,
) -> Optional[str]:
    """Read ``username`` from the encrypted vault, falling back to legacy Keychain.

    When a legacy Keychain entry is read and ``migrate`` is True, it is copied
    into ``vault.enc`` and the legacy Keychain slot is removed so subsequent
    macOS updates do not re-prompt for it.
    """
    from gaia.connectors import store

    if keyring_mod is None:
        store.verify_keyring_backend()

    path = vault_path()
    entries = _read_vault_entries(path, for_delete=False, keyring_mod=keyring_mod)
    if entries is not None and username in entries:
        return entries[username]

    legacy = _read_legacy_slot(username, for_delete=False, keyring_mod=keyring_mod)
    if legacy is None:
        return None

    if migrate:
        try:
            with _vault_locked():
                written = _write_entries_locked(
                    path, username, legacy, keyring_mod=keyring_mod
                )
        except (OSError, store.KeyringRefused) as e:
            logger.warning(
                "Could not write vault while migrating %s (keeping legacy "
                "keyring entry): %s",
                username,
                e,
            )
            return legacy
        if written:
            try:
                _delete_legacy_slot(username, keyring_mod=keyring_mod)
            except (OSError, ConnectorsError) as e:
                logger.warning(
                    "Migrated %s into vault but could not remove legacy "
                    "keyring entry: %s",
                    username,
                    e,
                )
    return legacy


def _read_for_delete(username: str, *, keyring_mod=None) -> Optional[str]:
    """Read a slot on a delete path (allowed on a weak backend).

    Private helper: callers outside delete paths must use :func:`get`.
    """
    from gaia.connectors import keyring_access

    if keyring_mod is None:
        keyring_access.check_delete()

    path = vault_path()
    entries = _read_vault_entries(path, for_delete=True, keyring_mod=keyring_mod)
    if entries is not None and username in entries:
        return entries[username]
    return _read_legacy_slot(username, for_delete=True, keyring_mod=keyring_mod)


def delete(username: str, *, keyring_mod=None) -> None:
    """Remove ``username`` from the vault and any legacy Keychain slot. Idempotent."""
    from gaia.connectors import keyring_access, store

    if keyring_mod is None:
        keyring_access.check_delete()
        store.retry_keyring()

    path = vault_path()
    raw_key: Optional[str] = None
    if path.exists():
        raw_key = _raw_get(
            MASTER_KEY_USERNAME, for_delete=True, keyring_mod=keyring_mod
        )

    with _vault_locked():
        if path.exists():
            if not raw_key:
                raw_key = _raw_get(
                    MASTER_KEY_USERNAME, for_delete=True, keyring_mod=keyring_mod
                )
            if raw_key:
                key = _parse_master_key(raw_key)
                entries = decrypt_envelope(key, path.read_bytes())
                if username in entries:
                    del entries[username]
                    if entries:
                        _write_private(path, encrypt_envelope(key, entries))
                    else:
                        path.unlink()
                        _raw_delete(MASTER_KEY_USERNAME, keyring_mod=keyring_mod)

        _delete_legacy_slot(username, keyring_mod=keyring_mod)


def slot_in_use(username: str, *, keyring_mod=None) -> bool:
    """Whether ``username`` has a stored value in the vault or legacy Keychain."""
    return _read_for_delete(username, keyring_mod=keyring_mod) is not None
