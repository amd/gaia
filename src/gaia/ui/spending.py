# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""What cloud models cost, what this month's cloud chats spent, and the limit.

Prices are list prices from the one rate card GAIA keeps
(:mod:`gaia.eval.bench.metering`), cached input at its cached rate, so the price
shown before a model is picked, the cost recorded after a turn, and the turn's
own estimate all agree. A model
with no card has an unknown price: it is shown as unknown and counted apart,
never as $0. A priced turn whose usage never came back (a stopped answer) is
counted apart the same way, as unreported rather than unpriced.

The limit applies to Agent UI chats. Once this month's known spending reaches
it, a cloud turn is refused with how to go on, until the user raises the limit
or allows more for the month.
"""

from __future__ import annotations

import datetime as dt
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
#: Where prices come from when no priced model is named.
ANY_PRICE_SOURCE = (
    "each model's list price: Fireworks AI's, the AMD LLM Gateway's own price "
    "list, or Anthropic's"
)


def current_month(now: Optional[dt.datetime] = None) -> str:
    """``YYYY-MM`` in local time: the month a user's limit covers."""
    return (now or dt.datetime.now()).strftime("%Y-%m")


def cloud_provider(model_id: Optional[str]) -> Optional[str]:
    """``fireworks`` / ``amd`` for a cloud model, None for a local one."""
    if not model_id:
        return None
    from ._chat_helpers import _ui_cloud_provider

    return _ui_cloud_provider(model_id)


#: The provider a direct ``claude-*`` turn is billed by.
ANTHROPIC = "anthropic"


def billed_provider(model_id: Optional[str]) -> Optional[str]:
    """Who bills a turn on *model_id*: a cloud provider, ``anthropic``, or None (local)."""
    if model_id and model_id.startswith("claude-"):
        return ANTHROPIC
    return cloud_provider(model_id)


def price_card(model_id: str) -> Optional[Dict[str, Any]]:
    """Price per million tokens for *model_id*, or None when unknown.

    Fireworks models use the shared rate card, AMD LLM Gateway models the
    gateway's own price list (as last read; see :mod:`gaia.llm.gateway_prices`),
    and direct ``claude-*`` models Anthropic's list price.
    ``cached_input_per_million`` is None when only the cached rate is unknown.
    """
    if model_id.startswith("claude-"):
        return _anthropic_price(model_id)
    provider = cloud_provider(model_id)
    if provider == "amd":
        return _gateway_price(model_id)
    if provider != "fireworks":
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


def turn_cost(
    model_id: str, input_tokens: int, output_tokens: int, cached_tokens: int = 0
) -> Optional[float]:
    """Dollars for one turn, cached input at the cached rate; None if unknown.

    Cached input on a model whose cached rate is unknown makes the cost unknown.
    """
    if cloud_provider(model_id) == "fireworks":
        from gaia.eval.bench.metering import price

        # The one rate card the benchmark meters with.
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


#: The saved cost of a cloud turn no reply came from: not a stopped answer.
NO_REPLY_COST_KIND = "no_reply"


def never_answered(failed: bool, stats: Optional[Dict[str, Any]]) -> bool:
    """Whether a turn failed with no tokens reported: no reply, so nothing billed.

    The one rule for a turn left out of spending, in Settings and the composer.
    """
    stats = stats or {}
    inp, out = stats.get("input_tokens"), stats.get("output_tokens")
    return failed and not (isinstance(inp, int) and isinstance(out, int) and inp + out)


def mark_no_reply(summary: Dict[str, Any]) -> None:
    """Save a cloud turn's cost as none: no reply came from it."""
    cost = summary.get("cost")
    if isinstance(cost, dict) and cost.get("kind") in ("unreported", "unknown"):
        summary["cost"] = {"kind": NO_REPLY_COST_KIND}


def record_turn(
    db,
    model_id: Optional[str],
    stats: Optional[Dict[str, Any]],
    failed: bool = False,
    route: Optional[Dict[str, Any]] = None,
) -> None:
    """Add a finished cloud turn to this month's spending.

    A *failed* turn that reported no tokens never got a reply, so it is left out.
    A hybrid turn's *route* adds its cloud classifier's call as well.
    """
    if route:
        record_classifier(db, route)
    provider = billed_provider(model_id)
    if provider is None:
        return
    stats = stats or {}
    inp, out = stats.get("input_tokens"), stats.get("output_tokens")
    if never_answered(failed, stats):
        return
    usd = None
    if isinstance(inp, int) and isinstance(out, int):
        cached = stats.get("cached_tokens")
        # A count that can't be part of this input is priced as uncached.
        if isinstance(cached, bool) or not isinstance(cached, int):
            cached = 0
        cached = cached if 0 <= cached <= inp else 0
        # Priced as each answer's own estimate is, so the month and "today" agree.
        usd = turn_cost(model_id, inp, out, cached)
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


def record_classifier(db, route: Dict[str, Any]) -> None:
    """Add a hybrid classifier call on a cloud model to this month's spending.

    A call that reported no tokens got no answer (it failed), so it is left out.
    """
    model = route.get("classifier_model")
    usage = route.get("classifier_usage")
    if not isinstance(model, str) or billed_provider(model) is None:
        return
    counts = _token_counts(usage) if isinstance(usage, dict) else None
    if counts is None:
        return
    inp, out, cached = counts
    db.add_cloud_spend(
        current_month(),
        model,
        billed_provider(model),
        inp,
        out,
        turn_cost(model, inp, out, cached),
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
                "unreported_turns": 0,
                "price_source": _source(r["model"]),
            },
        )
        entry["turns"] += 1
        if r["usd"] is not None:
            entry["usd"] += r["usd"]
        elif price_card(r["model"]) is None:
            entry["unpriced_turns"] += 1
        else:
            entry["unreported_turns"] += 1
    total = sum(e["usd"] for e in by_model.values())
    unpriced = sum(e["unpriced_turns"] for e in by_model.values())
    unreported = sum(e["unreported_turns"] for e in by_model.values())
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
        "unreported_turns": unreported,
        "limit_usd": limit,
        "state": state,
        "by_model": sorted(by_model.values(), key=lambda e: -e["usd"]),
        "price_source": _sources(e["price_source"] for e in by_model.values()),
    }


def _source(model_id: str) -> Optional[str]:
    card = price_card(model_id)
    return card["source"] if card else None


def _sources(sources) -> str:
    """The distinct price sources named, or :data:`ANY_PRICE_SOURCE` for none."""
    named = sorted({s for s in sources if s})
    return "; ".join(named) if named else ANY_PRICE_SOURCE


def _parse_instant(text: str) -> dt.datetime:
    """An ISO timestamp as an aware datetime; a naive one is UTC, as SQLite writes it."""
    value = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def _answered_at(answer: Dict[str, Any]) -> dt.datetime:
    try:
        return _parse_instant(answer["created_at"])
    except (TypeError, ValueError) as e:
        raise RuntimeError(
            f"Message {answer['message_id']} in chat {answer['session_id']} has an "
            f"unreadable time {answer['created_at']!r}; inspect the messages table "
            "in the chat database."
        ) from e


def _cloud_answers(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The answers a cloud model gave; a local answer cost nothing and is left out.

    So is a turn no reply came from, as the month leaves it out.
    """
    return [
        r
        for r in rows
        if r["cost"].get("kind") == "estimate"
        or (r["remote"] is True and r["cost"].get("kind") != NO_REPLY_COST_KIND)
    ]


def _totals(answers: List[Dict[str, Any]]) -> Dict[str, Any]:
    priced = [a["cost"]["usd"] for a in answers if a["cost"].get("kind") == "estimate"]
    return {
        "usd": round(sum(priced), 6),
        "answers": len(answers),
        # Stopped or unpriced answers are counted apart, never as $0.
        "unpriced_answers": len(answers) - len(priced),
    }


def chat_spend(
    db, session_id: str, model_id: Optional[str], since: str
) -> Dict[str, Any]:
    """What this chat's cloud answers cost, and every chat's since *since*.

    *since* is the start of the viewer's local day as an ISO instant, so "today"
    follows the user's clock. This chat's costs are the ones each answer's
    details show; today's are every turn billed since *since*, the record the
    month total reads, so an answer edited away or retried still counts.
    ``paid`` says whether *model_id* is a priced cloud model, the only time the
    composer shows spending.
    """
    try:
        start = _parse_instant(since)
    except ValueError as e:
        raise ValueError(
            f"'since' must be an ISO timestamp such as 2026-11-05T08:00:00Z, got {since!r}."
        ) from e
    start_utc = start.astimezone(dt.timezone.utc)
    # Sorted by instant: old and new timestamp formats don't sort together as text.
    chat = sorted(
        _cloud_answers(db.answer_costs(session_id=session_id)), key=_answered_at
    )
    # Billed turns, as the month total counts them: an answer edited away,
    # retried or deleted was still billed.
    billed = db.cloud_spend_since(start_utc)
    priced = [r["usd"] for r in billed if r["usd"] is not None]
    answered_today = [
        a
        for a in _cloud_answers(db.answer_costs(since_day=start_utc.date().isoformat()))
        if _answered_at(a) >= start_utc
    ]
    return {
        "paid": bool(model_id) and price_card(model_id) is not None,
        "model": model_id,
        "chat": {
            **_totals(chat),
            "items": [
                {
                    "message_id": a["message_id"],
                    "created_at": a["created_at"],
                    "model": a["model"],
                    "kind": a["cost"].get("kind"),
                    "usd": a["cost"].get("usd"),
                }
                for a in chat
            ],
        },
        "today": {
            "usd": round(sum(priced), 6),
            "answers": len(billed),
            "unpriced_answers": len(billed) - len(priced),
            "chats": len({a["session_id"] for a in answered_today}),
            "since": start_utc.isoformat(),
        },
        "price_source": _sources([_source(model_id)] if model_id else []),
    }


def limit_refusal(db, model_id: Optional[str]) -> Optional[str]:
    """Why a turn on *model_id* must not start, or None when it may."""
    from gaia.llm.hybrid import candidate_models

    provider = next(
        filter(None, (billed_provider(m) for m in candidate_models(model_id))), None
    )
    if provider is None:
        return None
    report = month_report(db)
    if report["state"] != "reached":
        return None
    name = {
        "fireworks": "Fireworks AI",
        ANTHROPIC: "Anthropic",
    }.get(provider, "the AMD LLM Gateway")
    return (
        f"This month's cloud chats have cost about ${report['total_usd']:.2f} at "
        f"list price, which reaches your ${report['limit_usd']:.2f} monthly limit, "
        f"so GAIA stopped before sending this to {name}. To go on, raise the "
        "limit or allow more this month in Settings › Model, or pick a model "
        "that runs on this PC."
    )


def price_list(provider: str) -> List[Dict[str, Any]]:
    """Every priced model a provider offers, recommended first, before connecting."""
    from gaia.llm.fireworks_known_models import offered_models
    from gaia.llm.recommended_models import rank

    if provider != "fireworks":
        return []
    out = []
    for name in offered_models():
        model_id = f"fireworks.{name}"
        ranked = rank(model_id)
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


def _token_counts(stats: Dict[str, Any]) -> Optional[tuple]:
    """(input, output, cached) a turn reported, or None when it reported none."""
    inp, out = stats.get("input_tokens"), stats.get("output_tokens")
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (inp, out)):
        return None
    cached = stats.get("cached_tokens")
    if (
        isinstance(cached, bool)
        or not isinstance(cached, int)
        or not 0 <= cached <= inp
    ):
        cached = 0
    return inp, out, cached


#: Where a gateway model's price comes from.
GATEWAY_PRICE_SOURCE = "the AMD LLM Gateway's own price list"


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


def _anthropic_price(model_id: str) -> Optional[Dict[str, Any]]:
    """Anthropic's list price for a direct ``claude-*`` model, or None."""
    from gaia.eval.config import MODEL_PRICING

    card = MODEL_PRICING.get(model_id)
    if card is None:
        return None
    return {
        "input_per_million": card["input_per_mtok"],
        # Anthropic's table: no cached rate means cached input bills as input.
        "cached_input_per_million": card.get("cached_per_mtok", card["input_per_mtok"]),
        "output_per_million": card["output_per_mtok"],
        "source": ANTHROPIC_PRICE_SOURCE,
    }


#: Where a direct Claude model's price comes from.
ANTHROPIC_PRICE_SOURCE = "Anthropic list price (anthropic.com/pricing)"


def _list_cost(model: str, counts: Optional[tuple]) -> Optional[float]:
    """Dollars for *counts* on *model* at its price; a local model costs nothing.

    Usage records no cache writes, so none are charged.
    """
    if billed_provider(model) is None:
        return 0.0
    if not counts:
        return None
    inp, out, cached = counts
    return turn_cost(model, inp, out, cached)


def _classifier_cost(route: Dict[str, Any]) -> Optional[float]:
    """What the route's classifier call cost; 0 for none or a local one, None if unknown."""
    model = route.get("classifier_model")
    if not isinstance(model, str) or billed_provider(model) is None:
        return 0.0
    usage = route.get("classifier_usage")
    if not isinstance(usage, dict):
        # Asked but reported nothing: a failed call is not billed.
        return 0.0 if not route.get("classified") else None
    return _list_cost(model, _token_counts(usage))


#: What :func:`hybrid_stats` covers: answers in the Agent UI's chat database.
STATS_SCOPE = "agent_ui_chats"
#: Shown verbatim by the apps next to the numbers.
STATS_SCOPE_LABEL = (
    "Agent UI chats only; turns from the terminal UI and one-off command-line "
    "prompts aren't counted yet"
)


def hybrid_stats(db, settings) -> Dict[str, Any]:
    """How every recorded ``hybrid`` turn routed, and what it cost against all-strong.

    Covers every hybrid answer still in the chat database (deleting a chat
    removes its turns); ``since`` is when the oldest was answered. Costs are
    the models' prices (:func:`model_price`): ``cost`` is what the turns cost on
    the models they ran on, ``cost_if_all_strong`` the same tokens at the strong
    model's price (the one *settings* name now). Either is None, never $0, when
    any turn's price is unknown; ``unpriced_turns`` counts those turns. ``cost``
    includes each turn's classifier call, which an all-strong session wouldn't make.
    """
    turns = cheap = failed = unpriced = 0
    cost: Optional[float] = 0.0
    all_strong: Optional[float] = 0.0
    since = None
    for row in db.hybrid_turns():
        route = row["stats"]["route"]
        model = route.get("model")
        if not isinstance(model, str) or not model:
            continue
        turns += 1
        since = since or _parse_instant(row["created_at"]).isoformat()
        # Records from before ``tier`` existed are told apart by today's cheap model.
        tier = route.get("tier") or (
            "cheap" if model == settings.cheap_model else "strong"
        )
        cheap += tier == "cheap"
        # Only a failed classification carries a fix; an empty request does not.
        if not route.get("classified") and route.get("fix"):
            failed += 1
        counts = _token_counts(row["stats"])
        ran = _list_cost(model, counts)
        strong = _list_cost(settings.strong_model, counts)
        classifier = _classifier_cost(route)
        ran = None if ran is None or classifier is None else ran + classifier
        if ran is None or strong is None:
            unpriced += 1
        cost = None if cost is None or ran is None else cost + ran
        all_strong = (
            None if all_strong is None or strong is None else all_strong + strong
        )
    saving = None
    if cost is not None and all_strong:
        saving = round(100 * (1 - cost / all_strong), 1)
    return {
        "since": since,
        "turns": turns,
        "cheap_turns": cheap,
        "strong_turns": turns - cheap,
        "failed_closed": failed,
        "cost": None if cost is None else round(cost, 6),
        "cost_if_all_strong": None if all_strong is None else round(all_strong, 6),
        "saving_pct": saving,
        "unpriced_turns": unpriced,
        # Only Agent UI chats record their turns here; the TUI and gaia -p don't.
        "scope": STATS_SCOPE,
        "scope_label": STATS_SCOPE_LABEL,
    }
