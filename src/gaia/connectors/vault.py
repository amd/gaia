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
Keychain items are copied into the vault lazily on first read.

Transition release: legacy per-slot items are kept so a downgrade still finds
them. A user-initiated save also rewrites a slot's legacy item, but only when
that slot was migrated from one (recorded in the vault at migration time) and
the value changed since it was last mirrored. Background token refreshes update
the vault only, and a fresh install never gets legacy items. Reads always
prefer the vault. The next release stops mirroring and removes the legacy items.

MCP server slots (``mcp-*``) remain directly in the OS Keychain because the
C++ runtime (``cpp/src/keyring.cpp``) resolves ``$keyring`` references there.

Envelope wire format (version 1, language-neutral for C++ portability):

    bytes[0:6]   = b"GAIAV1"                       (magic)
    bytes[6:7]   = b"\\x01"                         (format version)
    bytes[7:19]  = 12-byte random AES-GCM nonce
    bytes[19:]   = AES-256-GCM ciphertext + 16-byte tag
    AAD          = b"gaia.connections:vault:v1"
    plaintext    = UTF-8 JSON ``{"entries": {"<slot>": "<value>"}, "v": 1}``,
                   plus ``"legacy_mirror": {"<slot>": "synced"|"stale"}`` for
                   slots migrated from a legacy item (whether that item still
                   holds the vault value)
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

MIRROR_USER = "user"
MIRROR_BACKGROUND = "background"
MIRROR_NEVER = "never"
_MIRROR_MODES = (MIRROR_USER, MIRROR_BACKGROUND, MIRROR_NEVER)
_SYNCED = "synced"
_STALE = "stale"

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


def encrypt_envelope(key: bytes, entries: Dict[str, str]) -> bytes:
    """Serialize and encrypt ``entries`` into the version-1 vault envelope.

    Always draws a fresh random nonce: reusing one under the same key breaks
    AES-GCM.
    """
    return _encrypt_envelope_with_nonce(key, entries, os.urandom(_NONCE_BYTES))


def _encrypt_payload(
    key: bytes, entries: Dict[str, str], legacy_mirror: Dict[str, str]
) -> bytes:
    return _encrypt_envelope_with_nonce(
        key, entries, os.urandom(_NONCE_BYTES), legacy_mirror=legacy_mirror
    )


def _encrypt_envelope_with_nonce(
    key: bytes,
    entries: Dict[str, str],
    iv: bytes,
    *,
    legacy_mirror: Optional[Dict[str, str]] = None,
) -> bytes:
    """Test-vector hook only; production code must call :func:`encrypt_envelope`."""
    if len(key) != _KEY_BYTES:
        raise ConnectorsError(
            f"Vault master key must be {_KEY_BYTES} bytes, got {len(key)}."
        )
    if len(iv) != _NONCE_BYTES:
        raise ConnectorsError(
            f"Vault nonce must be {_NONCE_BYTES} bytes, got {len(iv)}."
        )
    payload: dict = {"v": VAULT_VERSION, "entries": entries}
    if legacy_mirror:
        payload["legacy_mirror"] = legacy_mirror
    plaintext = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    ciphertext = _aesgcm()(key).encrypt(iv, plaintext, VAULT_AAD)
    return VAULT_MAGIC + bytes([VAULT_VERSION]) + iv + ciphertext


def decrypt_envelope(key: bytes, blob: bytes) -> Dict[str, str]:
    """Decrypt and validate a version-1 vault envelope into ``{slot: value}``."""
    return _decrypt_payload(key, blob)[0]


def _decrypt_payload(key: bytes, blob: bytes) -> tuple[Dict[str, str], Dict[str, str]]:
    """Decrypt into ``(entries, legacy_mirror)``."""
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
        raw_mirror = parsed.get("legacy_mirror", {})
        if not isinstance(raw_mirror, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in raw_mirror.items()
        ):
            raise ValueError("legacy_mirror must map strings to strings")
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, ValueError) as e:
        raise ConnectorsError(
            f"The credential vault payload at {vault_path()} is malformed ({e}). "
            "Remove the file and reconnect your accounts."
        ) from e
    return dict(raw_entries), dict(raw_mirror)


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


def _mirror_legacy_slot(username: str, value: str, *, keyring_mod=None) -> None:
    """Rewrite a migrated slot's legacy per-slot item (transition release).

    Writes only, never reads. :func:`put` decides whether to call it and how a
    failure is reported.
    """
    if keyring_mod is not None:
        keyring_mod.write(SERVICE_NAME, username, value)
        return
    from gaia.connectors import store

    store._kr_legacy_set(username, value)


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

    Returns ``None`` only when ``allow_mint`` is False and no key is stored.
    Raises when the active backend silently discards writes (``NullKeyring``).
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
        # Never create a vault file whose master key cannot be read back.
        raise _discarded_write_error()
    if readback.strip() != key_hex:
        raise ConnectorsError(
            "The OS credential store returned a different value than the vault "
            "master key that was just written."
        )
    return key


def _discarded_write_error() -> ConnectorsError:
    configured = os.environ.get("PYTHON_KEYRING_BACKEND") or "auto-detected"
    return ConnectorsError(
        "Could not save the secret: the OS credential store discarded the "
        f"credential vault's master key (keyring backend: {configured}). This "
        "happens with keyring's null backend, which stores nothing. Install a "
        "system credential store (gnome-keyring or kwallet on Linux; macOS "
        "Keychain and Windows Credential Locker are built in), or unset "
        "PYTHON_KEYRING_BACKEND, and restart GAIA. Where no credential store "
        "exists, supply the secret through its environment variable instead. "
        "See docs/security/connections.mdx."
    )


def _missing_master_key_error(path: Path) -> ConnectorsError:
    return ConnectorsError(
        f"The credential vault at {path} exists, but its master key "
        f"`{MASTER_KEY_USERNAME}` is missing from your OS credential store. "
        f"Remove {path} and reconnect your accounts."
    )


def _open_locked(
    path: Path, *, preloaded_key: Optional[bytes] = None, keyring_mod=None
) -> tuple[bytes, Dict[str, str], Dict[str, str]]:
    """Return ``(key, entries, legacy_mirror)`` for ``path``; caller holds the lock.

    Mints a fresh master key and starts empty when ``path`` does not exist.
    """
    if not path.exists():
        # Never reuse a preloaded key when the vault file is gone: another
        # process may have deleted both vault.enc and vault-master-key since
        # the pre-lock read.
        key = _load_or_mint_master_key(allow_mint=True, keyring_mod=keyring_mod)
        return key, {}, {}
    blob = path.read_bytes()
    key = _load_or_mint_master_key(
        allow_mint=False, preloaded_key=preloaded_key, keyring_mod=keyring_mod
    )
    if key is None:
        raise _missing_master_key_error(path)
    try:
        entries, legacy_mirror = _decrypt_payload(key, blob)
    except ConnectorsError:
        if preloaded_key is None:
            raise
        # Another process may have deleted and re-minted the vault under a
        # fresh master key after our pre-lock read; re-read Keychain once.
        fresh_key = _load_or_mint_master_key(allow_mint=False, keyring_mod=keyring_mod)
        if fresh_key is None or fresh_key == key:
            raise
        key = fresh_key
        entries, legacy_mirror = _decrypt_payload(key, blob)
    return key, entries, legacy_mirror


def _write_entries_locked(
    path: Path,
    username: str,
    value: str,
    *,
    preloaded_key: Optional[bytes] = None,
    only_if_absent: bool = False,
    migrated_from_legacy: bool = False,
    keyring_mod=None,
) -> tuple[bool, str, Optional[str]]:
    """Persist ``username -> value`` into ``path`` while holding ``_vault_locked``.

    Returns ``(written, effective_value, mirror_state)``. ``mirror_state`` is
    ``"synced"`` or ``"stale"`` for a slot migrated from a legacy item (whether
    that item still holds the vault value), else ``None``. When
    ``only_if_absent`` is True and ``username`` is already present, the
    existing value is kept (a slow legacy migration must not overwrite a newer
    token) and ``written`` is False.

    ``migrated_from_legacy`` marks ``value`` as just read from a legacy item,
    so later user saves keep that item current for a downgrade.
    """
    key, entries, legacy_mirror = _open_locked(
        path, preloaded_key=preloaded_key, keyring_mod=keyring_mod
    )
    if only_if_absent and username in entries:
        if migrated_from_legacy and username not in legacy_mirror:
            legacy_mirror[username] = _SYNCED if entries[username] == value else _STALE
            _write_private(path, _encrypt_payload(key, entries, legacy_mirror))
        return False, entries[username], legacy_mirror.get(username)
    if migrated_from_legacy and username not in legacy_mirror:
        legacy_mirror[username] = _SYNCED
    elif username in legacy_mirror and entries.get(username) != value:
        legacy_mirror[username] = _STALE
    entries[username] = value
    _write_private(path, _encrypt_payload(key, entries, legacy_mirror))
    return True, value, legacy_mirror.get(username)


def _record_mirrored_locked(
    path: Path,
    username: str,
    value: str,
    *,
    preloaded_key: Optional[bytes] = None,
    keyring_mod=None,
) -> None:
    """Record that the legacy item now holds ``value``, unless a newer write won."""
    if not path.exists():
        return
    key, entries, legacy_mirror = _open_locked(
        path, preloaded_key=preloaded_key, keyring_mod=keyring_mod
    )
    if username not in legacy_mirror or entries.get(username) != value:
        return
    legacy_mirror[username] = _SYNCED
    _write_private(path, _encrypt_payload(key, entries, legacy_mirror))


def _read_vault_entries(
    path: Path, *, for_delete: bool, keyring_mod=None
) -> Optional[Dict[str, str]]:
    """Read and decrypt ``path`` without holding ``_vault_locked`` during Keychain I/O.

    Returns ``None`` when ``path`` does not exist (or when ``for_delete`` sees
    no master key so legacy Keychain cleanup can still proceed). Retries once
    if another process deleted and re-minted the vault between the pre-lock key
    read and the locked file read.
    """
    if not path.exists():
        return None
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
    try:
        return decrypt_envelope(_parse_master_key(raw_key), blob)
    except ConnectorsError:
        fresh_raw = _raw_get(
            MASTER_KEY_USERNAME, for_delete=for_delete, keyring_mod=keyring_mod
        )
        if not fresh_raw or fresh_raw == raw_key:
            raise
        with _vault_locked():
            if not path.exists():
                return None
            fresh_blob = path.read_bytes()
        return decrypt_envelope(_parse_master_key(fresh_raw), fresh_blob)


def put(
    username: str, value: str, *, keyring_mod=None, mirror: str = MIRROR_USER
) -> None:
    """Store ``value`` under ``username`` in the encrypted vault.

    ``mirror`` says who is saving, which decides how a slot migrated from a
    legacy item keeps that item current during the transition release:

    - ``MIRROR_USER`` (sign-in, API-key save, messaging setup): rewrite it if
      it no longer matches the vault; a failure raises so the user can act and
      retry.
    - ``MIRROR_BACKGROUND``: rewrite it if it no longer matches the vault; a
      failure is logged as a warning and the save still succeeds.
    - ``MIRROR_NEVER`` (background token refresh): vault only.

    A slot never migrated from a legacy item is never mirrored, so a fresh
    install gets no legacy items.
    """
    if not username or username == MASTER_KEY_USERNAME:
        raise ConnectorsError(f"Invalid credential slot name: {username!r}")
    if mirror not in _MIRROR_MODES:
        raise ConnectorsError(
            f"Invalid legacy mirror mode {mirror!r}; expected one of {_MIRROR_MODES}."
        )
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
        _, _, mirror_state = _write_entries_locked(
            path,
            username,
            value,
            preloaded_key=preloaded_key,
            keyring_mod=keyring_mod,
        )
    if mirror == MIRROR_NEVER or mirror_state != _STALE:
        return
    try:
        _mirror_legacy_slot(username, value, keyring_mod=keyring_mod)
    except Exception as e:  # noqa: BLE001 - re-raised or logged per mode below
        if mirror == MIRROR_BACKGROUND:
            logger.warning(
                "Saved a secret to the credential vault, but could not update "
                "its older per-item entry kept for downgrades (%s); the next "
                "save retries it.",
                type(e).__name__,
            )
            return
        raise ConnectorsError(
            f"Saved {username!r} to the credential vault, but could not update "
            "the older per-item entry kept in the OS credential store so a "
            f"downgrade still works: {e}. Allow the credential-store prompt "
            "and save again. See docs/security/connections.mdx."
        ) from e
    try:
        with _vault_locked():
            _record_mirrored_locked(
                path,
                username,
                value,
                preloaded_key=preloaded_key,
                keyring_mod=keyring_mod,
            )
    except (ConnectorsError, OSError) as e:
        if mirror != MIRROR_BACKGROUND:
            raise
        # Left "stale", so the next save just rewrites the item again.
        logger.warning(
            "Could not record a legacy credential mirror (%s)",
            type(e).__name__,
        )


def get(
    username: str,
    *,
    migrate: bool = True,
    keyring_mod=None,
) -> Optional[str]:
    """Read ``username`` from the encrypted vault, falling back to legacy Keychain.

    When a legacy Keychain entry is read and ``migrate`` is True, it is copied
    into ``vault.enc`` only if ``username`` is still absent there, and the slot
    is recorded as migrated so later user saves keep the legacy item current.
    The legacy item is kept for this transition release so a downgrade still
    finds it; later reads come from the vault and never touch it.
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
        preloaded_key: Optional[bytes] = None
        if path.exists():
            raw_key = _raw_get(
                MASTER_KEY_USERNAME, for_delete=False, keyring_mod=keyring_mod
            )
            if raw_key:
                preloaded_key = _parse_master_key(raw_key)
        try:
            with _vault_locked():
                _, effective, _ = _write_entries_locked(
                    path,
                    username,
                    legacy,
                    preloaded_key=preloaded_key,
                    only_if_absent=True,
                    migrated_from_legacy=True,
                    keyring_mod=keyring_mod,
                )
        except (OSError, store.KeyringRefused) as e:
            logger.warning(
                "Could not write vault while migrating %s (keeping legacy "
                "keyring entry): %s",
                username,
                e,
            )
            return legacy
        return effective
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
                entries, legacy_mirror = _decrypt_payload(key, path.read_bytes())
                if username in entries or username in legacy_mirror:
                    entries.pop(username, None)
                    legacy_mirror.pop(username, None)
                    if entries:
                        _write_private(
                            path, _encrypt_payload(key, entries, legacy_mirror)
                        )
                    else:
                        path.unlink()
                        _raw_delete(MASTER_KEY_USERNAME, keyring_mod=keyring_mod)

    _sweep_legacy_slot_no_read(username, keyring_mod=keyring_mod)


def slot_in_use(username: str, *, keyring_mod=None) -> bool:
    """Whether ``username`` has a stored value in the vault or legacy Keychain."""
    return _read_for_delete(username, keyring_mod=keyring_mod) is not None
