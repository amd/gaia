# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Read tools mixin for ``EmailTriageAgent``.

Tools: ``list_inbox``, ``get_message``, ``get_thread``, ``summarize_thread``,
``search_messages``, ``list_labels``, ``triage_inbox``, ``pre_scan_inbox``.

Each tool returns a JSON string with the canonical envelope::

    {"ok": true, "data": ...}      -- on success
    {"ok": false, "error": "..."}  -- on backend failure

Body content sent to the LLM is wrapped in an UNTRUSTED-INPUT delimiter
(see Phase I1 — system prompt hardening). The wrapper exists in this
module because every read tool that returns body bytes needs to honor it.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from gaia_agent_email.body_normalize import normalize_email_body
from gaia_agent_email.config import default_inbox_scan_ceiling
from gaia_agent_email.context_budget import (
    active_profile_ctx_size,
    envelope_budget_tokens,
    estimate_tokens_json,
)
from gaia_agent_email.gmail_backend import decode_message_body
from gaia_agent_email.tools.envelope import _envelope_err, _envelope_ok

# Re-exported so the pre-scan tests can monkeypatch ``read_tools.make_llm_classifier``
# to prove pre-scan never wires the LLM (test_pre_scan_counts.py).
from gaia_agent_email.tools.llm_triage import make_llm_classifier  # noqa: F401
from gaia_agent_email.tools.triage_condense import condense_triage_result

# Read-only reuse of the existing automated-sender signal for needs_review's
# display ordering (#2584) — NOT a new heuristic phrase list (that's #2581's
# job; triage_heuristics.py itself is untouched). Single source of truth
# stays in triage_heuristics; this module never redefines it.
from gaia_agent_email.tools.triage_heuristics import (
    _AUTOMATED_SENDER_KEYWORDS as _NEEDS_REVIEW_AUTOMATED_SENDER_KEYWORDS,
)
from gaia_agent_email.tools.triage_heuristics import (
    CATEGORY_FYI,
    CATEGORY_NEEDS_RESPONSE,
    CATEGORY_PROMOTIONAL,
    CATEGORY_URGENT,
    classify_category_heuristic,
    group_by_category,
)
from gaia_agent_email.tools.usage import aggregate_usage_stats
from gaia_agent_email.verbose import (
    log_tool_call,
    log_triage_decision,
    log_triage_dispatch,
)

from gaia.agents.base.tools import tool
from gaia.connectors.errors import ConnectorsError
from gaia.connectors.formatting import format_connector_error
from gaia.logger import get_logger

log = get_logger(__name__)


# Maximum body length sent to the LLM. Larger messages are truncated with
# a ``...[truncated]`` marker. Prevents context blow-up and limits the
# attack surface for indirect prompt injection.
DEFAULT_BODY_LIMIT_CHARS = 4000

# Opt-in ceiling for ``get_message(full_body=True)``. Finite on purpose —
# an unbounded body is a single-email context DoS on a fixed-ctx local model.
MAX_FULL_BODY_CHARS = 50_000

# Combined body budget for a whole-thread transcript (#1268). Bounds the prompt
# so a long thread can't overflow a local model's context window. When a thread
# exceeds it, the per-message budget shrinks so every message stays represented
# rather than dropping the oldest (which would defeat full-thread comprehension).
DEFAULT_THREAD_TRANSCRIPT_CHARS = 24000

# Floor so that, even in a very long thread, each message still carries enough
# body to be meaningful after the proportional shrink above.
THREAD_MIN_PER_MESSAGE_CHARS = 200

# Wrapper used to delimit untrusted email body content. The system prompt
# (see ``agent.py``) tells the LLM that anything inside this wrapper is
# DATA, never an instruction to execute. Phase I1 / S2.M3.
UNTRUSTED_BODY_OPEN = "<<<UNTRUSTED_EMAIL_BODY_START>>>"
UNTRUSTED_BODY_CLOSE = "<<<UNTRUSTED_EMAIL_BODY_END>>>"

# Actionable empty-state error for read tools that scan the connected set
# directly. Construction now tolerates zero connectors (agent constructs so
# conversational questions still reach the LLM), so these tools must fail loudly
# per call instead of dividing the per-mailbox budget by zero.
NO_MAILBOX_CONNECTED_MESSAGE = (
    "No mailbox connected — connect Google or Microsoft in "
    "Settings → Connectors to read your inbox."
)


class EnvelopeBudgetExceeded(RuntimeError):
    """Raised when even the per-message floor can't fit every requested
    message inside the active context budget (#2514).

    The only acceptable failure mode for a combined-envelope budget: a
    caller must never learn a request was too big by silently getting back
    fewer messages than it asked for (the N=10-truncated-to-8 bug this
    exception replaces).
    """


def wrap_untrusted_body(body: str) -> str:
    """Wrap a body in the untrusted-input delimiter pair."""
    return f"{UNTRUSTED_BODY_OPEN}\n{body}\n{UNTRUSTED_BODY_CLOSE}"


def _truncate(text: str, limit: int) -> tuple[str, int]:
    """Return (possibly-truncated text, chars dropped). Dropped == 0 means untouched."""
    if limit <= 0:
        raise ValueError(f"body limit must be positive, got {limit}")
    if len(text) <= limit:
        return text, 0
    return text[:limit] + "\n...[truncated]", len(text) - limit


def _format_message_for_llm(
    msg: Dict[str, Any], *, body_limit: int = DEFAULT_BODY_LIMIT_CHARS
) -> Dict[str, Any]:
    """Reduce a Gmail-API-shape message to fields the LLM can act on.

    The body is decoded via the production decoder, stripped of known
    mail-infrastructure banners (#2642), and wrapped in the untrusted-input
    delimiter so the LLM never confuses content with instructions.
    """
    payload = msg.get("payload") or {}
    headers = {
        (h.get("name") or "").lower(): h.get("value", "")
        for h in payload.get("headers", [])
    }
    body, attachments = decode_message_body(payload)
    body = normalize_email_body(body)
    body_chars_dropped = 0
    if body:
        body, body_chars_dropped = _truncate(body, body_limit)
    return {
        "id": msg.get("id"),
        "thread_id": msg.get("threadId"),
        "subject": headers.get("subject", ""),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "date": headers.get("date", ""),
        "label_ids": list(msg.get("labelIds", [])),
        "snippet": msg.get("snippet", ""),
        "body": wrap_untrusted_body(body),
        "body_truncated": body_chars_dropped > 0,
        "body_chars_dropped": body_chars_dropped,
        "attachments": attachments,
    }


# ---------------------------------------------------------------------------
# Pure tool implementations (testable without the agent class)
# ---------------------------------------------------------------------------


def _format_messages_within_budget(
    full_msgs: List[Dict[str, Any]],
    *,
    tool_name: str,
    max_results: int,
    budget_tokens: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Format ``full_msgs`` for the LLM under a COMBINED envelope budget (#2514).

    Shared by ``list_inbox_impl`` and ``search_messages_impl`` — both loop
    ``gmail.get_message()`` -> ``_format_message_for_llm`` with no combined
    cap today, so a realistic ``max_results`` batch can overflow the NPU
    profile's 32768-token context window on the first call of a fresh
    conversation. Mirrors ``get_thread_impl``'s shrink-together philosophy
    (every message stays represented, none dropped) but adds two things that
    path doesn't need: a context-aware token budget (not a fixed char
    constant) and a fail-loud path when even the per-message floor can't
    fit — silently truncating the message COUNT (this issue's N=10-becomes-8
    bug) is exactly what must never happen again.

    ``budget_tokens`` defaults to the ACTIVE device profile's envelope budget
    (GPU/CPU 65536, NPU 32768) rather than the fixed eval-harness target, so
    a GPU box gets its real headroom instead of being capped to the NPU's
    conservative ceiling.

    Binary-searches the largest shared per-message body limit (bounded below
    by ``THREAD_MIN_PER_MESSAGE_CHARS``) that keeps the serialized envelope
    within budget. A single proportional guess (scale the default limit by
    budget/measured-total) systematically undershoots: per-message JSON
    overhead (id/subject/dates/label_ids/etc.) does not shrink with the
    body, so only a measured search converges reliably.
    """
    if budget_tokens is None:
        budget_tokens = envelope_budget_tokens(ctx_size=active_profile_ctx_size())

    out = [_format_message_for_llm(m) for m in full_msgs]
    if not out:
        return out
    if (
        estimate_tokens_json(json.dumps({"messages": out}, default=str))
        <= budget_tokens
    ):
        return out

    lo, hi = THREAD_MIN_PER_MESSAGE_CHARS, DEFAULT_BODY_LIMIT_CHARS - 1
    best: Optional[List[Dict[str, Any]]] = None
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = [_format_message_for_llm(m, body_limit=mid) for m in full_msgs]
        tokens = estimate_tokens_json(json.dumps({"messages": candidate}, default=str))
        if tokens <= budget_tokens:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1

    if best is None:
        raise EnvelopeBudgetExceeded(
            f"{tool_name}: cannot fit {len(full_msgs)} messages (max_results="
            f"{max_results}) within the {budget_tokens}-token context budget "
            f"even at the {THREAD_MIN_PER_MESSAGE_CHARS}-char minimum "
            "per-message body limit. Reduce max_results and try again."
        )
    return best


def list_inbox_impl(
    gmail,
    *,
    max_results: int = 25,
    debug: bool = False,
    budget_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    with log_tool_call("list_inbox", {"max_results": max_results}, debug=debug) as st:
        listing = gmail.list_messages(label_ids=["INBOX"], max_results=max_results)
        full_msgs = [
            gmail.get_message(stub["id"]) for stub in listing.get("messages", [])
        ]
        out = _format_messages_within_budget(
            full_msgs,
            tool_name="list_inbox",
            max_results=max_results,
            budget_tokens=budget_tokens,
        )
        st["result_summary"] = {"count": len(out)}
        return {"messages": out, "next_page_token": listing.get("nextPageToken")}


def get_message_impl(
    gmail,
    *,
    message_id: str,
    body_limit: int = DEFAULT_BODY_LIMIT_CHARS,
    debug: bool = False,
) -> Dict[str, Any]:
    with log_tool_call(
        "get_message",
        {"message_id": message_id, "body_limit": body_limit},
        debug=debug,
    ) as st:
        msg = gmail.get_message(message_id)
        formatted = _format_message_for_llm(msg, body_limit=body_limit)
        st["result_summary"] = {
            "id": formatted["id"],
            "subject": formatted["subject"],
        }
        return formatted


def get_thread_impl(gmail, *, thread_id: str, debug: bool = False) -> Dict[str, Any]:
    """Fetch every message in a thread, sorted chronologically (oldest first).

    #2531: Gmail's thread API does not guarantee message order (it is
    "usually" oldest-first, not always) — the same risk
    ``_thread_message_sort_key`` already defends against for
    ``summarize_thread``. This path used to trust raw backend order instead,
    and a live run showed the consequence: the calling LLM, handed an
    unlabeled JSON array it had to sort and enumerate itself, returned the
    right message COUNT but dropped/duplicated entries and inverted the
    trailing pair. Sorting here, and numbering each message with its
    position, gives the model an authoritative order instead of one it has
    to compute.

    The combined body budget mirrors ``_format_thread_for_summary``'s
    soft-target semantics (#2073): under ``DEFAULT_THREAD_TRANSCRIPT_CHARS``
    the per-message default limit applies untouched; over budget, every
    message is re-formatted at a shared fair-share limit (floored at
    ``THREAD_MIN_PER_MESSAGE_CHARS``) so long threads stay bounded without
    ever dropping a message.
    """
    with log_tool_call("get_thread", {"thread_id": thread_id}, debug=debug) as st:
        thread = gmail.get_thread(thread_id)
        messages = sorted(thread.get("messages", []), key=_thread_message_sort_key)
        out = [_format_message_for_llm(m) for m in messages]
        total = sum(len(f["body"]) for f in out)
        if messages and total > DEFAULT_THREAD_TRANSCRIPT_CHARS:
            # Duplicated (not shared with) _format_thread_for_summary's
            # fair-share formula on purpose: that helper's limit<=0
            # unlimited-mode semantics don't belong on a read tool.
            fair_share = max(
                THREAD_MIN_PER_MESSAGE_CHARS,
                DEFAULT_THREAD_TRANSCRIPT_CHARS // len(messages),
            )
            if fair_share < DEFAULT_BODY_LIMIT_CHARS:
                out = [
                    _format_message_for_llm(m, body_limit=fair_share) for m in messages
                ]
        for position, formatted in enumerate(out, start=1):
            formatted["index"] = position
            formatted["of_total"] = len(out)
        bodies_clipped = sum(1 for f in out if f["body_truncated"])
        st["result_summary"] = {
            "thread_id": thread_id,
            "count": len(out),
            "bodies_clipped": bodies_clipped,
        }
        return {"thread_id": thread_id, "messages": out}


def _thread_message_sort_key(msg: Dict[str, Any]) -> int:
    """Chronological sort key for a raw thread message.

    Gmail ``threads.get`` returns messages oldest-first, but we sort
    defensively by ``internalDate`` (millis since epoch) so a misordered
    backend can't make the LLM read the conversation out of sequence.
    """
    try:
        return int(msg.get("internalDate", "0"))
    except (TypeError, ValueError):
        return 0


def _thread_message_blocks(
    messages: List[Dict[str, Any]],
    *,
    per_message_body_limit: int,
    start_index: int = 1,
    total_count: Optional[int] = None,
) -> Tuple[List[str], List[str]]:
    """Render each message (already sorted) as one numbered, wrapped block.

    Shared by :func:`_format_thread_for_summary` (the full-thread join) and
    the #1889 over-budget fold path (message-boundary bucketing) so there is
    exactly one place that defines what a message block looks like — no
    duplicate formatting to drift.

    Returns ``(blocks, decoded_bodies)`` — ``decoded_bodies`` is each message's
    decoded, stripped, PRE-truncation body in the same order. A caller that
    needs one message's own body (e.g. the #2641 meeting-signal scan over
    the newest message) reuses ``decoded_bodies[-1]`` instead of paying for a
    second MIME decode of the same payload — which would also feed the
    heuristic the rendered block's header/delimiter framing rather than the
    plain body, risking a false match against e.g. the ``Date:`` header's
    own ``HH:MM:SS``.
    """
    total = total_count if total_count is not None else len(messages)
    blocks: List[str] = []
    decoded_bodies: List[str] = []
    for offset, msg in enumerate(messages):
        idx = start_index + offset
        payload = msg.get("payload") or {}
        headers = {
            (h.get("name") or "").lower(): h.get("value", "")
            for h in payload.get("headers", [])
        }
        body, _attachments = decode_message_body(payload)
        body = (body or "").strip()
        body = normalize_email_body(body)  # strip infra banners (#2642)
        decoded_bodies.append(body)
        rendered_body = body
        if per_message_body_limit > 0 and len(body) > per_message_body_limit:
            rendered_body = body[:per_message_body_limit] + "\n...[truncated]"
        blocks.append(
            f"--- Message {idx} of {total} ---\n"
            f"From: {headers.get('from', '')}\n"
            f"Date: {headers.get('date', '')}\n"
            f"{wrap_untrusted_body(rendered_body)}"
        )
    return blocks, decoded_bodies


def _format_thread_for_summary(
    messages: List[Dict[str, Any]],
    *,
    per_message_body_limit: int,
    max_total_transcript_chars: Optional[int] = DEFAULT_THREAD_TRANSCRIPT_CHARS,
) -> str:
    """Render an oldest-first transcript of the FULL thread for the LLM.

    Every message is numbered and labelled with From/Date, and each body is
    wrapped in the untrusted-input delimiters — so the model comprehends the
    whole conversation (early decisions included), never just the latest reply,
    yet still treats body text as data, never instructions.

    ``max_total_transcript_chars`` steers the COMBINED body budget toward that
    target so a long thread doesn't balloon the prompt (50 messages × the
    per-message limit could otherwise reach hundreds of KB). When the total
    would exceed it, we shrink the per-message budget so every message stays
    represented — we do NOT drop the oldest messages, because the whole point of
    thread summarization is that an early decision survives. It is a soft
    target, not a hard ceiling: ``THREAD_MIN_PER_MESSAGE_CHARS`` is a per-message
    floor, so a thread with very many messages can still exceed the target
    (floor × count) rather than starve each message below readability.
    ``None`` disables the cap entirely — used by the #1889 token-budget gate,
    which replaces this char cap as the fits criterion.
    """
    ordered = sorted(messages, key=_thread_message_sort_key)
    effective_body_limit = per_message_body_limit
    if max_total_transcript_chars and ordered:
        # Keep every message present; divide the total body budget across them
        # (with a small floor so each still carries enough to be meaningful).
        fair_share = max(
            THREAD_MIN_PER_MESSAGE_CHARS, max_total_transcript_chars // len(ordered)
        )
        if effective_body_limit <= 0 or fair_share < effective_body_limit:
            effective_body_limit = fair_share
    blocks, _decoded_bodies = _thread_message_blocks(
        ordered, per_message_body_limit=effective_body_limit
    )
    return "\n\n".join(blocks)


def _build_thread_user_prompt(
    subject: str, transcript: str, *, meeting_detected: bool = False
) -> str:
    """Build the user-turn prompt for whole-thread summarization.

    Unlike the single-email prompt, this does NOT clip the body to a single
    message's budget — the transcript is the FULL conversation and each
    message body is already individually wrapped + truncated by
    ``_format_thread_for_summary``. Re-clipping here would drop later
    messages and defeat full-thread comprehension.

    ``meeting_detected`` is the deterministic, heuristic-only signal from
    ``detect_meeting_request_heuristic`` run over the newest message's own
    decoded body (#2641) — never the model's free-form read of the
    transcript. A plain bool is the only thing this function accepts, so
    ``MeetingDetection.signals``/``.reason`` (raw, sender-authored
    substrings) can never reach the prompt; the note is a fixed,
    non-authoritative sentence, not an asserted fact.
    """
    instruction = (
        "Summarize this email thread as a whole. Reflect decisions, asks, and "
        "outcomes from EVERY message — including earlier messages the latest "
        "reply does not repeat. Give the newest message's still-open asks the "
        "same weight as an early decision: if the latest message raises an "
        "unanswered question or a pending request, name it.\n"
    )
    if meeting_detected:
        instruction += (
            "The newest message appears to propose a meeting time; if the "
            "body actually names one, state the day and time in the "
            "summary.\n"
        )
    return f"{instruction}\nSubject: {subject}\nThread (oldest first):\n{transcript}\n"


def summarize_thread_impl(
    gmail,
    chat,
    *,
    thread_id: str,
    max_chars: Optional[int] = None,
    per_message_body_limit: int = DEFAULT_BODY_LIMIT_CHARS,
    debug: bool = False,
) -> Dict[str, Any]:
    """Summarize a whole email thread, comprehending the FULL conversation.

    Reads every message via ``get_thread``, renders them oldest-first into a
    single transcript, and summarizes that transcript — so a decision made in
    an early message that the latest reply doesn't repeat is still reflected.

    Reuses the per-email summarization contract (#1267) — the shared system
    prompt, the empty-output guard, the word-boundary length bound, and the
    ``EmailSummarizeError`` type — so the bounded, fail-loud behavior is
    identical: an empty thread or an LLM failure raises rather than silently
    collapsing to a latest-only summary (repo "No Silent Fallbacks" rule). The
    user-turn prompt is thread-shaped (no single-email body clip) so the whole
    conversation reaches the model.

    The token-budget gate (#1889) REPLACES the legacy
    ``max_total_transcript_chars`` fair-share char cap as the fits criterion:
    the full, uncapped transcript is tried first and used unchanged whenever
    it fits ``context_budget.thread_budget_tokens()`` — a thread between the
    old 24K-char cap and the token budget is no longer clipped. Only when the
    full transcript doesn't fit does the thread get folded: the latest
    message stays verbatim and every older message is condensed into ONE
    digest via a single LLM call (``tools.thread_fold``). Threads beyond the
    message-count ceiling are pre-sliced to the most recent
    ``DEFAULT_THREAD_FOLD_MESSAGE_CEILING`` messages BEFORE any per-message
    decode (explicit ``[omitted N older messages]`` marker, never silent).
    When the fold ran, the result carries its LLM usage under ``usage`` (a
    plain dict via ``aggregate_usage_stats``, #1891); the fits path has no
    extra call, so no ``usage`` key.
    """
    # Deferred imports: these modules import from this one, so a top-level
    # import would create a cycle.
    from gaia_agent_email.context_budget import estimate_tokens, thread_budget_tokens
    from gaia_agent_email.tools.summarize_tools import (
        _THREAD_SYSTEM_PROMPT,
        DEFAULT_SUMMARY_CHAR_LIMIT,
        EmailSummarizeError,
        _bound_to_length,
    )
    from gaia_agent_email.tools.thread_fold import (
        DEFAULT_THREAD_FOLD_MESSAGE_CEILING,
        fold_older_blocks,
    )

    if max_chars is None:
        max_chars = DEFAULT_SUMMARY_CHAR_LIMIT

    with log_tool_call("summarize_thread", {"thread_id": thread_id}, debug=debug) as st:
        if chat is None:
            # message_id field reused to carry the thread_id throughout this path.
            raise EmailSummarizeError(
                f"summarize_thread has no LLM connection for thread "
                f"{thread_id!r}; the agent's chat client is not initialized",
                message_id=thread_id,
            )
        thread = gmail.get_thread(thread_id)
        messages = thread.get("messages", []) or []
        if not messages:
            raise EmailSummarizeError(
                f"thread {thread_id!r} has no messages to summarize",
                message_id=thread_id,
            )

        ordered = sorted(messages, key=_thread_message_sort_key)
        total_count = len(ordered)
        first_headers = {
            (h.get("name") or "").lower(): h.get("value", "")
            for h in (ordered[0].get("payload") or {}).get("headers", [])
        }
        subject = first_headers.get("subject", "")

        # Message-count ceiling BEFORE any per-message decode/render work — a
        # cheap slice keeping the most recent messages, so an absurdly long
        # thread never pays O(N) MIME decoding just to be folded anyway.
        ceiling_dropped = 0
        if total_count > DEFAULT_THREAD_FOLD_MESSAGE_CEILING:
            ceiling_dropped = total_count - DEFAULT_THREAD_FOLD_MESSAGE_CEILING
            ordered = ordered[ceiling_dropped:]

        # Render each message exactly ONCE (one decode per message — both the
        # fits check and the fold reuse these blocks). The joined blocks are
        # byte-identical to the pre-existing uncapped renderer's output
        # (``_format_thread_for_summary(..., max_total_transcript_chars=None)``),
        # which delegates to the same ``_thread_message_blocks``.
        blocks, decoded_bodies = _thread_message_blocks(
            ordered, per_message_body_limit=per_message_body_limit
        )

        # Deterministic meeting-request scan over the NEWEST message's own
        # decoded body (#2641), reusing the decode above rather than paying
        # for a second one — same heuristic triage_inbox runs on the
        # snippet, but this path already has the full body, so use it.
        from gaia_agent_email.tools.calendar_tools import (
            detect_meeting_request_heuristic,
        )

        meeting = detect_meeting_request_heuristic(subject, decoded_bodies[-1])
        # Same high-confidence-only gate as triage_inbox (~line 988) — a
        # confidence="low" result always pairs with is_meeting_request=False
        # today, but the explicit AND keeps this call site correct even if
        # the heuristic's confidence semantics change later.
        meeting_detected = meeting.is_meeting_request and meeting.confidence == "high"

        full_transcript = "\n\n".join(blocks)
        fold_stats: List[dict] = []
        if estimate_tokens(full_transcript) <= thread_budget_tokens():
            transcript = full_transcript
            if ceiling_dropped:
                # Bounded and visible, never a silent clip (same marker as the
                # fold input's) — oldest-first transcript, so it leads.
                transcript = (
                    f"[omitted {ceiling_dropped} older messages]\n\n{transcript}"
                )
        else:
            # Over budget: keep the latest message's block verbatim; fold
            # every older block into ONE digest call.
            digest = fold_older_blocks(
                blocks[:-1],
                chat=chat,
                subject=subject,
                collect_stats=fold_stats,
                pre_omitted=ceiling_dropped,
            )
            condensed_block = (
                f"--- Condensed summary of {len(blocks) - 1 + ceiling_dropped} "
                f"earlier messages ---\n{wrap_untrusted_body(digest)}"
            )
            transcript = "\n\n".join([condensed_block, blocks[-1]])

        prompt = _build_thread_user_prompt(
            subject, transcript, meeting_detected=meeting_detected
        )
        try:
            response = chat.send_messages(
                [{"role": "user", "content": prompt}],
                system_prompt=_THREAD_SYSTEM_PROMPT,
                temperature=0.0,
            )
        except Exception as exc:  # LLM/transport failure — surface, never default
            raise EmailSummarizeError(
                f"LLM thread summarization call failed for thread {thread_id!r}: "
                f"{type(exc).__name__}: {exc}",
                message_id=thread_id,
            ) from exc

        text = getattr(response, "text", None)
        if text is None:
            text = response if isinstance(response, str) else ""
        text = str(text).strip()
        if not text:
            raise EmailSummarizeError(
                f"LLM thread summarization returned an empty summary for thread "
                f"{thread_id!r}",
                message_id=thread_id,
            )
        summary = _bound_to_length(text, max_chars)

        st["result_summary"] = {
            "thread_id": thread_id,
            "message_count": total_count,
            "chars": len(summary),
        }
        result = {
            "thread_id": thread_id,
            "subject": subject,
            "message_count": total_count,
            "summary": summary,
        }
        # Fold-call usage mirrors the REST path's accounting (#1891): a plain
        # dict, present only when the fold actually ran — absent on the fits
        # path (no extra LLM call to account for).
        usage = aggregate_usage_stats(fold_stats)
        if usage is not None:
            result["usage"] = usage
        return result


# Gmail's after:/before:/older:/newer: operators only accept YYYY/MM/DD (or
# epoch seconds). Anything else — e.g. the model's `after:July 1` — is treated
# as a free-text content match, silently returning 0 results (#2161).
_MONTH_NAMES = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}  # fmt: skip

_MONTH_ALT = "|".join(sorted(_MONTH_NAMES, key=len, reverse=True))

# Value grammar: quoted string, "July 1[, 2026]", "1 July[, 2026]", or a
# single token. `older_than:`/`newer_than:` never match — the op name must be
# followed immediately by a colon.
_DATE_OP_RE = re.compile(
    rf"""
    \b(?P<op>after|before|older|newer):
    (?P<val>
        "[^"]*"
      | (?:{_MONTH_ALT})\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s*\d{{4}}\b)?
      | \d{{1,2}}\s+(?:{_MONTH_ALT})\b(?:,?\s*\d{{4}}\b)?
      | \S+
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

_ORDINAL_RE = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?$", re.IGNORECASE)


def _parse_gmail_date_value(raw: str, *, op: str) -> str:
    """Parse one date-operator value into Gmail's ``YYYY/MM/DD`` form.

    Accepts the formats the model actually produces: ``2026/07/01``,
    ``2026-07-01``, ``7/1/2026`` (US month-first), ``July 1[, 2026]``,
    ``1 July [2026]``. Epoch values (all digits, >= 8 chars) pass through —
    Gmail accepts them natively. Anything else raises ``ValueError``.
    """
    value = raw.strip().strip('"').strip()
    if value.isdigit() and len(value) >= 8:
        return value

    y = mo = d = None
    m = re.fullmatch(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", value)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", value)
        if m:
            mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            tokens = [t for t in re.split(r"[\s,]+", value) if t]
            if len(tokens) in (2, 3):
                a, b = tokens[0].lower(), tokens[1].lower()
                day_tok = None
                if a in _MONTH_NAMES and _ORDINAL_RE.fullmatch(b):
                    mo, day_tok = _MONTH_NAMES[a], b
                elif b in _MONTH_NAMES and _ORDINAL_RE.fullmatch(a):
                    mo, day_tok = _MONTH_NAMES[b], a
                if day_tok is not None:
                    d = int(_ORDINAL_RE.fullmatch(day_tok).group(1))
                    if len(tokens) == 3:
                        if not re.fullmatch(r"\d{4}", tokens[2]):
                            mo = d = None
                        else:
                            y = int(tokens[2])
                    else:
                        y = date.today().year

    if y is None or mo is None or d is None:
        raise ValueError(
            f"search_messages: cannot parse date value {raw!r} for the "
            f"'{op}:' operator. Use Gmail date format {op}:YYYY/MM/DD "
            f"(e.g. {op}:2026/07/01)."
        )
    try:
        date(y, mo, d)
    except ValueError as exc:
        raise ValueError(
            f"search_messages: {raw!r} is not a valid calendar date for the "
            f"'{op}:' operator ({exc}). Use {op}:YYYY/MM/DD "
            f"(e.g. {op}:2026/07/01)."
        ) from exc
    return f"{y:04d}/{mo:02d}/{d:02d}"


# Relative day-words Gmail cannot parse as absolute dates. For recency
# operators (after/newer) map them to the timezone-robust ``newer_than:``
# window instead of a fragile absolute date: Gmail evaluates ``after:DATE``
# against a Pacific-time day boundary, so a same-day message can fall on the
# wrong side of it for accounts in other timezones and be missed (#2406).
# ``newer_than:1d`` is relative to *now* and has no such boundary.
_RELATIVE_DAY_WINDOWS = {"today": "1d", "yesterday": "2d"}


def normalize_gmail_date_operators(query: str) -> str:
    """Rewrite date-operator values in ``query`` to Gmail's ``YYYY/MM/DD``.

    Relative recency words (``after:today`` / ``newer:yesterday``) are rewritten
    to the timezone-robust ``newer_than:`` window so a present same-day message
    is reliably matched. Raises ``ValueError`` on an otherwise-unparseable value
    — a loud error beats passing it through as free text and returning a false
    zero-result.
    """

    def _sub(m: "re.Match[str]") -> str:
        op = m.group("op").lower()
        bare = m.group("val").strip().strip('"').strip().lower()
        if op in ("after", "newer") and bare in _RELATIVE_DAY_WINDOWS:
            return f"newer_than:{_RELATIVE_DAY_WINDOWS[bare]}"
        return f"{op}:{_parse_gmail_date_value(m.group('val'), op=op)}"

    return _DATE_OP_RE.sub(_sub, query)


# Gmail search operators (a leading ``token:`` in the query). If a query
# already uses one, we treat it as intentional and never rewrite it.
_GMAIL_OPERATORS = (
    "from",
    "to",
    "cc",
    "bcc",
    "subject",
    "label",
    "is",
    "in",
    "has",
    "filename",
    "after",
    "before",
    "older",
    "newer",
    "older_than",
    "newer_than",
    "category",
    "list",
    "deliveredto",
    "rfc822msgid",
    "larger",
    "smaller",
    "size",
)
_OPERATOR_RE = re.compile(
    r"\b(?:" + "|".join(_GMAIL_OPERATORS) + r")\s*:", re.IGNORECASE
)


def has_gmail_operator(query: str) -> bool:
    """True if ``query`` already uses a Gmail search operator (``from:`` …)."""
    return bool(_OPERATOR_RE.search(query or ""))


def operatorize_query(query: str) -> str:
    """Turn a bare literal phrase into an operator query.

    A verbatim subject/brand phrase (e.g. ``"Netflix promotional email"``)
    matched as free text often returns zero hits even when the message is
    present; ``from:``/``subject:`` operators find it. Widen the search to
    match the phrase in either the sender or the subject.
    """
    cleaned = " ".join((query or "").split())
    return f"from:({cleaned}) OR subject:({cleaned})"


def search_messages_impl(
    gmail,
    *,
    query: str,
    max_results: int = 25,
    debug: bool = False,
    operator_retry: bool = True,
    budget_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    query = normalize_gmail_date_operators(query)
    with log_tool_call(
        "search_messages",
        {"query": query, "max_results": max_results},
        debug=debug,
    ) as st:
        listing = gmail.list_messages(query=query, max_results=max_results)
        stubs = listing.get("messages", [])
        retried_query = None
        # A literal-phrase query with zero hits is the #2114 failure mode:
        # retry once as an operator query before giving up. Only when the
        # user's query carried no operator of its own (else we'd second-guess
        # an intentional ``from:`` search).
        if not stubs and operator_retry and not has_gmail_operator(query):
            retried_query = operatorize_query(query)
            if retried_query != query:
                listing = gmail.list_messages(
                    query=retried_query, max_results=max_results
                )
                stubs = listing.get("messages", [])
        full_msgs = [gmail.get_message(stub["id"]) for stub in stubs]
        out = _format_messages_within_budget(
            full_msgs,
            tool_name="search_messages",
            max_results=max_results,
            budget_tokens=budget_tokens,
        )
        summary: Dict[str, Any] = {"count": len(out)}
        if retried_query is not None:
            summary["operator_retry"] = retried_query
        st["result_summary"] = summary
        return {"messages": out, "operator_retry": retried_query}


def list_labels_impl(gmail, *, debug: bool = False) -> List[Dict[str, Any]]:
    with log_tool_call("list_labels", debug=debug) as st:
        labels = gmail.list_labels()
        st["result_summary"] = {"count": len(labels)}
        return labels


def extract_sender_email(sender_header: str) -> str:
    """Extract the bare email address from a ``From`` header value.

    ``"Alice <alice@example.com>"`` → ``"alice@example.com"``. Falls back
    to the lowercased trimmed header when no angle brackets are present.
    Used by session-preference matching so users can name a sender by bare
    address regardless of how the underlying message renders the header.
    """
    if not sender_header:
        return ""
    raw = sender_header.strip()
    open_idx = raw.find("<")
    close_idx = raw.find(">", open_idx + 1) if open_idx >= 0 else -1
    if open_idx >= 0 and close_idx > open_idx:
        return raw[open_idx + 1 : close_idx].strip().lower()
    return raw.lower()


def _apply_session_preferences(
    decision: Dict[str, Any], prefs: Mapping[str, Any]
) -> Dict[str, Any]:
    """Layer session-scoped sender overrides onto a heuristic decision.

    Mutates a copy of ``decision`` and returns it. Sender overrides take
    precedence over the heuristic; the original heuristic rationale is
    preserved alongside the override reason so the UI / logs still see
    why the heuristic would have classified the message differently.

    Safety override: a phishing-flagged message bypasses BOTH priority
    and low-priority sender preferences. A user can't safely promote a
    phishing message to urgent (the LLM might act on its links) or
    silently archive one (then they never see the threat). Phishing
    messages stay where the heuristic put them — typically actionable
    in the pre-scan envelope — so the user reviews them. Spam follows
    the same rule for the same reason.
    """
    sender_addr = extract_sender_email(decision.get("from", ""))
    priority_senders = prefs.get("priority_senders") or set()
    low_priority_senders = prefs.get("low_priority_senders") or set()
    out = dict(decision)
    if decision.get("is_phishing") or decision.get("is_spam"):
        # Phishing / spam wins over preferences. Record that we
        # considered an override but refused so logs make the decision
        # visible during incident review.
        if sender_addr and (
            sender_addr in priority_senders or sender_addr in low_priority_senders
        ):
            out["preference_applied"] = "skipped_phishing_or_spam"
        return out
    if sender_addr and sender_addr in priority_senders:
        out["category"] = CATEGORY_URGENT
        out["confident"] = True
        out["preference_applied"] = "priority_sender"
        out["rationale"] = (
            f"priority sender (session preference): {sender_addr} "
            f"[heuristic said: {decision.get('rationale', '')}]"
        )
    elif sender_addr and sender_addr in low_priority_senders:
        out["category"] = CATEGORY_PROMOTIONAL
        out["confident"] = True
        out["preference_applied"] = "low_priority_sender"
        out["rationale"] = (
            f"low-priority sender (session preference): {sender_addr} "
            f"[heuristic said: {decision.get('rationale', '')}]"
        )
    return out


def _list_all_stubs(
    gmail,
    *,
    label_ids: Optional[List[str]],
    max_messages: int,
) -> Dict[str, Any]:
    """Page through ``gmail.list_messages`` until ``max_messages`` unique
    stubs are collected or the backend has no more (#2634).

    ``nextPageToken`` is followed verbatim across calls — for Outlook that
    token IS the ``@odata.nextLink`` absolute URL, so re-deriving params
    instead of passing it straight back would silently restart at page 1.
    Never trusts a page to honour ``max_results``: Outlook's continuation
    ignores it entirely and can hand back more than requested, so the
    accumulator is clamped to ``max_messages`` after every page. Message
    ids are de-duplicated across pages — a mailbox has no snapshot
    isolation, so the same id can legitimately reappear on two pages if
    the mailbox mutates mid-scan.

    Each call requests ``max_results=`` however many messages are still
    wanted, never a fixed page-size constant (a fixed constant would ask
    for more than the caller's own budget on a later page).

    A page-2+ failure propagates (never a silent partial result) — this
    function adds no try/except around ``list_messages``, so whatever the
    backend raises reaches the caller unchanged, consistent with the
    fail-loud rule the rest of this package follows.

    Returns ``{"stubs": [...], "scanned": int, "scan_truncated": bool,
    "resultSizeEstimate": Any}``. ``scan_truncated`` is derived solely from
    the last-fetched page's own cursor — never from ``len(stubs) >=
    max_messages`` alone, which is honest only by coincidence and wrong
    the moment a mailbox's true size exactly equals the request.
    """
    labels = list(label_ids) if label_ids else ["INBOX"]
    stubs: List[Dict[str, Any]] = []
    seen_ids: set = set()
    page_token: Optional[str] = None
    next_token: Optional[str] = None
    result_size_estimate: Any = None
    first_page = True

    while len(stubs) < max_messages:
        remaining = max_messages - len(stubs)
        listing = gmail.list_messages(
            label_ids=labels,
            max_results=remaining,
            page_token=page_token,
        )
        if first_page:
            result_size_estimate = listing.get("resultSizeEstimate")
            first_page = False
        for stub in listing.get("messages", []) or []:
            mid = stub.get("id")
            if mid in seen_ids:
                continue
            seen_ids.add(mid)
            stubs.append(stub)
        if len(stubs) > max_messages:
            stubs = stubs[:max_messages]
        next_token = listing.get("nextPageToken")
        if not next_token:
            break
        page_token = next_token

    return {
        "stubs": stubs,
        "scanned": len(stubs),
        "scan_truncated": bool(next_token),
        "resultSizeEstimate": result_size_estimate,
    }


def triage_inbox_impl(
    gmail,
    *,
    max_messages: int = 25,
    label_ids: Optional[List[str]] = None,
    session_preferences: Optional[Mapping[str, Any]] = None,
    force_llm: bool = False,
    classifier: Optional[Callable[..., Mapping[str, Any]]] = None,
    debug: bool = False,
    progress: Optional[Callable[[int, int, str], None]] = None,
) -> Dict[str, Any]:
    """Triage the inbox using heuristic fast path + LLM fallback.

    ``progress(done, total, subject)`` is called after each message when
    supplied. A single LLM follow-up costs 9-31s locally, so a 25-message scan
    can sit silent for a minute; the callback is what turns that into visible
    movement. It must never break the scan — callers get their exceptions
    swallowed and logged, because narration is not worth losing a triage over.

    For each message: fetch metadata, run the heuristic. If the heuristic
    is confident, record its category as the triage decision. Otherwise
    (and always for ``urgent`` vs ``actionable``, which depend on body
    content) the message needs LLM follow-up.

    LLM follow-up (#1107): when ``classifier`` is provided, a heuristic
    ``confident=False`` message has its body read and classified by the
    LLM via ``classifier(subject=, sender=, body=, message_id=)`` →
    ``{category, is_spam, confidence, reasoning}``. The result is recorded
    with ``confident=True`` and ``source="llm"``. If the classifier raises
    (LLM unreachable, unparseable output, or an out-of-taxonomy category)
    the exception propagates — we never silently default to
    ``informational``. When ``classifier`` is None, the message is left
    flagged (``confident=False``) for a caller that sequences LLM calls
    itself — preserving the heuristic-only path.

    ``is_spam`` follow-up (#1906) is independent of category confidence: the
    heuristic only commits ``is_spam`` for a narrow, mechanical sender-pattern
    signal (``spam_confident=True``); a ``spam_confident=False`` message gets
    the same LLM call (no extra round-trip) and only its ``is_spam`` field is
    applied from the response — an already-confident category is never
    silently overridden by a spam-only escalation, and vice versa.

    When ``force_llm`` is True, every message is routed to the classifier
    (if provided) regardless of heuristic confidence — used for
    benchmarking to measure true inference cost across all emails.

    When ``session_preferences`` is provided, sender-based overrides
    (priority / low-priority) are layered on top of the heuristic before
    the result is recorded. The override is recorded in the decision's
    ``preference_applied`` field for downstream inspection.

    Returns a summary listing per-message classifications + a bucketed
    view via ``group_by_category``. Also passes through the listing call's
    raw ``resultSizeEstimate`` (whatever the backend reports — a real
    mailbox estimate for Gmail, ``None`` for Outlook, #2584) and an honest
    ``scan_truncated`` (#2634 — True only when the backend's own paging
    cursor says more mail exists beyond what was collected) so a caller
    like ``pre_scan_inbox_impl`` can report scan coverage without a second
    round-trip. ``label_ids`` defaults to ``["INBOX"]`` (this tool's
    existing behavior); a caller wanting a narrower query (e.g. unread-only
    for coverage honesty) can override it.

    The listing itself pages via ``_list_all_stubs`` (#2634) until
    ``max_messages`` is collected or the mailbox is exhausted — previously
    this issued a single ``list_messages`` call and silently capped
    coverage at one provider page regardless of what was requested.
    """
    # Local import breaks a real import cycle: calendar_tools imports
    # DEFAULT_BODY_LIMIT_CHARS from this module at module scope, so importing
    # calendar_tools back at module scope here would close the loop.
    from gaia_agent_email.tools.calendar_tools import detect_meeting_request_heuristic

    prefs = session_preferences or {}
    with log_tool_call(
        "triage_inbox", {"max_messages": max_messages}, debug=debug
    ) as st:
        listing = _list_all_stubs(
            gmail, label_ids=label_ids, max_messages=max_messages
        )
        stubs = listing["stubs"]
        results: List[Dict[str, Any]] = []
        for stub in stubs:
            msg = gmail.get_message(stub["id"])
            payload_headers = {
                (h.get("name") or "").lower(): h.get("value", "")
                for h in (msg.get("payload") or {}).get("headers", [])
            }
            heuristic = classify_category_heuristic(
                subject=payload_headers.get("subject", ""),
                sender=payload_headers.get("from", ""),
                label_ids=msg.get("labelIds", []),
                body=msg.get("snippet", ""),
            )
            # Meeting-request detection (#2583) — reads the same already-
            # fetched snippet as the category heuristic above, never the
            # decoded full body, so the scan stays cheap (#1265). Gated on
            # BOTH is_meeting_request and confidence=="high": the heuristic's
            # no-signal branch also returns confidence="high" (a confident
            # NEGATIVE), so confidence alone is not a safe gate.
            meeting = detect_meeting_request_heuristic(
                payload_headers.get("subject", ""), msg.get("snippet", "")
            )
            is_meeting_request = (
                meeting.is_meeting_request and meeting.confidence == "high"
            )
            log_triage_dispatch(
                message_id=msg["id"],
                decision="heuristic" if heuristic.confident else "needs_llm",
                label_ids=msg.get("labelIds", []),
                rule_reason=heuristic.reason,
            )
            decision = {
                "id": msg["id"],
                "thread_id": msg.get("threadId"),
                "subject": payload_headers.get("subject", ""),
                "from": payload_headers.get("from", ""),
                # Provider system labels (Gmail labelIds / Outlook-derived) —
                # the autonomy cycle reads the IMPORTANT flag off this to gate
                # auto-archive (#2426).
                "label_ids": list(msg.get("labelIds", [])),
                "category": heuristic.category,
                "is_spam": heuristic.is_spam,
                "is_phishing": heuristic.is_phishing,
                "confident": heuristic.confident and not force_llm,
                "rationale": (
                    f"forced LLM bypass (was: {heuristic.reason})"
                    if force_llm and heuristic.confident
                    else heuristic.reason
                ),
                "source": "heuristic",
                # Epoch-millis string (Gmail-native; #2584 — used by pre-scan
                # to order the needs_review bucket newest-first). Not part of
                # any public envelope; internal-only.
                "internal_date": msg.get("internalDate"),
                # Meeting-request signal (#2583) — orthogonal to category;
                # carried through to the pre-scan envelope for downstream
                # rendering (#2582).
                "is_meeting_request": is_meeting_request,
            }

            # LLM follow-up (#1107; is_spam added #1906): re-classify when the
            # heuristic is not confident about category OR not confident about
            # is_spam (or force_llm), if a classifier is wired in. Raises on
            # failure — never silently defaults the category. Category and
            # is_spam are applied independently: a spam-only escalation must
            # not let the LLM silently override an already-confident category,
            # and vice versa.
            needs_llm = (
                not heuristic.confident or not heuristic.spam_confident or force_llm
            )
            if classifier is not None and needs_llm:
                body_text, _ = decode_message_body(msg.get("payload") or {})
                llm = classifier(
                    subject=decision["subject"],
                    sender=decision["from"],
                    body=body_text,
                    message_id=msg["id"],
                )
                if not heuristic.confident or force_llm:
                    decision["category"] = llm["category"]
                    decision["confident"] = True
                    decision["source"] = "llm"
                    if llm.get("reasoning"):
                        decision["rationale"] = llm["reasoning"]
                    if llm.get("confidence") is not None:
                        decision["llm_confidence"] = llm["confidence"]
                if not heuristic.spam_confident:
                    decision["is_spam"] = bool(llm.get("is_spam", heuristic.is_spam))

            decision = _apply_session_preferences(decision, prefs)
            log_triage_decision(
                message_id=msg["id"],
                category=decision["category"],
                is_spam=decision["is_spam"],
                is_phishing=decision["is_phishing"],
                confidence="heuristic" if decision["confident"] else "needs_llm",
                rationale=decision["rationale"],
                debug=debug,
            )
            results.append(decision)
            if progress is not None:
                try:
                    progress(
                        len(results),
                        len(stubs),
                        payload_headers.get("subject", "") or "(no subject)",
                    )
                except Exception as exc:  # noqa: BLE001
                    log.debug("triage progress callback failed: %s", exc)
        grouped = group_by_category(results)
        st["result_summary"] = {
            "total": grouped["total"],
            "spam_count": len(grouped["spam"]),
            "phishing_count": len(grouped["phishing"]),
        }
        return {
            "results": results,
            "grouped": grouped,
            "resultSizeEstimate": listing["resultSizeEstimate"],
            "scan_truncated": listing["scan_truncated"],
        }


# Default per-section caps for the pre-scan envelope. Small enough to be
# scannable in a single screen; large enough to surface most of the inbox
# signal for a typical morning triage session. Callers can override via
# the tool kwargs if a heavier inbox needs more headroom.
PRE_SCAN_URGENT_CAP = 5
PRE_SCAN_ACTIONABLE_CAP = 5
PRE_SCAN_ARCHIVE_CAP = 10
# A real corpus with no label signal is majority-unconfident (#2584 —
# 296/305 messages in the vendor seed corpus) — uncapped, this bucket would
# read as "0 actionable, 290 need review" and be a worse UX than the bug it
# fixes. Capped like its three siblings above; the uncapped count still
# reaches the caller via ``totals["needs_review"]``.
PRE_SCAN_NEEDS_REVIEW_CAP = 5

# Adding UNREAD narrows the pre-scan listing query to unread mail only, so the
# backend's resultSizeEstimate means "how many unread" (the honest coverage
# denominator, #2584) instead of "how big is the inbox". triage_inbox (the
# expensive full-triage tool) keeps its default ["INBOX"] query — this is
# pre-scan-only.
_PRE_SCAN_LABEL_IDS = ["INBOX", "UNREAD"]


def _parse_epoch_millis(raw: Any) -> int:
    """Parse a Gmail-style epoch-millis string; 0 (oldest) when absent/bad.

    Mirrors ``_thread_message_sort_key``'s defensive parsing so a missing or
    malformed timestamp sorts last rather than raising.
    """
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _looks_automated(sender: str) -> bool:
    """Cheap human-vs-automated signal for needs_review ordering only.

    Does not affect classification or bucketing — display ordering only.
    """
    sender_lower = (sender or "").lower()
    return any(kw in sender_lower for kw in _NEEDS_REVIEW_AUTOMATED_SENDER_KEYWORDS)


def _needs_review_sort_key(decision: Mapping[str, Any]) -> tuple:
    """Deterministic needs_review order: newest first, human senders before
    automated ones on a same-timestamp tie (#2584).

    An arbitrary slice of a 295-candidate bucket down to 5 rendered rows is
    close to useless to a reader — this makes which 5 surface a defensible,
    stated policy instead of an accident of backend scan order.
    """
    internal_date = _parse_epoch_millis(decision.get("internal_date"))
    return (-internal_date, _looks_automated(decision.get("from", "")))


def _fetch_total_unread(gmail) -> Optional[int]:
    """Exact unread-inbox count via ``labels().get`` — NOT ``list_messages``'s
    ``resultSizeEstimate`` (#2584).

    Measured against a real mailbox: ``resultSizeEstimate`` for
    ``label_ids=[INBOX, UNREAD]`` reported 201 while full pagination of the
    identical query found 523 real message ids — Google documents that field
    as approximate, and 2.6x off is not a fabricated-placeholder-grade lie
    (the Outlook page-size case) but it is still not honest enough to state
    as the scan-coverage denominator. The label resource's ``messagesUnread``
    is an exact integer. One call per SCAN, not per message — ``list_labels``
    returns the minimal label form with no counts, so this must be
    ``get_label``, not that.

    Backends that can't provide an honest count (Outlook — Graph has no
    equivalent resource) return ``messagesUnread: None`` from their own
    ``get_label``, which flows straight through here with no per-provider
    branching. A backend that doesn't implement ``get_label`` at all (a
    minimal test double, or a future provider) degrades the same way: this
    field is supplementary coverage metadata, never allowed to abort the
    scan itself if it can't be produced.
    """
    get_label = getattr(gmail, "get_label", None)
    if not callable(get_label):
        return None
    try:
        label = get_label("INBOX")
    except ConnectorsError as exc:
        log.warning("pre-scan: get_label(INBOX) failed, total_unread unknown: %s", exc)
        return None
    value = (label or {}).get("messagesUnread")
    return int(value) if isinstance(value, (int, float)) else None


def needs_review_decision(r: Mapping[str, Any]) -> bool:
    """True when a triage result belongs in the needs_review bucket (#2584).

    Single source of truth for "unconfident low-signal" routing: spam/phishing
    always wins (never needs_review — they're actionable), URGENT and
    NEEDS_RESPONSE never demote out of their buckets regardless of
    confidence, and everything else needs_review only when the heuristic was
    NOT confident. ``pre_scan_inbox_impl`` and the attention-view aggregator
    (#2582) both call this instead of each keeping their own copy of the
    routing rule, so a future change to it (like #2584 narrowing which
    categories it applies to) cannot silently diverge between the two.
    """
    if r.get("is_spam") or r.get("is_phishing"):
        return False
    if r.get("category") in (CATEGORY_URGENT, CATEGORY_NEEDS_RESPONSE):
        return False
    return not r.get("confident", True)


def pre_scan_inbox_impl(
    gmail,
    *,
    max_messages: int = 25,
    urgent_cap: int = PRE_SCAN_URGENT_CAP,
    actionable_cap: int = PRE_SCAN_ACTIONABLE_CAP,
    archive_cap: int = PRE_SCAN_ARCHIVE_CAP,
    needs_review_cap: int = PRE_SCAN_NEEDS_REVIEW_CAP,
    session_preferences: Optional[Mapping[str, Any]] = None,
    force_llm: bool = False,
    debug: bool = False,
) -> Dict[str, Any]:
    """Pre-scan the inbox for the chat surface.

    Reshapes ``triage_inbox_impl`` output into a typed envelope optimized
    for a daily-driver triage card: top-N urgent, top-N actionable,
    informational count, suggested archives derived from the low-priority
    bucket and (when configured) from category defaults, and a needs-review
    bucket for messages the heuristic was not confident about (#2584). The
    caller is expected to set ``kind`` in the rendered output to
    ``email_pre_scan`` so the chat surface can detect and render the
    structured card component.

    ``session_preferences`` flow through to ``triage_inbox_impl`` so
    sender overrides shape the underlying classification, and category
    defaults applied here move informational items into
    ``suggested_archives`` when the user has previously asked for that.

    A ``confident=False`` heuristic result is a placeholder guess, not a
    real classification. It overrides routing into the two LOW-SIGNAL
    buckets only — ``informational`` and ``suggested_archives`` — sending
    the message to ``needs_review`` instead (an unconfident PROMOTIONAL
    guess, for instance, must not be recommended for archival). It does
    NOT pull a message out of ``urgent``/``actionable``: an unconfident
    guess toward a HIGH-signal category (e.g. an IMPORTANT/STARRED-flagged
    message the heuristic can't yet tell is urgent vs. merely actionable)
    already errs toward surfacing, which is the direction to err in — an
    unconfident guess must never make a message LESS visible than a
    confident one would. This check runs AFTER the spam/phishing safety
    override, which still always wins. ``needs_review`` is ordered
    newest-first (human senders before automated ones on a timestamp tie)
    before the cap is applied, so which N of a large uncapped bucket
    surface is a stated policy, not scan-order luck.

    Drafts are intentionally left as an empty list in this version — the
    ``suggested_drafts`` field is reserved for future LLM-driven draft
    generation. Returning the field with a stable shape lets the frontend
    schema lock in now and lets the backend fill it later without a
    breaking change.
    """
    prefs = session_preferences or {}
    category_defaults = prefs.get("category_defaults") or {}

    with log_tool_call(
        "pre_scan_inbox",
        {"max_messages": max_messages},
        debug=debug,
    ) as st:
        triage = triage_inbox_impl(
            gmail,
            max_messages=max_messages,
            label_ids=_PRE_SCAN_LABEL_IDS,
            session_preferences=prefs,
            force_llm=force_llm,
            debug=debug,
        )
        urgent: List[Dict[str, Any]] = []
        actionable: List[Dict[str, Any]] = []
        informational: List[Dict[str, Any]] = []
        suggested_archives: List[Dict[str, Any]] = []
        needs_review_ranked: List[tuple] = []

        for r in triage["results"]:
            base = {
                "message_id": r["id"],
                "thread_id": r.get("thread_id"),
                "sender": r.get("from", ""),
                "subject": r.get("subject", ""),
                "is_meeting_request": bool(r.get("is_meeting_request", False)),
            }
            why = r.get("rationale", "")
            category = r.get("category", CATEGORY_FYI)

            if r.get("is_spam") or r.get("is_phishing"):
                # Phishing/spam should never be silently archived from a
                # pre-scan suggestion. The user must see them. Surface as
                # actionable with a strong reason so the user reviews
                # before any automated action.
                actionable.append(
                    {
                        **base,
                        "why": (
                            (
                                "flagged as phishing"
                                if r.get("is_phishing")
                                else "flagged as spam"
                            )
                            + f" — {why}"
                            if why
                            else ""
                        ),
                    }
                )
                continue

            # confident=False only overrides routing into the two LOW-SIGNAL
            # buckets (#2584) — an unconfident guess must never make a
            # message LESS visible than a confident one would, so URGENT and
            # NEEDS_RESPONSE keep their category-based routing regardless of
            # confidence (e.g. an IMPORTANT/STARRED message the heuristic
            # can't yet tell is urgent vs. merely actionable already errs
            # toward surfacing — that is the correct direction to err).
            if category == CATEGORY_URGENT:
                urgent.append({**base, "why": why})
            elif category == CATEGORY_NEEDS_RESPONSE:
                actionable.append({**base, "why": why})
            elif category == CATEGORY_PROMOTIONAL:
                if needs_review_decision(r):
                    needs_review_ranked.append(
                        (_needs_review_sort_key(r), {**base, "why": why})
                    )
                else:
                    suggested_archives.append({**base, "reason": why})
            else:
                # FYI and PERSONAL share the keep / no-action bucket when
                # confident; unconfident goes to needs_review instead (the
                # #2584 incident: a bare question falling through every rule
                # to the terminal FYI-placeholder fallback). Routed through
                # needs_review_decision (shared with the attention-view
                # aggregator, #2582) rather than a local confidence check.
                if needs_review_decision(r):
                    needs_review_ranked.append(
                        (_needs_review_sort_key(r), {**base, "why": why})
                    )
                else:
                    informational.append({**base, "why": why})

        needs_review_ranked.sort(key=lambda pair: pair[0])
        needs_review = [item for _, item in needs_review_ranked]

        # Apply the FYI category default: when the user has previously asked
        # us to archive FYI mail, lift those items into suggested_archives.
        # (The ``informational`` list holds both FYI and PERSONAL — the keep
        # bucket — but only the FYI default promotes to archive.) Never
        # applies to ``needs_review`` — an unconfident guess must not be
        # silently archived by a stale category preference.
        if category_defaults.get(CATEGORY_FYI) == "archive":
            for item in informational:
                suggested_archives.append(
                    {
                        "message_id": item["message_id"],
                        "thread_id": item.get("thread_id"),
                        "sender": item["sender"],
                        "subject": item["subject"],
                        "is_meeting_request": item.get("is_meeting_request", False),
                        "reason": (
                            "informational + session default 'archive'"
                            f" — {item.get('why', '')}"
                        ).rstrip(" —"),
                    }
                )
            informational = []

        scanned = len(triage["results"])
        out = {
            "kind": "email_pre_scan",
            "urgent": urgent[: max(0, urgent_cap)],
            "actionable": actionable[: max(0, actionable_cap)],
            "informational_count": len(informational),
            "suggested_archives": suggested_archives[: max(0, archive_cap)],
            "suggested_drafts": [],
            "needs_review": needs_review[: max(0, needs_review_cap)],
            "preferences_applied": {
                "priority_senders": sorted(prefs.get("priority_senders") or []),
                "low_priority_senders": sorted(prefs.get("low_priority_senders") or []),
                "category_defaults": dict(category_defaults),
            },
            "totals": {
                "urgent": len(urgent),
                "actionable": len(actionable),
                "informational": len(informational),
                "suggested_archives": len(suggested_archives),
                "needs_review": len(needs_review),
            },
            "scanned": scanned,
            "total_unread": _fetch_total_unread(gmail),
            # Single-backend call: a backend failure always raises (never a
            # silent partial result), so this layer never degrades on its
            # own — only merge_pre_scan_backends' multi-mailbox fan-out can.
            "degraded": False,
        }
        st["result_summary"] = {
            "urgent": out["totals"]["urgent"],
            "actionable": out["totals"]["actionable"],
            "informational": out["totals"]["informational"],
            "suggested_archives": out["totals"]["suggested_archives"],
            "needs_review": out["totals"]["needs_review"],
            "scanned": scanned,
        }
        return out


def merge_pre_scan_backends(
    backends: "Mapping[str, Any]",
    *,
    max_messages: int = 25,
    session_preferences: Optional[Mapping[str, Any]] = None,
    force_llm: bool = False,
    debug: bool = False,
    remember_mailbox: Optional[Callable[[Optional[str], str], None]] = None,
) -> Dict[str, Any]:
    """Pre-scan every connected mailbox, tag each item, merge under budget.

    Single home for the multi-inbox consolidation (#1603/#1614) so both the
    agent loop (``EmailTriageAgent._pre_scan_all_backends``) and the REST
    ``/prescan`` path produce the identical envelope. Splits the total
    ``max_messages`` budget across ``backends`` (an ordered ``provider ->
    backend`` map); each merged item gains a ``mailbox`` tag. This is NOT a
    silent pick-one — every connected mailbox is scanned.

    A single backend's ``ConnectorsError`` (e.g. a revoked agent grant) is
    recorded in ``mailbox_errors`` and the loop continues with the rest; when
    EVERY backend fails the error is raised rather than returning a misleading
    empty pre-scan. A failed backend's share of ``max_messages`` is reclaimed
    by whichever backends are tried after it (#2584) — the split is
    recomputed each iteration from what's actually left, not fixed up front,
    so the surviving mailbox(es) get the full allowance instead of losing
    half the budget to a dead one. ``remember_mailbox`` is the agent's
    optional message-id -> mailbox recorder for action routing; the stateless
    REST path omits it.
    """
    prefs = session_preferences
    provider_backends = list(backends.items())
    remaining_budget = max_messages
    urgent: List[Dict[str, Any]] = []
    actionable: List[Dict[str, Any]] = []
    suggested_archives: List[Dict[str, Any]] = []
    needs_review: List[Dict[str, Any]] = []
    informational_count = 0
    scanned = 0
    total_unread = 0
    total_unread_unknown = False
    merged_prefs_applied: Dict[str, Any] = {}
    mailbox_errors: List[Dict[str, Any]] = []
    for index, (provider, backend) in enumerate(provider_backends):
        if scanned >= max_messages:
            break
        # Recomputed each iteration (never precomputed for every backend up
        # front): a backend that already failed does not consume a slot
        # below, so its share rolls forward to whatever is tried next
        # instead of being silently lost.
        backends_left = len(provider_backends) - index
        per_backend = max(1, remaining_budget // backends_left)
        try:
            out = pre_scan_inbox_impl(
                backend,
                max_messages=per_backend,
                session_preferences=prefs,
                force_llm=force_llm,
                debug=debug,
            )
        except ConnectorsError as exc:
            msg = format_connector_error(exc)
            mailbox_errors.append({"mailbox": provider, "error": msg})
            log.warning("email pre-scan: skipping %s mailbox — %s", provider, msg)
            continue
        remaining_budget = max(0, remaining_budget - per_backend)
        # Count messages actually returned, not the cap — an under-filled
        # backend would otherwise trip the budget guard and skip a later one.
        backend_totals = out.get("totals", {})
        scanned += (
            int(backend_totals.get("urgent", 0))
            + int(backend_totals.get("actionable", 0))
            + int(backend_totals.get("suggested_archives", 0))
            + int(backend_totals.get("needs_review", 0))
            + int(out.get("informational_count", 0))
        )
        merged_prefs_applied = out.get("preferences_applied", merged_prefs_applied)
        for section, dest in (
            ("urgent", urgent),
            ("actionable", actionable),
            ("suggested_archives", suggested_archives),
            ("needs_review", needs_review),
        ):
            for item in out.get(section, []):
                item["mailbox"] = provider
                if remember_mailbox is not None:
                    remember_mailbox(item.get("message_id"), provider)
                    remember_mailbox(item.get("thread_id"), provider)
                dest.append(item)
        informational_count += int(out.get("informational_count", 0))
        backend_total_unread = out.get("total_unread")
        if backend_total_unread is None:
            # This backend can't honestly report an unread count (Outlook,
            # #2584) — the merged total can't claim to be a whole-mailbox
            # number either, so it stays unknown rather than silently
            # summing only the known part.
            total_unread_unknown = True
        else:
            total_unread += int(backend_total_unread)
    result = {
        "kind": "email_pre_scan",
        "urgent": urgent[: max(0, PRE_SCAN_URGENT_CAP)],
        "actionable": actionable[: max(0, PRE_SCAN_ACTIONABLE_CAP)],
        "informational_count": informational_count,
        "suggested_archives": suggested_archives[: max(0, PRE_SCAN_ARCHIVE_CAP)],
        "suggested_drafts": [],
        "needs_review": needs_review[: max(0, PRE_SCAN_NEEDS_REVIEW_CAP)],
        "preferences_applied": merged_prefs_applied,
        "totals": {
            "urgent": len(urgent),
            "actionable": len(actionable),
            "informational": informational_count,
            "suggested_archives": len(suggested_archives),
            "needs_review": len(needs_review),
        },
        "scanned": scanned,
        "total_unread": None if total_unread_unknown else total_unread,
        "degraded": bool(mailbox_errors),
    }
    if mailbox_errors and len(mailbox_errors) == len(backends):
        # Every connected mailbox failed — surface it loudly rather than
        # returning ok with zero results (which reads as "empty inbox").
        raise ConnectorsError(
            "All connected mailboxes failed during pre-scan: "
            + "; ".join(f"{e['mailbox']}: {e['error']}" for e in mailbox_errors)
        )
    if mailbox_errors:
        result["mailbox_errors"] = mailbox_errors
    return result


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------


class ReadToolsMixin:
    """Mixin that registers the read-side tools.

    The mixin is state-free at construction time — it relies on the agent
    class having set ``self._gmail``, ``self._backends``, and the
    ``_backend_for_message`` routing helper (#1603 Phase 2) before invoking
    ``self._register_read_tools()``. The ``agent`` closure capture is used so
    triage / pre-scan tools can read live ``self._session_preferences`` (set
    on the agent instance) at call time, not snapshot at registration time.
    """

    def _register_read_tools(self) -> None:
        gmail = self._gmail
        debug_flag = bool(getattr(self.config, "debug", False))
        # An explicit EmailAgentConfig(inbox_scan_ceiling=...) must win over the
        # environment. Hosts may pass a duck-typed config (see debug_flag), so
        # an absent field falls back to the same env resolution the config field
        # uses — resolved once here, never re-read per call.
        scan_ceiling = getattr(self.config, "inbox_scan_ceiling", None)
        if scan_ceiling is None:
            scan_ceiling = default_inbox_scan_ceiling()
        agent = self  # captured for live access to ``_session_preferences``

        @tool
        def list_inbox(max_results: int = 25) -> str:
            """List the most recent INBOX messages.

            When multiple mailboxes are connected, lists from ALL of them with a
            shared total budget (never per-mailbox-doubled). Each returned message
            carries a ``mailbox`` field ('google' / 'microsoft') so downstream
            tools can route actions without re-asking. One mailbox failing (e.g. a
            broken token) does not abort the others — its messages are omitted and
            a ``mailbox_errors`` entry is added; only if EVERY mailbox fails does
            the tool return an error.

            A large ``max_results`` may shrink every message's body TOGETHER
            (never independently, never dropping a message) so the whole result
            stays within the model's context window — shrunk messages report
            ``body_truncated: true``. If even the smallest usable body can't fit
            every requested message, the tool returns an actionable error instead
            of silently returning fewer messages than asked for — retry with a
            smaller ``max_results``.

            Args:
                max_results: How many messages to return in total (default 25, max 100).

            Returns:
                JSON envelope with ``{"messages": [...]}`` per message:
                id, thread_id, subject, from, to, date, label_ids,
                snippet, body (wrapped in untrusted-input delimiters),
                body_truncated, body_chars_dropped, attachments, mailbox.
                A ``mailbox_errors`` list is present when a connected mailbox
                failed but at least one other returned results.
            """
            try:
                max_results = max(1, min(int(max_results or 25), 100))
                backends = agent._backends
                if not backends:
                    return _envelope_err(NO_MAILBOX_CONNECTED_MESSAGE)
                per_backend = max(1, max_results // len(backends))
                merged: List[Dict[str, Any]] = []
                mailbox_errors: List[Dict[str, Any]] = []
                for provider, backend in backends.items():
                    if len(merged) >= max_results:
                        break
                    # Isolate per-provider failures: a broken token on one
                    # mailbox (e.g. Microsoft invalid_request on refresh) must
                    # not abort the listing across a healthy Google mailbox.
                    try:
                        result = list_inbox_impl(
                            backend, max_results=per_backend, debug=debug_flag
                        )
                    except ConnectorsError as exc:
                        msg = format_connector_error(exc)
                        mailbox_errors.append({"mailbox": provider, "error": msg})
                        log.warning(
                            "email list_inbox: skipping %s mailbox — %s", provider, msg
                        )
                        continue
                    for msg in result.get("messages", []):
                        msg["mailbox"] = provider
                        agent._remember_message_mailbox(msg.get("id"), provider)
                        agent._remember_message_mailbox(msg.get("thread_id"), provider)
                        merged.append(msg)
                if mailbox_errors and len(mailbox_errors) == len(backends):
                    # Every connected mailbox failed — surface it loudly rather
                    # than returning ok with zero results (reads as empty inbox).
                    raise ConnectorsError(
                        "All connected mailboxes failed during list_inbox: "
                        + "; ".join(
                            f"{e['mailbox']}: {e['error']}" for e in mailbox_errors
                        )
                    )
                out: Dict[str, Any] = {
                    "messages": merged[:max_results],
                    "next_page_token": None,
                }
                if mailbox_errors:
                    out["mailbox_errors"] = mailbox_errors
                return _envelope_ok(out)
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def get_message(
            message_id: str, mailbox: str = "", full_body: bool = False
        ) -> str:
            """Fetch a single message by id.

            The body is truncated at 4000 chars by default for context safety.
            Set ``full_body=True`` ONLY when the user explicitly asks to see
            the complete/untruncated message — never as a self-directed step
            while triaging or analyzing a message on your own initiative. The
            body stays wrapped in the untrusted-input delimiters either way,
            and the result reports ``body_truncated`` / ``body_chars_dropped``.

            ``mailbox`` (optional) names the source mailbox ('google' /
            'microsoft') from triage output so the read routes correctly when
            multiple mailboxes are connected.
            """
            try:
                body_limit = (
                    MAX_FULL_BODY_CHARS if full_body else DEFAULT_BODY_LIMIT_CHARS
                )
                backend = agent._backend_for_message(message_id, mailbox or None)
                return _envelope_ok(
                    get_message_impl(
                        backend,
                        message_id=message_id,
                        body_limit=body_limit,
                        debug=debug_flag,
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def get_thread(thread_id: str, mailbox: str = "") -> str:
            """Fetch every message in a thread (conversation view).

            Messages are returned sorted chronologically (oldest first) and
            each carries ``index``/``of_total`` (its 1-based position in the
            thread) — use these, not the raw list order, when listing or
            counting messages. Long threads share a combined body budget:
            over-budget message bodies are clipped with a
            ``...[truncated]`` marker; messages are never dropped.
            ``mailbox`` (optional) routes when multiple mailboxes are
            connected.
            """
            try:
                backend = agent._backend_for_message(thread_id, mailbox or None)
                return _envelope_ok(
                    get_thread_impl(backend, thread_id=thread_id, debug=debug_flag)
                )
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def summarize_thread(thread_id: str, mailbox: str = "") -> str:
            """Summarize an entire email thread, not just its latest message.

            Reads every message in the thread and produces one concise,
            length-bounded summary that reflects decisions, asks, and outcomes
            across the WHOLE conversation — including earlier messages the most
            recent reply does not restate. Use this when the user asks what a
            thread or conversation is about, to catch up on a thread, or to
            summarize a multi-message exchange (prefer ``summarize_message`` for
            a single message).

            Args:
                thread_id: The id of the thread to summarize.

            Returns:
                JSON envelope ``{"ok": true, "data": {"thread_id", "subject",
                "message_count", "summary"}}`` — ``summary`` is a short,
                length-bounded string covering the full thread. When an
                over-budget thread was condensed to fit (#1889), ``data``
                also carries ``usage`` with the condense call's LLM tokens.
            """
            try:
                # Deferred import avoids a module-load cycle with summarize_tools.
                from gaia_agent_email.tools.summarize_tools import (
                    THREAD_SUMMARY_CHAR_LIMIT,
                    EmailSummarizeError,
                )

                chat = getattr(agent, "chat", None)
                backend = agent._backend_for_message(thread_id, mailbox or None)
                return _envelope_ok(
                    summarize_thread_impl(
                        backend,
                        chat,
                        thread_id=thread_id,
                        max_chars=THREAD_SUMMARY_CHAR_LIMIT,
                        debug=debug_flag,
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except EmailSummarizeError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def search_messages(query: str, max_results: int = 25) -> str:
            """Search across ALL connected mailboxes.

            When multiple mailboxes are connected, searches both with a shared
            total budget. Each returned message carries a ``mailbox`` field so
            downstream tools route actions without re-asking. One mailbox failing
            (e.g. a broken token) does not abort the others — its hits are omitted
            and a ``mailbox_errors`` entry is added to the envelope; only if EVERY
            mailbox fails does the tool return an error.

            ``query`` uses Gmail search syntax. ALWAYS prefer operators over a
            verbatim user phrase — a literal phrase like
            ``"Netflix promotional email"`` usually returns zero hits even when
            the message is present. Map the user's words to operators instead:

              - a sender / brand name → ``from:netflix`` (e.g. "the Netflix
                promo" → ``from:netflix``)
              - words expected in the subject → ``subject:invoice``
              - status / recency → ``is:unread``, ``newer_than:7d``,
                ``label:promotions``

            Combine them: ``"from:boss@example.com is:unread newer_than:7d"``.
            Date operators require ``YYYY/MM/DD`` — e.g. ``after:2026/07/01
            before:2026/07/08``, never ``after:July 1``. If a bare phrase is
            passed and returns nothing, the tool retries once as an operator
            query automatically, but forming the operator query yourself is
            more reliable.

            A large ``max_results`` may shrink every hit's body TOGETHER (never
            independently, never dropping a hit) so the whole result stays
            within the model's context window — shrunk messages report
            ``body_truncated: true``. If even the smallest usable body can't fit
            every requested hit, the tool returns an actionable error instead of
            silently returning fewer hits than asked for — retry with a smaller
            ``max_results``.
            """
            try:
                max_results = max(1, min(int(max_results or 25), 100))
                backends = agent._backends
                if not backends:
                    return _envelope_err(NO_MAILBOX_CONNECTED_MESSAGE)
                per_backend = max(1, max_results // len(backends))
                merged: List[Dict[str, Any]] = []
                mailbox_errors: List[Dict[str, Any]] = []
                for provider, backend in backends.items():
                    if len(merged) >= max_results:
                        break
                    # Isolate per-provider failures: a broken token on one
                    # mailbox (e.g. Microsoft invalid_request on refresh) must
                    # not abort the search across a healthy Google mailbox.
                    try:
                        result = search_messages_impl(
                            backend,
                            query=query,
                            max_results=per_backend,
                            debug=debug_flag,
                        )
                    except ConnectorsError as exc:
                        msg = format_connector_error(exc)
                        mailbox_errors.append({"mailbox": provider, "error": msg})
                        log.warning(
                            "email search_messages: skipping %s mailbox — %s",
                            provider,
                            msg,
                        )
                        continue
                    for msg in result.get("messages", []):
                        msg["mailbox"] = provider
                        agent._remember_message_mailbox(msg.get("id"), provider)
                        agent._remember_message_mailbox(msg.get("thread_id"), provider)
                        merged.append(msg)
                if mailbox_errors and len(mailbox_errors) == len(backends):
                    # Every connected mailbox failed — surface it loudly rather
                    # than returning ok with zero results (reads as no matches).
                    raise ConnectorsError(
                        "All connected mailboxes failed during search: "
                        + "; ".join(
                            f"{e['mailbox']}: {e['error']}" for e in mailbox_errors
                        )
                    )
                out: Dict[str, Any] = {"messages": merged[:max_results]}
                if mailbox_errors:
                    out["mailbox_errors"] = mailbox_errors
                return _envelope_ok(out)
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def list_labels() -> str:
            """List every label (system + user-defined) in the mailbox."""
            try:
                return _envelope_ok(list_labels_impl(gmail, debug=debug_flag))
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        # Full-inbox triage legitimately runs minutes on consumer hardware
        # (~55-65 tok/s) — grant headroom over the 180s global tool timeout so
        # a real triage isn't abandoned mid-run (#2114). pre_scan_inbox stays
        # the fast alternative for "what's urgent right now" asks.
        @tool(timeout=600.0)
        def triage_inbox(max_messages: int = 25) -> str:
            """Raw per-message classifier. NOT the tool for "triage my inbox".

            When the user asks to triage, review, or check their inbox, call
            ``pre_scan_inbox`` instead: it returns the typed card the chat
            surface draws, so every message is shown. This tool returns an
            unrendered verdict list that a model can only paraphrase — which
            loses most of the inbox. Use it only when you need the raw
            per-message categories for further computation.

            Categories: ``URGENT``, ``NEEDS_RESPONSE``, ``FYI``,
            ``PROMOTIONAL``, ``PERSONAL``. Each result also has ``is_spam`` and
            ``is_phishing`` booleans. The ``confident`` field is True
            when the heuristic alone was sufficient; False means the
            agent should re-classify the body via LLM follow-up.

            Session preferences set via ``set_priority_sender`` /
            ``set_low_priority_sender`` are honored — those senders
            bypass the heuristic and are recorded with
            ``preference_applied`` for downstream inspection.

            For a large batch the per-message ``results`` list may be
            condensed to fit the context budget: ``results_condensed`` is
            True, ``results_omitted`` counts the verdicts dropped from
            ``results``, and the ``grouped`` map still carries every
            message's id-to-category assignment. Use ``grouped`` (not
            ``results``) as the complete view when results are condensed.
            """
            try:
                max_messages = max(1, min(int(max_messages or 25), scan_ceiling))

                # Phase 2 (#1603): scan every connected mailbox, tag each item
                # with its source mailbox, split the budget across mailboxes,
                # and merge. LLM follow-up (#1107) is wired inside the agent
                # orchestration so agent.chat is initialized at call time.
                #
                # Condense the result envelope to the agent-loop ctx budget
                # (#2087): a large batch's verbatim verdict list overflows
                # CONTEXT_TARGET_TOKENS when the agent re-reads it next turn.
                # No-op below budget; verdicts themselves are unchanged.
                # Narrate per message: a single LLM follow-up is 9-31s locally,
                # so a silent scan looks hung. print_info reaches the live stream.
                def _narrate(done: int, total: int, subject: str) -> None:
                    console = getattr(agent, "console", None)
                    emit = getattr(console, "print_info", None)
                    if callable(emit):
                        emit(f"Triaged {done}/{total} — {subject[:60]}")

                # Only hosts that accept it get narration. Checked by signature,
                # not try/except TypeError — that would swallow a real TypeError
                # raised inside triage and blame it on the callback.
                kwargs = {"max_messages": max_messages}
                try:
                    import inspect as _inspect

                    if (
                        "progress"
                        in _inspect.signature(agent._triage_all_backends).parameters
                    ):
                        kwargs["progress"] = _narrate
                except (TypeError, ValueError) as exc:
                    log.debug("triage: host signature unreadable (%s)", exc)

                return _envelope_ok(
                    condense_triage_result(agent._triage_all_backends(**kwargs))
                )
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def pre_scan_inbox(max_messages: int = 25) -> str:
            """Pre-scan the inbox into a typed envelope for the chat
            triage card.

            Reshapes the per-message triage decisions into four sections
            (urgent, actionable, needs review, suggested archives), an
            informational count, and an empty drafts placeholder. The
            result has ``kind: "email_pre_scan"`` so the chat surface
            renders the structured card component instead of plain text.
            ``needs_review`` holds messages the heuristic was NOT
            confident about — a placeholder guess, not a real
            classification — so they are surfaced for you to look at
            rather than silently filed as informational or archived.

            The result is a PARTIAL view of the mailbox, not the whole
            inbox: ``scanned`` reports how many messages were actually
            looked at this call, and ``total_unread`` reports the
            mailbox's unread count when known (Gmail; Outlook cannot
            report this honestly and returns null). ALWAYS mention scan
            coverage in your framing sentence when scanned is less than
            total_unread — e.g. "12 of 508 unread scanned — 3
            actionable, 2 need review." — never phrase a partial scan as
            if it covered the whole inbox. When ``degraded`` is true or
            ``mailbox_errors`` is non-empty, say which mailbox couldn't
            be scanned.

            The chat surface injects the triage card automatically from
            the tool result — do NOT copy, re-serialize, or paraphrase
            the JSON envelope into your reply. Re-emitting the full
            envelope wastes the output budget on long message/thread IDs
            and truncates the prose summary before the user can read it.
            After this tool returns, write ONE short framing sentence
            (e.g. "Here's your inbox pre-scan — 3 actionable, 1 urgent,
            12 of 508 unread scanned.") and stop. The card is already
            visible to the user.

            Args:
                max_messages: How many unread INBOX messages to scan
                    (default 25, max 100).
            """
            try:
                max_messages = max(1, min(int(max_messages or 25), scan_ceiling))
                # Phase 2 (#1603): pre-scan every connected mailbox, tag each
                # section item with its source mailbox, split the budget, merge.
                return _envelope_ok(
                    agent._pre_scan_all_backends(max_messages=max_messages)
                )
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")
