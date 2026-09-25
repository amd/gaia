# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What a run cost, and where each figure comes from.

Every cost carries its source, because the three are different kinds of dollar:

- ``metered``: the provider's own billing meter, a snapshot before and after
  the run. Real money.
- ``api_equivalent``: Claude Code's ``total_cost_usd`` on Anthropic models. On
  a Claude subscription that is the list price of the tokens, not money spent:
  comparable as a price of compute, never as out-of-pocket cost.
- ``harness_counts``: the harness's own per-call token counts (GAIA's, or the
  model gateway's for Claude Code on an open model) priced with a rate card,
  when there is no meter snapshot.

Fireworks' ``billingUsage`` returns daily buckets and trails the calls it
counts by about 90 s, so a run waits out the lag before its second snapshot,
and both snapshots share one fixed window start. A sliding window ("the last N
days") drops older usage between the two snapshots and made deltas negative.
The meter is account-wide: two runs on one account at once pollute each
other's deltas.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Mapping, Optional, Tuple

import requests

from gaia.llm.providers.fireworks import resolve_fireworks_api_key
from gaia.logger import get_logger

log = get_logger(__name__)

METERED = "metered"
API_EQUIVALENT = "api_equivalent"
HARNESS_COUNTS = "harness_counts"
COST_SOURCES = (METERED, API_EQUIVALENT, HARNESS_COUNTS)

FIREWORKS_API = "https://api.fireworks.ai/v1"

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
#: Public model name -> the deployment Fireworks bills it under. Both Qwen 3.8
#: names answer as "Qwen 3.8 Max" and bill to one thinking deployment; metering
#: by the public name reads zero.
BILLED_AS = {
    "qwen3p8-max": "qwen3p8-max-llm-thinking-nvfp4-0902",
    "qwen3p8-2p4t-a95b": "qwen3p8-max-llm-thinking-nvfp4-0902",
}
#: A model priced at another's card, where it has none of its own.
PRICED_AS = {"qwen3p8-2p4t-a95b": "qwen3p8-max"}


class MeterError(RuntimeError):
    """The billing meter could not be read; the message names the fix."""


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


def billed_name(model: str) -> str:
    name = bare_model(model)
    return BILLED_AS.get(name, name)


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


# ---------------------------------------------------------------------------
# The Fireworks billing meter
# ---------------------------------------------------------------------------


def _stamp(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _api_key() -> str:
    key = resolve_fireworks_api_key()
    if not key:
        raise MeterError(
            "Metering needs a Fireworks API key. Set FIREWORKS_API_KEY, or save "
            "one to the OS keyring with `gaia connectors`."
        )
    return key


def window_start(now: Optional[dt.datetime] = None) -> dt.datetime:
    """Midnight UTC a day back: covers a run that crosses midnight."""
    now = now or dt.datetime.now(dt.timezone.utc)
    day = now.astimezone(dt.timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return day - dt.timedelta(days=1)


def fetch_usage(account: str, start: dt.datetime, end: dt.datetime) -> Dict[str, Any]:
    """Raw ``billingUsage`` for serverless models, grouped by model."""
    try:
        response = requests.get(
            f"{FIREWORKS_API}/accounts/{account}/billingUsage",
            headers={"Authorization": f"Bearer {_api_key()}"},
            params={
                "startTime": _stamp(start),
                "endTime": _stamp(end),
                "usageType": "SERVERLESS",
                "groupBy": "model_name",
            },
            timeout=60,
        )
    except requests.RequestException as exc:
        raise MeterError(
            f"Fireworks billing API unreachable: {type(exc).__name__}"
        ) from exc
    if not response.ok:
        raise MeterError(
            f"Fireworks billingUsage for account {account!r} returned HTTP "
            f"{response.status_code}. Check the account id "
            "(FIREWORKS_ACCOUNT_ID) and that the key belongs to it."
        )
    return response.json()


def snapshot(account: str, start: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """Cumulative tokens per billed model since *start*.

    Pass the first snapshot's ``window_start`` to the second: both must count
    from the same moment or the difference is meaningless.
    """
    start = start or window_start()
    end = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)
    body = fetch_usage(account, start, end)
    models: Dict[str, Dict[str, int]] = {}
    for row in body.get("serverlessCosts") or []:
        name = str(row.get("modelName", "")).rsplit("/", 1)[-1]
        totals = models.setdefault(
            name, {"prompt": 0, "cached": 0, "uncached": 0, "completion": 0}
        )
        for key, field_name in (
            ("prompt", "promptTokens"),
            ("cached", "cachedPromptTokens"),
            ("uncached", "uncachedPromptTokens"),
            ("completion", "completionTokens"),
        ):
            totals[key] += int(row.get(field_name) or 0)
    return {
        "at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "window_start": _stamp(start),
        "models": models,
    }


def parse_window_start(snap: Mapping[str, Any]) -> dt.datetime:
    return dt.datetime.strptime(snap["window_start"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=dt.timezone.utc
    )


def metered_cost(
    before: Mapping[str, Any], after: Mapping[str, Any], model: str
) -> Dict[str, Any]:
    """Tokens and dollars *model* was billed between two snapshots."""
    if before.get("window_start") != after.get("window_start"):
        raise MeterError(
            "The two meter snapshots count from different window starts "
            f"({before.get('window_start')} vs {after.get('window_start')}); their "
            "difference would not be this run's usage."
        )
    billed = billed_name(model)
    b = (before.get("models") or {}).get(billed, {})
    a = (after.get("models") or {}).get(billed, {})
    delta = {k: a.get(k, 0) - b.get(k, 0) for k in ("cached", "uncached", "completion")}
    if any(v < 0 for v in delta.values()):
        raise MeterError(
            f"The meter went backwards for {billed} ({delta}). The snapshots do not "
            "cover the same window."
        )
    card = rate_card(model)
    usd = None
    if card is not None:
        usd = (
            delta["uncached"] * card[0]
            + delta["cached"] * card[1]
            + delta["completion"] * card[2]
        ) / 1e6
    return {
        "source": METERED,
        "billed_as": billed,
        "tokens": delta["cached"] + delta["uncached"] + delta["completion"],
        "input_tokens": delta["cached"] + delta["uncached"],
        "cached_tokens": delta["cached"],
        "output_tokens": delta["completion"],
        "usd": usd,
    }
