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
import os
import threading
from typing import Any, Dict, List, Mapping, Optional

import requests

from gaia.llm.pricing import (  # noqa: F401  pylint: disable=unused-import
    PRICED_AS,
    RATES,
    bare_model,
    price,
    rate_card,
)
from gaia.llm.providers.fireworks import resolve_fireworks_api_key
from gaia.logger import get_logger

log = get_logger(__name__)

METERED = "metered"
API_EQUIVALENT = "api_equivalent"
HARNESS_COUNTS = "harness_counts"
COST_SOURCES = (METERED, API_EQUIVALENT, HARNESS_COUNTS)

FIREWORKS_API = "https://api.fireworks.ai/v1"

#: Public model name -> the deployment Fireworks bills it under. Both Qwen 3.8
#: names answer as "Qwen 3.8 Max" and bill to one thinking deployment; metering
#: by the public name reads zero.
BILLED_AS = {
    "qwen3p8-max": "qwen3p8-max-llm-thinking-nvfp4-0902",
    "qwen3p8-2p4t-a95b": "qwen3p8-max-llm-thinking-nvfp4-0902",
}


class MeterError(RuntimeError):
    """The billing meter could not be read; the message names the fix."""


def billed_name(model: str) -> str:
    name = bare_model(model)
    return BILLED_AS.get(name, name)


# ---------------------------------------------------------------------------
# The Fireworks billing meter
# ---------------------------------------------------------------------------


def _stamp(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


#: How long to wait for the OS credential store before giving up on it.
KEYRING_TIMEOUT_S = 15


def _api_key(timeout_s: int = KEYRING_TIMEOUT_S) -> str:
    """The Fireworks key, without ever blocking a run on a credential prompt.

    A macOS keychain item whose ACL demands authorisation blocks its reader
    until someone clicks, which on an unattended run is a hang with no output
    rather than a failure anyone can act on.
    """
    for name in ("FIREWORKS_API_KEY", "LEMONADE_FIREWORKS_API_KEY"):
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    found: List[Optional[str]] = []
    reader = threading.Thread(
        target=lambda: found.append(resolve_fireworks_api_key()), daemon=True
    )
    reader.start()
    reader.join(timeout_s)
    key = found[0] if found else None
    if not key:
        raise MeterError(
            "Metering needs a Fireworks API key. Set FIREWORKS_API_KEY"
            + (
                f" — the OS credential store did not answer within {timeout_s}s, "
                "which on macOS means the keychain is waiting for authorisation "
                "nobody is there to give."
                if not found
                else ", or save one with `gaia connectors`."
            )
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
