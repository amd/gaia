# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The one rate card: list prices per model, and what a token count costs.

Read by the agent loop (``cost_usd`` on a turn result, a turn's dollar budget),
the eval harness and the Agent UI. A model with no published card has no price
here: its cost is ``None``, never a guess.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

#: USD per million tokens: (uncached input, cached input, output). Checked
#: against https://docs.fireworks.ai/serverless/pricing on 2026-09-19. A model
#: with no published card is left out on purpose: its cost reads "n/a".
RATES: Dict[str, Tuple[float, float, float]] = {
    "deepseek-v4p1-flash": (0.30, 0.006, 1.20),
    # A dated snapshot no longer on the list, priced at its successor's card so
    # a comparison never flatters it.
    "deepseek-v4-flash-0731": (0.30, 0.006, 1.20),
    "deepseek-v4-pro-0813": (1.32, 0.044, 3.96),
    "glm-5p3-flash": (0.15, 0.03, 0.50),
    "glm-5p3": (1.40, 0.26, 4.40),
    "minimax-m3": (0.30, 0.06, 1.20),
    "kimi-k2p7-code": (0.95, 0.19, 4.00),
    "nemotron-3-ultra-nvfp4": (0.60, 0.12, 2.40),
    "kimi-k3": (3.00, 0.30, 15.00),
    "qwen3p8-max": (2.00, 0.25, 6.00),
}
#: Router aliases resolve server-side to the plain model and share its card.
_ALIASES = {
    "accounts/fireworks/routers/glm-5p3-fast": "glm-5p3",
    "accounts/fireworks/routers/kimi-k3-fast": "kimi-k3",
    "glm-5p3-fast": "glm-5p3",
    "kimi-k3-fast": "kimi-k3",
}
#: A model priced at another's card, where it has none of its own.
PRICED_AS = {"qwen3p8-2p4t-a95b": "qwen3p8-max"}


def bare_model(model: str) -> str:
    """``fireworks.glm-5p3-flash`` and ``accounts/fireworks/models/x`` -> the bare id."""
    name = model.removeprefix("fireworks.")
    if name.startswith("accounts/fireworks/routers/"):
        return name
    return name.rsplit("/", 1)[-1]


def rate_card(model: str) -> Optional[Tuple[float, float, float]]:
    name = bare_model(model)
    name = _ALIASES.get(name, name)
    return RATES.get(PRICED_AS.get(name, name))


def price(
    model: str, input_tokens: int, cached_tokens: int, output_tokens: int
) -> Optional[float]:
    """Dollars for these tokens at *model*'s card; ``None`` when it has none."""
    card = rate_card(model)
    if card is None:
        return None
    uncached_rate, cached_rate, output_rate = card
    uncached = max(0, input_tokens - cached_tokens)
    return (
        uncached * uncached_rate
        + cached_tokens * cached_rate
        + output_tokens * output_rate
    ) / 1e6
