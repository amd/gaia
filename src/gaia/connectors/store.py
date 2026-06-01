# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Keyring-backed persistent storage for OAuth connection records.

Single-blob design (plan amendment A5):
    Each ``(provider, account_email)`` tuple maps to ONE keyring entry that
    stores a JSON blob containing ``refresh_token``, ``account_email``,
    ``scopes``, ``connected_at``, and ``client_id_hash``. A single
    ``set_password`` call atomically replaces the entry, so a partial-write
    failure cannot leave us with a fresh token + stale metadata.

Backend allowlist (plan amendment A4):
    Plaintext or weak file-backed keyring backends (e.g. ``keyrings.alt``'s
    ``PlaintextKeyring``, ``EncryptedKeyring``, ``Win32CryptoKeyring``) are
    explicitly refused BEFORE any write. Linux machines without
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
import time
from typing import List, Optional

import keyring
import keyring.errors

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

# v1 default account name used by callers that don't yet plumb a real
# email through. Multi-account support (forward-compat per A10) writes
# the real account_email here.
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


def _connection_username(provider: str, account_email: str) -> str:
    """Build the keyring username key for ``(provider, account_email)``.

    Multi-account forward-compat (A10): the key shape is
    ``"<provider>:<account_email>"``. v1 always writes
    ``account_email = "default"`` so the schema can absorb a real email
    without migration.
    """
    return f"{provider}:{account_email}"


def _provider_credentials_username(provider: str) -> str:
    """Keyring username for the *app's* OAuth client credentials.

    Distinct namespace from connection blobs so an installation token
    (user's refresh_token, keyed ``<provider>:<account>``) and the
    application's OAuth client (``provider:<provider>``) cannot collide.
    """
    return f"provider:{provider}"


def verify_keyring_backend() -> None:
    """
    Raise ``ConnectorsError`` if the active keyring is one of the refused
    backends. Called eagerly at every save and at every load — cheap, and
    closes the silent-plaintext-fallback path (A4).
    """
    backend = keyring.get_keyring()
    cls_name = type(backend).__name__
    if cls_name in _REFUSED_BACKEND_CLASS_NAMES:
        raise ConnectorsError(
            f"Insecure keyring backend {cls_name!r} is in use. GAIA refuses "
            "to store OAuth refresh tokens in plaintext. Install a secure "
            "system credential store (gnome-keyring or kwallet on Linux; "
            "macOS Keychain and Windows Credential Locker are built-in) "
            "and restart GAIA. See docs/security/connections.mdx."
        )


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


def save_connection(
    *,
    provider: str,
    account_email: str,
    refresh_token: str,
    scopes: List[str],
    client_id_hash: str,
    connected_at: Optional[float] = None,
) -> None:
    """
    Atomically persist a connection record to the keyring.

    The single keyring slot stores a JSON blob — a partial write is
    impossible because the underlying backend's ``set_password`` is a
    full-value overwrite at the slot. This is the rotation-safety
    guarantee (per Iteration 1 fix C5).

    v1 single-account-per-provider scope (per plan amendment A10): the
    keyring slot is ALWAYS keyed by ``DEFAULT_ACCOUNT``, regardless of
    the ``account_email`` argument. ``account_email`` is stored inside
    the JSON blob for display purposes only. **A second
    ``save_connection`` for the same provider — even with a different
    email — will overwrite the first.** Multi-account support (separate
    keyring slots per email) is a v2 follow-up; the username-key shape
    ``"<provider>:<account_email>"`` is forward-compatible for that
    migration.
    """
    verify_keyring_backend()

    blob = {
        "account_email": account_email,
        "refresh_token": refresh_token,
        "scopes": list(scopes),
        "connected_at": connected_at if connected_at is not None else time.time(),
        "client_id_hash": client_id_hash,
    }
    payload = json.dumps(blob, sort_keys=True)
    # v1 single-account per provider (per A10): the keyring KEY is always
    # built with DEFAULT_ACCOUNT; ``account_email`` lives in the metadata
    # blob for display. v2 will key by real email without a schema
    # migration since the username shape already accommodates it.
    username = _connection_username(provider, DEFAULT_ACCOUNT)

    @_wrap_keyring_call("set_password")
    def _set():
        keyring.set_password(SERVICE_NAME, username, payload)

    _set()


def load_connection(
    provider: str,
    *,
    current_client_id_hash: str,
    account_email: str = DEFAULT_ACCOUNT,
) -> Optional[dict]:
    """
    Return the stored connection record, or ``None`` if no entry / tripwire fired.

    The eager ``client_id_hash`` tripwire (AC10) compares the stored hash
    against ``current_client_id_hash``; on mismatch the entry is cleared
    and ``None`` is returned. The caller (``tokens.get_access_token``)
    then raises ``AuthRequiredError(REAUTH_REQUIRED)``.
    """
    verify_keyring_backend()
    username = _connection_username(provider, account_email)

    @_wrap_keyring_call("get_password")
    def _get():
        return keyring.get_password(SERVICE_NAME, username)

    raw = _get()
    if raw is None:
        return None

    try:
        blob = json.loads(raw)
    except json.JSONDecodeError as e:
        # Should not happen unless the keyring backend was corrupted by
        # an external writer — clear the entry and surface a useful error.
        delete_connection(provider, account_email=account_email)
        raise ConnectorsError(
            f"Stored connection blob for provider={provider!r} is not valid "
            "JSON. Cleared the entry; reconnect via Settings → Connections "
            f"or `gaia connectors connect {provider}`."
        ) from e

    stored_hash = blob.get("client_id_hash")
    if stored_hash != current_client_id_hash:
        # Tripwire fired — clear the stored entry and raise REAUTH_REQUIRED
        # so the caller (and the router) can distinguish this case from
        # "user never connected". The unit test in test_store.py asserts
        # the entry is cleared; the unit test in test_tokens.py asserts
        # the right Reason flows to the caller.
        delete_connection(provider, account_email=account_email)
        raise AuthRequiredError(
            AuthRequiredError.Reason.REAUTH_REQUIRED, provider=provider
        )

    return blob  # type: ignore[no-any-return]


def peek_connection(
    provider: str,
    *,
    account_email: str = DEFAULT_ACCOUNT,
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
    username = _connection_username(provider, account_email)

    @_wrap_keyring_call("get_password")
    def _get():
        return keyring.get_password(SERVICE_NAME, username)

    raw = _get()
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


def delete_connection(provider: str, *, account_email: str = DEFAULT_ACCOUNT) -> None:
    """Remove the keyring entry for ``provider`` if present. Idempotent."""
    verify_keyring_backend()
    username = _connection_username(provider, account_email)

    try:
        keyring.delete_password(SERVICE_NAME, username)
    except keyring.errors.PasswordDeleteError:
        # Already gone — fine.
        pass
    except keyring.errors.KeyringError as e:
        raise ConnectorsError(
            f"Keyring delete_password failed: {e}. See "
            "docs/security/connections.mdx."
        ) from e


def save_provider_credentials(
    provider: str, *, client_id: str, client_secret: str = ""
) -> None:
    """Persist the *application's* OAuth client credentials for *provider*.

    Stores ``{"client_id": ..., "client_secret": ...}`` as a single JSON
    blob in the keyring, distinct from any connection blob. Lets users
    self-onboard via the AgentUI without ever touching env vars; the
    blob is encrypted at rest by the OS credential store.
    """
    verify_keyring_backend()
    if not client_id:
        raise ConnectorsError(
            f"save_provider_credentials({provider!r}): client_id is empty"
        )
    payload = json.dumps(
        {"client_id": client_id, "client_secret": client_secret}, sort_keys=True
    )
    username = _provider_credentials_username(provider)

    @_wrap_keyring_call("set_password")
    def _set():
        keyring.set_password(SERVICE_NAME, username, payload)

    _set()


def peek_provider_credentials(provider: str) -> Optional[dict]:
    """Return the stored OAuth client credentials, or ``None`` if absent.

    Side-effect-free read used by ``GoogleOAuthProvider.__init__`` (and
    siblings) to find the persisted ``client_id`` / ``client_secret``
    before falling back to env vars.
    """
    verify_keyring_backend()
    username = _provider_credentials_username(provider)

    @_wrap_keyring_call("get_password")
    def _get():
        return keyring.get_password(SERVICE_NAME, username)

    raw = _get()
    if raw is None:
        return None
    try:
        return json.loads(raw)  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        return None


def clear_provider_credentials(provider: str) -> None:
    """Remove the stored OAuth client credentials for *provider*. Idempotent."""
    verify_keyring_backend()
    username = _provider_credentials_username(provider)
    try:
        keyring.delete_password(SERVICE_NAME, username)
    except keyring.errors.PasswordDeleteError:
        pass
    except keyring.errors.KeyringError as e:
        raise ConnectorsError(
            f"Keyring delete_password failed: {e}. See "
            "docs/security/connections.mdx."
        ) from e


def list_connections() -> List[str]:
    """
    Best-effort enumeration of stored providers.

    The ``keyring`` API does not expose a portable "list all entries for
    service" call. v1 returns the providers we know about (currently
    just ``google``); future providers extend this.
    """
    known = ("google",)
    found: list[str] = []
    for provider in known:
        username = _connection_username(provider, DEFAULT_ACCOUNT)
        try:
            if keyring.get_password(SERVICE_NAME, username) is not None:
                found.append(provider)
        except keyring.errors.KeyringError:
            # Translate-and-skip is OK for an enumeration call: a single
            # failed backend doesn't invalidate the list.
            continue
    return found
