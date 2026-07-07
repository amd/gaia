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
import mimetypes
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from gaia_agent_email import action_store
from gaia_agent_email.tools.read_tools import extract_sender_email
from gaia_agent_email.verbose import log_tool_call

from gaia.agents.base.tools import tool
from gaia.connectors.errors import ConnectorsError
from gaia.connectors.formatting import format_connector_error
from gaia.logger import get_logger

log = get_logger(__name__)


# Gmail caps the whole raw message at 25 MB — mirror the contract's
# MAX_ATTACHMENT_BYTES without importing the (pydantic-only) contract module
# into the agent tool path.
_MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024


def _load_attachment_files(paths: str) -> Optional[List[Dict[str, Any]]]:
    """Resolve a comma/newline-separated list of file paths into backend
    attachment dicts (``filename``/``mime_type``/``content``) — #1542.

    Fail-loud by design: a missing file, an empty file, a file over the 25 MB
    Gmail cap, or an extension whose MIME type cannot be determined raises a
    ``ValueError`` with the offending path — never a silently skipped or
    mislabeled attachment.
    """
    entries = [p.strip() for chunk in paths.split("\n") for p in chunk.split(",")]
    entries = [p for p in entries if p]
    if not entries:
        return None
    out: List[Dict[str, Any]] = []
    for entry in entries:
        path = Path(entry).expanduser()
        if not path.is_file():
            raise ValueError(
                f"attachment not found: {entry!r} — pass the full path to an "
                f"existing file"
            )
        content = path.read_bytes()
        if not content:
            raise ValueError(f"attachment is empty: {entry!r}")
        if len(content) > _MAX_ATTACHMENT_BYTES:
            raise ValueError(
                f"attachment {entry!r} is {len(content)} bytes; the maximum is "
                f"{_MAX_ATTACHMENT_BYTES} bytes (25 MB)"
            )
        mime_type, _ = mimetypes.guess_type(path.name)
        if not mime_type:
            raise ValueError(
                f"cannot determine the MIME type of {entry!r} from its "
                f"extension — rename the file with a standard extension "
                f"(e.g. .pdf, .png, .csv) and retry"
            )
        out.append(
            {"filename": path.name, "mime_type": mime_type, "content": content}
        )
    return out


def _envelope_ok(data: Any) -> str:
    return json.dumps({"ok": True, "data": data}, default=str)


def _envelope_err(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


def _compute_reply_latency_seconds(original_msg: Dict[str, Any]) -> Optional[float]:
    """Seconds between the original message's receipt and now (the reply time).

    Accepts two receipt-anchor formats that the email backends use:

    - **Gmail** ``internalDate``: numeric millis since Unix epoch (int or str),
      e.g. ``"1717502400000"``.
    - **Outlook** ``receivedDateTime`` (mapped to ``internalDate`` by the Outlook
      backend): ISO-8601 string, e.g. ``"2026-06-04T12:00:00Z"``.

    Returns ``None`` when no usable anchor is present or parsing fails — the
    caller skips recording rather than fabricating a latency.
    """
    raw = original_msg.get("internalDate")
    if raw in (None, ""):
        return None
    # Try numeric (Gmail millis) first.
    try:
        received_s = int(raw) / 1000.0
        return time.time() - received_s
    except (TypeError, ValueError):
        pass
    # Try ISO-8601 string (Outlook receivedDateTime). Handle trailing 'Z'
    # which datetime.fromisoformat does not accept before Python 3.11.
    try:
        iso = str(raw).strip()
        if iso.endswith("Z"):
            iso = iso[:-1] + "+00:00"
        received_s = datetime.fromisoformat(iso).astimezone(timezone.utc).timestamp()
        return time.time() - received_s
    except (TypeError, ValueError):
        pass
    return None


def _record_reply_observation(agent, original_msg: Dict[str, Any]) -> None:
    """Record a reply-latency observation for the sender being replied to.

    Memory-guarded inside ``_record_reply_interaction`` (skips when memory is
    disabled). Skips silently when the original message has no receipt anchor
    (``internalDate``) or no resolvable sender — we never fabricate a latency.
    """
    if agent is None:
        return
    record = getattr(agent, "_record_reply_interaction", None)
    if record is None:
        return
    latency = _compute_reply_latency_seconds(original_msg)
    if latency is None:
        return
    headers_dict = {
        (h.get("name") or "").lower(): h.get("value", "")
        for h in (original_msg.get("payload") or {}).get("headers", [])
    }
    sender = extract_sender_email(headers_dict.get("from", ""))
    if not sender:
        return
    record(sender, latency_seconds=latency)


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
    attachments: Optional[List[Dict[str, Any]]] = None,
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
            to=to,
            subject=subject,
            body=body,
            headers=threading,
            attachments=attachments,
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
            "attachments": [a["filename"] for a in attachments or []],
            # The original message is returned so the caller can record a
            # reply-latency observation (behavioral learning, #1290). Kept out
            # of the user-facing envelope by the closure.
            "_original_msg": original,
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
    attachments: Optional[List[Dict[str, Any]]] = None,
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
        result = gmail.send_message(
            to=to, subject=subject, body=body, attachments=attachments
        )
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
        return {
            "sent_id": sent_id,
            "to": to,
            "subject": subject,
            "attachments": [a["filename"] for a in attachments or []],
            "sent": True,
        }


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
        db = self
        agent = self  # per-message backend routing (#1603 Phase 2)
        debug_flag = bool(getattr(self.config, "debug", False))

        @tool
        def draft_reply(
            message_id: str, body: str, mailbox: str = "", attachments: str = ""
        ) -> str:
            """Create a reply draft for a message (does NOT send).

            ``mailbox`` (optional) names the source mailbox so the draft is
            created in the right account when multiple mailboxes are connected.
            ``attachments`` (optional) is a comma-separated list of full paths
            to local files to attach to the draft.
            """
            try:
                provider = agent._provider_for_message(message_id, mailbox or None)
                backend = agent._backends[provider]
                result = draft_reply_impl(
                    backend,
                    db,
                    message_id=message_id,
                    body=body,
                    attachments=_load_attachment_files(attachments),
                    debug=debug_flag,
                )
                # Remember which mailbox holds this draft so send_draft routes
                # back to the same backend.
                agent._remember_draft_mailbox(result.get("draft_id"), provider)
                # Behavioral learning (#1290): observe how fast the user replied
                # to this sender, using the original message as the receipt
                # anchor. Pop the internal field so it never reaches the user.
                # The draft already succeeded — a bookkeeping failure here must
                # not turn that success into an error the user might retry, so
                # this specific post-commit write is logged, not raised.
                original_msg = result.pop("_original_msg", None)
                if original_msg is not None:
                    try:
                        _record_reply_observation(agent, original_msg)
                    except (
                        sqlite3.Error,
                        json.JSONDecodeError,
                        AttributeError,
                    ) as obs_exc:
                        log.warning(
                            "draft_reply: reply observation failed (%s) — draft "
                            "DID succeed; behavioral signal skipped",
                            obs_exc,
                        )
                return _envelope_ok(result)
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def draft_forward(
            message_id: str, to: str, body: str = "", mailbox: str = ""
        ) -> str:
            """Create a forward draft for a message (does NOT send).

            ``mailbox`` (optional) routes when multiple mailboxes are connected.
            """
            try:
                provider = agent._provider_for_message(message_id, mailbox or None)
                backend = agent._backends[provider]
                result = draft_forward_impl(
                    backend,
                    db,
                    message_id=message_id,
                    to=to,
                    body=body,
                    debug=debug_flag,
                )
                agent._remember_draft_mailbox(result.get("draft_id"), provider)
                return _envelope_ok(result)
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def send_draft(draft_id: str, mailbox: str = "") -> str:
            """Send a previously-created draft. Requires user confirmation.

            Routes to the mailbox the draft was created in (remembered from
            ``draft_reply`` / ``draft_forward``). ``mailbox`` overrides that.
            """
            try:
                backend = agent._backend_for_draft(draft_id, mailbox or None)
                return _envelope_ok(
                    send_draft_impl(backend, db, draft_id=draft_id, debug=debug_flag)
                )
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def send_now(
            to: str, subject: str, body: str, mailbox: str = "", attachments: str = ""
        ) -> str:
            """Send an email immediately, no draft step. Requires user confirmation.

            ``mailbox`` (optional) chooses which account sends when multiple are
            connected; defaults to the primary mailbox. ``attachments``
            (optional) is a comma-separated list of full paths to local files
            to attach.
            """
            try:
                backend = agent._send_backend(mailbox or None)
                return _envelope_ok(
                    send_now_impl(
                        backend,
                        db,
                        to=to,
                        subject=subject,
                        body=body,
                        attachments=_load_attachment_files(attachments),
                        debug=debug_flag,
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def forward_message(
            message_id: str, to: str, note: str = "", mailbox: str = ""
        ) -> str:
            """Forward an email to a new recipient. Requires user confirmation.

            ``mailbox`` (optional) routes when multiple mailboxes are connected.
            """
            try:
                provider = agent._provider_for_message(message_id, mailbox or None)
                return _envelope_ok(
                    forward_message_impl(
                        agent._backends[provider],
                        db,
                        message_id=message_id,
                        to=to,
                        note=note,
                        debug=debug_flag,
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")
