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

import inspect
import json
import re
import time
from datetime import date
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from gaia_agent_email.body_normalize import (
    normalize_email_body,
    strip_quoted_trail,
    strip_reply_chain_and_signature,
)
from gaia_agent_email.config import (
    DEFAULT_INBOX_SCAN_MESSAGES,
    default_inbox_scan_ceiling,
)
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
    detect_phishing,
    group_by_category,
)
from gaia_agent_email.tools.usage import aggregate_usage_stats
from gaia_agent_email.verbose import (
    log_tool_call,
    log_triage_decision,
    log_triage_dispatch,
)

from gaia.agents.base.tools import tool
from gaia.connectors.errors import ConnectorsError, RateLimitedError
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
    decoded, banner-stripped, PRE-quote-strip, PRE-truncation body in the same
    order. A caller that needs one message's own body (e.g. the #2641
    meeting-signal scan over the newest message) reuses ``decoded_bodies[-1]``
    instead of paying for a second MIME decode of the same payload — which
    would also feed the heuristic the rendered block's header/delimiter
    framing rather than the plain body, risking a false match against e.g.
    the ``Date:`` header's own ``HH:MM:SS``. The RENDERED block body is
    additionally quote-trail-stripped (#2653, ``strip_quoted_trail``) — this
    function is used exclusively by the two thread-SUMMARY renderers, where
    every earlier message's own block already carries its own content, so an
    inlined quoted copy of it in a later reply is pure duplication (and, per
    #2653, where a stripped banner reappears). ``decoded_bodies`` stays
    quote-INTACT so the #2641 meeting-signal scan and any other reuse of the
    return value are unaffected by this change.
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
        transcript_body = strip_quoted_trail(body)  # drop inlined quote trail (#2653)
        rendered_body = transcript_body
        if per_message_body_limit > 0 and len(transcript_body) > per_message_body_limit:
            rendered_body = (
                transcript_body[:per_message_body_limit] + "\n...[truncated]"
            )
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
        # Real cursor only -- never len(stubs) == max_results (see
        # _list_all_stubs's scan_truncated docstring above for why that
        # heuristic is wrong the moment a mailbox's true size matches the ask).
        truncated = bool(listing.get("nextPageToken"))
        summary: Dict[str, Any] = {"count": len(out), "truncated": truncated}
        if retried_query is not None:
            summary["operator_retry"] = retried_query
        st["result_summary"] = summary
        return {
            "messages": out,
            "operator_retry": retried_query,
            "truncated": truncated,
        }


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

    Mutates a copy of ``decision`` and returns it.

    Resolution rule (#2632): a priority-sender match never overrides
    ``category`` — content (the heuristic or the LLM) decides severity,
    a sender preference only raises salience. "I care about this sender"
    is not "this message is urgent": a newsletter from a priority sender
    stays exactly as low-signal as its content says, tagged with
    ``preference_applied`` so a caller can still surface/order it earlier.
    The low-priority-sender branch is unchanged — it still commits
    PROMOTIONAL, since that is an explicit user request to downrank a
    sender rather than an inferred one.

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
        out["preference_applied"] = "priority_sender"
        # #2744 (this module's half): a fact about the message and the
        # sender, never the classifier's own bookkeeping language. #2632
        # still requires the rule stated explicitly -- "category unchanged"
        # -- so a priority match is never misread as itself an urgency
        # claim the category doesn't back.
        out["rationale"] = (
            f"From a priority sender · category unchanged · {decision.get('rationale', '')}"
        )
    elif sender_addr and sender_addr in low_priority_senders:
        out["category"] = CATEGORY_PROMOTIONAL
        out["confident"] = True
        out["preference_applied"] = "low_priority_sender"
        out["rationale"] = (
            f"From a low-priority sender · {decision.get('rationale', '')}"
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


def _fetch_messages(
    gmail, ids: List[str], *, format: str, on_rate_limit: str = "raise"
) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """Fetch ``ids`` in as few round-trips as ``gmail`` supports (#2643).

    Prefers the backend's own ``get_messages_batch`` — a duck-typed
    capability, not a formal ``GmailBackend`` Protocol method (see that
    Protocol's ``get_message`` docstring for why) — when present. Otherwise
    falls back to a per-id ``get_message`` loop, passing ``format`` only when
    the backend's ``get_message`` actually accepts it: checked ONCE via
    signature introspection (mirrors the existing ``progress``-support check
    a few lines below in the ``triage_inbox`` tool wrapper), never a
    try/except around the call itself, which could mistake an unrelated
    ``TypeError`` raised inside a real ``get_message`` for missing format
    support and silently swallow a genuine bug.

    Returns ``(fetched, dropped_ids)``. With the default ``on_rate_limit=
    "raise"``, ``dropped_ids`` is always empty and the original all-or-
    nothing contract holds unchanged: every id in ``ids`` is guaranteed a
    corresponding entry in ``fetched``, or this raises — a backend handing
    back fewer than requested is a silently partial scan, which this
    package never allows (mirrors ``_list_all_stubs``'s
    page-failure-propagates rule and ``EnvelopeBudgetExceeded``'s
    fail-loud contract elsewhere in this file).

    ``on_rate_limit="skip"`` catches ONLY ``RateLimitedError`` — never a
    bare ``ConnectorsError``, so a genuine 404/auth failure still aborts
    loudly — keeps whatever messages the backend already fetched, and
    reports the rate-limited ids via ``dropped_ids`` instead of raising.
    A known-skipped id is never "missing": only an id absent from both
    ``fetched`` and ``dropped_ids`` (a real backend bug) still raises.
    """
    if not ids:
        return {}, []
    dropped: List[str] = []
    batch_fn = getattr(gmail, "get_messages_batch", None)
    if callable(batch_fn):
        try:
            out = dict(batch_fn(ids, format=format))
        except RateLimitedError as exc:
            if on_rate_limit != "skip":
                raise
            out = dict(exc.partial_results)
            dropped = list(exc.message_ids)
    else:
        supports_format = "format" in inspect.signature(gmail.get_message).parameters
        out = {}
        for mid in ids:
            try:
                out[mid] = (
                    gmail.get_message(mid, format=format)
                    if supports_format
                    else gmail.get_message(mid)
                )
            except RateLimitedError:
                if on_rate_limit != "skip":
                    raise
                dropped.append(mid)
    missing = [mid for mid in ids if mid not in out and mid not in dropped]
    if missing:
        raise RuntimeError(
            f"mail backend returned {len(out)} of {len(ids)} requested "
            f"message(s) during a triage scan; missing: {missing[:5]}"
        )
    return out, dropped


def triage_inbox_impl(
    gmail,
    *,
    max_messages: int = DEFAULT_INBOX_SCAN_MESSAGES,
    label_ids: Optional[List[str]] = None,
    session_preferences: Optional[Mapping[str, Any]] = None,
    force_llm: bool = False,
    classifier: Optional[Callable[..., Mapping[str, Any]]] = None,
    slm_classifier: Optional[Callable[..., Optional[Mapping[str, Any]]]] = None,
    slm_phishing_classifier: Optional[Callable[..., Optional[bool]]] = None,
    debug: bool = False,
    progress: Optional[Callable[[int, int, str], None]] = None,
    on_rate_limit: str = "raise",
) -> Dict[str, Any]:
    """Triage the inbox using heuristic fast path + SLM + LLM fallback.

    ``progress(done, total, subject)`` is called after each message when
    supplied. A single LLM follow-up costs 9-31s locally, so a 25-message scan
    can sit silent for a minute; the callback is what turns that into visible
    movement. It must never break the scan — callers get their exceptions
    swallowed and logged, because narration is not worth losing a triage over.

    Two-phase fetch (#2643): phase 1 fetches METADATA ONLY for every scanned
    message (subject/from/labelIds/snippet/List-Unsubscribe — no body) and
    runs the heuristic on that alone. Phase 2 fetches the FULL body, but only
    for messages phase 1 flagged as needing LLM follow-up — most real inboxes
    resolve confidently from labels/snippet/headers and never reach phase 2
    at all. Both phases batch through ``_fetch_messages``, so a full scan
    costs at most 2 round-trips to the mail backend regardless of message
    count (barring Gmail's 100-subrequest batch chunking on very large
    scans). If the heuristic is confident, its category is the triage
    decision. Otherwise (and always for ``urgent`` vs ``actionable``, which
    depend on body content) the message needs LLM follow-up.

    LLM follow-up (#1107): when ``classifier`` is provided, a heuristic
    ``confident=False`` message has its REAL decoded body (phase 2's fetch,
    never the phase-1 metadata stub) read and classified by the LLM via
    ``classifier(subject=, sender=, body=, message_id=)`` →
    ``{category, is_spam, confidence, reasoning}``. The result is recorded
    with ``confident=True`` and ``source="llm"``. If the classifier raises
    (LLM unreachable, unparseable output, or an out-of-taxonomy category)
    the exception propagates — we never silently default to
    ``informational``. When ``classifier`` is None, the message is left
    flagged (``confident=False``) for a caller that sequences LLM calls
    itself — preserving the heuristic-only path, AND phase 2 never runs at
    all for that scan (``pre_scan_inbox_impl`` never wires a classifier, so
    it never pays for a body fetch nobody reads).

    ``is_spam`` follow-up (#1906) is independent of category confidence: the
    heuristic only commits ``is_spam`` for a narrow, mechanical sender-pattern
    signal (``spam_confident=True``); a ``spam_confident=False`` message gets
    the same LLM call (no extra round-trip) and only its ``is_spam`` field is
    applied from the response — an already-confident category is never
    silently overridden by a spam-only escalation, and vice versa.

    When ``slm_classifier`` is provided and the heuristic is not confident, the
    SLM classifies first (``source="slm"``); a miss falls through to the LLM.
    Skipped under ``force_llm``. When ``slm_phishing_classifier`` is provided it
    owns the phishing verdict — the keyword/domain heuristic is not consulted —
    and a ``None`` result falls back to ``detect_phishing``. Both SLMs read
    phase 1's subject + snippet, the same input the category heuristic reads, so
    enabling them costs no extra round-trip.

    When ``force_llm`` is True, every message is routed to the classifier
    (if provided) regardless of heuristic confidence — used for
    benchmarking to measure true inference cost across all emails. Phase 2
    then fetches every message's full body, matching the pre-#2643 cost for
    that specific (opt-in, benchmarking-only) mode.

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

    ``on_rate_limit``: ``"raise"`` (the default) preserves the original
    contract — a Gmail rate-limit that survives retry propagates as
    ``RateLimitedError``, like any other ``ConnectorsError``. ``"skip"``
    degrades instead: a rate-limited message is left out of ``results``
    (never a half-built decision) and its id is added to the returned
    ``dropped_ids`` list. Callers that need every message or nothing (the
    LLM-facing tool, the chat-surface pre-scan) keep the default; only the
    read-only attention view opts into ``"skip"``, since surfacing 99 of
    100 signals beats a 500 over one rate-limited message.
    """
    # Local import breaks a real import cycle: calendar_tools imports
    # DEFAULT_BODY_LIMIT_CHARS from this module at module scope, so importing
    # calendar_tools back at module scope here would close the loop.
    from gaia_agent_email.tools.calendar_tools import detect_meeting_request_heuristic

    prefs = session_preferences or {}
    with log_tool_call(
        "triage_inbox", {"max_messages": max_messages}, debug=debug
    ) as st:
        listing = _list_all_stubs(gmail, label_ids=label_ids, max_messages=max_messages)
        stubs = listing["stubs"]
        stub_ids = [stub["id"] for stub in stubs]

        # Phase 1: metadata-only fetch for the whole scan (#2643 lever 1+2).
        metadata_by_id, metadata_dropped_ids = _fetch_messages(
            gmail, stub_ids, format="metadata", on_rate_limit=on_rate_limit
        )

        prepared: List[Dict[str, Any]] = []
        escalate_ids: List[str] = []
        for stub in stubs:
            if stub["id"] in metadata_dropped_ids:
                # Rate-limited away (on_rate_limit="skip") -- no metadata to
                # classify with, so this message is simply absent from the
                # result rather than a half-built decision. Its id is
                # reported via dropped_ids below.
                continue
            msg = metadata_by_id[stub["id"]]
            payload_headers = {
                (h.get("name") or "").lower(): h.get("value", "")
                for h in (msg.get("payload") or {}).get("headers", [])
            }
            # Phishing is resolved below when an SLM is wired, so the heuristic
            # never runs its own detector for that message.
            phishing_slm_applies = slm_phishing_classifier is not None
            heuristic = classify_category_heuristic(
                subject=payload_headers.get("subject", ""),
                sender=payload_headers.get("from", ""),
                label_ids=msg.get("labelIds", []),
                body=msg.get("snippet", ""),
                check_phishing=not phishing_slm_applies,
                # RFC 2369 bulk-mail signal (#2643) — arrives with the
                # metadata fetch above, no body read needed.
                has_list_unsubscribe=bool(
                    (payload_headers.get("list-unsubscribe", "") or "").strip()
                ),
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
                # #2744 (this module's half): force_llm re-runs the LLM
                # regardless of heuristic confidence, but that's internal
                # pipeline state the user has no reason to know about —
                # the heuristic's own reason is still an accurate fact
                # about the message either way.
                "rationale": heuristic.reason,
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

            # Both SLMs read phase 1's snippet, never a decoded body, so an
            # enabled SLM never adds a round-trip to the scan (#2643). The
            # phishing SLM owns the verdict when it returns one; ``None`` falls
            # back to the same detector on the same input, so the fallback is
            # identical to the no-SLM path.
            if phishing_slm_applies:
                snippet = msg.get("snippet", "")
                slm_is_phishing = slm_phishing_classifier(
                    subject=decision["subject"],
                    sender=decision["from"],
                    body=snippet,
                )
                if slm_is_phishing is None:
                    decision["is_phishing"] = detect_phishing(
                        decision["subject"], decision["from"], snippet
                    )
                else:
                    decision["is_phishing"] = slm_is_phishing
                    decision["phishing_source"] = "slm"

            # Category SLM before the LLM; a miss falls through to the LLM
            # follow-up below.
            if slm_classifier is not None and not force_llm and not heuristic.confident:
                slm = slm_classifier(
                    subject=decision["subject"],
                    sender=decision["from"],
                    body=msg.get("snippet", ""),
                    message_id=msg["id"],
                )
                if slm:
                    decision["category"] = slm["category"]
                    decision["confident"] = True
                    decision["source"] = "slm"
                    # #2744 (this module's half): the heuristic's own
                    # reason describes why IT was unconfident, not why the
                    # SLM chose this category — restating it here would
                    # read as contradicting the confident verdict, so it
                    # is dropped rather than reworded.
                    decision["rationale"] = f"SLM classified as {slm['category']}"
                    if slm.get("confidence") is not None:
                        decision["slm_confidence"] = slm["confidence"]

            # LLM follow-up (#1107; is_spam added #1906): re-classify when the
            # category is still unresolved OR the heuristic is not confident
            # about is_spam (or force_llm), if a classifier is wired in.
            # Category and is_spam are applied independently: a spam-only
            # escalation must not let the LLM silently override an
            # already-resolved category, and vice versa. Only messages that
            # need it are queued for the phase-2 full-body fetch below.
            category_resolved = decision["confident"]
            needs_llm = (
                not category_resolved or not heuristic.spam_confident or force_llm
            )
            escalate = classifier is not None and needs_llm
            if escalate:
                escalate_ids.append(stub["id"])
            prepared.append(
                {
                    "stub_id": stub["id"],
                    "decision": decision,
                    "heuristic": heuristic,
                    "escalate": escalate,
                    "category_resolved": category_resolved,
                    "subject_for_progress": payload_headers.get("subject", "")
                    or "(no subject)",
                }
            )

        # Phase 2: full-body fetch ONLY for messages phase 1 flagged for LLM
        # follow-up (#2643 lever 1+2) — empty (and zero round-trips) whenever
        # nothing escalates, e.g. pre_scan_inbox's classifier=None path.
        full_by_id, full_dropped_ids = (
            _fetch_messages(
                gmail, escalate_ids, format="full", on_rate_limit=on_rate_limit
            )
            if escalate_ids
            else ({}, [])
        )

        results: List[Dict[str, Any]] = []
        for item in prepared:
            if item["escalate"] and item["stub_id"] in full_dropped_ids:
                # Rate-limited during the phase-2 body fetch -- same
                # skip-not-crash treatment as a phase-1 drop above.
                continue
            decision = item["decision"]
            heuristic = item["heuristic"]
            if item["escalate"]:
                full_msg = full_by_id[item["stub_id"]]
                body_text, _ = decode_message_body(full_msg.get("payload") or {})
                # #2643 lever 4: cut the quoted reply chain and signature
                # block before the classifier reads it -- boilerplate that
                # costs tokens without changing the category decision. Not
                # applied to any read-tool display path (get_message et al.)
                # -- only this LLM-classification input.
                body_text = strip_reply_chain_and_signature(body_text)
                llm = classifier(
                    subject=decision["subject"],
                    sender=decision["from"],
                    body=body_text,
                    message_id=decision["id"],
                )
                if not item["category_resolved"] or force_llm:
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
                message_id=decision["id"],
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
                    progress(len(results), len(stubs), item["subject_for_progress"])
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
            # Message ids skipped under on_rate_limit="skip" -- always empty
            # under the default "raise" (any drop would have propagated as
            # RateLimitedError instead of reaching this return).
            "dropped_ids": metadata_dropped_ids + full_dropped_ids,
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

# #2743 — the "one card" worklist. Capped small on purpose: a triage card
# listing 30 rows is a report, not a worklist a person can act on today.
NEEDS_YOU_CAP = 5

# Filter-test ids for BulkSummary.filter_tests (#2743) — ids, never prose
# (see contract.py's NeedsYouItem/BulkSummary docstrings): a renderer maps
# each id to a sentence, so a description can't silently go stale the
# moment the routing below changes.
#
# Named after the QUESTION asked of the message, not the category it
# landed in (checkpoint review, #2743 redirect): "category_promotional"
# just relabels the bare count with an unchallengeable tag; "no
# direct question" is falsifiable — it invites "what about a receipt
# over $500?", which is the user checking the agent's work, the entire
# point of naming the test instead of the bucket.
FILTER_TEST_NO_DIRECT_QUESTION = "no_direct_question"
FILTER_TEST_NO_DEADLINE_SIGNAL = "no_deadline_signal"
FILTER_TEST_NO_MEETING_PROPOSAL = "no_meeting_proposal"
FILTER_TEST_MATCHED_ARCHIVE_PREFERENCE = "matched_your_archive_preference"

# Plain INBOX — read AND unread mail (#2638). Previously ["INBOX", "UNREAD"],
# on the rationale that narrowing to unread-only made the listing query's
# resultSizeEstimate mean "how many unread" instead of "how big is the
# inbox" (#2584). That rationale no longer holds: #2584 ALSO stopped sourcing
# the coverage denominator from resultSizeEstimate at all (it now comes from
# labels().get(INBOX), see _fetch_inbox_counts below), so narrowing the
# listing query bought nothing — while making the single highest-value
# triage bucket (read-but-unanswered mail: opened on a phone, meant to
# reply, forgot — invisible the moment it's opened) permanently invisible.
# The attention view (attention_tools._scan_one_backend) and
# list_waiting_on_you already scan all of INBOX regardless of read state;
# this makes pre-scan agree with them instead of being the one narrower
# surface. Decision: pre-scan covers read mail. If that decision reverses,
# this comment and the coverage-line prose in pre_scan_inbox's docstring and
# EmailTriageAgent._SYSTEM_PROMPT (both explicitly describe "read + unread")
# must be updated together.
_PRE_SCAN_LABEL_IDS = ["INBOX"]


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


def _fetch_inbox_counts(gmail) -> Dict[str, Optional[int]]:
    """Exact INBOX message/unread counts via ONE ``labels().get`` call
    (#2584, extended #2638) — NOT ``list_messages``'s ``resultSizeEstimate``.

    Measured against a real mailbox: ``resultSizeEstimate`` for
    ``label_ids=[INBOX, UNREAD]`` reported 201 while full pagination of the
    identical query found 523 real message ids — Google documents that field
    as approximate, and 2.6x off is not a fabricated-placeholder-grade lie
    (the Outlook page-size case) but it is still not honest enough to state
    as the scan-coverage denominator. The label resource's ``messagesTotal``
    / ``messagesUnread`` are exact integers. One call per SCAN, not per
    message — ``list_labels`` returns the minimal label form with no counts,
    so this must be ``get_label``, not that.

    Returns both counts from the SAME call (#2638): now that pre-scan covers
    all of INBOX, not just unread, ``total_unread`` alone is no longer an
    honest "how much of the inbox did this scan cover" denominator —
    ``total`` (``messagesTotal``) is. Fetching both from one call, rather
    than two separate helpers each hitting ``get_label`` on their own, keeps
    this at the one-round-trip-per-scan cost #2643 cares about.

    Backends that can't provide an honest count (Outlook — Graph has no
    equivalent resource) return ``messagesTotal``/``messagesUnread: None``
    from their own ``get_label``, which flows straight through here with no
    per-provider branching. A backend that doesn't implement ``get_label``
    at all (a minimal test double, or a future provider) degrades the same
    way: this is supplementary coverage metadata, never allowed to abort the
    scan itself if it can't be produced.
    """
    get_label = getattr(gmail, "get_label", None)
    if not callable(get_label):
        return {"total": None, "unread": None}
    try:
        label = get_label("INBOX")
    except ConnectorsError as exc:
        log.warning("pre-scan: get_label(INBOX) failed, inbox counts unknown: %s", exc)
        return {"total": None, "unread": None}
    label = label or {}
    total = label.get("messagesTotal")
    unread = label.get("messagesUnread")
    return {
        "total": int(total) if isinstance(total, (int, float)) else None,
        "unread": int(unread) if isinstance(unread, (int, float)) else None,
    }


def _fetch_total_unread(gmail) -> Optional[int]:
    """Exact unread-inbox count — thin wrapper over ``_fetch_inbox_counts``
    kept for ``attention_tools.build_attention_view_impl``'s own coverage
    line, which (unlike pre-scan) never needed a ``total_inbox`` companion —
    it has always scanned all of INBOX, so it never had #2638's
    unread-only-denominator problem to begin with.
    """
    return _fetch_inbox_counts(gmail)["unread"]


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


# kind priority for needs_you ordering (#2743 redirect, tuned again after
# checkpoint review): grouped by the VERB increment 2's renderer maps a kind
# to, not interleaved — a DECIDE row wedged between two REPLY rows would
# read as arbitrary ordering to a user. REPLY (urgent, waiting_on_you,
# needs_response, in that internal priority) comes first, then DECIDE
# (meeting_request), then CHECK (needs_review), then action_item last (a
# task the user already triaged once, carried over). Within REPLY, a
# category-confirmed URGENT still outranks a detector-only signal, which in
# turn outranks an ordinary actionable ask. Matches the published
# ``AttentionItemKind`` values so a renderer never has to special-case a
# string it doesn't recognize.
_NEEDS_YOU_KIND_ORDER = {
    "urgent": 0,
    "waiting_on_you": 1,
    "needs_response": 2,
    "meeting_request": 3,
    "needs_review": 4,
    "action_item": 5,
}

# One day in epoch milliseconds — synthesizes an approximate ``internal_date``
# for a needs_you candidate whose source only gives an age in days rather
# than a millisecond timestamp (waiting-on-you detections, #2743 redirect).
_NEEDS_YOU_DAY_MS = 24 * 60 * 60 * 1000


def _needs_you_waiting_on_you_candidate(
    w: Mapping[str, Any], *, now_ms: int
) -> Dict[str, Any]:
    """One ``detect_waiting_on_you_impl`` row -> a needs_you candidate dict.

    Mirrors ``attention_tools._waiting_on_you_item``'s field shape (#2743
    redirect: reusing that call shape without calling
    ``build_attention_view_impl`` itself, which would reintroduce the second
    aggregation pass this design exists to eliminate). Only ``age_days`` is
    available (not a millisecond timestamp), so ``internal_date`` is
    synthesized from it — accurate to the day, which is all the source data
    supports.
    """
    age_days = w.get("age_days") or 0
    return {
        "kind": "waiting_on_you",
        "message_id": w.get("message_id"),
        "thread_id": w.get("thread_id"),
        "sender": w.get("sender", ""),
        "subject": w.get("subject", ""),
        "internal_date": now_ms - int(age_days) * _NEEDS_YOU_DAY_MS,
        "why": f"waiting {age_days}d on your reply",
    }


def _needs_you_action_item_candidate(task: Mapping[str, Any]) -> Dict[str, Any]:
    """One persisted ``task_store`` row -> a needs_you candidate dict (#2743
    redirect).

    Mirrors ``attention_tools._action_item``'s field shape. ``message_id``
    can be ``None`` for a task carried from a prior triage with no
    recoverable source message (``NeedsYouItem.message_id`` documents this
    explicitly) — never dropped or defaulted to a placeholder id.
    """
    created_at = task.get("created_at")
    return {
        "kind": "action_item",
        "message_id": task.get("message_id"),
        "thread_id": None,
        "sender": "",
        "subject": task.get("description", ""),
        "internal_date": int(created_at * 1000) if created_at else None,
        "why": "open action item from a previous triage",
        "due_hint": task.get("due_hint"),
    }


def _finalize_needs_you_item(
    candidate: Mapping[str, Any], *, ref: int, now_ms: int
) -> Dict[str, Any]:
    """Turn one ordered candidate dict into the final ``NeedsYouItem`` shape.

    ``age_seconds`` is computed here, uniformly, from the working-only
    ``internal_date`` field every candidate carries by this point (never
    part of the public contract, see ``_drop_internal_date`` below) — the
    ONE place this computation happens, so a merge-level candidate (added
    after a per-backend view is already built, see
    ``merge_pre_scan_backends``) gets the identical treatment.

    ``due_hint`` (action items only) is wrapped in the same untrusted-input
    delimiters that cover a raw body read before it leaves this function
    (#2743): it is regex-extracted verbatim from a message body
    (``extract_action_items_from_body``, ``api_routes.py``) and persisted,
    so by the time it reaches a needs_you row it is attacker-influenced
    text re-entering the calling agent's own tool-result context — the
    same threat model as a raw body read, independent of whether anything
    else in this view is LLM-derived. ``normalize_email_body`` runs first
    to scrub any forged delimiter tokens the source sentence might itself
    contain, exactly as ``_format_message_for_llm`` does for a body.
    """
    internal_date = _parse_epoch_millis(candidate.get("internal_date"))
    age_seconds = max(0, (now_ms - internal_date) // 1000) if internal_date else None
    due_hint = candidate.get("due_hint")
    if due_hint:
        due_hint = wrap_untrusted_body(normalize_email_body(due_hint))
    return {
        "ref": ref,
        "kind": candidate["kind"],
        "message_id": candidate.get("message_id"),
        "thread_id": candidate.get("thread_id"),
        "sender": candidate.get("sender", ""),
        "subject": candidate.get("subject", ""),
        "age_seconds": age_seconds,
        "why": candidate.get("why") or candidate.get("reason") or "",
        # Always empty for now (#2743) -- the contract reserves this field
        # for a per-item LLM extraction pass that shipped and was then
        # withdrawn from this issue before merge; see commit 25738509 for
        # the working implementation (extraction, injection-defense
        # wrapping, and the bounded-fill design a follow-up issue will
        # reuse) and NeedsYouItem's own docstring in contract.py.
        "detail": [],
        "due_hint": due_hint,
        "mailbox": candidate.get("mailbox"),
    }


def _build_needs_you_view(
    *,
    urgent: List[Dict[str, Any]],
    actionable: List[Dict[str, Any]],
    needs_review: List[Dict[str, Any]],
    waiting_on_you: Optional[List[Dict[str, Any]]] = None,
    action_items: Optional[List[Dict[str, Any]]] = None,
    cap: int = NEEDS_YOU_CAP,
) -> Dict[str, Any]:
    """Build the ``needs_you`` worklist as a VIEW over already-classified
    buckets PLUS the waiting-on-you detector and persisted action items —
    never a second, independent classification pass (#2743 Adversarial
    Reflection #1: urgent mail must never vanish because this view
    re-derived from raw scan results and missed it).

    Every ``urgent`` item is tagged ``urgent`` and every ``actionable`` item
    ``needs_response``, unless the heuristic already flagged
    ``is_meeting_request``, in which case it's tagged ``meeting_request``
    instead; every ``needs_review`` item is tagged ``needs_review``. These
    three are category classifications — never the detector's own
    ``waiting_on_you`` value (#2743 redirect: the initial cut mislabeled
    them that way, which collided with the detector's real meaning the
    moment its output was wired in below).

    ``waiting_on_you`` (raw ``detect_waiting_on_you_impl`` rows) and
    ``action_items`` (raw ``task_store.list_tasks`` rows) carry NO category
    bucket of their own, so each is appended only when its ``message_id``
    isn't already present above — a message the detector also flags, or a
    task tied to a message already surfaced by category, must never
    double-count as two rows.

    Ordered by kind (see ``_NEEDS_YOU_KIND_ORDER``), then oldest-first
    within a kind (mirrors ``_needs_review_sort_key``), then capped at
    ``cap`` — ``needs_you_total`` carries the true pre-cap count so a
    renderer can say "N of M" honestly rather than silently truncating.
    """
    now_ms = int(time.time() * 1000)
    candidates: List[Dict[str, Any]] = []
    seen_message_ids: set = set()

    def _remember(mid: Optional[str]) -> None:
        if mid:
            seen_message_ids.add(mid)

    for item in urgent:
        kind = "meeting_request" if item.get("is_meeting_request") else "urgent"
        candidates.append({**item, "kind": kind})
        _remember(item.get("message_id"))
    for item in actionable:
        kind = (
            "meeting_request" if item.get("is_meeting_request") else "needs_response"
        )
        candidates.append({**item, "kind": kind})
        _remember(item.get("message_id"))
    for item in needs_review:
        candidates.append({**item, "kind": "needs_review"})
        _remember(item.get("message_id"))

    for w in waiting_on_you or []:
        candidate = _needs_you_waiting_on_you_candidate(w, now_ms=now_ms)
        mid = candidate.get("message_id")
        if mid and mid in seen_message_ids:
            continue
        candidates.append(candidate)
        _remember(mid)
    for task in action_items or []:
        candidate = _needs_you_action_item_candidate(task)
        mid = candidate.get("message_id")
        if mid and mid in seen_message_ids:
            continue
        candidates.append(candidate)
        _remember(mid)

    candidates.sort(
        key=lambda c: (
            _NEEDS_YOU_KIND_ORDER.get(c["kind"], 99),
            _parse_epoch_millis(c.get("internal_date")),
        )
    )

    total = len(candidates)
    needs_you = [
        _finalize_needs_you_item(c, ref=ref, now_ms=now_ms)
        for ref, c in enumerate(candidates[: max(0, cap)], start=1)
    ]
    return {"needs_you": needs_you, "needs_you_total": total}


def _merge_needs_you_candidates(
    candidates: List[Dict[str, Any]], *, cap: int = NEEDS_YOU_CAP
) -> List[Dict[str, Any]]:
    """Re-order, re-cap, and re-number ``ref`` across every backend's own
    ``needs_you`` list (#2743 Increment 1 step 5: "re-assign ref after the
    merge so numbering is contiguous across mailboxes").

    Each backend already ordered and capped its own candidates via
    :func:`_build_needs_you_view`; this only re-sorts the union by the same
    kind-then-age policy and reassigns 1-based, contiguous ``ref`` — it never
    re-derives ``kind``/``why`` from anything.
    """
    ordered = sorted(
        candidates,
        key=lambda c: (
            _NEEDS_YOU_KIND_ORDER.get(c.get("kind"), 99),
            -(c.get("age_seconds") or 0),
        ),
    )
    merged: List[Dict[str, Any]] = []
    for ref, c in enumerate(ordered[: max(0, cap)], start=1):
        merged.append({**c, "ref": ref})
    return merged


def pre_scan_inbox_impl(
    gmail,
    *,
    max_messages: int = DEFAULT_INBOX_SCAN_MESSAGES,
    urgent_cap: int = PRE_SCAN_URGENT_CAP,
    actionable_cap: int = PRE_SCAN_ACTIONABLE_CAP,
    archive_cap: int = PRE_SCAN_ARCHIVE_CAP,
    needs_review_cap: int = PRE_SCAN_NEEDS_REVIEW_CAP,
    session_preferences: Optional[Mapping[str, Any]] = None,
    force_llm: bool = False,
    slm_classifier: Optional[Callable[..., Optional[Mapping[str, Any]]]] = None,
    slm_phishing_classifier: Optional[Callable[..., Optional[bool]]] = None,
    include_informational: bool = False,
    action_db: Any = None,
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

    ``include_informational`` (#2633): the default envelope carries only
    ``informational_count`` — a bare number, on purpose, so the default
    card stays scannable. But a bare count is unauditable: a caller has no
    way to tell a correctly-filtered newsletter from a miscategorized
    message that needed a reply. When True, the ``informational`` field
    carries the full list (id/sender/subject/why) of every message this
    call classified informational, bounded only by ``max_messages`` — the
    same list this function already builds internally, just not discarded.

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

    ``needs_you`` (#2743 redirect) also runs a waiting-on-you sub-scan of
    this SAME mailbox at this SAME depth (``max_messages``) — never the
    ``waiting_on_you_tools`` module's own, independently-set default — so a
    REPLY row is trustworthy to the same scan depth the coverage line
    claims. ``action_db``, when given, is an optional ``DatabaseMixin``
    handle (see ``gaia_agent_email.task_store``) whose open tasks are folded
    in too; omit it and the action_item signal is simply absent — this
    function never fails because the task store wasn't wired in.
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
            slm_classifier=slm_classifier,
            slm_phishing_classifier=slm_phishing_classifier,
            debug=debug,
        )
        urgent: List[Dict[str, Any]] = []
        actionable: List[Dict[str, Any]] = []
        informational: List[Dict[str, Any]] = []
        suggested_archives: List[Dict[str, Any]] = []
        needs_review_ranked: List[tuple] = []
        # Ids of the filter tests actually applied this run (#2743) — feeds
        # ``bulk.filter_tests``. A set, not a list: the same routing branch
        # firing on 50 messages names its test once, not 50 times.
        filter_test_ids: set = set()

        for r in triage["results"]:
            base = {
                "message_id": r["id"],
                "thread_id": r.get("thread_id"),
                "sender": r.get("from", ""),
                "subject": r.get("subject", ""),
                "is_meeting_request": bool(r.get("is_meeting_request", False)),
                # Epoch-millis string, carried through so #2743's needs_you
                # view can order oldest-first and compute age_seconds —
                # never part of the public PreScanItem shape.
                "internal_date": r.get("internal_date"),
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
                    filter_test_ids.add(FILTER_TEST_NO_DIRECT_QUESTION)
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
                    filter_test_ids.add(
                        FILTER_TEST_NO_DEADLINE_SIGNAL
                        if category == CATEGORY_FYI
                        else FILTER_TEST_NO_MEETING_PROPOSAL
                    )

        needs_review_ranked.sort(key=lambda pair: pair[0])
        needs_review = [item for _, item in needs_review_ranked]

        # Apply the FYI category default: when the user has previously asked
        # us to archive FYI mail, lift those items into suggested_archives.
        # (The ``informational`` list holds both FYI and PERSONAL — the keep
        # bucket — but only the FYI default promotes to archive.) Never
        # applies to ``needs_review`` — an unconfident guess must not be
        # silently archived by a stale category preference.
        if category_defaults.get(CATEGORY_FYI) == "archive":
            if informational:
                filter_test_ids.add(FILTER_TEST_MATCHED_ARCHIVE_PREFERENCE)
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

        # #2743 redirect: waiting-on-you detections and persisted action
        # items carry no category bucket of their own — reuses
        # ``build_attention_view_impl``'s call shape (never calls it, which
        # would reintroduce the second aggregation pass this design exists
        # to eliminate). Local import: read_tools sits below
        # waiting_on_you_tools in this package's own import graph (the
        # latter imports FROM read_tools), so a module-level import here
        # would be circular.
        from gaia_agent_email.tools.waiting_on_you_tools import (
            detect_waiting_on_you_impl,
        )

        waiting_on_you = detect_waiting_on_you_impl(
            gmail, max_inbox=max_messages, debug=debug
        )["waiting_on_you"]
        action_items: List[Dict[str, Any]] = []
        if action_db is not None:
            from gaia_agent_email import task_store

            action_items = task_store.list_tasks(action_db, status="open")

        # #2743: needs_you is a VIEW over the just-built urgent/actionable/
        # needs_review buckets plus the two signals above — built from the
        # TRUE (pre-cap) lists so a message dropped by an unrelated
        # per-section cap can never silently miss this view. Computed
        # BEFORE stripping ``internal_date`` below.
        needs_you_view = _build_needs_you_view(
            urgent=urgent,
            actionable=actionable,
            needs_review=needs_review,
            waiting_on_you=waiting_on_you,
            action_items=action_items,
        )
        # bulk is the filtered informational/promotional remainder, computed
        # AFTER the archive-preference promotion above so it reflects what
        # actually ended up filtered, not an intermediate state.
        bulk_count = len(informational) + len(suggested_archives)
        bulk_view = {"count": bulk_count, "filter_tests": sorted(filter_test_ids)}

        def _drop_internal_date(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            # ``internal_date`` is a needs_you-only working field (#2743) —
            # never part of the public PreScanItem shape (extra="forbid").
            return [{k: v for k, v in item.items() if k != "internal_date"} for item in items]

        scanned = len(triage["results"])
        inbox_counts = _fetch_inbox_counts(gmail)
        out = {
            "kind": "email_pre_scan",
            "urgent": _drop_internal_date(urgent[: max(0, urgent_cap)]),
            "actionable": _drop_internal_date(actionable[: max(0, actionable_cap)]),
            "informational_count": len(informational),
            # #2633: empty unless the caller opted in — the full list was
            # already computed above, so honoring the flag costs nothing
            # beyond what this call already did.
            "informational": (
                _drop_internal_date(informational) if include_informational else []
            ),
            "suggested_archives": _drop_internal_date(
                suggested_archives[: max(0, archive_cap)]
            ),
            "suggested_drafts": [],
            "needs_review": _drop_internal_date(needs_review[: max(0, needs_review_cap)]),
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
            "total_unread": inbox_counts["unread"],
            # Whole-INBOX denominator (#2638) — now that the scan covers read
            # + unread, this (not total_unread) is the honest "how much of
            # the inbox did we look at" figure. Exact Gmail messagesTotal;
            # None when the backend can't report it (Outlook), never a
            # fabricated number.
            "total_inbox": inbox_counts["total"],
            # Single-backend call: a backend failure always raises (never a
            # silent partial result), so this layer never degrades on its
            # own — only merge_pre_scan_backends' multi-mailbox fan-out can.
            "degraded": False,
            "needs_you": needs_you_view["needs_you"],
            "needs_you_total": needs_you_view["needs_you_total"],
            "bulk": bulk_view,
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
    max_messages: int = DEFAULT_INBOX_SCAN_MESSAGES,
    session_preferences: Optional[Mapping[str, Any]] = None,
    force_llm: bool = False,
    include_informational: bool = False,
    debug: bool = False,
    remember_mailbox: Optional[Callable[[Optional[str], str], None]] = None,
    slm_classifier: Optional[Callable[..., Optional[Mapping[str, Any]]]] = None,
    slm_phishing_classifier: Optional[Callable[..., Optional[bool]]] = None,
    action_db: Any = None,
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

    ``include_informational`` (#2633) is forwarded verbatim to every
    per-backend ``pre_scan_inbox_impl`` call; the merged ``informational``
    list is tagged with ``mailbox`` the same way the other four sections are.
    ``slm_classifier`` / ``slm_phishing_classifier`` are forwarded the same
    way when the agent has the SLM classifiers loaded.

    ``action_db`` (#2743 redirect) is deliberately NOT forwarded to the
    per-backend ``pre_scan_inbox_impl`` calls below — persisted tasks aren't
    mailbox-scoped, so passing it to every backend would fold the SAME open
    tasks into the merge N times. It is queried exactly once, after the
    loop, mirroring ``build_attention_view_impl``'s own post-loop union.
    """
    prefs = session_preferences
    provider_backends = list(backends.items())
    remaining_budget = max_messages
    urgent: List[Dict[str, Any]] = []
    actionable: List[Dict[str, Any]] = []
    suggested_archives: List[Dict[str, Any]] = []
    needs_review: List[Dict[str, Any]] = []
    informational: List[Dict[str, Any]] = []
    informational_count = 0
    scanned = 0
    total_unread = 0
    total_unread_unknown = False
    total_inbox = 0
    total_inbox_unknown = False
    merged_prefs_applied: Dict[str, Any] = {}
    mailbox_errors: List[Dict[str, Any]] = []
    # #2743: each backend already built its own needs_you/bulk view; merge
    # (never re-derive) across every connected mailbox.
    needs_you_candidates: List[Dict[str, Any]] = []
    needs_you_total = 0
    bulk_count = 0
    bulk_filter_test_ids: set = set()
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
                include_informational=include_informational,
                debug=debug,
                slm_classifier=slm_classifier,
                slm_phishing_classifier=slm_phishing_classifier,
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
            ("informational", informational),
        ):
            for item in out.get(section, []):
                item["mailbox"] = provider
                if remember_mailbox is not None:
                    remember_mailbox(item.get("message_id"), provider)
                    remember_mailbox(item.get("thread_id"), provider)
                dest.append(item)
        for item in out.get("needs_you", []):
            item["mailbox"] = provider
            needs_you_candidates.append(item)
        needs_you_total += int(out.get("needs_you_total", 0))
        backend_bulk = out.get("bulk") or {}
        bulk_count += int(backend_bulk.get("count", 0))
        bulk_filter_test_ids.update(backend_bulk.get("filter_tests") or [])
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
        backend_total_inbox = out.get("total_inbox")
        if backend_total_inbox is None:
            # Same honesty rule as total_unread above (#2638): Outlook can't
            # report messagesTotal either, so the merged figure stays
            # unknown rather than silently summing only the known mailbox.
            total_inbox_unknown = True
        else:
            total_inbox += int(backend_total_inbox)

    # Tasks aren't mailbox-scoped (#2743 redirect) — queried once here,
    # never per-backend above, mirroring build_attention_view_impl's own
    # post-loop union. Still deduped against every candidate already
    # collected: a task tied to a message some backend already surfaced
    # under its category kind must not double-count as a second row.
    if action_db is not None:
        from gaia_agent_email import task_store

        now_ms = int(time.time() * 1000)
        seen_message_ids = {
            c.get("message_id") for c in needs_you_candidates if c.get("message_id")
        }
        for task in task_store.list_tasks(action_db, status="open"):
            candidate = _needs_you_action_item_candidate(task)
            mid = candidate.get("message_id")
            if mid and mid in seen_message_ids:
                continue
            needs_you_candidates.append(
                _finalize_needs_you_item(candidate, ref=0, now_ms=now_ms)
            )
            if mid:
                seen_message_ids.add(mid)
            needs_you_total += 1

    result = {
        "kind": "email_pre_scan",
        "urgent": urgent[: max(0, PRE_SCAN_URGENT_CAP)],
        "actionable": actionable[: max(0, PRE_SCAN_ACTIONABLE_CAP)],
        "informational_count": informational_count,
        "informational": informational,
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
        "total_inbox": None if total_inbox_unknown else total_inbox,
        "degraded": bool(mailbox_errors),
        "needs_you": _merge_needs_you_candidates(needs_you_candidates),
        "needs_you_total": needs_you_total,
        "bulk": {"count": bulk_count, "filter_tests": sorted(bulk_filter_test_ids)},
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

            Returns:
                JSON envelope with ``{"messages": [...]}`` plus ``count`` (the
                exact length of ``messages`` — state this number verbatim in
                your reply rather than counting the list yourself) and
                ``truncated`` (true when more matches exist beyond
                ``max_results`` — say "at least N", never present N as the
                total). REPORT EVERY ENTRY in ``messages`` individually — do
                not summarize, merge, or quietly drop entries from a long
                list. If ``operator_retry`` is present, the literal query you
                passed found nothing and this is the broadened operator query
                that was retried instead — say the search was broadened
                before stating the count, since it may include hits (e.g. a
                subject match) beyond what the user's literal phrase meant.
                If ``mailbox_errors`` is present, ``count`` and ``truncated``
                cover only the mailboxes that succeeded — say the count is
                partial, not the complete total across every connected
                mailbox.
            """
            try:
                max_results = max(1, min(int(max_results or 25), 100))
                backends = agent._backends
                if not backends:
                    return _envelope_err(NO_MAILBOX_CONNECTED_MESSAGE)
                per_backend = max(1, max_results // len(backends))
                merged: List[Dict[str, Any]] = []
                mailbox_errors: List[Dict[str, Any]] = []
                # Computed here, not forwarded from search_messages_impl alone
                # (#2756) -- this closure builds a fresh envelope dict and
                # previously kept only "messages" off each backend's result,
                # which is why operator_retry never reached the model despite
                # being computed since inception.
                truncated = False
                operator_retry_query: Optional[str] = None
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
                    truncated = truncated or bool(result.get("truncated"))
                    if result.get("operator_retry"):
                        operator_retry_query = result["operator_retry"]
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
                messages = merged[:max_results]
                out: Dict[str, Any] = {
                    "messages": messages,
                    # Exact, precomputed -- state this number verbatim rather
                    # than counting the list yourself (#2756).
                    "count": len(messages),
                    "truncated": truncated,
                    "operator_retry": operator_retry_query,
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
        def triage_inbox(max_messages: int = DEFAULT_INBOX_SCAN_MESSAGES) -> str:
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
                max_messages = max(
                    1, min(int(max_messages or DEFAULT_INBOX_SCAN_MESSAGES), scan_ceiling)
                )

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
        def pre_scan_inbox(
            max_messages: int = DEFAULT_INBOX_SCAN_MESSAGES,
            include_informational: bool = False,
        ) -> str:
            """Pre-scan the inbox into a typed envelope for the chat
            triage card.

            The result has ``kind: "email_pre_scan"`` so the chat surface
            renders the structured card component instead of plain text.
            The card's ONE worklist is ``needs_you`` (#2743) — up to
            ``NEEDS_YOU_CAP`` (5) things that genuinely need you, each
            tagged with a verb (REPLY/DECIDE/CHECK/DO) and why it surfaced.
            ``detail`` is reserved on the wire for a couple of lines of real
            substance per surfaced item (the question actually asked, the
            meeting time actually proposed, the deadline actually quoted)
            but is ALWAYS EMPTY today — the extraction pass that would fill
            it shipped and was withdrawn before this reached main; do not
            tell the user it carries anything. It is a deterministic VIEW
            over the legacy ``urgent``/``actionable``/``needs_review``
            buckets (still present below, unchanged, for callers that read
            them directly) plus the waiting-on-you detector and any open
            action items from a prior triage — never a second
            classification pass, so nothing those buckets caught can go
            missing from it.
            ``needs_you_total`` carries the true pre-cap count. Everything
            NOT in ``needs_you`` — informational and low-signal mail — is
            summarized in ``bulk``: a count PLUS ``filter_tests``, the ids
            of the tests that actually filtered it, so a claim like "47
            filtered" is auditable rather than a bare number to take on
            faith. Covers read AND unread INBOX mail (#2638) — a message
            you already opened but never answered is exactly the bucket
            this view exists to surface.

            ``informational_count`` alone is a bare number — it is NOT
            proof every one of those messages is truly low-priority
            (#2633). When the user asks what got filtered, what's in the
            informational count, or to double-check nothing important was
            skipped, call this tool AGAIN with ``include_informational=True``
            — the ``informational`` field then carries the full id/sender/
            subject list for that count instead of an empty list, at no
            extra scan cost (same underlying data, already computed).
            Leave it False for a normal triage request — the point of the
            count is to keep the default card short.

            The result is a PARTIAL view of the mailbox, not the whole
            inbox: ``scanned`` reports how many messages were actually
            looked at this call, and ``total_inbox`` reports the
            mailbox's total INBOX message count when known (Gmail;
            Outlook cannot report this honestly and returns null) —
            ``total_unread`` is also present as a secondary "how many of
            these are still unread" figure. ALWAYS mention scan coverage
            in your framing sentence when scanned is less than
            total_inbox — e.g. "3 need you (2 replies, 1 meeting), 47
            filtered, 50 of 812 in the inbox scanned." — never phrase a
            partial scan as if it covered the whole inbox, and never state
            a global verdict ("nothing needs you") from a partial one. When
            ``degraded`` is true or ``mailbox_errors`` is non-empty, say
            which mailbox couldn't be scanned.

            The chat surface injects the triage card automatically from
            the tool result — do NOT copy, re-serialize, or paraphrase
            the JSON envelope into your reply. Re-emitting the full
            envelope wastes the output budget on long message/thread IDs
            and truncates the prose summary before the user can read it.
            After this tool returns, write ONE short framing sentence
            (e.g. "Here's your inbox pre-scan — 3 need you, 47 filtered,
            50 of 812 in the inbox scanned.") and stop. The card is
            already visible to the user.

            Args:
                max_messages: How many INBOX messages (read + unread) to
                    scan (default 50, max 100).
                include_informational: When True, return the full
                    informational message list instead of just its count
                    (default False — see above).
            """
            try:
                max_messages = max(
                    1, min(int(max_messages or DEFAULT_INBOX_SCAN_MESSAGES), scan_ceiling)
                )
                # Phase 2 (#1603): pre-scan every connected mailbox, tag each
                # section item with its source mailbox, split the budget, merge.
                return _envelope_ok(
                    agent._pre_scan_all_backends(
                        max_messages=max_messages,
                        include_informational=bool(include_informational),
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")
