# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Vault-backed persistent storage for OAuth connection records and secrets.

Single-master-key vault design:
    One 32-byte AES-256-GCM master key lives in the OS keyring
    (``gaia.connections`` / ``vault-master-key``) and unlocks
    ``~/.gaia/connectors/vault.enc``. Each ``(provider, slot)`` pair maps to
    one JSON blob entry inside the vault (``refresh_token``, ``account_email``,
    ``scopes``, ``connected_at``, ``client_id_hash``), persisted via atomic
    temp-file replace under a cross-process lock so a partial write cannot
    leave a fresh token with stale metadata. Legacy per-slot OS keyring entries
    are copied in lazily on first read and, for one transition release, kept
    and updated on every write so older builds still read current values.

Backend allowlist (plan amendment A4):
    Plaintext or weak file-backed keyring backends (e.g. ``keyrings.alt``'s
    ``PlaintextKeyring``, ``EncryptedKeyring``, ``Win32CryptoKeyring``) are
    explicitly refused BEFORE any write — including when one sits anywhere
    inside a chained keyring. Linux machines without
    SecretService produce an actionable error pointing at the runbook
    instead of silently writing tokens to disk in plaintext.

Eager ``client_id_hash`` tripwire (plan amendment from Iteration 1, AC10):
    Every ``load_connection`` compares the stored hash against the current
    one. A mismatch means the OAuth client was rotated (or the user moved
    their installation between machines with different env configurations);
    we clear the stored entry, emit ``connection.revoked``, and return
    ``None`` so the caller raises ``REAUTH_REQUIRED``.

All log statements in this module emit only metadata (provider IDs, counts,
truncated fingerprints) — never tokens, passwords, or full hashes.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import zlib
from typing import List, Optional

from gaia.connectors import keyring_access
from gaia.connectors._keyring import (  # actionable error if missing (#1621)
    _KEYRING_BACKEND_ENV_VAR,
    _NULL_KEYRING_ALIASES,
    _NULL_KEYRING_BACKEND,
    keyring,
    normalize_keyring_backend_env,
)
from gaia.connectors.errors import (
    AuthRequiredError,
    ConnectorsError,
)

logger = logging.getLogger(__name__)


# Keyring service name kept as "gaia.connections" intentionally (plan
# amendment A3): renaming to match the module rename would orphan every
# dev's existing keyring entries from #915 with zero benefit. The constant
# is internal — not user-visible — so it does not need to track the
# Python module name.
SERVICE_NAME = "gaia.connections"

# The keyring slot every connection used before several accounts were
# supported. Its account keeps it; later accounts get ``id:<stable id>`` slots
# (see ``accounts.py``).
DEFAULT_ACCOUNT = "default"

# Backend class names we refuse outright. These are the ``keyrings.alt``
# fallbacks that store in plaintext or with a weak passphrase scheme.
_REFUSED_BACKEND_CLASS_NAMES: frozenset[str] = frozenset(
    {
        "PlaintextKeyring",
        "EncryptedKeyring",
        "Win32CryptoKeyring",
    }
)

# Nesting limit when expanding chained backends; deeper is refused, not trusted.
_MAX_CHAIN_DEPTH = 8


class WeakKeyringError(ConnectorsError):
    """The keyring backend is, or may be, plaintext or weak.

    Reads and writes refuse it. Deletes go ahead with a warning, since removing
    a secret only reduces exposure (see ``keyring_access.check_delete``).
    """


class UninspectableKeyringError(WeakKeyringError):
    """A chained keyring GAIA cannot fully inspect; it may be secure, or not."""


def _refused_class_name(backend: object) -> Optional[str]:
    """Name of the refused class ``backend`` is or derives from, else ``None``."""
    for klass in type(backend).__mro__:
        if klass.__name__ in _REFUSED_BACKEND_CLASS_NAMES:
            return klass.__name__
    return None


def _is_chain(backend: object) -> bool:
    from keyring.backends.chainer import ChainerBackend

    if isinstance(backend, ChainerBackend):
        return True
    if "backends" in getattr(backend, "__dict__", {}):
        return True
    return any("backends" in vars(klass) for klass in type(backend).__mro__)


def _chain_error(detail: str) -> ConnectorsError:
    return UninspectableKeyringError(
        f"{detail} GAIA will not store credentials until it can confirm every "
        "backend in the chain is secure. Configure a single system credential "
        f"store (set {_KEYRING_BACKEND_ENV_VAR} to it) and restart GAIA. See "
        "docs/security/connections.mdx."
    )


def _write_targets(
    backend: object, depth: int = 0, expanded: Optional[set] = None
) -> List[object]:
    """``backend`` plus every backend a chain under it can write to.

    ``expanded`` holds the ids of chains already walked, so a chain repeated
    across the tree is expanded once rather than once per path to it.
    """
    if not _is_chain(backend):
        return [backend]
    if expanded is None:
        expanded = set()
    if id(backend) in expanded:
        return []
    if depth >= _MAX_CHAIN_DEPTH:
        raise UninspectableKeyringError(
            f"The chained keyring backend is nested more than {_MAX_CHAIN_DEPTH} "
            "levels deep, so GAIA cannot confirm every store it writes to is "
            "secure. Configure a single system credential store (set "
            f"{_KEYRING_BACKEND_ENV_VAR} to it) and restart GAIA. See "
            "docs/security/connections.mdx."
        )
    try:
        members = list(backend.backends)
    except Exception as e:  # noqa: BLE001 - fail closed with context below
        raise _chain_error(
            f"Could not inspect the chained keyring backend "
            f"{type(backend).__name__!r}: {e}."
        ) from e
    from keyring.backend import KeyringBackend

    targets: List[object] = [backend]
    for member in members:
        if not isinstance(member, KeyringBackend):
            raise _chain_error(
                f"Could not inspect the chained keyring backend "
                f"{type(backend).__name__!r}: it holds "
                f"{type(member).__name__!r}, which is not a keyring backend."
            )
        targets.extend(_write_targets(member, depth + 1, expanded))
    expanded.add(id(backend))
    return targets


def _connection_username(provider: str, slot: str) -> str:
    """Keyring username of one stored account: ``"<provider>:<slot>"``."""
    return f"{provider}:{slot}"


def _provider_credentials_username(provider: str) -> str:
    """Keyring username for the *app's* OAuth client credentials.

    Distinct namespace from connection blobs so an installation token
    (user's refresh_token, keyed ``<provider>:<account>``) and the
    application's OAuth client (``provider:<provider>``) cannot collide.
    """
    return f"provider:{provider}"


def verify_keyring_backend() -> None:
    """
    Raise ``ConnectorsError`` if the active keyring is, or derives from, one of
    the refused backends. A chained keyring is expanded and every backend it
    can write to is checked; a chain that cannot be inspected is refused.
    Runs before every keyring read and write through ``keyring_access`` —
    cheap, and closes the silent-plaintext-fallback path (A4). Deletes run it
    through ``keyring_access.check_delete``, which warns instead of refusing
    a ``WeakKeyringError``.

    Also the single eager choke point where an unresolvable
    ``PYTHON_KEYRING_BACKEND`` first surfaces (#2441): keyring resolves the var
    as a ``module.Class`` path, so a bad value raises deep inside keyring
    (a dotless value → ``ValueError: Empty module name``). Turn that opaque
    failure into an actionable error naming the offending value and the correct
    form — never let it bubble up as an unexplained sidecar 502.
    """
    normalize_keyring_backend_env()
    try:
        backend = keyring.get_keyring()
    except Exception as e:  # noqa: BLE001 - re-raised loudly with context below
        configured = os.environ.get(_KEYRING_BACKEND_ENV_VAR)
        hint = ""
        if configured:
            hint = (
                f" {_KEYRING_BACKEND_ENV_VAR}={configured!r} is not a valid "
                "keyring backend identifier: keyring expects a fully-qualified "
                "'module.Class' path (for example "
                f"'{_NULL_KEYRING_BACKEND}' to disable the store in "
                "dev/testing), or one of the GAIA shorthands "
                f"{sorted(_NULL_KEYRING_ALIASES)}."
            )
        raise ConnectorsError(
            f"Could not initialize the keyring backend: {e}.{hint} "
            "See docs/security/connections.mdx."
        ) from e
    for target in _write_targets(backend):
        cls_name = _refused_class_name(target)
        if cls_name is None:
            continue
        raise WeakKeyringError(
            f"Insecure keyring backend {cls_name!r} is in use. GAIA refuses "
            "to store OAuth refresh tokens in plaintext. Install a secure "
            "system credential store (gnome-keyring or kwallet on Linux; "
            "macOS Keychain and Windows Credential Locker are built-in) "
            "and restart GAIA. See docs/security/connections.mdx."
        )


def keyring_read_refusal() -> Optional[WeakKeyringError]:
    """Why keyring reads are refused (a weak or uninspectable backend), else None.

    For delete paths that must still work there. Any other backend failure
    raises, as :func:`verify_keyring_backend` does.
    """
    try:
        verify_keyring_backend()
    except WeakKeyringError as e:
        return e
    return None


def _wrap_keyring_call(operation: str):
    """Decorator-like helper: translate keyring exceptions into
    ``ConnectorsError`` with actionable text per CLAUDE.md."""

    def wrapper(fn):
        def inner(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except keyring.errors.KeyringError as e:
                raise ConnectorsError(
                    f"Keyring {operation} failed: {e}. Install a system "
                    "credential store (gnome-keyring on Linux, or rely on "
                    "the macOS Keychain / Windows Credential Locker), "
                    "configure it, and restart GAIA. See "
                    "docs/security/connections.mdx."
                ) from e

        return inner

    return wrapper


# ---------------------------------------------------------------------------
# Transparent large-blob chunking (#1275 Windows fix)
# ---------------------------------------------------------------------------
# Windows Credential Manager caps a single credential blob at 2560 bytes
# (CRED_MAX_CREDENTIAL_BLOB_SIZE). keyring stores the value as UTF-16, so the
# practical ceiling is ~1280 chars — and CredWrite fails with a cryptic
# "The stub received bad data" (error 1783) past it. Google refresh tokens
# (~100 chars) never came close, so this went unnoticed; Microsoft Graph
# refresh tokens (~1600 chars) blow straight past it. We transparently split
# an oversized value across extra keyring slots (``<username>#<idx>``) and
# reassemble on read. Small values stay in a single raw slot — backward
# compatible with every pre-existing entry and the short Google blobs.
#
# Torn-write safety: keyring has no atomic multi-slot transaction, and
# Microsoft rotates the refresh token on every refresh (varying length →
# varying chunk count), so an overwrite can crash mid-rewrite with slots and
# manifest out of sync. The manifest therefore carries a CRC32 of the full
# payload; ``_kr_get`` validates the reassembled value against it and returns
# ``None`` ("reconnect") on any mismatch — a torn state can never surface as a
# truncated-but-valid-looking token.
_CHUNK_CHARS = 1024
# No NUL: the macOS backend cuts a stored value at the first one. A JSON blob
# starts with "{", and no credential format starts with this.
_CHUNK_SENTINEL = "gaia-chunked-v2:"
# Earlier releases wrote this. It still reads back whole on Windows and Linux.
_LEGACY_CHUNK_SENTINEL = "\x00gaia-chunked\x00"
_CHUNK_SENTINELS = (_CHUNK_SENTINEL, _LEGACY_CHUNK_SENTINEL)


def _payload_crc(payload: str) -> str:
    """Non-cryptographic integrity check for reassembled chunk payloads."""
    return format(zlib.crc32(payload.encode("utf-8")), "08x")


def _chunk_username(username: str, idx: int) -> str:
    return f"{username}#{idx}"


def _kr_raw_set(username: str, value: str) -> None:
    @_wrap_keyring_call("set_password")
    def _set():
        keyring_access.write(SERVICE_NAME, username, value)

    _set()


class KeyringRefused(ConnectorsError):
    """The credential store stayed locked: its unlock prompt was dismissed."""


#: How long a dismissed prompt holds reads back. Long enough that pollers stop
#: reopening it; short enough that a process nobody retries in (the daemon's
#: token refresh) recovers once the user unlocks the store elsewhere.
HOLD_SECONDS = 300.0

_refusal_lock = threading.Lock()
_refusal: Optional[str] = None
_refused_at = 0.0
# One read probes the store once a hold expires; the rest stay held until it ends.
_probing = False


def _held_locked() -> bool:
    return _refusal is not None and (
        _probing or time.monotonic() - _refused_at < HOLD_SECONDS
    )


def keyring_refusal() -> Optional[str]:
    """Why reads are held back after a dismissed unlock prompt, or None."""
    with _refusal_lock:
        return _refusal if _held_locked() else None


def retry_keyring() -> None:
    """Let the next read ask the OS again; for an action the user just took."""
    global _refusal
    with _refusal_lock:
        _refusal = None


def _refuse(error: Exception) -> KeyringRefused:
    global _refusal, _refused_at
    minutes = max(1, round(HOLD_SECONDS / 60))
    message = (
        "The OS credential store is locked: its unlock prompt was closed "
        f"({error}). GAIA won't ask again for {minutes} minutes; to unlock it "
        "now, choose Try again under Settings → Agent & model, or connect or test a "
        "connector."
    )
    with _refusal_lock:
        _refusal = message
        _refused_at = time.monotonic()
    logger.warning(
        "Keyring read refused; holding reads for %ds or until the user retries",
        HOLD_SECONDS,
    )
    return KeyringRefused(message)


def _kr_raw_get(username: str, *, for_delete: bool = False) -> Optional[str]:
    global _probing
    # Every read after a dismissed prompt would open a new one at once.
    with _refusal_lock:
        if _held_locked():
            raise KeyringRefused(_refusal)
        probe = _refusal is not None
        if probe:
            _probing = True
    try:
        read = keyring_access.read_for_delete if for_delete else keyring_access.read
        value = read(SERVICE_NAME, username)
    except (keyring.errors.KeyringLocked, keyring.errors.InitError) as e:
        raise _refuse(e) from e
    except keyring.errors.KeyringError as e:
        if probe:
            retry_keyring()
        raise ConnectorsError(
            f"Keyring get_password failed: {e}. Install a system "
            "credential store (gnome-keyring on Linux, or rely on "
            "the macOS Keychain / Windows Credential Locker), "
            "configure it, and restart GAIA. See "
            "docs/security/connections.mdx."
        ) from e
    finally:
        if probe:
            with _refusal_lock:
                _probing = False
    if probe:
        retry_keyring()
    return value


def _kr_raw_delete(username: str) -> None:
    try:
        keyring_access.delete(SERVICE_NAME, username)
    except keyring.errors.PasswordDeleteError:
        pass
    except keyring.errors.KeyringError as e:
        raise ConnectorsError(
            f"Keyring delete_password failed: {e}. See "
            "docs/security/connections.mdx."
        ) from e


def _kr_clear_chunks(username: str, *, for_delete: bool = False) -> None:
    """Best-effort removal of any leftover chunk slots for ``username``."""
    idx = 0
    while (
        _kr_raw_get(_chunk_username(username, idx), for_delete=for_delete) is not None
    ):
        _kr_raw_delete(_chunk_username(username, idx))
        idx += 1


def _parse_manifest(raw: str) -> Optional[tuple[int, Optional[str]]]:
    """Parse ``<SENTINEL><count>[:<crc>]`` → ``(count, crc)``. ``None`` if the
    manifest is malformed. ``crc`` is ``None`` for a legacy count-only manifest."""
    sentinel = (
        _CHUNK_SENTINEL if raw.startswith(_CHUNK_SENTINEL) else _LEGACY_CHUNK_SENTINEL
    )
    body = raw[len(sentinel) :]
    count_str, _, crc = body.partition(":")
    try:
        return int(count_str), (crc or None)
    except ValueError:
        return None


def _kr_legacy_get(username: str, *, for_delete: bool = False) -> Optional[str]:
    """Read a legacy (possibly chunked) raw OS keyring entry.

    Any partial/torn state (missing slot, or a CRC mismatch from a crashed
    mid-rewrite) resolves to ``None`` — never a truncated-but-valid-looking
    value. ``for_delete`` reads the way a delete is checked; only a delete path
    may pass it."""
    raw = _kr_raw_get(username, for_delete=for_delete)
    if raw == "":
        # An earlier release's manifest as macOS kept it; nothing stores an empty value.
        logger.warning(
            "Keychain entry %s lost its chunk manifest; treating it as not stored. "
            "Save or reconnect it to replace it.",
            username,
        )
        return None
    if raw is None or not raw.startswith(_CHUNK_SENTINELS):
        return raw
    parsed = _parse_manifest(raw)
    if parsed is None:
        return None
    count, crc = parsed
    parts: list[str] = []
    for idx in range(count):
        part = _kr_raw_get(_chunk_username(username, idx), for_delete=for_delete)
        if part is None:
            # Missing slot — torn entry; treat as "not configured".
            return None
        parts.append(part)
    value = "".join(parts)
    if crc is not None and _payload_crc(value) != crc:
        # Reassembled payload doesn't match the manifest CRC — a crashed
        # mid-rewrite left slots and manifest out of sync. Fail safe.
        return None
    return value


def _kr_legacy_delete(username: str) -> None:
    """Delete a legacy (possibly chunked) raw OS keyring entry and its chunks."""
    raw = _kr_raw_get(username, for_delete=True)
    _kr_raw_delete(username)
    if raw is not None and raw.startswith(_CHUNK_SENTINELS):
        parsed = _parse_manifest(raw)
        count = parsed[0] if parsed else 0
        for idx in range(count):
            _kr_raw_delete(_chunk_username(username, idx))
    _kr_clear_chunks(username, for_delete=True)


def _kr_sweep_chunks_from(username: str, start: int) -> None:
    """Delete chunk slots ``start``, ``start+1``, ... until one is absent.

    Deletes without reading, so no macOS read prompt opens.
    """
    idx = start
    while True:
        try:
            keyring_access.delete(SERVICE_NAME, _chunk_username(username, idx))
        except keyring.errors.PasswordDeleteError:
            return
        except keyring.errors.KeyringError as e:
            raise ConnectorsError(
                f"Keyring delete_password failed: {e}. See "
                "docs/security/connections.mdx."
            ) from e
        idx += 1


def _kr_legacy_set(username: str, payload: str) -> None:
    """Write a legacy (possibly chunked) per-slot entry older builds can read.

    Chunks are written BEFORE the manifest so a reader never sees a manifest
    pointing at not-yet-written parts.
    """
    if len(payload) <= _CHUNK_CHARS:
        _kr_raw_set(username, payload)
        _kr_sweep_chunks_from(username, 0)
        return
    chunks = [
        payload[i : i + _CHUNK_CHARS] for i in range(0, len(payload), _CHUNK_CHARS)
    ]
    for idx, chunk in enumerate(chunks):
        _kr_raw_set(_chunk_username(username, idx), chunk)
    _kr_raw_set(username, f"{_CHUNK_SENTINEL}{len(chunks)}:{_payload_crc(payload)}")
    _kr_sweep_chunks_from(username, len(chunks))


def _kr_set(username: str, payload: str, *, mirror: str = "user") -> None:
    """Store ``payload`` under ``username`` in the single-master-key vault.

    ``mirror`` is a ``vault.MIRROR_*`` mode: who is saving, for the legacy item.
    """
    from gaia.connectors import vault

    vault.put(username, payload, mirror=mirror)


def _kr_get(username: str, *, for_delete: bool = False) -> Optional[str]:
    """Read ``username`` from the vault, migrating any legacy keyring entry."""
    from gaia.connectors import vault

    if for_delete:
        return vault._read_for_delete(username)
    return vault.get(username)


def _kr_delete(username: str) -> None:
    """Delete ``username`` from the vault and any legacy keyring slots."""
    from gaia.connectors import vault

    vault.delete(username)


def save_connection(
    *,
    provider: str,
    account_email: str,
    refresh_token: str,
    slot: str = DEFAULT_ACCOUNT,
    account_id: Optional[str] = None,
    scopes: List[str],
    client_id_hash: str,
    connected_at: Optional[float] = None,
    tenant: Optional[str] = None,
    account_type: Optional[str] = None,
    forwarded: bool = False,
    background_refresh: bool = False,
) -> None:
    """
    Atomically persist a connection record to the keyring.

    The single keyring slot stores a JSON blob — a partial write is
    impossible because the underlying backend's ``set_password`` is a
    full-value overwrite at the slot. This is the rotation-safety
    guarantee (per Iteration 1 fix C5).

    ``slot`` picks the keyring entry (``accounts.py`` decides it: the
    connector's first account uses ``default``). ``account_email`` is the
    display label and ``account_id`` the provider's stable id for the account;
    the id is omitted from the blob when unknown (a connection saved before it
    was recorded) and filled in by the next refresh.

    ``tenant`` (D8, #2628): the OAuth authority that minted this
    connection's refresh token, e.g. ``"organizations"`` or a specific
    Directory (tenant) ID. Omitted from the blob when ``None`` (mirrors
    ``save_provider_credentials``'s A7 contract) so a legacy blob with no
    recorded tenant is distinguishable from one that recorded a value —
    ``load_connection`` treats the two very differently.

    ``account_type`` records what KIND of account signed in when the provider
    can tell (Microsoft derives ``personal`` / ``work`` from the id_token ``tid``
    claim, #2466). Distinct from ``tenant``, which records the authority the app
    signed in *against*: the ``common`` authority admits both kinds, so the two
    answer different questions. Omitted from the blob when ``None`` — an absent
    key means "unknown", which readers must handle explicitly rather than
    assuming a kind. Callers that re-save an existing connection (e.g.
    refresh-token rotation) MUST pass both values through or they are lost.

    ``forwarded`` (#2591 review): True marks a connection imported via
    ``api.import_forwarded_connection`` — its refresh token was minted
    under a host app's OAuth client, not GAIA's own. ``flow.revoke_provider_token``
    reads this to refuse a remote revoke (it would revoke the host app's
    grant, not just GAIA's copy of it). Omitted from the blob when ``False``,
    mirroring ``tenant``/``account_type``; a legacy blob with no key is
    correctly read back as not-forwarded. Callers that re-save an existing
    connection (refresh-token rotation) MUST pass the stored value through
    or a forwarded connection silently loses its protection.

    ``background_refresh``: True for a token-refresh re-save, which updates the
    vault only and never the legacy per-item entry (a refresh nobody asked for
    must not raise an OS credential-store prompt).
    """
    verify_keyring_backend()

    blob: dict = {
        "account_email": account_email,
        "refresh_token": refresh_token,
        "scopes": list(scopes),
        "connected_at": connected_at if connected_at is not None else time.time(),
        "client_id_hash": client_id_hash,
    }
    if account_id:
        blob["account_id"] = account_id
    if tenant is not None:
        blob["tenant"] = tenant
    if account_type:
        blob["account_type"] = account_type
    if forwarded:
        blob["forwarded"] = True
    payload = json.dumps(blob, sort_keys=True)
    username = _connection_username(provider, slot)

    _kr_set(username, payload, mirror="never" if background_refresh else "user")
    # A completed write supersedes an interrupted forward's recovery record.
    _drop_forward_recovery_for(provider, slot)


def load_connection(
    provider: str,
    *,
    current_client_id_hash: str,
    current_tenant: Optional[str] = None,
    slot: str = DEFAULT_ACCOUNT,
) -> Optional[dict]:
    """
    Return the stored connection record, or ``None`` if no entry / tripwire fired.

    The eager ``client_id_hash`` tripwire (AC10) compares the stored hash
    against ``current_client_id_hash``; on mismatch the entry is cleared
    and ``None`` is returned. The caller (``tokens.get_access_token``)
    then raises ``AuthRequiredError(REAUTH_REQUIRED)``.

    ``current_tenant`` (D8/A6/A18, #2628): a second, independent tripwire
    mirroring the hash check above. When the caller passes the tenant the
    connector currently resolves to AND the stored blob recorded a tenant
    (i.e. NOT a legacy pre-#2628 blob) AND the two disagree, that is a
    genuine positive signal — clear the entry and raise
    ``AuthRequiredError(TENANT_MISMATCH)`` naming both values. A legacy
    blob with no recorded tenant, or a caller that passes
    ``current_tenant=None`` (the default), skips this check entirely —
    "we don't know" is not the same as "we know they disagree".
    """
    verify_keyring_backend()
    username = _connection_username(provider, slot)

    raw = _kr_get(username)
    if raw is None:
        return None

    try:
        blob = json.loads(raw)
    except json.JSONDecodeError as e:
        # Should not happen unless the keyring backend was corrupted by
        # an external writer — clear the entry and surface a useful error.
        delete_connection(provider, slot=slot)
        raise ConnectorsError(
            f"Stored connection blob for provider={provider!r} is not valid "
            "JSON. Cleared the entry; reconnect via Settings → Connectors "
            f"or `gaia connectors connect {provider}`."
        ) from e

    stored_hash = blob.get("client_id_hash")
    if stored_hash != current_client_id_hash:
        # Tripwire fired — clear the stored entry and raise REAUTH_REQUIRED
        # so the caller (and the router) can distinguish this case from
        # "user never connected". The unit test in test_store.py asserts
        # the entry is cleared; the unit test in test_tokens.py asserts
        # the right Reason flows to the caller.
        delete_connection(provider, slot=slot)
        raise AuthRequiredError(
            AuthRequiredError.Reason.REAUTH_REQUIRED, provider=provider
        )

    stored_tenant = blob.get("tenant")
    if (
        current_tenant is not None
        and stored_tenant is not None
        and stored_tenant != current_tenant
    ):
        delete_connection(provider, slot=slot)
        raise AuthRequiredError(
            AuthRequiredError.Reason.TENANT_MISMATCH,
            provider=provider,
            message=(
                f"The stored {provider!r} connection was authenticated "
                f"against tenant {stored_tenant!r}, but this connector "
                f"currently resolves to tenant {current_tenant!r}. This "
                "usually means the account is a different type than "
                "expected, or a Directory (tenant) ID setup field changed "
                "after connecting. Reconnect via the correct connector, or "
                f"run `gaia connectors connect {provider}` again once the "
                "tenant configuration is fixed."
            ),
        )

    return blob  # type: ignore[no-any-return]


def peek_connection(
    provider: str,
    *,
    slot: str = DEFAULT_ACCOUNT,
) -> Optional[dict]:
    """
    Return the stored connection blob for display, or ``None`` if absent.

    Read-only sibling of ``load_connection`` for UI/CLI catalog rendering:
    no tripwire, no side effects, no exceptions for a missing entry. The
    blob includes ``account_email``, ``scopes``, ``connected_at``, and
    ``client_id_hash``; the secret ``refresh_token`` field is also
    present, so callers MUST NOT log the result wholesale.

    **Tripwire semantics**: ``peek_connection`` returns the blob even
    when its ``client_id_hash`` no longer matches the live provider —
    i.e. the catalog tile will keep showing "configured" right up until
    the next auth-path read (``load_connection`` via ``tokens.get_or_refresh``)
    fires the tripwire and clears the entry. That is intentional: a
    catalog render is a side-effect-free operation, and clearing
    credentials from a list-call would be surprising. Use
    ``load_connection`` for auth-path reads where the tripwire is
    required.

    **Corrupt blob**: returns ``None`` and leaves the keyring entry in
    place. ``load_connection`` (auth path) clears corrupt entries; we
    don't here for the same side-effect-free reason.
    """
    verify_keyring_backend()
    username = _connection_username(provider, slot)

    raw = _kr_get(username)
    if raw is None:
        return None
    try:
        return json.loads(raw)  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        # Corrupt blob — caller treats as "not configured" without
        # rewriting state. ``load_connection`` (auth path) still clears
        # the corrupt entry; we don't here because peek_connection is
        # called during catalog render and must be side-effect-free.
        return None


def _forward_recovery_username(provider: str) -> str:
    """Keyring slot for an interrupted forwarded-connection save."""
    return f"recovery:forward:{provider}"


def has_forward_recovery(provider: str) -> bool:
    """Whether a previous forwarded save left a recovery marker."""
    verify_keyring_backend()
    return _kr_get(_forward_recovery_username(provider)) is not None


def forward_recovery_slot(provider: str) -> Optional[str]:
    """The account slot an interrupted forward left a marker for, or None."""
    verify_keyring_backend()
    raw = _kr_get(_forward_recovery_username(provider))
    if raw is None:
        return None
    try:
        slot = json.loads(raw).get("account_slot", DEFAULT_ACCOUNT)
    except (json.JSONDecodeError, AttributeError):
        # recover_forward_recovery reports the corrupt marker loudly.
        return DEFAULT_ACCOUNT
    return slot if isinstance(slot, str) else DEFAULT_ACCOUNT


def write_forward_recovery(
    provider: str, snapshot: dict, *, slot: str = DEFAULT_ACCOUNT
) -> None:
    """Record the old pair before a forwarded save changes either slot.

    ``slot`` names the account the forward writes. The marker is itself stored
    through the torn-write-safe keyring helper. It contains encrypted credential
    values and is never logged.
    """
    verify_keyring_backend()
    payload = json.dumps(
        {"version": 2, "account_slot": slot, "slots": snapshot}, sort_keys=True
    )
    _kr_set(_forward_recovery_username(provider), payload, mirror="background")


def clear_forward_recovery(provider: str) -> None:
    """Remove the recovery marker after a forwarded save is complete."""
    keyring_access.check_delete()
    _kr_delete(_forward_recovery_username(provider))


def recover_forward_recovery(provider: str) -> bool:
    """Restore an interrupted forwarded save; return whether one existed."""
    verify_keyring_backend()
    username = _forward_recovery_username(provider)
    raw = _kr_get(username)
    if raw is None:
        return False
    try:
        marker = json.loads(raw)
        if marker.get("version") not in (1, 2) or not isinstance(
            marker.get("slots"), dict
        ):
            raise ValueError("unsupported marker")
        # Version 1 markers predate several accounts and always named "default".
        account_slot = marker.get("account_slot", DEFAULT_ACCOUNT)
        if not isinstance(account_slot, str):
            raise ValueError("unsupported marker")
        expected = {
            _provider_credentials_username(provider),
            _connection_username(provider, account_slot),
        }
        slots = marker["slots"]
        # Restore only this provider's two slots, never a slot the marker names.
        if set(slots) != expected or not all(
            v is None or isinstance(v, str) for v in slots.values()
        ):
            raise ValueError("unexpected marker slots")
    except (json.JSONDecodeError, AttributeError, ValueError) as exc:
        raise ConnectorsError(
            f"The recovery record for the {provider} connection is corrupt. "
            "Reconnect from Settings → Connectors before using it again."
        ) from exc
    restore_connection_slots(slots)
    _kr_delete(username)
    return True


def snapshot_connection_slots(provider: str, *, slot: str = DEFAULT_ACCOUNT) -> dict:
    """The stored client config and one account's connection for ``provider``.

    For :func:`restore_connection_slots`, to undo a multi-slot write that
    failed partway. A torn chunked value reads as absent. Holds secrets:
    never log it.
    """
    verify_keyring_backend()
    return {
        username: _kr_get(username)
        for username in (
            _provider_credentials_username(provider),
            _connection_username(provider, slot),
        )
    }


def restore_connection_slots(snapshot: dict) -> None:
    """Put back the values :func:`snapshot_connection_slots` read; an absent
    one (including a torn one) is deleted."""
    verify_keyring_backend()
    for username, raw in snapshot.items():
        if raw is None:
            _kr_delete(username)
        else:
            _kr_set(username, raw, mirror="background")


def delete_connection(provider: str, *, slot: str = DEFAULT_ACCOUNT) -> None:
    """Remove one stored account's keyring entry if present. Idempotent."""
    keyring_access.check_delete()
    username = _connection_username(provider, slot)
    _kr_delete(username)
    # Otherwise the next read would restore the connection just removed.
    _drop_forward_recovery_for(provider, slot)


def connection_slot_in_use(provider: str, *, slot: str = DEFAULT_ACCOUNT) -> bool:
    """Whether a stored account's slot holds anything, for a delete path.

    Checked like a delete, so it answers on a weak backend; it never returns
    the stored value.
    """
    from gaia.connectors import vault

    return vault.slot_in_use(_connection_username(provider, slot))


def _drop_forward_recovery_for(provider: str, slot: str) -> None:
    """Delete an interrupted forward's marker if it covers ``slot``."""
    raw = _kr_get(_forward_recovery_username(provider), for_delete=True)
    if raw is None:
        return
    try:
        marked = json.loads(raw).get("account_slot", DEFAULT_ACCOUNT)
    except (json.JSONDecodeError, AttributeError):
        marked = slot
    if marked == slot:
        _kr_delete(_forward_recovery_username(provider))


def _secret_username(name: str) -> str:
    """Keyring slot for a standalone secret, namespaced away from connections."""
    return f"secret:{name}"


def save_secret(name: str, value: str) -> None:
    """Persist a standalone secret in the OS credential store.

    For credentials that are not OAuth connections — an API key, a gateway
    token — where :func:`save_connection` (which wants a refresh token, scopes
    and a client-id hash) and :func:`save_provider_credentials` (which requires
    a client id) would both mean inventing fields to satisfy a schema.

    Encrypted at rest by the OS: DPAPI on Windows, Keychain on macOS,
    SecretService on Linux. ``verify_keyring_backend`` refuses plaintext
    backends, so this cannot silently degrade to a readable file.
    """
    verify_keyring_backend()
    if not value:
        raise ConnectorsError(f"save_secret({name!r}): value is empty")
    _kr_set(_secret_username(name), value)


def peek_secret(name: str) -> Optional[str]:
    """Read a secret saved by :func:`save_secret`, or None when absent."""
    verify_keyring_backend()
    return _kr_get(_secret_username(name))


def secret_in_use(name: str) -> bool:
    """Whether :func:`save_secret` kept something under ``name``, for a delete path.

    Checked like a delete, so it answers on a weak backend; it never returns
    the stored value.
    """
    from gaia.connectors import vault

    return vault.slot_in_use(_secret_username(name))


def delete_secret(name: str) -> None:
    """Remove a secret saved by :func:`save_secret`. Absent is not an error."""
    keyring_access.check_delete()
    _kr_delete(_secret_username(name))


def save_provider_credentials(
    provider: str,
    *,
    client_id: str,
    client_secret: str = "",
    tenant: Optional[str] = None,
) -> None:
    """Persist the *application's* OAuth client credentials for *provider*.

    Stores ``{"client_id": ..., "client_secret": ...}`` as a single JSON
    blob in the keyring, distinct from any connection blob. Lets users
    self-onboard via the AgentUI without ever touching env vars; the
    blob is encrypted at rest by the OS credential store.

    ``tenant`` (plan amendment D5/A7, #2628): the optional single-tenant
    override a ``microsoft_work``-style connector's setup form collects
    (its ``ConfigField(key="tenant_id")``). Omitted from the blob entirely
    when ``None`` — a test locks the exact two-key shape of an existing
    Google blob, so this must never add a ``tenant`` key unless the caller
    actually passed one.
    """
    verify_keyring_backend()
    if not client_id:
        raise ConnectorsError(
            f"save_provider_credentials({provider!r}): client_id is empty"
        )
    blob: dict = {"client_id": client_id, "client_secret": client_secret}
    if tenant is not None:
        blob["tenant"] = tenant
    payload = json.dumps(blob, sort_keys=True)
    username = _provider_credentials_username(provider)
    _kr_set(username, payload)


def peek_provider_credentials(provider: str) -> Optional[dict]:
    """Return the stored OAuth client credentials, or ``None`` if absent.

    Side-effect-free read used by ``GoogleOAuthProvider.__init__`` (and
    siblings) to find the persisted ``client_id`` / ``client_secret``
    before falling back to env vars.
    """
    verify_keyring_backend()
    username = _provider_credentials_username(provider)

    raw = _kr_get(username)
    if raw is None:
        return None
    try:
        return json.loads(raw)  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        return None


def clear_provider_credentials(provider: str) -> None:
    """Remove the stored OAuth client credentials for *provider*. Idempotent."""
    keyring_access.check_delete()
    username = _provider_credentials_username(provider)
    _kr_delete(username)


def list_connections() -> List[str]:
    """
    Best-effort enumeration of providers with at least one stored account.

    The ``keyring`` API does not expose a portable "list all entries for
    service" call, so each registered OAuth provider's accounts are probed
    (``accounts.list_accounts``). The provider set is registry-driven (every
    ``oauth_pkce`` spec), so a newly added provider is enumerated without
    editing this function.
    """
    # Function-local import: keep this module free of a module-level registry
    # dependency (mirrors ``api._require_mcp_server_for_activation``). Importing
    # the catalog registers the built-in specs; the module cache makes repeats a
    # no-op.
    import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
    from gaia.connectors.accounts import list_accounts
    from gaia.connectors.registry import REGISTRY

    known = tuple(spec.id for spec in REGISTRY.all() if spec.type == "oauth_pkce")
    found: list[str] = []
    for provider in known:
        try:
            if any(a.status == "ok" for a in list_accounts(provider)):
                found.append(provider)
        except ConnectorsError:
            # Translate-and-skip is OK for an enumeration call: a single
            # failed backend doesn't invalidate the list.
            continue
    return found
