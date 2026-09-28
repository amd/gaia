# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Ask Fireworks what it serves, rather than what a gateway chose to expose.

GAIA reaches Fireworks through Lemonade's cloud routing, and Lemonade advertises
a discovered subset — no Gemma at all and a few of the Qwen 3.x variants when
observed in 2026-09, subject to change (lemonade-sdk/lemonade#3570). A model missing
from that subset cannot be requested: Lemonade answers ``model_not_found``. So
"which models could I run" had no answer short of reading a vendor web page.

This module asks the provider directly. It only ever *reads* the catalogue —
inference still goes through the configured backend — so the two can be compared
and the gap named instead of guessed at. It is not an ``LLMClient`` provider:
nothing here routes inference, and ``create_client("fireworks")`` does not exist.

Needs a Fireworks key of GAIA's own: the one Lemonade uses lives in its process
and is not readable from here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

#: Fireworks' OpenAI-compatible endpoint — the same base Lemonade routes to.
FIREWORKS_BASE_URL = "https://api.fireworks.ai/inference/v1"

#: Checked in order. The second is Lemonade's own name for the same secret, so a
#: host already configured for Lemonade's cloud routing needs no new variable.
API_KEY_ENV_VARS = ("FIREWORKS_API_KEY", "LEMONADE_FIREWORKS_API_KEY")

#: Keyring slot, so the key survives a restart without living in a shell profile
#: or a file. Written with ``gaia.connectors.store.save_secret``.
API_KEY_SECRET_NAME = "fireworks_api_key"

#: Fireworks' control plane — the whole model library, most of which is NOT
#: servable per-token. See :func:`fetch_library`.
FIREWORKS_CONTROL_URL = "https://api.fireworks.ai/v1/accounts/fireworks/models"

#: Prefix Lemonade's cloud routing expects on a Fireworks model id.
LEMONADE_PREFIX = "fireworks."

_MODELS_PATH = "/models"
_DEFAULT_TIMEOUT = 30


class FireworksError(RuntimeError):
    """A Fireworks request failed, with what to do about it."""


@dataclass
class FireworksModel:
    """One model as Fireworks describes it."""

    id: str
    context_length: Optional[int] = None
    owned_by: str = ""
    serverless: Optional[bool] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def short_name(self) -> str:
        """Trailing segment — ``accounts/fireworks/models/x`` becomes ``x``."""
        return self.id.rsplit("/", 1)[-1]

    @property
    def lemonade_id(self) -> str:
        """The id to pass to a Lemonade-routed request.

        Lemonade accepts both the full path and the short name behind its
        ``fireworks.`` prefix; the short form is what its own listing shows,
        so it is what a user will recognise.
        """
        return LEMONADE_PREFIX + self.short_name


def resolve_fireworks_api_key(api_key: Optional[str] = None) -> Optional[str]:
    """Resolve the key: argument, environment, OS keyring, else ``None``.

    Whitespace-only values count as unset — sending ``Bearer `` produces a 401
    that reads like a wrong key rather than a missing one. An explicit argument
    or environment value wins over the stored one: a key someone set for this
    run must not be overridden by whatever the keyring happens to hold.

    Raises :class:`FireworksError` when nothing is set and the OS credential
    store cannot be read, naming the environment variable that avoids it.
    """
    if api_key is not None and api_key.strip():
        return api_key.strip()
    for name in API_KEY_ENV_VARS:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    from gaia.connectors.errors import ConnectorsError
    from gaia.connectors.store import peek_secret

    try:
        stored = peek_secret(API_KEY_SECRET_NAME)
    except ConnectorsError as e:
        raise FireworksError(
            f"No Fireworks key in the environment, and the OS credential store "
            f"could not be read to look for a saved one: {e} Set "
            f"{API_KEY_ENV_VARS[0]} to skip the credential store."
        ) from e
    return stored.strip() if stored and stored.strip() else None


def _missing_key_error() -> FireworksError:
    return FireworksError(
        "No Fireworks API key. The key Lemonade uses for cloud routing lives in "
        "its own process and cannot be read from here, so GAIA needs its own. "
        f"Set {API_KEY_ENV_VARS[0]} (or {API_KEY_ENV_VARS[1]}, which Lemonade "
        "already uses) to a key from https://app.fireworks.ai/settings/users/api-keys"
    )


def fetch_models(
    api_key: Optional[str] = None,
    *,
    base_url: str = FIREWORKS_BASE_URL,
    timeout: int = _DEFAULT_TIMEOUT,
) -> List[FireworksModel]:
    """Every model Fireworks reports for this account.

    Raises :class:`FireworksError` with an actionable message rather than
    returning an empty list — "no models" and "could not ask" are different
    answers, and a caller comparing this against a gateway's list would read a
    silent empty as "the gateway has them all".
    """
    key = resolve_fireworks_api_key(api_key)
    if not key:
        raise _missing_key_error()

    url = base_url.rstrip("/") + _MODELS_PATH
    try:
        response = requests.get(
            url, headers={"Authorization": f"Bearer {key}"}, timeout=timeout
        )
    except requests.RequestException as e:
        raise FireworksError(f"Could not reach Fireworks at {url}: {e}") from e

    if response.status_code == 401:
        raise FireworksError(
            f"Fireworks rejected the API key (401) at {url}. Check the value of "
            f"{API_KEY_ENV_VARS[0]} against https://app.fireworks.ai/settings/users/api-keys"
        )
    if response.status_code != 200:
        raise FireworksError(
            f"Fireworks returned HTTP {response.status_code} for {url}: "
            f"{response.text[:300]}"
        )

    try:
        payload = response.json()
    except ValueError as e:
        raise FireworksError(f"Fireworks returned a non-JSON body for {url}") from e

    entries = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise FireworksError(
            f"Unexpected catalogue shape from {url}: expected a list of models"
        )

    models: List[FireworksModel] = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        models.append(
            FireworksModel(
                id=str(entry["id"]),
                context_length=entry.get("context_length"),
                owned_by=str(entry.get("owned_by") or ""),
                serverless=entry.get("supports_serverless"),
                raw=entry,
            )
        )
    return sorted(models, key=lambda m: m.id)


def search(models: List[FireworksModel], term: str) -> List[FireworksModel]:
    """Case-insensitive substring match on the id."""
    needle = (term or "").strip().lower()
    if not needle:
        return list(models)
    return [m for m in models if needle in m.id.lower()]


def missing_from_gateway(
    catalogue: List[FireworksModel], gateway_ids: List[str]
) -> List[FireworksModel]:
    """Models Fireworks serves that the gateway never offered.

    The point of asking the provider directly: naming the gap turns "the model
    I want is not in the list" into a checkable claim.
    """
    known = set()
    for gid in gateway_ids or []:
        trimmed = (
            gid[len(LEMONADE_PREFIX) :] if gid.startswith(LEMONADE_PREFIX) else gid
        )
        known.add(trimmed)
        known.add(trimmed.rsplit("/", 1)[-1])
    return [m for m in catalogue if m.id not in known and m.short_name not in known]


@dataclass
class LibraryModel:
    """One model in Fireworks' library, servable or not."""

    name: str
    serverless: bool = False
    state: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def short_name(self) -> str:
        return self.name.rsplit("/", 1)[-1]


def fetch_library(
    api_key: Optional[str] = None,
    *,
    url: str = FIREWORKS_CONTROL_URL,
    timeout: int = _DEFAULT_TIMEOUT,
    page_size: int = 200,
) -> List[LibraryModel]:
    """Every model in Fireworks' library, with whether it is servable per-token.

    :func:`fetch_models` answers "what can I call right now"; this answers "what
    exists". The two differ by roughly an order of magnitude (observed 2026-09,
    subject to change) because most of the library is
    ``supports_serverless: false``: real models that need a dedicated
    deployment, billed by GPU-hour rather than per token.

    That distinction is the whole point of having this. A vendor page lists the
    library without marking it, so "Fireworks has Gemma 4 31B" is true and
    "I can call Gemma 4 31B per-token" is false, and nothing short of this flag
    tells the two apart.
    """
    key = resolve_fireworks_api_key(api_key)
    if not key:
        raise _missing_key_error()

    headers = {"Authorization": f"Bearer {key}"}
    models: List[LibraryModel] = []
    page_token: Optional[str] = None
    seen_tokens = set()
    while True:
        params: Dict[str, Any] = {"pageSize": page_size}
        if page_token:
            params["pageToken"] = page_token
        try:
            response = requests.get(
                url, headers=headers, params=params, timeout=timeout
            )
        except requests.RequestException as e:
            raise FireworksError(f"Could not reach Fireworks at {url}: {e}") from e
        if response.status_code != 200:
            raise FireworksError(
                f"Fireworks returned HTTP {response.status_code} for {url}: "
                f"{response.text[:300]}"
            )
        try:
            payload = response.json()
        except ValueError as e:
            raise FireworksError(f"Fireworks returned a non-JSON body for {url}") from e
        if not isinstance(payload, dict) or not isinstance(
            payload.get("models", []), list
        ):
            raise FireworksError(
                f"Unexpected library shape from {url}: expected an object with "
                "a 'models' list"
            )
        for entry in payload.get("models", []):
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            models.append(
                LibraryModel(
                    name=str(entry["name"]),
                    serverless=bool(entry.get("supportsServerless")),
                    state=str(entry.get("state") or ""),
                    raw=entry,
                )
            )
        page_token = payload.get("nextPageToken")
        if not page_token:
            break
        if page_token in seen_tokens:
            raise FireworksError(
                f"Fireworks repeated page token {page_token!r} at {url}; "
                "refusing to page forever"
            )
        seen_tokens.add(page_token)
    return sorted(models, key=lambda m: m.short_name)


def needs_deployment(library: List[LibraryModel], term: str = "") -> List[LibraryModel]:
    """Library models that exist but cannot be called per-token.

    The answer to "why can I not reach this model": not missing, not gated by
    the local gateway — simply not offered serverless.
    """
    needle = (term or "").strip().lower()
    return [
        m
        for m in library
        if not m.serverless and (not needle or needle in m.short_name.lower())
    ]
