# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Keep a cloud provider's API key across Lemonade restarts and TUI instances.

Lemonade holds a pasted key (``POST /api/v1/cloud/auth``) in process memory
only. So a Fireworks key typed into one TUI worked until that Lemonade stopped,
and every TUI started afterwards — or pointed at a freshly started embedded
Lemonade — had to be given it again.

This keeps a copy in the OS credential store (DPAPI / Keychain / SecretService,
never a GAIA file) and replays it into any Lemonade that has the provider
registered but no key. ``LEMONADE_<PROVIDER>_API_KEY`` in Lemonade's own
environment still takes precedence, and is the remedy where no credential store
exists.

The AMD gateway already had this (``gaia.llm.gateway``); its slot and replay are
reused here so both providers answer to one implementation.
"""

from __future__ import annotations

import sys
from typing import Any, Dict, Optional

import requests

from gaia.llm.gateway import GATEWAY_PROVIDER, GATEWAY_SECRET_NAME
from gaia.llm.lemonade_client import (
    LemonadeClient,
    lemonade_auth_headers,
    resolve_lemonade_api_key,
)
from gaia.llm.providers.fireworks import API_KEY_SECRET_NAME
from gaia.logger import get_logger

log = get_logger(__name__)

#: Credential-store slot per provider. Fireworks' slot is the one
#: ``gaia.llm.providers.fireworks`` already reads, so a key saved from the TUI
#: also serves GAIA's own Fireworks catalogue and metering.
_SECRET_NAMES = {
    "fireworks": API_KEY_SECRET_NAME,
    GATEWAY_PROVIDER: GATEWAY_SECRET_NAME,
}

PROVIDERS = tuple(_SECRET_NAMES)

_TIMEOUT = 60


class CloudKeyError(Exception):
    """A key could not be kept or replayed, with what to do about it."""


def _check(provider: str) -> str:
    if provider not in _SECRET_NAMES:
        raise CloudKeyError(
            f"Unknown cloud provider {provider!r}. Known: {', '.join(PROVIDERS)}."
        )
    return _SECRET_NAMES[provider]


def env_var_for(provider: str) -> str:
    """The variable Lemonade reads for this provider — safe to print."""
    _check(provider)
    return f"LEMONADE_{provider.upper()}_API_KEY"


def remember_key(provider: str, key: str) -> None:
    """Keep *key* in the OS credential store. Raises when it was not kept.

    Read back after writing: keyring's null backend (headless Linux,
    ``PYTHON_KEYRING_BACKEND=null``) accepts a write and stores nothing, and
    telling the user their key was saved right before asking for it again is
    the failure this module exists to remove.
    """
    from gaia.connectors.errors import ConnectorsError
    from gaia.connectors.store import peek_secret, save_secret

    slot = _check(provider)
    key = (key or "").strip()
    if not key:
        raise CloudKeyError("No key given, so there is nothing to keep.")
    try:
        save_secret(slot, key)
        kept = peek_secret(slot) == key
    except ConnectorsError as e:
        raise CloudKeyError(_no_store_message(provider, str(e))) from e
    if not kept:
        raise CloudKeyError(_no_store_message(provider, "the write was discarded"))


def recall_key(provider: str) -> Optional[str]:
    """The kept key, or None when none is stored or the store cannot be read.

    An unreadable store is "nothing stored": the caller's next step is to ask
    for the key, which is right either way. The reason is logged.
    """
    from gaia.connectors.errors import ConnectorsError
    from gaia.connectors.store import peek_secret

    slot = _check(provider)
    try:
        value = peek_secret(slot)
    except ConnectorsError as e:
        log.debug(f"Could not read the stored {provider} key: {e}")
        return None
    return value.strip() if value and value.strip() else None


def forget_key(provider: str) -> bool:
    """Remove the kept key. True when one was stored."""
    from gaia.connectors.store import delete_secret

    slot = _check(provider)
    had = recall_key(provider) is not None
    delete_secret(slot)
    return had


def ensure_authenticated(
    provider: str, client: Optional[LemonadeClient] = None
) -> bool:
    """Give Lemonade the kept key if it has the provider but no key.

    True when the provider is usable afterwards — models discovered, not merely
    a key held: Lemonade stores a key without checking it, so a revoked one
    reports authenticated and discovers nothing.
    """
    _check(provider)
    if provider == GATEWAY_PROVIDER:
        from gaia.llm.gateway import GatewayManager

        return GatewayManager(client).ensure_authenticated()

    base_url = (client or LemonadeClient(verbose=False)).base_url
    entry = _provider_entry(base_url, provider)
    if entry is None:
        return False
    if entry.get("env_var_set") or entry.get("runtime_key_set"):
        return bool(entry.get("models_discovered"))
    key = recall_key(provider)
    if not key:
        return False
    result = _request(
        base_url, "POST", "cloud/auth", {"provider": provider, "api_key": key}
    )
    del key
    if not result.get("models_discovered"):
        log.warning(
            f"The stored {provider} key was handed to Lemonade but no models were "
            f"discovered, so {provider} rejected it. Enter a current key in the "
            f"TUI's /provider panel."
        )
        return False
    return True


def _provider_entry(base_url: str, provider: str) -> Optional[Dict[str, Any]]:
    info = _request(base_url, "GET", "system-info")
    for entry in (info.get("cloud") or {}).get("providers") or []:
        if entry.get("name") == provider:
            return entry
    return None


def _request(
    base_url: str, method: str, path: str, payload: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    url = f"{base_url.rstrip('/')}/{path}"
    headers = {
        "Content-Type": "application/json",
        **lemonade_auth_headers(resolve_lemonade_api_key()),
    }
    try:
        response = requests.request(
            method, url, json=payload, headers=headers, timeout=_TIMEOUT
        )
    except requests.RequestException as e:
        raise CloudKeyError(f"Lemonade is not reachable at {base_url}: {e}") from e
    if response.status_code >= 400:
        # The body can echo the request; it is never included.
        raise CloudKeyError(
            f"Lemonade answered {method} /{path} with HTTP {response.status_code}."
        )
    try:
        return response.json() if response.content else {}
    except ValueError as e:
        raise CloudKeyError(f"Lemonade returned a non-JSON reply to /{path}.") from e


def _no_store_message(provider: str, reason: str) -> str:
    var = env_var_for(provider)
    export = (
        f"$env:{var} = '<your-key>'"
        if sys.platform == "win32"
        else f"export {var}=<your-key>"
    )
    return (
        f"The OS credential store did not keep the {provider} key ({reason}), so "
        f"it works for this Lemonade session only. To keep it, set it in "
        f"Lemonade's own environment before starting Lemonade: {export}"
    )


__all__ = [
    "CloudKeyError",
    "PROVIDERS",
    "ensure_authenticated",
    "env_var_for",
    "forget_key",
    "recall_key",
    "remember_key",
]
