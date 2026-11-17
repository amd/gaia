# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What a cloud model's tokens cost: the one place GAIA looks up list prices.

Fireworks models use GAIA's rate card, AMD LLM Gateway models the gateway's own
price list (as last read; see :mod:`gaia.llm.gateway_prices`), and direct
``claude-*`` models Anthropic's list price. A model with no known price has
none here: its cost is unknown, never $0. The eval code reads the same tables.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from gaia.logger import get_logger

log = get_logger(__name__)

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
ALIASES = {
    "accounts/fireworks/routers/glm-5p3-fast": "glm-5p3",
    "accounts/fireworks/routers/kimi-k3-fast": "kimi-k3",
    "glm-5p3-fast": "glm-5p3",
    "kimi-k3-fast": "kimi-k3",
}
#: A model priced at another's card, where it has none of its own.
PRICED_AS = {"qwen3p8-2p4t-a95b": "qwen3p8-max"}

# Anthropic list prices, per million tokens. ``cached_per_mtok`` absent =
# cached input bills at the input rate.
# https://www.anthropic.com/pricing (read 2026-11-09)
ANTHROPIC_PRICES: Dict[str, Dict[str, float]] = {
    # Claude 5 family
    "claude-opus-5-5": {
        "input_per_mtok": 4.00,
        "output_per_mtok": 20.00,
        "cached_per_mtok": 0.20,
    },
    "claude-sonnet-5-5": {
        "input_per_mtok": 2.00,
        "output_per_mtok": 10.00,
        "cached_per_mtok": 0.10,
    },
    # Prompts over 100K tokens bill at $0.50 / $2.50.
    "claude-haiku-5-5": {
        "input_per_mtok": 0.10,
        "output_per_mtok": 0.50,
        "cached_per_mtok": 0.01,
    },
    "claude-fable-5-1": {
        "input_per_mtok": 10.00,
        "output_per_mtok": 50.00,
        "cached_per_mtok": 0.25,
    },
    "claude-opus-5": {"input_per_mtok": 5.00, "output_per_mtok": 25.00},
    "claude-sonnet-5": {"input_per_mtok": 2.00, "output_per_mtok": 10.00},
    "claude-fable-5": {"input_per_mtok": 10.00, "output_per_mtok": 50.00},
    # Claude 4.x family
    "claude-opus-4-8": {"input_per_mtok": 5.00, "output_per_mtok": 25.00},
    "claude-opus-4-7": {"input_per_mtok": 5.00, "output_per_mtok": 25.00},
    "claude-opus-4-6": {"input_per_mtok": 5.00, "output_per_mtok": 25.00},
    "claude-opus-4.1": {"input_per_mtok": 15.00, "output_per_mtok": 75.00},
    "claude-opus-4": {"input_per_mtok": 15.00, "output_per_mtok": 75.00},
    "claude-haiku-4-5": {"input_per_mtok": 1.00, "output_per_mtok": 5.00},
    "claude-haiku-4-5-20251001": {"input_per_mtok": 1.00, "output_per_mtok": 5.00},
    "claude-sonnet-4-6": {"input_per_mtok": 3.00, "output_per_mtok": 15.00},
    "claude-sonnet-4.5": {"input_per_mtok": 3.00, "output_per_mtok": 15.00},
    "claude-sonnet-4-5-20250929": {"input_per_mtok": 3.00, "output_per_mtok": 15.00},
    "claude-sonnet-4": {"input_per_mtok": 3.00, "output_per_mtok": 15.00},
    "claude-sonnet-4-20250514": {"input_per_mtok": 3.00, "output_per_mtok": 15.00},
    # Claude 3.x family
    "claude-3-7-sonnet-20250219": {"input_per_mtok": 3.00, "output_per_mtok": 15.00},
    "claude-3-5-sonnet-20241022": {
        "input_per_mtok": 3.00,
        "output_per_mtok": 15.00,
    },  # deprecated
    "claude-3-5-haiku-20241022": {"input_per_mtok": 0.80, "output_per_mtok": 4.00},
    "claude-3-opus-20240229": {
        "input_per_mtok": 15.00,
        "output_per_mtok": 75.00,
    },  # deprecated
    "claude-3-haiku-20240307": {"input_per_mtok": 0.25, "output_per_mtok": 1.25},
}

#: Where each kind of price comes from, shown next to it.
FIREWORKS_PRICE_SOURCE = "Fireworks AI list price, checked 2026-09-19"
GATEWAY_PRICE_SOURCE = "the AMD LLM Gateway's own price list"
ANTHROPIC_PRICE_SOURCE = "Anthropic list price (anthropic.com/pricing)"


def bare_model(model: str) -> str:
    """``fireworks.glm-5p3-flash`` and ``accounts/fireworks/models/x`` -> the bare id."""
    name = model.removeprefix("fireworks.")
    if name.startswith("accounts/fireworks/routers/"):
        return name
    return name.rsplit("/", 1)[-1]


def rate_card(model: str) -> Optional[Tuple[float, float, float]]:
    """A Fireworks model's (uncached, cached, output) rates, or None."""
    name = bare_model(model)
    name = ALIASES.get(name, name)
    return RATES.get(PRICED_AS.get(name, name))


def price(
    model: str, input_tokens: int, cached_tokens: int, output_tokens: int
) -> Optional[float]:
    """Dollars for these tokens at a Fireworks *model*'s card; ``None`` when it has none."""
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


def cloud_provider(model_id: str) -> Optional[str]:
    """``fireworks``, ``amd`` or another cloud provider for *model_id*; None if local."""
    from gaia.llm.lemonade_client import cloud_model_provider, is_cloud_model

    metadata = {"recipe": "cloud"} if is_cloud_model(model_id) else None
    return cloud_model_provider(model_id, metadata)


def price_card(model_id: str) -> Optional[Dict[str, Any]]:
    """Price per million tokens for *model_id*, or None when unknown.

    ``cached_input_per_million`` is None when only the cached rate is unknown.
    """
    if model_id.startswith("claude-"):
        return _anthropic_price(model_id)
    provider = cloud_provider(model_id)
    if provider == "amd":
        return _gateway_price(model_id)
    if provider != "fireworks":
        return None
    card = rate_card(model_id)
    if card is None:
        return None
    uncached, cached, output = card
    return {
        "input_per_million": uncached,
        "cached_input_per_million": cached,
        "output_per_million": output,
        "source": FIREWORKS_PRICE_SOURCE,
    }


def model_price(model_id: str) -> Optional[Dict[str, Any]]:
    """:func:`price_card` with the cached rate named ``cache_read_per_million``."""
    card = price_card(model_id)
    if card is None:
        return None
    return {
        "input_per_million": card["input_per_million"],
        "output_per_million": card["output_per_million"],
        "cache_read_per_million": card["cached_input_per_million"],
        "source": card["source"],
    }


def turn_cost(
    model_id: str, input_tokens: int, output_tokens: int, cached_tokens: int = 0
) -> Optional[float]:
    """Dollars for one turn, cached input at the cached rate; None if unknown.

    Cached input on a model whose cached rate is unknown makes the cost unknown.
    """
    if not model_id.startswith("claude-") and cloud_provider(model_id) == "fireworks":
        return price(model_id, input_tokens, cached_tokens, output_tokens)
    card = price_card(model_id)
    if card is None:
        return None
    cached_rate = card["cached_input_per_million"]
    if cached_tokens and cached_rate is None:
        return None
    uncached = max(0, input_tokens - cached_tokens)
    return (
        uncached * card["input_per_million"]
        + cached_tokens * (cached_rate or 0.0)
        + output_tokens * card["output_per_million"]
    ) / 1e6


def _gateway_price(model_id: str) -> Optional[Dict[str, Any]]:
    from gaia.llm.gateway_prices import GatewayPriceError, gateway_price

    try:
        gateway = gateway_price(model_id)
    except GatewayPriceError as e:
        # Unknown, as with no price list; the hybrid settings report the fault.
        log.warning("No gateway price for %s: %s", model_id, e)
        return None
    if gateway is None:
        return None
    tier = f" ({gateway['source']})" if gateway.get("source") else ""
    return {
        "input_per_million": gateway["input_per_million"],
        "cached_input_per_million": gateway.get("cache_read_per_million"),
        "output_per_million": gateway["output_per_million"],
        "source": f"{GATEWAY_PRICE_SOURCE}{tier}",
    }


def _anthropic_price(model_id: str) -> Optional[Dict[str, Any]]:
    card = ANTHROPIC_PRICES.get(model_id)
    if card is None:
        return None
    return {
        "input_per_million": card["input_per_mtok"],
        "cached_input_per_million": card.get("cached_per_mtok", card["input_per_mtok"]),
        "output_per_million": card["output_per_mtok"],
        "source": ANTHROPIC_PRICE_SOURCE,
    }
