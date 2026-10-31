# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The one list of wordings that say a Claude account is out of quota or rejected.

Three places decide "out of quota, so try the other account": the CI account
picker (``util/classify_claude_probe.py``), the eval runner
(:mod:`gaia.eval.claude_outage`), and ``claude-run.yml``. They read this list,
and a unit test pins the workflow's copy to it, so they cannot disagree.

The rule for HTTP 429: only wording moves a job to the other account. A
subscription that is out says so ("You've hit your limit · resets 3pm"), whatever
status code carries it. A bare per-minute 429 (``rate_limit_error``, "rate limit
exceeded") is throttling on an account that still has quota; switching would
burn the backup for nothing, so it is never quota. 402 alone is quota.

stdlib only, and ASCII only: ``util/`` scripts load this file by path on runners
without GAIA installed, and the real refusal text carries a U+00B7 that the
phrases must match around, never on.
"""

from __future__ import annotations

import re

#: Matched case-insensitively. Each says the account is spent, not throttled.
QUOTA_PHRASES: tuple[str, ...] = (
    "hit your limit",
    "limit reached",
    "limit resets",
    "spend limit",
    "usage limit",
    "weekly limit",
    "session limit",
    "quota",
    "credit balance",
    "purchase credits",
    "out of credit",
    "insufficient credit",
    "billing",
    "upgrade your plan",
    "admin-settings/usage",
)

CREDENTIAL_PHRASES: tuple[str, ...] = (
    "authentication_error",
    "authentication failed",
    "invalid api key",
    "invalid x-api-key",
    "invalid bearer token",
    "invalid_token",
    "unauthorized",
    "oauth token",
    "token has expired",
    "please run /login",
    "not logged in",
    "no credential",
)

#: Throttling: named so a reader sees it was considered. Never quota on its own.
THROTTLE_PHRASES: tuple[str, ...] = ("rate limit", "rate_limit_error")

QUOTA_CODES: tuple[int, ...] = (402,)
CREDENTIAL_CODES: tuple[int, ...] = (401, 403)


def _hits(text: str, phrases: tuple[str, ...], codes: tuple[int, ...]) -> list[str]:
    lowered = text.lower()
    found = [p for p in phrases if p in lowered]
    # A bare code needs a boundary: token counts and ids contain the digits.
    found += [str(c) for c in codes if re.search(rf"(?<!\d){c}(?!\d)", lowered)]
    return found


def quota_hits(text: str) -> list[str]:
    """The quota phrases and codes in *text*."""
    return _hits(text, QUOTA_PHRASES, QUOTA_CODES)


def credential_hits(text: str) -> list[str]:
    """The credential phrases and codes in *text*."""
    return _hits(text, CREDENTIAL_PHRASES, CREDENTIAL_CODES)


def grep_pattern(phrases: tuple[str, ...]) -> str:
    """The ``grep -iE`` alternation a workflow uses for *phrases*."""
    # re.escape would add "\-", which GNU grep 3.8+ warns about; escape ERE only.
    return "|".join(re.sub(r"([.\[\](){}*+?|^$\\])", r"\\\1", p) for p in phrases)
