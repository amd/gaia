"""Keyring-backed storage for refresh tokens and metadata.

This module uses the `keyring` package to store refresh tokens in the OS
keychain. Metadata is stored as a JSON blob in a companion keyring entry.
"""
import json
from typing import Optional

try:
    import keyring
except Exception:  # pragma: no cover - keyring availability is env-specific
    keyring = None


SERVICE_NAME = "gaia.connections"
META_SUFFIX = "_meta"


def save_refresh_token(provider: str, account: str, refresh_token: str, meta: dict | None = None) -> None:
    if keyring is None:
        raise RuntimeError("keyring package is not available; install python-keyring")
    key = f"{SERVICE_NAME}.{provider}"
    keyring.set_password(key, account, refresh_token)
    if meta is not None:
        keyring.set_password(key + META_SUFFIX, account, json.dumps(meta))


def get_refresh_token(provider: str, account: str) -> Optional[str]:
    if keyring is None:
        raise RuntimeError("keyring package is not available; install python-keyring")
    key = f"{SERVICE_NAME}.{provider}"
    return keyring.get_password(key, account)


def delete_refresh_token(provider: str, account: str) -> None:
    if keyring is None:
        raise RuntimeError("keyring package is not available; install python-keyring")
    key = f"{SERVICE_NAME}.{provider}"
    try:
        keyring.delete_password(key, account)
    except Exception:
        # keyring implementations raise different exceptions when missing
        pass


def get_metadata(provider: str, account: str) -> Optional[dict]:
    if keyring is None:
        raise RuntimeError("keyring package is not available; install python-keyring")
    key = f"{SERVICE_NAME}.{provider}" + META_SUFFIX
    raw = keyring.get_password(key, account)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


__all__ = ["save_refresh_token", "get_refresh_token", "delete_refresh_token", "get_metadata"]
