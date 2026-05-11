# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Read tools mixin for ``EmailTriageAgent``.

Tools: ``list_inbox``, ``get_message``, ``get_thread``, ``search_messages``,
``list_labels``, ``triage_inbox``, ``pre_scan_inbox``.

Each tool returns a JSON string with the canonical envelope::

    {"ok": true, "data": ...}      -- on success
    {"ok": false, "error": "..."}  -- on backend failure

Body content sent to the LLM is wrapped in an UNTRUSTED-INPUT delimiter
(see Phase I1 — system prompt hardening). The wrapper exists in this
module because every read tool that returns body bytes needs to honor it.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional

from gaia.agents.base.tools import tool
from gaia.agents.email.gmail_backend import decode_message_body
from gaia.agents.email.tools.triage_heuristics import (
    CATEGORY_ACTIONABLE,
    CATEGORY_INFORMATIONAL,
    CATEGORY_LOW_PRIORITY,
    CATEGORY_URGENT,
    classify_category_heuristic,
    group_by_category,
)
from gaia.agents.email.verbose import (
    log_tool_call,
    log_triage_decision,
    log_triage_dispatch,
)
from gaia.connectors.errors import ConnectorsError
from gaia.logger import get_logger

log = get_logger(__name__)

# Maximum body length sent to the LLM. Larger messages are truncated with
# a ``...[truncated]`` marker. Prevents context blow-up and limits the
# attack surface for indirect prompt injection.
DEFAULT_BODY_LIMIT_CHARS = 4000

# Wrapper used to delimit untrusted email body content. The system prompt
# (see ``agent.py``) tells the LLM that anything inside this wrapper is
# DATA, never an instruction to execute. Phase I1 / S2.M3.
UNTRUSTED_BODY_OPEN = "<<<UNTRUSTED_EMAIL_BODY_START>>>"
UNTRUSTED_BODY_CLOSE = "<<<UNTRUSTED_EMAIL_BODY_END>>>"


def wrap_untrusted_body(body: str) -> str:
    """Wrap a body in the untrusted-input delimiter pair."""
    return f"{UNTRUSTED_BODY_OPEN}\n{body}\n{UNTRUSTED_BODY_CLOSE}"


def _envelope_ok(data: Any) -> str:
    return json.dumps({"ok": True, "data": data}, default=str)


def _envelope_err(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n...[truncated]", True


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
    body_truncated = False
    if body:
        body, body_truncated = _truncate(body, body_limit)
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
        "body_truncated": body_truncated,
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


def get_message_impl(gmail, *, message_id: str, debug: bool = False) -> Dict[str, Any]:
    with log_tool_call("get_message", {"message_id": message_id}, debug=debug) as st:
        msg = gmail.get_message(message_id)
        formatted = _format_message_for_llm(msg)
        st["result_summary"] = {
            "id": formatted["id"],
            "subject": formatted["subject"],
        }
        return formatted


def get_thread_impl(gmail, *, thread_id: str, debug: bool = False) -> Dict[str, Any]:
    with log_tool_call("get_thread", {"thread_id": thread_id}, debug=debug) as st:
        thread = gmail.get_thread(thread_id)
        out = [_format_message_for_llm(m) for m in thread.get("messages", [])]
        st["result_summary"] = {"thread_id": thread_id, "count": len(out)}
        return {"thread_id": thread_id, "messages": out}


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
        out["category"] = CATEGORY_LOW_PRIORITY
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
    debug: bool = False,
) -> Dict[str, Any]:
    """Triage the inbox using heuristic fast path + LLM fallback.

    For each message: fetch metadata, run the heuristic. If the heuristic
    is confident, record its category as the triage decision. Otherwise
    flag the message for LLM follow-up — the LLM tool call happens in the
    agent's planning loop, not in this tool body (the heuristic alone is
    cheap; LLM round-trips are expensive and are sequenced by the agent).

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
                "confident": heuristic.confident,
                "rationale": heuristic.reason,
            }
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
            category = r.get("category", CATEGORY_INFORMATIONAL)

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
            elif category == CATEGORY_ACTIONABLE:
                actionable.append({**base, "why": why})
            elif category == CATEGORY_LOW_PRIORITY:
                suggested_archives.append({**base, "reason": why})
            else:
                informational.append({**base, "why": why})

        # Apply the informational category default: when the user has
        # previously asked us to archive informational mail, lift those
        # items into suggested_archives.
        if category_defaults.get(CATEGORY_INFORMATIONAL) == "archive":
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
    class having set ``self._gmail`` (and optionally ``self.config.debug``)
    before invoking ``self._register_read_tools()``. The ``agent``
    closure capture is used so triage / pre-scan tools can read live
    ``self._session_preferences`` (set on the agent instance) at call
    time, not snapshot at registration time.
    """

    def _register_read_tools(self) -> None:
        gmail = self._gmail
        debug_flag = bool(getattr(self.config, "debug", False))
        agent = self  # captured for live access to ``_session_preferences``

        @tool
        def list_inbox(max_results: int = 25) -> str:
            """List the most recent INBOX messages.

            Args:
                max_results: How many messages to return (default 25, max 100).

            Returns:
                JSON envelope with ``{"messages": [...]}`` per message:
                id, thread_id, subject, from, to, date, label_ids,
                snippet, body (wrapped in untrusted-input delimiters),
                body_truncated, attachments.
            """
            try:
                max_results = max(1, min(int(max_results or 25), 100))
                return _envelope_ok(
                    list_inbox_impl(gmail, max_results=max_results, debug=debug_flag)
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def get_message(message_id: str) -> str:
            """Fetch a single message by id, including full body."""
            try:
                return _envelope_ok(
                    get_message_impl(gmail, message_id=message_id, debug=debug_flag)
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def get_thread(thread_id: str) -> str:
            """Fetch every message in a thread (conversation view)."""
            try:
                return _envelope_ok(
                    get_thread_impl(gmail, thread_id=thread_id, debug=debug_flag)
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def search_messages(query: str, max_results: int = 25) -> str:
            """Search the user's mailbox.

            ``query`` uses Gmail search syntax (e.g.
            ``"from:boss@example.com is:unread newer_than:7d"``).
            """
            try:
                max_results = max(1, min(int(max_results or 25), 100))
                return _envelope_ok(
                    search_messages_impl(
                        gmail,
                        query=query,
                        max_results=max_results,
                        debug=debug_flag,
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def list_labels() -> str:
            """List every label (system + user-defined) in the mailbox."""
            try:
                return _envelope_ok(list_labels_impl(gmail, debug=debug_flag))
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def triage_inbox(max_messages: int = 25) -> str:
            """Triage the inbox, returning per-message categories.

            Categories: ``urgent``, ``actionable``, ``informational``,
            ``low priority``. Each result also has ``is_spam`` and
            ``is_phishing`` booleans. The ``confident`` field is True
            when the heuristic alone was sufficient; False means the
            agent should re-classify the body via LLM follow-up.

            Session preferences set via ``set_priority_sender`` /
            ``set_low_priority_sender`` are honored — those senders
            bypass the heuristic and are recorded with
            ``preference_applied`` for downstream inspection.
            """
            try:
                max_messages = max(1, min(int(max_messages or 25), 100))
                return _envelope_ok(
                    triage_inbox_impl(
                        gmail,
                        max_messages=max_messages,
                        session_preferences=getattr(
                            agent, "_session_preferences", None
                        ),
                        debug=debug_flag,
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
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

            CRITICAL OUTPUT FORMAT for the LLM:
            After this tool returns, your response to the user MUST be a
            single fenced code block tagged ``email_pre_scan`` with the
            ``data`` field's JSON inside it, exactly like::

                ```email_pre_scan
                {"kind": "email_pre_scan", ...}
                ```

            Optionally include ONE short framing sentence before the
            block (e.g. "Here's your morning pre-scan:"). The frontend
            detects the language tag and renders a triage card; if you
            paraphrase the JSON or omit the fence, the user sees raw
            text instead of the card.

            Args:
                max_messages: How many INBOX messages to scan
                    (default 25, max 100).
            """
            try:
                max_messages = max(1, min(int(max_messages or 25), 100))
                return _envelope_ok(
                    pre_scan_inbox_impl(
                        gmail,
                        max_messages=max_messages,
                        session_preferences=getattr(
                            agent, "_session_preferences", None
                        ),
                        debug=debug_flag,
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")
