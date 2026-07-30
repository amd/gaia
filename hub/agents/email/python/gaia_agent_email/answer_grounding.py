# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Deterministic post-checks on the agent's own final answer text.

The system prompt asks the model to stay honest about what it actually did
and what it actually saw, but prompt compliance is probabilistic — a model
can still narrate a mutation it never called a tool for, contradict a tool
result it just received, or echo internal payload scaffolding into prose.
These functions inspect the FINAL answer text against the turn's own tool
trace (``result["conversation"]``, which ``Agent._process_query_impl``
resets to empty at the start of every call, so it always scopes to exactly
this turn) and either flag or rewrite the parts that are not grounded in
what actually happened.

Every function here is pure and side-effect free — no LLM calls, no I/O —
so the guard is unit-testable without a live model or a live mailbox.
``EmailTriageAgent.process_query`` is the single call site that wires these
into the output boundary.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterator, List, Optional

from gaia.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Shared helpers — reading the turn's own tool trace
# ---------------------------------------------------------------------------


def _parse_tool_payload(content: Any) -> Optional[Dict[str, Any]]:
    """Best-effort decode of a ``role: tool`` conversation entry's content.

    Handles every shape a tool result can arrive in: a JSON string, a native
    tool-calling wire block (``[{"type": "text", "text": "..."}]``), or an
    already-parsed dict. Unwraps the ``{"ok": true, "data": {...}}`` envelope
    convention when present. Returns ``None`` when the content cannot be
    read as a mapping — never raises, since a conversation entry from an
    unrelated tool shape is not this function's error to report.
    """
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return None
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return _parse_tool_payload(block.get("text"))
        return None
    if not isinstance(content, dict):
        return None
    data = content.get("data")
    if isinstance(data, dict):
        return data
    return content


def _tool_entries(conversation: Optional[List[Dict[str, Any]]]) -> Iterator[Dict[str, Any]]:
    for entry in conversation or []:
        if isinstance(entry, dict) and entry.get("role") == "tool":
            yield entry


def tools_called_this_turn(conversation: Optional[List[Dict[str, Any]]]) -> List[str]:
    """Names of every tool invoked in this turn's conversation trace."""
    return [entry.get("name") for entry in _tool_entries(conversation) if entry.get("name")]


def last_tool_payload(
    conversation: Optional[List[Dict[str, Any]]], tool_name: str
) -> Optional[Dict[str, Any]]:
    """The most recent parsed result of ``tool_name`` called this turn, if any."""
    payload = None
    for entry in _tool_entries(conversation):
        if entry.get("name") == tool_name:
            parsed = _parse_tool_payload(entry.get("content"))
            if parsed is not None:
                payload = parsed
    return payload


# ---------------------------------------------------------------------------
# Guard 1 — a mutation claimed without a matching tool call this turn
# ---------------------------------------------------------------------------

# Completion framing: the shapes a model uses to say "this already happened",
# as opposed to explaining what an action does or offering to perform one.
_COMPLETION_LEAD = (
    r"(?:has|have|was|were)\s+(?:now\s+|already\s+|successfully\s+)?(?:been\s+)?"
    r"|i(?:'ve|\s+have)\s+(?:now\s+|already\s+|successfully\s+)?"
    r"|(?:successfully|done)[\s:—-]*(?:i\s+)?(?:just\s+)?"
    r"|is\s+now\s+|are\s+now\s+|just\s+got\s+"
)
_MUTATION_VERB = (
    r"archiv\w*|(?:un)?star\w*|marked\s+(?:as\s+)?(?:un)?read|trashed"
    r"|deleted|label(?:l)?ed|quarantined|unquarantined|restored|sent"
    r"|forwarded|scheduled|snoozed"
)
_SUCCESS_CLAIM_RE = re.compile(
    rf"\b(?:{_COMPLETION_LEAD})(?:{_MUTATION_VERB})\b"
    rf"|\bmoved\s+to\s+(?:trash|the\s+\S+\s+label)\b",
    re.IGNORECASE,
)

UNGROUNDED_SUCCESS_FALLBACK = (
    "I was not able to confirm that action actually completed — no tool call "
    "was recorded for this turn. Please ask again and I will only report it "
    "as done once a tool call actually confirms it."
)


def find_ungrounded_success_claim(
    final_answer: Optional[str], conversation: Optional[List[Dict[str, Any]]]
) -> Optional[str]:
    """Return the matched phrase when ``final_answer`` claims a mutation
    completed but this turn's tool trace is empty; ``None`` when grounded.

    Deliberately turn-scoped and tool-agnostic: it does not try to match the
    claimed verb to a specific tool name (a model paraphrases too freely for
    that to be reliable). Any completion-framed mutation claim is
    contradicted by the plain fact that zero tools ran this turn — the agent
    has no other channel through which a mailbox mutation could happen.
    """
    if not final_answer:
        return None
    if tools_called_this_turn(conversation):
        return None
    match = _SUCCESS_CLAIM_RE.search(final_answer)
    return match.group(0) if match else None


# ---------------------------------------------------------------------------
# Guard 2 — negative claims contradicted by this turn's own scan result
# ---------------------------------------------------------------------------

_NO_URGENT_RE = re.compile(r"\bno\b[^.!?]{0,40}\burgent\b", re.IGNORECASE)
_NO_ACTIONABLE_RE = re.compile(r"\bno\b[^.!?]{0,40}\bactionable\b", re.IGNORECASE)
_ALL_CLEAR_RE = re.compile(
    r"\b(?:nothing needs|inbox is clear|all clear|nothing urgent|nothing actionable)\b",
    re.IGNORECASE,
)
_COVERAGE_QUALIFIER_RE = re.compile(
    r"\bscanned\b|\bout of\b|\bof the\b|\bmost recent\b|\bunread\b|\bolder\b"
    r"|\bnot everything\b|\bso far\b|\bpartial\b",
    re.IGNORECASE,
)


def find_unqualified_negative_claim(
    final_answer: Optional[str], conversation: Optional[List[Dict[str, Any]]]
) -> Optional[str]:
    """Return a reason string when ``final_answer`` contradicts this turn's
    own ``pre_scan_inbox`` result, ``None`` when the claim is grounded.

    Two independent checks, both scoped to the SAME envelope the model
    itself received this turn (never a separately-rendered surface it has
    no visibility into):

    - "no urgent" / "no actionable" while the matching list is non-empty.
    - an unqualified all-clear phrase while ``scanned`` under-covers
      ``total_unread`` and the answer carries no coverage qualifier.
    """
    if not final_answer:
        return None
    envelope = last_tool_payload(conversation, "pre_scan_inbox")
    if envelope is None:
        return None

    urgent = envelope.get("urgent") or []
    actionable = envelope.get("actionable") or []
    if urgent and _NO_URGENT_RE.search(final_answer):
        return f"claims no urgent items while pre_scan_inbox returned {len(urgent)}"
    if actionable and _NO_ACTIONABLE_RE.search(final_answer):
        return f"claims no actionable items while pre_scan_inbox returned {len(actionable)}"

    scanned = envelope.get("scanned")
    total_unread = envelope.get("total_unread")
    if (
        isinstance(scanned, int)
        and isinstance(total_unread, int)
        and scanned < total_unread
        and _ALL_CLEAR_RE.search(final_answer)
        and not _COVERAGE_QUALIFIER_RE.search(final_answer)
    ):
        return (
            f"unqualified all-clear claim while scanned={scanned} < "
            f"total_unread={total_unread}"
        )
    return None


# ---------------------------------------------------------------------------
# Guard 3 — internal payload scaffolding leaking into prose
# ---------------------------------------------------------------------------

_SHOWN_TO_USER_MARKER_RE = re.compile(
    r"\n*\[shown to the user\]\n*", re.IGNORECASE
)
_ENVELOPE_FIELD_LABEL_RE = re.compile(
    r"\[(?:urgent|actionable|informational|suggested_archives|needs_review"
    r"|preferences_applied|totals|items|coverage|waiting_on_you|action_item"
    r"|meeting_request|mailbox_errors|scan_truncated|degraded)\]\s*",
    re.IGNORECASE,
)
_RAW_MESSAGE_ID_RE = re.compile(r"\(id [0-9a-f]{16}\)\s*|\b[0-9a-f]{16}\b", re.IGNORECASE)
_UNICODE_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")

_SCAFFOLDING_CHECKS = (
    ("shown-to-user marker", _SHOWN_TO_USER_MARKER_RE),
    ("envelope field-name label", _ENVELOPE_FIELD_LABEL_RE),
    ("raw provider message id", _RAW_MESSAGE_ID_RE),
    ("undecoded unicode escape", _UNICODE_ESCAPE_RE),
)


def find_scaffolding_leak(text: Optional[str]) -> Optional[str]:
    """Return which known internal-scaffolding pattern appears in ``text``,
    or ``None`` when the text is clean. Detection only — see
    ``strip_scaffolding_leaks`` for the rewrite."""
    if not text:
        return None
    for label, pattern in _SCAFFOLDING_CHECKS:
        if pattern.search(text):
            return label
    return None


def decode_stray_unicode_escapes(text: str) -> str:
    """Turn a literal ``\\uXXXX`` escape sequence into the character it
    names. A safety net for any path that still hands the model (or the
    model's own output) an ``ensure_ascii``-escaped string."""
    if not text or "\\u" not in text:
        return text
    return _UNICODE_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), text)


def strip_scaffolding_leaks(text: str) -> str:
    """Remove internal render/envelope scaffolding from a final answer.

    Targeted substitution rather than replacing the whole message: the
    surrounding prose is presumed fine, only these specific tokens are not
    meant for the reader.
    """
    if not text:
        return text
    cleaned = _SHOWN_TO_USER_MARKER_RE.sub("\n", text)
    cleaned = _ENVELOPE_FIELD_LABEL_RE.sub("", cleaned)
    cleaned = _RAW_MESSAGE_ID_RE.sub("", cleaned)
    cleaned = decode_stray_unicode_escapes(cleaned)
    return cleaned.strip()


def _honest_prescan_summary(envelope: Dict[str, Any]) -> str:
    """A minimal, always-grounded pre-scan sentence built straight from the
    envelope's own counts — the fallback used when the model's own framing
    sentence contradicts that same envelope."""
    urgent = len(envelope.get("urgent") or [])
    actionable = len(envelope.get("actionable") or [])
    needs_review = len(envelope.get("needs_review") or [])
    parts = []
    if urgent:
        parts.append(f"{urgent} urgent")
    if actionable:
        parts.append(f"{actionable} actionable")
    if needs_review:
        parts.append(f"{needs_review} worth a closer look")
    summary = ", ".join(parts) if parts else "nothing urgent or actionable"
    coverage = f"{envelope.get('scanned', 0)} messages scanned"
    total_unread = envelope.get("total_unread")
    if isinstance(total_unread, int):
        coverage += f" · {total_unread} unread in your inbox"
    return f"Here's your inbox pre-scan — {summary}. {coverage}."


# ---------------------------------------------------------------------------
# Orchestration — the single call site EmailTriageAgent.process_query uses
# ---------------------------------------------------------------------------


def ground_final_answer(result: Dict[str, Any]) -> Dict[str, Any]:
    """Apply every deterministic post-check to ``result["result"]`` in place.

    Order matters: scaffolding is stripped first (a pure, always-safe
    rewrite), then the two contradiction checks run against the cleaned
    text. Either contradiction check fully replaces the answer with a
    grounded fallback rather than attempting a partial text patch — a
    claim that has already been shown false is not a base worth editing
    from. A replaced answer is not re-scanned by the other check: each
    fallback is already, by construction, clean of the pattern it replaces.
    """
    final_answer = result.get("result")
    if not isinstance(final_answer, str) or not final_answer:
        return result

    conversation = result.get("conversation")

    if find_scaffolding_leak(final_answer):
        final_answer = strip_scaffolding_leaks(final_answer)

    success_claim = find_ungrounded_success_claim(final_answer, conversation)
    if success_claim:
        logger.warning(
            "email agent: dropped ungrounded success claim %r — no tool call "
            "recorded this turn",
            success_claim,
        )
        result["result"] = UNGROUNDED_SUCCESS_FALLBACK
        return result

    negative_claim_reason = find_unqualified_negative_claim(final_answer, conversation)
    if negative_claim_reason:
        logger.warning(
            "email agent: rewrote contradicted pre-scan claim — %s",
            negative_claim_reason,
        )
        envelope = last_tool_payload(conversation, "pre_scan_inbox")
        result["result"] = _honest_prescan_summary(envelope or {})
        return result

    result["result"] = final_answer
    return result


__all__ = [
    "UNGROUNDED_SUCCESS_FALLBACK",
    "decode_stray_unicode_escapes",
    "find_scaffolding_leak",
    "find_ungrounded_success_claim",
    "find_unqualified_negative_claim",
    "ground_final_answer",
    "last_tool_payload",
    "strip_scaffolding_leaks",
    "tools_called_this_turn",
]
