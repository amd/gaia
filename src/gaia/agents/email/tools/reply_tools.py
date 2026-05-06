# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Reply / send / forward tools.

``send_draft``, ``send_now``, and ``forward_message`` are registered in
``TOOLS_REQUIRING_CONFIRMATION`` at the agent level — they never
auto-execute. The confirmation payload includes the LITERAL ``to``,
``subject``, and ``body[:200]`` (Phase I2 / S2.M1) so the user sees what
will actually be sent, not an LLM-generated paraphrase.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, Optional

from gaia.agents.base.tools import tool
from gaia.agents.email import action_store
from gaia.agents.email.verbose import log_tool_call
from gaia.connectors.errors import ConnectorsError
from gaia.logger import get_logger

log = get_logger(__name__)


def _envelope_ok(data: Any) -> str:
    return json.dumps({"ok": True, "data": data}, default=str)


def _envelope_err(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


def _build_threading_headers(original_msg: Dict[str, Any]) -> Dict[str, str]:
    """Build proper ``In-Reply-To`` and ``References`` headers."""
    headers = {
        (h.get("name") or "").lower(): h.get("value", "")
        for h in (original_msg.get("payload") or {}).get("headers", [])
    }
    msg_id = headers.get("message-id", "")
    refs = headers.get("references", "")
    out: Dict[str, str] = {}
    if msg_id:
        out["In-Reply-To"] = msg_id
        # Append to existing references chain.
        chain = refs.strip() + (" " if refs else "") + msg_id
        out["References"] = chain
    return out


def draft_reply_impl(
    gmail,
    db,
    *,
    message_id: str,
    body: str,
    subject_override: Optional[str] = None,
    debug: bool = False,
) -> Dict[str, Any]:
    with log_tool_call(
        "draft_reply",
        {"message_id": message_id, "body": body[:120]},
        debug=debug,
    ) as st:
        original = gmail.get_message(message_id)
        headers_dict = {
            (h.get("name") or "").lower(): h.get("value", "")
            for h in (original.get("payload") or {}).get("headers", [])
        }
        to = headers_dict.get("from", "")
        original_subject = headers_dict.get("subject", "")
        if not original_subject.lower().startswith("re:"):
            subject = f"Re: {original_subject}"
        else:
            subject = original_subject
        if subject_override:
            subject = subject_override
        threading = _build_threading_headers(original)
        result = gmail.create_draft(
            to=to, subject=subject, body=body, headers=threading
        )
        draft_id = result["id"]
        action_store.record_draft(
            db,
            draft_id=draft_id,
            to=to,
            subject=subject,
            body=body,
            in_reply_to=threading.get("In-Reply-To"),
        )
        st["result_summary"] = {"draft_id": draft_id, "to": to}
        return {
            "draft_id": draft_id,
            "to": to,
            "subject": subject,
            "body_preview": body[:200],
        }


def draft_forward_impl(
    gmail,
    db,
    *,
    message_id: str,
    to: str,
    body: str,
    debug: bool = False,
) -> Dict[str, Any]:
    with log_tool_call(
        "draft_forward",
        {"message_id": message_id, "to": to, "body": body[:120]},
        debug=debug,
    ) as st:
        original = gmail.get_message(message_id)
        headers_dict = {
            (h.get("name") or "").lower(): h.get("value", "")
            for h in (original.get("payload") or {}).get("headers", [])
        }
        subject = headers_dict.get("subject", "")
        if not subject.lower().startswith("fwd:"):
            subject = f"Fwd: {subject}"
        result = gmail.create_draft(
            to=to,
            subject=subject,
            body=body
            + "\n\n----- Forwarded message -----\n"
            + (original.get("snippet", "") or ""),
        )
        draft_id = result["id"]
        action_store.record_draft(
            db, draft_id=draft_id, to=to, subject=subject, body=body
        )
        st["result_summary"] = {"draft_id": draft_id, "to": to}
        return {"draft_id": draft_id, "to": to, "subject": subject}


def send_draft_impl(gmail, db, *, draft_id: str, debug: bool = False) -> Dict[str, Any]:
    with log_tool_call("send_draft", {"draft_id": draft_id}, debug=debug) as st:
        result = gmail.send_draft(draft_id)
        action_store.mark_draft_sent(db, draft_id=draft_id)
        st["result_summary"] = {"sent_id": result.get("id")}
        return {"draft_id": draft_id, "sent_id": result.get("id"), "sent": True}


def send_now_impl(
    gmail,
    db,
    *,
    to: str,
    subject: str,
    body: str,
    debug: bool = False,
) -> Dict[str, Any]:
    """One-shot send (no draft step). Confirmation-gated at the agent level.

    Records an audit row in ``email_drafts`` with both ``created_at`` and
    ``sent_at`` populated so a one-shot send is visible to any future
    audit-log inspection alongside the regular draft-then-send flow.
    Ordering invariant: Gmail call first, DB write only on success.
    """
    with log_tool_call(
        "send_now",
        {"to": to, "subject": subject, "body": body[:120]},
        debug=debug,
    ) as st:
        result = gmail.send_message(to=to, subject=subject, body=body)
        sent_id = result.get("id") or ""
        # The send-message API returns a Gmail message id, not a draft
        # id; we use that as the row key so the audit table stays
        # uniquely keyed.
        try:
            action_store.record_draft(
                db, draft_id=sent_id, to=to, subject=subject, body=body
            )
            action_store.mark_draft_sent(db, draft_id=sent_id)
        except sqlite3.Error as exc:
            # Audit-write failures must NOT mask a successful send. Log
            # but don't raise — the email already left the user's
            # account; the agent must not retry.
            log.warning(
                "send_now: audit write failed for sent_id=%s (%s) — "
                "send DID succeed but audit row missing",
                sent_id,
                exc,
            )
        st["result_summary"] = {"sent_id": sent_id}
        return {"sent_id": sent_id, "to": to, "subject": subject, "sent": True}


def forward_message_impl(
    gmail,
    _db,
    *,
    message_id: str,
    to: str,
    note: str = "",
    debug: bool = False,
) -> Dict[str, Any]:
    """Forward a message to a new recipient. Confirmation-gated."""
    with log_tool_call(
        "forward_message",
        {"message_id": message_id, "to": to, "note": note[:120]},
        debug=debug,
    ) as st:
        original = gmail.get_message(message_id)
        headers_dict = {
            (h.get("name") or "").lower(): h.get("value", "")
            for h in (original.get("payload") or {}).get("headers", [])
        }
        subject = headers_dict.get("subject", "")
        if not subject.lower().startswith("fwd:"):
            subject = f"Fwd: {subject}"
        snippet = original.get("snippet", "") or ""
        body = (
            (note + "\n\n" if note else "")
            + "----- Forwarded message -----\n"
            + snippet
        )
        result = gmail.send_message(to=to, subject=subject, body=body)
        st["result_summary"] = {"sent_id": result.get("id"), "to": to}
        return {"sent_id": result.get("id"), "to": to, "subject": subject, "sent": True}


class ReplyToolsMixin:
    def _register_reply_tools(self) -> None:
        gmail = self._gmail
        db = self
        debug_flag = bool(getattr(self.config, "debug", False))

        @tool
        def draft_reply(message_id: str, body: str) -> str:
            """Create a reply draft for a message (does NOT send)."""
            try:
                return _envelope_ok(
                    draft_reply_impl(
                        gmail, db, message_id=message_id, body=body, debug=debug_flag
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def draft_forward(message_id: str, to: str, body: str = "") -> str:
            """Create a forward draft for a message (does NOT send)."""
            try:
                return _envelope_ok(
                    draft_forward_impl(
                        gmail,
                        db,
                        message_id=message_id,
                        to=to,
                        body=body,
                        debug=debug_flag,
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def send_draft(draft_id: str) -> str:
            """Send a previously-created draft. Requires user confirmation."""
            try:
                return _envelope_ok(
                    send_draft_impl(gmail, db, draft_id=draft_id, debug=debug_flag)
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def send_now(to: str, subject: str, body: str) -> str:
            """Send an email immediately, no draft step. Requires user confirmation."""
            try:
                return _envelope_ok(
                    send_now_impl(
                        gmail,
                        db,
                        to=to,
                        subject=subject,
                        body=body,
                        debug=debug_flag,
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def forward_message(message_id: str, to: str, note: str = "") -> str:
            """Forward an email to a new recipient. Requires user confirmation."""
            try:
                return _envelope_ok(
                    forward_message_impl(
                        gmail,
                        db,
                        message_id=message_id,
                        to=to,
                        note=note,
                        debug=debug_flag,
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")
