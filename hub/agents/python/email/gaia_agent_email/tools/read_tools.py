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

import os
from typing import Any, Callable, Dict, List, Mapping, Optional

from gaia_agent_email.tools.envelope import _envelope_err, _envelope_ok
from gaia_agent_email.gmail_backend import decode_message_body

# Re-exported so the pre-scan tests can monkeypatch ``read_tools.make_llm_classifier``
# to prove pre-scan never wires the LLM (test_pre_scan_counts.py).
from gaia_agent_email.tools.llm_triage import make_llm_classifier  # noqa: F401
from gaia_agent_email.tools.triage_heuristics import (
    CATEGORY_FYI,
    CATEGORY_NEEDS_RESPONSE,
    CATEGORY_PROMOTIONAL,
    CATEGORY_URGENT,
    classify_category_heuristic,
    group_by_category,
)
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

# Default per-call ceiling for inbox-scanning tools (triage / pre-scan). Bounds
# an interactive call so the LLM can't trigger a thousand-message scan that
# blows latency and context. The eval benchmark scores a fixed labelled corpus
# and needs to cover all of it deterministically, so it raises this ceiling via
# GAIA_EMAIL_TRIAGE_MAX_MESSAGES — the per-email classification is identical
# whether batched at 100 or at the corpus size, so the override is
# measurement-neutral and only changes coverage, never a decision.
DEFAULT_INBOX_SCAN_CEILING = 100


def _inbox_scan_ceiling() -> int:
    """Per-call ceiling for triage/pre-scan, overridable for the eval harness."""
    raw = os.environ.get("GAIA_EMAIL_TRIAGE_MAX_MESSAGES")
    if not raw:
        return DEFAULT_INBOX_SCAN_CEILING
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_INBOX_SCAN_CEILING


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

    The body is decoded via the production decoder and wrapped in the
    untrusted-input delimiter so the LLM never confuses content with
    instructions.
    """
    payload = msg.get("payload") or {}
    headers = {
        (h.get("name") or "").lower(): h.get("value", "")
        for h in payload.get("headers", [])
    }
    body, attachments = decode_message_body(payload)
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


def list_inbox_impl(
    gmail, *, max_results: int = 25, debug: bool = False
) -> Dict[str, Any]:
    with log_tool_call("list_inbox", {"max_results": max_results}, debug=debug) as st:
        listing = gmail.list_messages(label_ids=["INBOX"], max_results=max_results)
        out = []
        for stub in listing.get("messages", []):
            full = gmail.get_message(stub["id"])
            out.append(_format_message_for_llm(full))
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
    """Fetch every message in a thread, backend order preserved (no sort).

    The combined body budget mirrors ``_format_thread_for_summary``'s
    soft-target semantics (#2073): under ``DEFAULT_THREAD_TRANSCRIPT_CHARS``
    the per-message default limit applies untouched; over budget, every
    message is re-formatted at a shared fair-share limit (floored at
    ``THREAD_MIN_PER_MESSAGE_CHARS``) so long threads stay bounded without
    ever dropping a message.
    """
    with log_tool_call("get_thread", {"thread_id": thread_id}, debug=debug) as st:
        thread = gmail.get_thread(thread_id)
        messages = thread.get("messages", [])
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
                    _format_message_for_llm(m, body_limit=fair_share)
                    for m in messages
                ]
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
    ``None`` disables the cap entirely.
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
    blocks: List[str] = []
    for idx, msg in enumerate(ordered, start=1):
        payload = msg.get("payload") or {}
        headers = {
            (h.get("name") or "").lower(): h.get("value", "")
            for h in payload.get("headers", [])
        }
        body, _attachments = decode_message_body(payload)
        body = (body or "").strip()
        if effective_body_limit > 0 and len(body) > effective_body_limit:
            body = body[:effective_body_limit] + "\n...[truncated]"
        blocks.append(
            f"--- Message {idx} of {len(ordered)} ---\n"
            f"From: {headers.get('from', '')}\n"
            f"Date: {headers.get('date', '')}\n"
            f"{wrap_untrusted_body(body)}"
        )
    return "\n\n".join(blocks)


def _build_thread_user_prompt(subject: str, transcript: str) -> str:
    """Build the user-turn prompt for whole-thread summarization.

    Unlike the single-email prompt, this does NOT clip the body to a single
    message's budget — the transcript is the FULL conversation and each
    message body is already individually wrapped + truncated by
    ``_format_thread_for_summary``. Re-clipping here would drop later
    messages and defeat full-thread comprehension.
    """
    return (
        "Summarize this email thread as a whole. Reflect decisions, asks, and "
        "outcomes from EVERY message — including earlier messages the latest "
        "reply does not repeat.\n\n"
        f"Subject: {subject}\n"
        f"Thread (oldest first):\n{transcript}\n"
    )


def summarize_thread_impl(
    gmail,
    chat,
    *,
    thread_id: str,
    max_chars: Optional[int] = None,
    per_message_body_limit: int = DEFAULT_BODY_LIMIT_CHARS,
    max_total_transcript_chars: Optional[int] = DEFAULT_THREAD_TRANSCRIPT_CHARS,
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
    """
    # Deferred import: ``summarize_tools`` imports from this module, so a
    # top-level import would create a cycle.
    from gaia_agent_email.tools.summarize_tools import (
        _THREAD_SYSTEM_PROMPT,
        DEFAULT_SUMMARY_CHAR_LIMIT,
        EmailSummarizeError,
        _bound_to_length,
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
        first_headers = {
            (h.get("name") or "").lower(): h.get("value", "")
            for h in (ordered[0].get("payload") or {}).get("headers", [])
        }
        subject = first_headers.get("subject", "")
        transcript = _format_thread_for_summary(
            ordered,
            per_message_body_limit=per_message_body_limit,
            max_total_transcript_chars=max_total_transcript_chars,
        )

        prompt = _build_thread_user_prompt(subject, transcript)
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
            "message_count": len(ordered),
            "chars": len(summary),
        }
        return {
            "thread_id": thread_id,
            "subject": subject,
            "message_count": len(ordered),
            "summary": summary,
        }


def search_messages_impl(
    gmail,
    *,
    query: str,
    max_results: int = 25,
    debug: bool = False,
) -> Dict[str, Any]:
    with log_tool_call(
        "search_messages",
        {"query": query, "max_results": max_results},
        debug=debug,
    ) as st:
        listing = gmail.list_messages(query=query, max_results=max_results)
        out = []
        for stub in listing.get("messages", []):
            msg = gmail.get_message(stub["id"])
            out.append(_format_message_for_llm(msg))
        st["result_summary"] = {"count": len(out)}
        return {"messages": out}


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


def triage_inbox_impl(
    gmail,
    *,
    max_messages: int = 25,
    session_preferences: Optional[Mapping[str, Any]] = None,
    force_llm: bool = False,
    classifier: Optional[Callable[..., Mapping[str, Any]]] = None,
    debug: bool = False,
) -> Dict[str, Any]:
    """Triage the inbox using heuristic fast path + LLM fallback.

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
    view via ``group_by_category``.
    """
    prefs = session_preferences or {}
    with log_tool_call(
        "triage_inbox", {"max_messages": max_messages}, debug=debug
    ) as st:
        listing = gmail.list_messages(label_ids=["INBOX"], max_results=max_messages)
        results: List[Dict[str, Any]] = []
        for stub in listing.get("messages", []):
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
        grouped = group_by_category(results)
        st["result_summary"] = {
            "total": grouped["total"],
            "spam_count": len(grouped["spam"]),
            "phishing_count": len(grouped["phishing"]),
        }
        return {"results": results, "grouped": grouped}


# Default per-section caps for the pre-scan envelope. Small enough to be
# scannable in a single screen; large enough to surface most of the inbox
# signal for a typical morning triage session. Callers can override via
# the tool kwargs if a heavier inbox needs more headroom.
PRE_SCAN_URGENT_CAP = 5
PRE_SCAN_ACTIONABLE_CAP = 5
PRE_SCAN_ARCHIVE_CAP = 10


def pre_scan_inbox_impl(
    gmail,
    *,
    max_messages: int = 25,
    urgent_cap: int = PRE_SCAN_URGENT_CAP,
    actionable_cap: int = PRE_SCAN_ACTIONABLE_CAP,
    archive_cap: int = PRE_SCAN_ARCHIVE_CAP,
    session_preferences: Optional[Mapping[str, Any]] = None,
    force_llm: bool = False,
    debug: bool = False,
) -> Dict[str, Any]:
    """Pre-scan the inbox for the chat surface.

    Reshapes ``triage_inbox_impl`` output into a typed envelope optimized
    for a daily-driver triage card: top-N urgent, top-N actionable,
    informational count, suggested archives derived from the low-priority
    bucket and (when configured) from category defaults. The caller is
    expected to set ``kind`` in the rendered output to ``email_pre_scan``
    so the chat surface can detect and render the structured card
    component.

    ``session_preferences`` flow through to ``triage_inbox_impl`` so
    sender overrides shape the underlying classification, and category
    defaults applied here move informational items into
    ``suggested_archives`` when the user has previously asked for that.

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
            session_preferences=prefs,
            force_llm=force_llm,
            debug=debug,
        )
        urgent: List[Dict[str, Any]] = []
        actionable: List[Dict[str, Any]] = []
        informational: List[Dict[str, Any]] = []
        suggested_archives: List[Dict[str, Any]] = []

        for r in triage["results"]:
            base = {
                "message_id": r["id"],
                "thread_id": r.get("thread_id"),
                "sender": r.get("from", ""),
                "subject": r.get("subject", ""),
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

            if category == CATEGORY_URGENT:
                urgent.append({**base, "why": why})
            elif category == CATEGORY_NEEDS_RESPONSE:
                actionable.append({**base, "why": why})
            elif category == CATEGORY_PROMOTIONAL:
                suggested_archives.append({**base, "reason": why})
            else:
                # FYI and PERSONAL share the keep / no-action bucket.
                informational.append({**base, "why": why})

        # Apply the FYI category default: when the user has previously asked
        # us to archive FYI mail, lift those items into suggested_archives.
        # (The ``informational`` list holds both FYI and PERSONAL — the keep
        # bucket — but only the FYI default promotes to archive.)
        if category_defaults.get(CATEGORY_FYI) == "archive":
            for item in informational:
                suggested_archives.append(
                    {
                        "message_id": item["message_id"],
                        "thread_id": item.get("thread_id"),
                        "sender": item["sender"],
                        "subject": item["subject"],
                        "reason": (
                            "informational + session default 'archive'"
                            f" — {item.get('why', '')}"
                        ).rstrip(" —"),
                    }
                )
            informational = []

        out = {
            "kind": "email_pre_scan",
            "urgent": urgent[: max(0, urgent_cap)],
            "actionable": actionable[: max(0, actionable_cap)],
            "informational_count": len(informational),
            "suggested_archives": suggested_archives[: max(0, archive_cap)],
            "suggested_drafts": [],
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
            },
        }
        st["result_summary"] = {
            "urgent": out["totals"]["urgent"],
            "actionable": out["totals"]["actionable"],
            "informational": out["totals"]["informational"],
            "suggested_archives": out["totals"]["suggested_archives"],
        }
        return out


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
        agent = self  # captured for live access to ``_session_preferences``

        @tool
        def list_inbox(max_results: int = 25) -> str:
            """List the most recent INBOX messages.

            When multiple mailboxes are connected, lists from ALL of them with a
            shared total budget (never per-mailbox-doubled). Each returned message
            carries a ``mailbox`` field ('google' / 'microsoft') so downstream
            tools can route actions without re-asking.

            Args:
                max_results: How many messages to return in total (default 25, max 100).

            Returns:
                JSON envelope with ``{"messages": [...]}`` per message:
                id, thread_id, subject, from, to, date, label_ids,
                snippet, body (wrapped in untrusted-input delimiters),
                body_truncated, body_chars_dropped, attachments, mailbox.
            """
            try:
                max_results = max(1, min(int(max_results or 25), 100))
                backends = agent._backends
                per_backend = max(1, max_results // len(backends))
                merged: List[Dict[str, Any]] = []
                for provider, backend in backends.items():
                    if len(merged) >= max_results:
                        break
                    result = list_inbox_impl(
                        backend, max_results=per_backend, debug=debug_flag
                    )
                    for msg in result.get("messages", []):
                        msg["mailbox"] = provider
                        agent._remember_message_mailbox(msg.get("id"), provider)
                        agent._remember_message_mailbox(msg.get("thread_id"), provider)
                        merged.append(msg)
                return _envelope_ok(
                    {"messages": merged[:max_results], "next_page_token": None}
                )
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

            Long threads share a combined body budget: over-budget message
            bodies are clipped with a ``...[truncated]`` marker; messages are
            never dropped. ``mailbox`` (optional) routes when multiple
            mailboxes are connected.
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
                length-bounded string covering the full thread.
            """
            try:
                # Deferred import avoids a module-load cycle with summarize_tools.
                from gaia_agent_email.tools.summarize_tools import (
                    EmailSummarizeError,
                )

                chat = getattr(agent, "chat", None)
                backend = agent._backend_for_message(thread_id, mailbox or None)
                return _envelope_ok(
                    summarize_thread_impl(
                        backend,
                        chat,
                        thread_id=thread_id,
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
            downstream tools route actions without re-asking.

            ``query`` uses Gmail search syntax (e.g.
            ``"from:boss@example.com is:unread newer_than:7d"``).
            """
            try:
                max_results = max(1, min(int(max_results or 25), 100))
                backends = agent._backends
                per_backend = max(1, max_results // len(backends))
                merged: List[Dict[str, Any]] = []
                for provider, backend in backends.items():
                    if len(merged) >= max_results:
                        break
                    result = search_messages_impl(
                        backend, query=query, max_results=per_backend, debug=debug_flag
                    )
                    for msg in result.get("messages", []):
                        msg["mailbox"] = provider
                        agent._remember_message_mailbox(msg.get("id"), provider)
                        agent._remember_message_mailbox(msg.get("thread_id"), provider)
                        merged.append(msg)
                return _envelope_ok({"messages": merged[:max_results]})
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

        @tool
        def triage_inbox(max_messages: int = 25) -> str:
            """Triage the inbox, returning per-message categories.

            Categories: ``URGENT``, ``NEEDS_RESPONSE``, ``FYI``,
            ``PROMOTIONAL``, ``PERSONAL``. Each result also has ``is_spam`` and
            ``is_phishing`` booleans. The ``confident`` field is True
            when the heuristic alone was sufficient; False means the
            agent should re-classify the body via LLM follow-up.

            Session preferences set via ``set_priority_sender`` /
            ``set_low_priority_sender`` are honored — those senders
            bypass the heuristic and are recorded with
            ``preference_applied`` for downstream inspection.
            """
            try:
                max_messages = max(
                    1, min(int(max_messages or 25), _inbox_scan_ceiling())
                )
                # Phase 2 (#1603): scan every connected mailbox, tag each item
                # with its source mailbox, split the budget across mailboxes,
                # and merge. LLM follow-up (#1107) is wired inside the agent
                # orchestration so agent.chat is initialized at call time.
                return _envelope_ok(
                    agent._triage_all_backends(max_messages=max_messages)
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

            Reshapes the per-message triage decisions into three sections
            (urgent, actionable, suggested archives), an informational
            count, and an empty drafts placeholder. The result has
            ``kind: "email_pre_scan"`` so the chat surface renders the
            structured card component instead of plain text.

            The chat surface injects the triage card automatically from
            the tool result — do NOT copy, re-serialize, or paraphrase
            the JSON envelope into your reply. Re-emitting the full
            envelope wastes the output budget on long message/thread IDs
            and truncates the prose summary before the user can read it.
            After this tool returns, write ONE short framing sentence
            (e.g. "Here's your inbox pre-scan — 3 actionable, 1 urgent.")
            and stop. The card is already visible to the user.

            Args:
                max_messages: How many INBOX messages to scan
                    (default 25, max 100).
            """
            try:
                max_messages = max(
                    1, min(int(max_messages or 25), _inbox_scan_ceiling())
                )
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
