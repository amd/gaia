# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The AMD LLM gateway's own per-model prices, kept with the time they were read.

The gateway's ``/models`` lists a ``pricing`` object per model (USD per million
tokens). Lemonade's model list doesn't pass it through, so GAIA reads it from
the gateway and keeps it in ``gateway-prices.json`` in the GAIA folder. A model
the gateway gives no price for has none here: its cost is unknown, never $0.
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from gaia.logger import get_logger

log = get_logger(__name__)

#: Prices older than this are read again from the gateway before use.
MAX_AGE_S = 24 * 3600
_TIMEOUT_S = 12
#: Gateway pricing field -> GAIA's name for it.
_FIELDS = {
    "input_per_million": "input_per_million",
    "output_per_million": "output_per_million",
    "cache_read_input_per_million": "cache_read_per_million",
    "cache_creation_input_per_million": "cache_write_per_million",
}


class GatewayPriceError(RuntimeError):
    """The gateway's prices could not be read or kept; the message says why."""


def prices_file() -> Path:
    from gaia import config as config_mod

    return Path(config_mod.GAIA_CONFIG_DIR) / "gateway-prices.json"


def _rate(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def parse_prices(body: Any) -> Dict[str, Dict[str, Any]]:
    """``amd.<id>`` -> price card, for every model the payload prices.

    A model needs both an input and an output rate to have a price.
    """
    from gaia.llm.gateway import GATEWAY_PROVIDER

    entries = body.get("data") if isinstance(body, dict) else None
    if not isinstance(entries, list):
        raise GatewayPriceError(
            "The gateway's /models answer has no 'data' list, so its prices "
            "could not be read."
        )
    prices: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            continue
        pricing = entry.get("pricing")
        if not isinstance(pricing, dict):
            continue
        card = {ours: _rate(pricing.get(theirs)) for theirs, ours in _FIELDS.items()}
        if card["input_per_million"] is None or card["output_per_million"] is None:
            continue
        source = pricing.get("source")
        card["source"] = source if isinstance(source, str) else None
        prices[f"{GATEWAY_PROVIDER}.{entry['id']}"] = card
    return prices


def record(body: Any, base_url: str) -> Dict[str, Any]:
    """Keep the prices in a ``/models`` payload; returns what was saved."""
    doc = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "fetched_at_epoch": time.time(),
        "base_url": base_url,
        "models": parse_prices(body),
    }
    path = prices_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        raise GatewayPriceError(
            f"Could not save the gateway's prices to {path}: {e}. Check that the "
            "GAIA folder is writable."
        ) from e
    return doc


def load() -> Optional[Dict[str, Any]]:
    """The kept prices, or None when none were read yet."""
    path = prices_file()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise GatewayPriceError(
            f"The gateway prices at {path} could not be read ({e}). Delete the "
            "file; GAIA reads the prices again."
        ) from e
    if not isinstance(doc, dict) or not isinstance(doc.get("models"), dict):
        raise GatewayPriceError(
            f"The gateway prices at {path} are not in GAIA's format. Delete the "
            "file; GAIA reads the prices again."
        )
    return doc


def refresh() -> Dict[str, Any]:
    """Read the prices from the gateway's ``/models`` and keep them."""
    from gaia.llm.gateway import (
        DEFAULT_AUTH_HEADER_NAME,
        DEFAULT_AUTH_HEADER_PREFIX,
        GatewayError,
        GatewayState,
        resolve_gateway_token_with_source,
    )
    from gaia.llm.local_only import cloud_blocked

    if cloud_blocked():
        raise GatewayPriceError(
            "Local only is on, so GAIA did not ask the gateway for its prices."
        )
    try:
        base_url = GatewayState.load().base_url
        token, _ = resolve_gateway_token_with_source()
    except GatewayError as e:
        raise GatewayPriceError(f"Could not read the gateway's prices: {e}") from e
    url = f"{base_url.rstrip('/')}/models"
    if not url.lower().startswith("https://"):
        raise GatewayPriceError(
            f"Not sending the gateway key to {url} to read prices: it isn't HTTPS."
        )
    try:
        response = requests.get(
            url,
            headers={DEFAULT_AUTH_HEADER_NAME: f"{DEFAULT_AUTH_HEADER_PREFIX}{token}"},
            timeout=_TIMEOUT_S,
            allow_redirects=False,
        )
    except requests.exceptions.RequestException as e:
        raise GatewayPriceError(
            f"Could not reach {url} to read the gateway's prices: {e}. Check "
            "that you are on the network or VPN that can see it."
        ) from e
    if response.status_code != 200:
        raise GatewayPriceError(
            f"The gateway answered {url} with HTTP {response.status_code}, so its "
            "prices could not be read. Check that your gateway key is valid."
        )
    try:
        body = response.json()
    except ValueError as e:
        raise GatewayPriceError(f"{url} did not return JSON: {e}") from e
    return record(body, base_url)


def ensure_fresh(max_age_s: float = MAX_AGE_S) -> Dict[str, Any]:
    """The kept prices, read again from the gateway when absent or older than *max_age_s*."""
    doc = load()
    age = None if doc is None else time.time() - float(doc.get("fetched_at_epoch", 0))
    if doc is None or age is None or age > max_age_s:
        doc = refresh()
        log.info("Read prices for %d gateway models", len(doc["models"]))
    return doc


def gateway_price(model_id: str) -> Optional[Dict[str, Any]]:
    """*model_id*'s kept gateway price, or None when the gateway gave none."""
    doc = load()
    if doc is None:
        return None
    card = doc["models"].get(model_id)
    return dict(card) if isinstance(card, dict) else None
