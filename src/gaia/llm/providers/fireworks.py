# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA's own Fireworks key, and a check that Fireworks accepts it.

GAIA reaches Fireworks through Lemonade's cloud routing. Nothing here routes
inference, and ``create_client("fireworks")`` does not exist. This module
resolves GAIA's Fireworks key (argument, environment, then keyring) and asks
Fireworks for its model list, so a rejected key can be told apart from
Fireworks failing to answer.

Needs a Fireworks key of GAIA's own: the one Lemonade uses lives in its process
and is not readable from here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import requests

from gaia.llm.retry import http_body_text

#: Fireworks' OpenAI-compatible endpoint — the same base Lemonade routes to.
FIREWORKS_BASE_URL = "https://api.fireworks.ai/inference/v1"

#: Checked in order. The second is Lemonade's own name for the same secret, so a
#: host already configured for Lemonade's cloud routing needs no new variable.
API_KEY_ENV_VARS = ("FIREWORKS_API_KEY", "LEMONADE_FIREWORKS_API_KEY")

#: Keyring slot, so the key survives a restart without living in a shell profile
#: or a file. Written with ``gaia.connectors.store.save_secret``.
API_KEY_SECRET_NAME = "fireworks_api_key"


#: Prefix Lemonade's cloud routing expects on a Fireworks model id.
LEMONADE_PREFIX = "fireworks."

_MODELS_PATH = "/models"
_LIST_CHECK_TIMEOUT = 10


class FireworksError(RuntimeError):
    """A Fireworks request failed, with what to do about it."""


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


class ModelListState(Enum):
    """What Fireworks said when asked for its model list."""

    OK = "ok"
    REJECTED = "rejected"
    FAILING = "failing"


@dataclass(frozen=True)
class ModelListCheck:
    state: ModelListState
    detail: str = ""


def check_model_list(
    api_key: str, *, timeout: int = _LIST_CHECK_TIMEOUT
) -> ModelListCheck:
    """Tell a rejected key apart from Fireworks failing to list its models."""
    url = FIREWORKS_BASE_URL + _MODELS_PATH
    try:
        response = requests.get(
            url, headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout
        )
    except requests.RequestException as e:
        return ModelListCheck(
            ModelListState.FAILING,
            f"could not reach Fireworks ({type(e).__name__})",
        )
    if response.status_code in (401, 403):
        return ModelListCheck(ModelListState.REJECTED)
    if response.status_code == 200:
        return ModelListCheck(ModelListState.OK)
    # Redact before cutting, or a key echoed across the cut leaks its prefix.
    body = http_body_text(response).replace(api_key, "[key]")[:200].strip()
    return ModelListCheck(
        ModelListState.FAILING, f"HTTP {response.status_code}: {body}"
    )
