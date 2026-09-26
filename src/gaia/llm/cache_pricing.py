# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Cached-versus-uncached input price ratios for the cloud models GAIA routes.

Evicting old tool results from the re-sent context changes the prompt prefix,
so the next call re-reads everything after the eviction point at the uncached
price once. Whether that pays back depends on how much cheaper a cached token
is: the ratio here is ``cached price / uncached price`` for input tokens, and
``context_eviction="auto"`` turns eviction on only when it is at least
:data:`AUTO_EVICTION_MIN_RATIO`. An unknown model is not guessed at.
"""

from typing import Dict, Optional

#: ``cached / uncached`` input price, keyed by the bare model id (no provider prefix).
CACHED_INPUT_PRICE_RATIO: Dict[str, float] = {
    "kimi-k2p7-code": 0.20,
    "kimi-k3": 0.10,
    "glm-5p3-flash": 0.20,
    "glm-5p3": 0.19,
    "deepseek-v4p1-flash": 0.02,
    "deepseek-v4-pro-0813": 0.03,
}

#: Below this, an eviction's cache break costs more than the tokens it saves.
AUTO_EVICTION_MIN_RATIO = 0.1

_PROVIDER_PREFIXES = ("fireworks.",)


def bare_model_id(model_id: str) -> str:
    """The model id without a routing prefix such as ``fireworks.``."""
    for prefix in _PROVIDER_PREFIXES:
        if model_id.startswith(prefix):
            return model_id[len(prefix) :]
    return model_id


def cached_input_price_ratio(model_id: Optional[str]) -> Optional[float]:
    """``cached / uncached`` input price for ``model_id``, or ``None`` when unknown."""
    if not model_id:
        return None
    return CACHED_INPUT_PRICE_RATIO.get(bare_model_id(model_id))
