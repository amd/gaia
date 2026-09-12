# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Encrypted browser session storage.

A logged-in session is cookies plus ``localStorage`` — tens of KB. That does
not fit in the OS credential store: Windows Credential Manager caps an entry
around 1280 characters, and ``connectors/mcp_server.py`` writes secrets with a
bare ``keyring.set_password`` (the chunking helpers in ``connectors/store.py``
are private to that module). Putting a cookie jar down that path fails on
Windows.

So the split is **key in the keyring, ciphertext on disk**:

    keyring:  gaia.connections / browser-session-key   (32-byte AES key)
    disk:     ~/.gaia/browser/sessions/<sha256(origin)>.enc   0600, AES-GCM
              ~/.gaia/browser/sessions/<sha256(origin)>.json  0600, metadata

The metadata file holds no secrets, so ``gaia browser sessions`` can list what
is stored without decrypting anything.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from gaia.browser.errors import SessionStoreError
from gaia.logger import get_logger

logger = get_logger(__name__)

#: Shared with connectors so GAIA owns one keyring service name, not two.
KEYRING_SERVICE = "gaia.connections"
KEYRING_USERNAME = "browser-session-key"

_KEY_BYTES = 32
_NONCE_BYTES = 12

#: Hostname, or a bracketless IPv6/IPv4 literal. Deliberately strict.
_HOSTNAME_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
    r"|^[0-9a-f:]+$"
)


def sessions_dir() -> Path:
    """``~/.gaia/browser/sessions``, created 0700 on first use."""
    d = Path.home() / ".gaia" / "browser" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            d.chmod(0o700)
            d.parent.chmod(0o700)
        except OSError as e:
            logger.debug("Could not tighten session dir permissions: %s", e)
    return d


def origin_of(url: str) -> str:
    """Normalized ``scheme://host`` — the unit a session is scoped to."""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.hostname or "").lower()
    # urlparse is permissive: it happily reports "not a url" as a hostname, so
    # a junk string would otherwise become a real-looking origin and get a
    # session slot of its own.
    if not host or not _HOSTNAME_RE.match(host):
        raise SessionStoreError(
            f"Cannot determine the site for {url!r}. Pass a full URL such as "
            "https://example.com."
        )
    return f"{parsed.scheme}://{host}"


def _slug(origin: str) -> str:
    return hashlib.sha256(origin.encode("utf-8")).hexdigest()[:32]


def _load_key() -> bytes:
    """Fetch the session key, minting one on first use."""
    try:
        import keyring
    except ImportError as e:
        raise SessionStoreError(
            "Saving a browser session needs the 'keyring' package, which "
            "should ship with GAIA. Reinstall with: pip install amd-gaia"
        ) from e

    try:
        existing = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except Exception as e:  # noqa: BLE001 — backend failure, re-raised actionably
        raise SessionStoreError(
            f"Could not read the OS credential store: {e}. "
            "On Linux this usually means no SecretService backend is running."
        ) from e

    if existing:
        try:
            key = bytes.fromhex(existing)
        except ValueError as e:
            raise SessionStoreError(
                "The stored browser session key is corrupt. Clear it with "
                "`gaia browser sessions clear` and sign in again."
            ) from e
        if len(key) == _KEY_BYTES:
            return key
        raise SessionStoreError(
            "The stored browser session key is the wrong length. Clear it "
            "with `gaia browser sessions clear` and sign in again."
        )

    key = os.urandom(_KEY_BYTES)
    try:
        keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, key.hex())
    except Exception as e:  # noqa: BLE001 — re-raised actionably
        raise SessionStoreError(
            f"Could not write to the OS credential store: {e}"
        ) from e
    return key


def _aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as e:
        raise SessionStoreError(
            "Browser session encryption needs the 'cryptography' package, "
            "which should ship with GAIA. Reinstall with: pip install amd-gaia"
        ) from e
    return AESGCM


def _write_private(path: Path, data: bytes) -> None:
    """Atomic 0600 write — never leave a half-written session behind."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        if os.name != "nt":
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save(url: str, storage_state: Dict[str, Any]) -> str:
    """Encrypt and persist ``storage_state`` for ``url``'s origin."""
    origin = origin_of(url)
    key = _load_key()
    AESGCM = _aesgcm()

    nonce = os.urandom(_NONCE_BYTES)
    plaintext = json.dumps(storage_state, separators=(",", ":")).encode("utf-8")
    # Bind the ciphertext to its origin: a blob moved to another site's slot
    # fails to decrypt rather than loading the wrong session.
    blob = nonce + AESGCM(key).encrypt(nonce, plaintext, origin.encode("utf-8"))

    d = sessions_dir()
    slug = _slug(origin)
    _write_private(d / f"{slug}.enc", blob)

    n_cookies = len(storage_state.get("cookies") or [])
    meta = {
        "origin": origin,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cookies": n_cookies,
        "bytes": len(blob),
    }
    _write_private(d / f"{slug}.json", json.dumps(meta, indent=2).encode("utf-8"))
    logger.info("Saved browser session for %s (%d cookies)", origin, n_cookies)
    return origin


def load(url: str) -> Optional[Dict[str, Any]]:
    """Return the stored session for ``url``'s origin, or ``None``."""
    origin = origin_of(url)
    path = sessions_dir() / f"{_slug(origin)}.enc"
    if not path.exists():
        return None

    key = _load_key()
    AESGCM = _aesgcm()
    blob = path.read_bytes()
    if len(blob) <= _NONCE_BYTES:
        raise SessionStoreError(
            f"The saved session for {origin} is truncated. Remove it with "
            f"`gaia browser sessions forget {origin}` and sign in again."
        )
    try:
        plaintext = AESGCM(key).decrypt(
            blob[:_NONCE_BYTES], blob[_NONCE_BYTES:], origin.encode("utf-8")
        )
    except Exception as e:  # noqa: BLE001 — re-raised actionably
        raise SessionStoreError(
            f"Could not decrypt the saved session for {origin}: the key "
            "changed or the file was tampered with. Remove it with "
            f"`gaia browser sessions forget {origin}` and sign in again."
        ) from e
    return json.loads(plaintext.decode("utf-8"))


def forget(url: str) -> bool:
    """Delete the stored session for ``url``'s origin."""
    origin = origin_of(url)
    slug = _slug(origin)
    d = sessions_dir()
    removed = False
    for suffix in (".enc", ".json"):
        p = d / f"{slug}{suffix}"
        if p.exists():
            p.unlink()
            removed = True
    if removed:
        logger.info("Removed browser session for %s", origin)
    return removed


def listing() -> List[Dict[str, Any]]:
    """Metadata for every stored session. Decrypts nothing."""
    out: List[Dict[str, Any]] = []
    for meta_path in sorted(sessions_dir().glob("*.json")):
        try:
            out.append(json.loads(meta_path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            logger.warning("Skipping unreadable session metadata %s: %s", meta_path, e)
    return out
