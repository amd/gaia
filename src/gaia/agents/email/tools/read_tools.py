# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Read tools mixin for ``EmailTriageAgent``.

Tools: ``list_inbox``, ``get_message``, ``get_thread``, ``search_messages``,
``list_labels``, ``triage_inbox``.

Each tool returns a JSON string with the canonical envelope::

    {"ok": true, "data": ...}      -- on success
    {"ok": false, "error": "..."}  -- on backend failure

Body content sent to the LLM is wrapped in an UNTRUSTED-INPUT delimiter
(see Phase I1 — system prompt hardening). The wrapper exists in this
module because every read tool that returns body bytes needs to honor it.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from gaia.agents.base.tools import tool
from gaia.agents.email.gmail_backend import decode_message_body
from gaia.agents.email.tools.triage_heuristics import (
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


def triage_inbox_impl(
    gmail,
    *,
    max_messages: int = 25,
    debug: bool = False,
) -> Dict[str, Any]:
    """Triage the inbox using heuristic fast path + LLM fallback.

    For each message: fetch metadata, run the heuristic. If the heuristic
    is confident, record its category as the triage decision. Otherwise
    flag the message for LLM follow-up — the LLM tool call happens in the
    agent's planning loop, not in this tool body (the heuristic alone is
    cheap; LLM round-trips are expensive and are sequenced by the agent).

    Returns a summary listing per-message classifications + a bucketed
    view via ``group_by_category``.
    """
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
            log_triage_decision(
                message_id=msg["id"],
                category=heuristic.category,
                is_spam=heuristic.is_spam,
                is_phishing=heuristic.is_phishing,
                confidence="heuristic" if heuristic.confident else "needs_llm",
                rationale=heuristic.reason,
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


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------


class ReadToolsMixin:
    """Mixin that registers the read-side tools.

    The mixin is state-free at construction time — it relies on the agent
    class having set ``self._gmail`` (and optionally ``self.config.debug``)
    before invoking ``self._register_read_tools()``.
    """

    def _register_read_tools(self) -> None:
        gmail = self._gmail
        debug_flag = bool(getattr(self.config, "debug", False))

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
            """
            try:
                max_messages = max(1, min(int(max_messages or 25), 100))
                return _envelope_ok(
                    triage_inbox_impl(
                        gmail, max_messages=max_messages, debug=debug_flag
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")
