# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""What cloud models cost, what this month's cloud chats spent, and the limit.

Prices are list prices from the one rate card GAIA keeps
(:mod:`gaia.eval.bench.metering`), so the price shown before a model is picked,
the cost recorded after a turn, and the turn's own estimate all agree. A model
with no card has an unknown price: it is shown as unknown and counted apart,
never as $0.

The limit applies to Agent UI chats. Once this month's known spending reaches
it, a cloud turn is refused with how to go on, until the user raises the limit
or allows more for the month.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Dict, List, Optional

from gaia.logger import get_logger

log = get_logger(__name__)

#: Settings keys in the Agent UI database.
LIMIT_SETTING = "cloud_spend_limit_usd"
ALLOWED_MONTH_SETTING = "cloud_spend_allowed_month"
#: Share of the limit at which the UI warns.
WARN_FRACTION = 0.8
#: Where the prices come from, shown next to them.
PRICE_SOURCE = "Fireworks AI list price, checked 2026-09-19"


def current_month(now: Optional[dt.datetime] = None) -> str:
    """``YYYY-MM`` in local time: the month a user's limit covers."""
    return (now or dt.datetime.now()).strftime("%Y-%m")


def cloud_provider(model_id: Optional[str]) -> Optional[str]:
    """``fireworks`` / ``amd`` for a cloud model, None for a local one."""
    if not model_id:
        return None
    from ._chat_helpers import _ui_cloud_provider

    return _ui_cloud_provider(model_id)


def price_card(model_id: str) -> Optional[Dict[str, Any]]:
    """List price per million tokens for *model_id*, or None when unknown."""
    if cloud_provider(model_id) != "fireworks":
        # The gateway's prices are set by the organization that runs it.
        return None
    from gaia.eval.bench.metering import rate_card

    card = rate_card(model_id)
    if card is None:
        return None
    uncached, cached, output = card
    return {
        "input_per_million": uncached,
        "cached_input_per_million": cached,
        "output_per_million": output,
        "source": PRICE_SOURCE,
    }


def turn_cost(model_id: str, input_tokens: int, output_tokens: int) -> Optional[float]:
    """List-price dollars for one turn, without cache discounts; None if unknown."""
    if price_card(model_id) is None:
        return None
    from gaia.eval.bench.metering import price

    return price(model_id, input_tokens, 0, output_tokens)


def record_turn(db, model_id: Optional[str], stats: Optional[Dict[str, Any]]) -> None:
    """Add a finished cloud turn to this month's spending."""
    provider = cloud_provider(model_id)
    if provider is None:
        return
    stats = stats or {}
    inp, out = stats.get("input_tokens"), stats.get("output_tokens")
    usd = None
    if isinstance(inp, int) and isinstance(out, int):
        usd = turn_cost(model_id, inp, out)
    else:
        # Counted as a turn of unknown cost, so the total never looks smaller.
        log.warning(
            "Cloud turn on %s reported no token counts; its cost is unknown.", model_id
        )
    db.add_cloud_spend(
        current_month(),
        model_id,
        provider,
        inp if isinstance(inp, int) else None,
        out if isinstance(out, int) else None,
        usd,
    )


def get_limit(db) -> Optional[float]:
    raw = db.get_setting(LIMIT_SETTING)
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError as e:
        raise ValueError(
            f"The saved monthly spending limit {raw!r} is not a number. Set it "
            "again in Settings › Model."
        ) from e
    return value


def set_limit(db, usd: Optional[float]) -> None:
    if usd is not None and usd <= 0:
        raise ValueError(
            "A spending limit must be more than $0. Leave it empty for none."
        )
    db.set_setting(LIMIT_SETTING, None if usd is None else f"{usd:.2f}")


def allow_this_month(db) -> None:
    db.set_setting(ALLOWED_MONTH_SETTING, current_month())


def month_report(db, month: Optional[str] = None) -> Dict[str, Any]:
    """This month's cloud spending, the limit, and where it stands."""
    month = month or current_month()
    rows = db.cloud_spend_for_month(month)
    by_model: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        entry = by_model.setdefault(
            r["model"],
            {
                "model": r["model"],
                "provider": r["provider"],
                "turns": 0,
                "usd": 0.0,
                "unpriced_turns": 0,
            },
        )
        entry["turns"] += 1
        if r["usd"] is None:
            entry["unpriced_turns"] += 1
        else:
            entry["usd"] += r["usd"]
    total = sum(e["usd"] for e in by_model.values())
    unpriced = sum(e["unpriced_turns"] for e in by_model.values())
    limit = get_limit(db)
    allowed = db.get_setting(ALLOWED_MONTH_SETTING) == month
    if limit is None:
        state = "no_limit"
    elif total >= limit:
        state = "allowed_over" if allowed else "reached"
    elif total >= limit * WARN_FRACTION:
        state = "near"
    else:
        state = "ok"
    return {
        "month": month,
        "total_usd": round(total, 4),
        "turns": len(rows),
        "unpriced_turns": unpriced,
        "limit_usd": limit,
        "state": state,
        "by_model": sorted(by_model.values(), key=lambda e: -e["usd"]),
        "price_source": PRICE_SOURCE,
    }


def limit_refusal(db, model_id: Optional[str]) -> Optional[str]:
    """Why a turn on *model_id* must not start, or None when it may."""
    provider = cloud_provider(model_id)
    if provider is None:
        return None
    report = month_report(db)
    if report["state"] != "reached":
        return None
    name = "Fireworks AI" if provider == "fireworks" else "the AMD LLM Gateway"
    return (
        f"This month's cloud chats have cost about ${report['total_usd']:.2f} at "
        f"list price, which reaches your ${report['limit_usd']:.2f} monthly limit, "
        f"so GAIA stopped before sending this to {name}. To go on, raise the "
        "limit or allow more this month in Settings › Model, or pick a model "
        "that runs on this PC."
    )


def price_list(provider: str) -> List[Dict[str, Any]]:
    """Every priced model a provider offers, recommended first, before connecting."""
    from gaia.eval.bench.metering import RATES
    from gaia.llm.recommended_models import rank

    if provider != "fireworks":
        return []
    out = []
    for name in RATES:
        model_id = f"fireworks.{name}"
        ranked = rank(model_id)
        # A dated snapshot priced only for old comparisons is no longer offered.
        if ranked is None and re.search(r"-\d{4}$", name):
            continue
        out.append(
            {
                "model": model_id,
                "name": name,
                "rank": ranked[0] if ranked else None,
                "note": ranked[1].note if ranked else None,
                "price": price_card(model_id),
            }
        )
    out.sort(key=lambda m: (m["rank"] is None, m["rank"] or 0, m["name"]))
    return out
