# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tell "Claude could not be reached" apart from a scenario that ran and failed.

When the Claude account behind ``gaia eval agent`` runs out of quota mid-run,
every later scenario's ``claude -p`` driver fails within seconds. Recorded as
ordinary errors, they made v0.25.1-rc2's run look complete (68 of 343
scenarios errored with zero tokens and the run stayed green). This module reads
one failed driver run and says whether the cause was the account, so the runner
can stop instead of burning through the rest of the suite.

Only the CLI's own failure fields are read: the ``result`` of an ``is_error``
payload, or the raw output when there is no JSON. A scenario's transcript is
never scanned, because the agent's own tool descriptions contain phrases such
as "until a rate limit resets".

The phrase lists extend :mod:`gaia.eval.judge_outage`'s with the wording the
subscription CLI prints ("You've hit your limit", "session limit"), which the
CI preflight probe (``util/classify_claude_probe.py``) also matches.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from gaia.eval.judge_outage import (
    CREDENTIAL_PHRASES,
    CREDENTIAL_STATUS,
    QUOTA_PHRASES,
    QUOTA_STATUS,
)

CLI_QUOTA_PHRASES: tuple[str, ...] = QUOTA_PHRASES + (
    "session limit",
    "hit your limit",
    "limit reached",
    "admin-settings/usage",
)
CLI_CREDENTIAL_PHRASES: tuple[str, ...] = CREDENTIAL_PHRASES + (
    "oauth token",
    "please run /login",
    "no credential",
)

# The runner already reports these as BUDGET_EXCEEDED / a capped run; they are
# the scenario's own limits, not the account's.
_OWN_LIMIT_SUBTYPES = frozenset({"error_max_budget_usd", "error_max_turns"})

_RESET_TEXT = re.compile(r"\bresets?\s+(?:at\s+)?([^|\"\n]{1,60})", re.IGNORECASE)
_RESET_EPOCH = re.compile(r"limit reached\|(\d{9,11})\b", re.IGNORECASE)

#: Kinds, each also the reason recorded on the scenarios left unrun.
QUOTA = "claude_quota"
CREDENTIAL = "claude_credential"
UNREACHABLE = "claude_unreachable"

HEADLINES = {
    QUOTA: "Claude quota exhausted",
    CREDENTIAL: "Claude credential rejected",
    UNREACHABLE: "Claude never answered",
}


@dataclass(frozen=True)
class ClaudeOutage:
    """Why the driver could not use Claude, with what the caller needs to act."""

    kind: str
    detail: str
    status_code: Optional[int] = None
    resets: Optional[str] = None

    @property
    def headline(self) -> str:
        return HEADLINES[self.kind]

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "detail": self.detail,
            "status_code": self.status_code,
            "resets": self.resets,
            "account": claude_account(),
        }


def claude_account() -> str:
    """Which credential the driver used, without revealing it."""
    label = os.environ.get("CLAUDE_ACCOUNT", "").strip()
    suffix = f" (CLAUDE_ACCOUNT={label})" if label else ""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return f"the API key in ANTHROPIC_API_KEY{suffix}"
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return f"the Claude subscription behind CLAUDE_CODE_OAUTH_TOKEN{suffix}"
    return f"the account the `claude` CLI is logged in to{suffix}"


def _reset_time(text: str) -> Optional[str]:
    epoch = _RESET_EPOCH.search(text)
    if epoch:
        when = datetime.fromtimestamp(int(epoch.group(1)), tz=timezone.utc)
        return when.strftime("%Y-%m-%d %H:%M UTC")
    match = _RESET_TEXT.search(text)
    if match:
        return match.group(1).strip().rstrip(".,;")
    return None


def _never_reached(payload: dict) -> bool:
    usage = payload.get("usage")
    if not isinstance(usage, dict) or payload.get("duration_api_ms"):
        return False
    return not any(
        usage.get(key)
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
    )


def classify_driver_failure(
    payload: Optional[dict], returncode: int, stdout: str = "", stderr: str = ""
) -> Optional[ClaudeOutage]:
    """Return a :class:`ClaudeOutage` if this ``claude -p`` run failed on Claude.

    ``None`` for a run that succeeded, stopped on its own budget, or failed
    for any reason Claude's availability does not explain — those stay the
    scenario's own result. ``payload`` is the parsed ``--output-format json``
    object, or ``None`` when stdout was not JSON.
    """
    failed = returncode != 0 or bool(payload and payload.get("is_error"))
    if not failed:
        return None
    if payload and payload.get("subtype") in _OWN_LIMIT_SUBTYPES:
        return None

    status = payload.get("api_error_status") if payload else None
    status = status if isinstance(status, int) else None
    if payload is not None:
        text = f"{payload.get('result') or ''} {stderr or ''}"
    else:
        text = f"{stderr or ''} {stdout or ''}"
    lowered = text.lower()
    detail = " ".join(text.split())[:400] or f"exit {returncode}, no output"

    quota = status in QUOTA_STATUS or any(p in lowered for p in CLI_QUOTA_PHRASES)
    credential = status in CREDENTIAL_STATUS or any(
        p in lowered for p in CLI_CREDENTIAL_PHRASES
    )
    # Both signatures, or a failure before Claude answered anything: every later
    # scenario would fail the same way, whatever the exact cause.
    if (quota and credential) or (
        not quota and not credential and payload is not None and _never_reached(payload)
    ):
        return ClaudeOutage(UNREACHABLE, detail, status, _reset_time(text))
    if quota:
        return ClaudeOutage(QUOTA, detail, status, _reset_time(text))
    if credential:
        return ClaudeOutage(CREDENTIAL, detail, status)
    return None
