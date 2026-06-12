# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Structured verbose-logging contract for the Email Triage Agent (Phase A5).

When ``EmailAgentConfig.debug=True`` (or ``gaia email -v``), every tool
emits structured log entries via the ``gaia_agent_email`` logger with
well-defined ``stage`` values so benchmark scripts can parse them
without scraping prose:

  - ``triage_dispatch``   — heuristic vs. LLM dispatch decision per message
  - ``triage_decision``   — final classification per message
  - ``tool_call``         — tool invocation (name + redacted args)
  - ``tool_result``       — tool outcome (envelope summary + latency)

Every record carries ``extra={"stage": str, ...}`` so structured-log
sinks pick the fields up. Sensitive payloads (full prompt, full LLM
response, full body bytes) are ONLY emitted when ``debug=True`` —
verbose mode is opt-in for benchmarking, not a default.

Why a thin module: the tools shouldn't repeat ``logger.info(..., extra=...)``
boilerplate; an LLM-effectiveness benchmark consumer shouldn't have to
parse multiple log formats. One choke-point makes both easier.
"""

from __future__ import annotations

import logging
import re
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

logger = logging.getLogger("gaia_agent_email")


# Patterns redacted from any tool-call args before logging. Conservative —
# better to drop a real string than leak an MFA code or single-use token.
# The third pattern requires at least one digit (to avoid matching long
# file paths, message IDs, and other non-secret identifiers) AND at least
# 40 chars (JWT header+payload sections are typically longer).
_REDACT_PATTERNS = [
    re.compile(r"\b\d{6,8}\b"),  # MFA codes
    re.compile(r"https?://\S{30,}"),  # password reset URLs
    re.compile(r"(?=[A-Za-z0-9_\-]{40,})(?=[A-Za-z0-9_\-]*\d)[A-Za-z0-9_\-]{40,}"),
]


def _redact(value: Any) -> Any:
    """Best-effort scrubbing of secrets from log-bound args."""
    if isinstance(value, str):
        out = value
        for pat in _REDACT_PATTERNS:
            out = pat.sub("[REDACTED]", out)
        return out
    if isinstance(value, dict):
        return {k: _redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_redact(v) for v in value)
    return value


def log_triage_dispatch(
    *,
    message_id: str,
    decision: str,
    label_ids: list[str],
    rule_reason: str,
) -> None:
    """One per message — heuristic-fast-path vs LLM-fallback decision."""
    logger.info(
        "triage_dispatch message=%s decision=%s",
        message_id,
        decision,
        extra={
            "stage": "triage_dispatch",
            "message_id": message_id,
            "decision": decision,
            "label_ids": list(label_ids),
            "rule_reason": rule_reason,
        },
    )


def log_triage_decision(
    *,
    message_id: str,
    category: str,
    is_spam: bool,
    is_phishing: bool,
    confidence: Optional[str] = None,
    rationale: Optional[str] = None,
    prompt: Optional[str] = None,
    response: Optional[str] = None,
    debug: bool = False,
) -> None:
    """One per message — final classification.

    ``prompt`` and ``response`` are emitted ONLY when ``debug=True``
    (sensitive payload).
    """
    extra: Dict[str, Any] = {
        "stage": "triage_decision",
        "message_id": message_id,
        "category": category,
        "is_spam": is_spam,
        "is_phishing": is_phishing,
    }
    if confidence is not None:
        extra["confidence"] = confidence
    if rationale is not None:
        extra["rationale"] = rationale
    if debug:
        if prompt is not None:
            extra["prompt"] = prompt
        if response is not None:
            extra["response"] = response
    logger.info(
        "triage_decision message=%s category=%s",
        message_id,
        category,
        extra=extra,
    )


@contextmanager
def log_tool_call(
    name: str, args: Optional[Dict[str, Any]] = None, *, debug: bool = False
) -> Iterator[Dict[str, Any]]:
    """Context manager: emits ``tool_call`` on enter, ``tool_result`` on exit.

    Yields a dict the caller can mutate to attach an ``ok`` / ``error`` /
    ``result_summary`` field that surfaces in the result log line. Latency
    is measured automatically.
    """
    redacted_args = _redact(args or {})
    # ``name`` and ``args`` are reserved attribute names on ``LogRecord``;
    # use ``tool_name`` and ``tool_args`` in the structured ``extra``.
    logger.info(
        "tool_call name=%s",
        name,
        extra={
            "stage": "tool_call",
            "tool_name": name,
            "tool_args": redacted_args,
        },
    )
    state: Dict[str, Any] = {"ok": True, "error": None, "result_summary": None}
    start = time.monotonic()
    try:
        yield state
    except BaseException as exc:
        state["ok"] = False
        state["error"] = repr(exc)
        raise
    finally:
        latency_ms = int((time.monotonic() - start) * 1000)
        extra: Dict[str, Any] = {
            "stage": "tool_result",
            "tool_name": name,
            "ok": state["ok"],
            "latency_ms": latency_ms,
        }
        if state.get("error"):
            extra["error"] = state["error"]
        if state.get("result_summary"):
            extra["result_summary"] = (
                state["result_summary"] if debug else _redact(state["result_summary"])
            )
        logger.info(
            "tool_result name=%s ok=%s latency=%sms",
            name,
            state["ok"],
            latency_ms,
            extra=extra,
        )


__all__ = [
    "log_triage_decision",
    "log_triage_dispatch",
    "log_tool_call",
    "logger",
]
