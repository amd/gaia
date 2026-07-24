# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
In-memory ``GmailBackend`` fake for eval mode.

Reads a local ``.mbox`` file, translates each message into the same
Gmail API v1 JSON shape that ``LiveGmailBackend`` returns, and exposes
the same Protocol surface — so the email agent's tools cannot tell
whether they are talking to live Gmail or the synthetic dataset.

Critical contract (CA-5): every method must return data in Gmail API
v1 shape — ``{"id": str, "threadId": str, "payload": {"headers":
[{"name", "value"}], "parts": [...], "body": {"data": ...}},
"labelIds": [...]}``. The shape parity is enforced by
``tests/unit/email/test_fake_gmail_shape_contract.py``.

The MIME body decoding uses the same module-level helpers from
``gmail_backend.py`` so the production decoder is what's exercised in
unit tests, NOT a parallel implementation.
"""

from __future__ import annotations

import base64
import hashlib
import mailbox
import re
import uuid
from datetime import datetime, timezone
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, cast

# ---------------------------------------------------------------------------
# mbox → Gmail-API translator
# ---------------------------------------------------------------------------


def _decode_header_value(raw: Optional[str]) -> str:
    """Decode RFC 2047 encoded-word header values into Unicode.

    Live Gmail returns header values already decoded by Google. The fake
    is reading raw mbox where headers may still be encoded — so it MUST
    decode here, while the production ``decode_message_body`` MUST NOT
    (it would double-decode and produce literal ``=?UTF-8?B?...?=``).
    """
    if raw is None:
        return ""
    try:
        parts = decode_header(raw)
    except Exception:
        return raw
    pieces: list[str] = []
    for chunk, charset in parts:
        if isinstance(chunk, bytes):
            try:
                pieces.append(chunk.decode(charset or "utf-8", errors="replace"))
            except (LookupError, UnicodeDecodeError):
                pieces.append(chunk.decode("latin-1", errors="replace"))
        else:
            pieces.append(chunk)
    return "".join(pieces)


def _parse_x_gmail_labels(raw: Optional[str]) -> List[str]:
    """Translate MBOX ``X-Gmail-Labels`` (human names) to system label IDs.

    MBOX exports give us strings like ``"Important,Promotions,Inbox"``.
    Live Gmail returns IDs like ``IMPORTANT``, ``CATEGORY_PROMOTIONS``,
    ``INBOX``. This map covers the system-label cases; user-defined
    labels are ignored (they have no stable opaque ID without an API
    round-trip to ``labels.list``).
    """
    if not raw:
        return []
    HUMAN_TO_ID = {
        "inbox": "INBOX",
        "important": "IMPORTANT",
        "starred": "STARRED",
        "unread": "UNREAD",
        "spam": "SPAM",
        "trash": "TRASH",
        "sent": "SENT",
        "draft": "DRAFT",
        "promotions": "CATEGORY_PROMOTIONS",
        "updates": "CATEGORY_UPDATES",
        "social": "CATEGORY_SOCIAL",
        "forums": "CATEGORY_FORUMS",
        "personal": "CATEGORY_PERSONAL",
        # Common Gmail-Takeout shorthand:
        "category promotions": "CATEGORY_PROMOTIONS",
        "category updates": "CATEGORY_UPDATES",
        "category social": "CATEGORY_SOCIAL",
        "category forums": "CATEGORY_FORUMS",
        "category personal": "CATEGORY_PERSONAL",
    }
    out: list[str] = []
    for raw_label in raw.split(","):
        normalized = raw_label.strip().lower()
        if normalized in HUMAN_TO_ID:
            out.append(HUMAN_TO_ID[normalized])
    return out


def _internal_date_ms(msg: Message) -> str:
    """Gmail API returns ``internalDate`` as a string of millis since epoch."""
    raw = msg.get("Date") or ""
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        dt = None
    if dt is None:
        dt = datetime.now(timezone.utc)
    return str(int(dt.timestamp() * 1000))


def _b64url(data: bytes) -> str:
    """URL-safe base64 with stripped padding (matches Gmail's wire format)."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _walk_mime_to_payload(part: Message) -> Dict[str, Any]:
    """Recursively translate an ``email.message.Message`` into Gmail's
    ``payload`` shape.

    Each node has ``mimeType``, ``filename``, ``headers``, ``body`` (with
    ``size`` + optional ``data`` or ``attachmentId``), and ``parts`` for
    multipart containers.
    """
    headers = [{"name": k, "value": _decode_header_value(v)} for k, v in part.items()]
    mime_type = part.get_content_type()
    filename = part.get_filename() or ""

    if part.is_multipart():
        return {
            "mimeType": mime_type,
            "filename": filename,
            "headers": headers,
            "body": {"size": 0},
            "parts": [
                _walk_mime_to_payload(p)
                for p in cast(List[Message], part.get_payload())
            ],
        }

    raw = cast(bytes, part.get_payload(decode=True) or b"")
    body: Dict[str, Any] = {"size": len(raw)}
    if mime_type.startswith("text/") or mime_type == "message/rfc822":
        body["data"] = _b64url(raw)
    else:
        # Treat as attachment — synth a deterministic attachmentId.
        att_id = "att_" + hashlib.sha256(raw).hexdigest()[:16] if raw else "att_empty"
        body["attachmentId"] = att_id
    return {
        "mimeType": mime_type,
        "filename": filename,
        "headers": headers,
        "body": body,
    }


def mbox_message_to_gmail_payload(msg: Message) -> Dict[str, Any]:
    """Translate an ``email.message.Message`` into a Gmail API v1 message dict.

    Returns the shape produced by ``users.messages.get?format=full``::

        {
            "id": str,
            "threadId": str,
            "labelIds": [str],
            "snippet": str,
            "internalDate": str (millis),
            "payload": {...},
            "sizeEstimate": int,
        }

    The id and threadId are SHA256-derived from ``Message-ID`` so the same
    mbox produces the same ids across test runs (deterministic).
    """
    msg_id_header = msg.get("Message-ID") or msg.get("Message-Id") or ""
    if msg_id_header:
        gid_seed = msg_id_header.encode("utf-8", errors="replace")
    else:
        gid_seed = (msg.get("Subject") or str(uuid.uuid4())).encode(
            "utf-8", errors="replace"
        )
    gid = hashlib.sha256(gid_seed).hexdigest()[:16]
    references = msg.get("References") or msg.get("In-Reply-To") or ""
    if references:
        thread_seed = references.split()[0].encode("utf-8", errors="replace")
        thread_id = hashlib.sha256(thread_seed).hexdigest()[:16]
    else:
        thread_id = gid

    payload = _walk_mime_to_payload(msg)
    snippet = _build_snippet(msg)

    label_ids = _parse_x_gmail_labels(msg.get("X-Gmail-Labels"))
    if (
        "INBOX" not in label_ids
        and "TRASH" not in label_ids
        and "SPAM" not in label_ids
    ):
        label_ids.append("INBOX")
    if msg.get("X-Read-State", "").lower() != "read" and "UNREAD" not in label_ids:
        # Default to UNREAD unless the fixture explicitly marks it read.
        label_ids.append("UNREAD")

    return {
        "id": gid,
        "threadId": thread_id,
        "labelIds": label_ids,
        "snippet": snippet,
        "internalDate": _internal_date_ms(msg),
        "payload": payload,
        "sizeEstimate": _estimate_size(msg),
    }


def _build_snippet(msg: Message) -> str:
    """Build a short text snippet from the first text/plain part."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                raw = cast(bytes, part.get_payload(decode=True) or b"")
                try:
                    text = raw.decode("utf-8", errors="replace")
                except Exception:
                    text = ""
                return _normalize_snippet(text)
        return ""
    raw = msg.get_payload(decode=True) or b""
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception:
            text = ""
    else:
        text = str(raw)
    return _normalize_snippet(text)


def _normalize_snippet(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[:200]


def _estimate_size(msg: Message) -> int:
    return len(msg.as_bytes()) if hasattr(msg, "as_bytes") else len(str(msg))


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


class FakeGmailTransport:
    """Records every call. Useful for assertions in tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def record(self, method: str, **kwargs: Any) -> None:
        self.calls.append((method, kwargs))

    def reset(self) -> None:
        self.calls.clear()


class FakeGmailBackend:
    """Implements ``GmailBackend`` against an in-memory mbox-derived store."""

    def __init__(
        self,
        mbox_path: Optional[Path] = None,
        *,
        user_email: str = "user@example.com",
        transport: Optional[FakeGmailTransport] = None,
    ):
        self._user_email = user_email
        self._transport = transport or FakeGmailTransport()
        self._messages: Dict[str, Dict[str, Any]] = {}
        self._labels: List[Dict[str, Any]] = _DEFAULT_SYSTEM_LABELS[:]
        self._drafts: Dict[str, Dict[str, Any]] = {}
        self._next_draft_seq = 1
        if mbox_path is not None:
            self.load_mbox(mbox_path)

    # -- Setup --------------------------------------------------------------

    def load_mbox(self, path: Path) -> None:
        mbox = mailbox.mbox(str(path))
        for msg in mbox:
            payload = mbox_message_to_gmail_payload(msg)
            self._messages[payload["id"]] = payload
        mbox.close()

    def add_message(self, payload: Dict[str, Any]) -> None:
        """Inject a pre-built Gmail-API-shape message (used by unit tests)."""
        self._messages[payload["id"]] = payload

    @property
    def transport(self) -> FakeGmailTransport:
        return self._transport

    # -- Read ---------------------------------------------------------------

    def get_user_email(self) -> str:
        self._transport.record("get_user_email")
        return self._user_email

    def list_messages(
        self,
        *,
        query: Optional[str] = None,
        label_ids: Optional[Iterable[str]] = None,
        max_results: int = 25,
        page_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._transport.record(
            "list_messages",
            query=query,
            label_ids=list(label_ids) if label_ids else None,
            max_results=max_results,
            page_token=page_token,
        )
        wanted_labels = set(label_ids or [])
        # Default to INBOX unless explicitly overridden.
        if not wanted_labels:
            wanted_labels = {"INBOX"}
        keep: list[dict] = []
        q_lower = (query or "").lower()
        for msg in self._messages.values():
            ids = set(msg.get("labelIds", []))
            if not (wanted_labels & ids):
                continue
            if q_lower and not _query_matches(q_lower, msg):
                continue
            keep.append({"id": msg["id"], "threadId": msg["threadId"]})
        # Stable ordering (newest first by internalDate).
        keep.sort(
            key=lambda m: int(self._messages[m["id"]].get("internalDate", "0")),
            reverse=True,
        )
        return {
            "messages": keep[:max_results],
            "nextPageToken": None,
            "resultSizeEstimate": len(keep),
        }

    def get_message(self, message_id: str) -> Dict[str, Any]:
        self._transport.record("get_message", message_id=message_id)
        if message_id not in self._messages:
            raise KeyError(f"FakeGmailBackend: no message {message_id!r}")
        return self._messages[message_id]

    def get_thread(self, thread_id: str) -> Dict[str, Any]:
        self._transport.record("get_thread", thread_id=thread_id)
        msgs = [m for m in self._messages.values() if m.get("threadId") == thread_id]
        return {"id": thread_id, "messages": msgs}

    def list_labels(self) -> List[Dict[str, Any]]:
        self._transport.record("list_labels")
        return list(self._labels)

    # -- Mutate -------------------------------------------------------------

    def _modify(
        self,
        message_id: str,
        *,
        add: Optional[Iterable[str]] = None,
        remove: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        if message_id not in self._messages:
            raise KeyError(f"FakeGmailBackend: no message {message_id!r}")
        msg = self._messages[message_id]
        ids = list(msg.get("labelIds", []))
        for lab in remove or ():
            if lab in ids:
                ids.remove(lab)
        for lab in add or ():
            if lab not in ids:
                ids.append(lab)
        msg["labelIds"] = ids
        return msg

    def archive_message(self, message_id: str) -> Dict[str, Any]:
        self._transport.record("archive_message", message_id=message_id)
        return self._modify(message_id, remove=["INBOX"])

    def mark_read(self, message_id: str) -> Dict[str, Any]:
        self._transport.record("mark_read", message_id=message_id)
        return self._modify(message_id, remove=["UNREAD"])

    def mark_unread(self, message_id: str) -> Dict[str, Any]:
        self._transport.record("mark_unread", message_id=message_id)
        return self._modify(message_id, add=["UNREAD"])

    def add_star(self, message_id: str) -> Dict[str, Any]:
        self._transport.record("add_star", message_id=message_id)
        return self._modify(message_id, add=["STARRED"])

    def remove_star(self, message_id: str) -> Dict[str, Any]:
        self._transport.record("remove_star", message_id=message_id)
        return self._modify(message_id, remove=["STARRED"])

    def add_label(self, message_id: str, label_id: str) -> Dict[str, Any]:
        self._transport.record("add_label", message_id=message_id, label_id=label_id)
        return self._modify(message_id, add=[label_id])

    def remove_label(self, message_id: str, label_id: str) -> Dict[str, Any]:
        self._transport.record("remove_label", message_id=message_id, label_id=label_id)
        return self._modify(message_id, remove=[label_id])

    def trash_message(self, message_id: str) -> Dict[str, Any]:
        self._transport.record("trash_message", message_id=message_id)
        # Live Gmail strips the message from INBOX and adds TRASH.
        return self._modify(message_id, add=["TRASH"], remove=["INBOX"])

    def untrash_message(self, message_id: str) -> Dict[str, Any]:
        self._transport.record("untrash_message", message_id=message_id)
        return self._modify(message_id, add=["INBOX"], remove=["TRASH"])

    def unarchive_message(
        self, message_id: str, prior_labels: List[str]
    ) -> Dict[str, Any]:
        self._transport.record(
            "unarchive_message", message_id=message_id, prior_labels=prior_labels
        )
        to_add = list({"INBOX", *(prior_labels or [])})
        return self._modify(message_id, add=to_add)

    def permanent_delete(self, message_id: str) -> None:
        self._transport.record("permanent_delete", message_id=message_id)
        self._messages.pop(message_id, None)

    # -- Drafts / send ------------------------------------------------------

    def create_draft(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        headers: Optional[Dict[str, str]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        self._transport.record(
            "create_draft",
            to=to,
            subject=subject,
            body=body,
            headers=headers,
            attachments=attachments,
        )
        draft_id = f"draft_{self._next_draft_seq}"
        self._next_draft_seq += 1
        self._drafts[draft_id] = {
            "to": to,
            "subject": subject,
            "body": body,
            "headers": dict(headers or {}),
            "attachments": list(attachments or []),
        }
        return {"id": draft_id}

    def send_draft(self, draft_id: str) -> Dict[str, Any]:
        self._transport.record("send_draft", draft_id=draft_id)
        if draft_id not in self._drafts:
            raise KeyError(f"FakeGmailBackend: no draft {draft_id!r}")
        sent = self._drafts.pop(draft_id)
        return {"id": f"sent_{draft_id}", "sent": sent}

    def send_message(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        headers: Optional[Dict[str, str]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        self._transport.record(
            "send_message",
            to=to,
            subject=subject,
            body=body,
            headers=headers,
            attachments=attachments,
        )
        return {"id": f"sent_{uuid.uuid4().hex[:8]}", "to": to, "subject": subject}

    def create_label(
        self, *, name: str, label_list_visibility: str = "labelShow"
    ) -> Dict[str, Any]:
        self._transport.record("create_label", name=name)
        new = {
            "id": f"Label_{uuid.uuid4().hex[:8]}",
            "name": name,
            "type": "user",
            "labelListVisibility": label_list_visibility,
            "messageListVisibility": "show",
        }
        self._labels.append(new)
        return new

    # -- Diagnostics --------------------------------------------------------

    def list_drafts(self, *, query: Optional[str] = None) -> List[Dict[str, Any]]:
        """Test-only — list current drafts. Not part of the Protocol."""
        out = []
        for did, d in self._drafts.items():
            if query and query.lower() not in (d.get("subject", "")).lower():
                continue
            out.append({"id": did, **d})
        return out


# ---------------------------------------------------------------------------
# Calendar fake (much simpler — used only by calendar tests)
# ---------------------------------------------------------------------------


class FakeCalendarBackend:
    def __init__(self) -> None:
        self.events: Dict[str, Dict[str, Any]] = {}
        self.calls: list[tuple[str, dict]] = []

    def list_calendars(self) -> List[Dict[str, Any]]:
        self.calls.append(("list_calendars", {}))
        return [{"id": "primary", "summary": "Primary"}]

    def list_events(
        self,
        *,
        calendar_id: str = "primary",
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        max_results: int = 25,
    ) -> Dict[str, Any]:
        self.calls.append(
            (
                "list_events",
                {
                    "calendar_id": calendar_id,
                    "time_min": time_min,
                    "time_max": time_max,
                    "max_results": max_results,
                },
            )
        )
        return {"items": list(self.events.values())[:max_results]}

    def get_event(
        self, *, calendar_id: str = "primary", event_id: str
    ) -> Dict[str, Any]:
        self.calls.append(
            ("get_event", {"calendar_id": calendar_id, "event_id": event_id})
        )
        return self.events.get(event_id, {"id": event_id})

    def update_event_rsvp(
        self,
        *,
        calendar_id: str = "primary",
        event_id: str,
        attendee_email: str,
        response_status: str,
    ) -> Dict[str, Any]:
        self.calls.append(
            (
                "update_event_rsvp",
                {
                    "calendar_id": calendar_id,
                    "event_id": event_id,
                    "attendee_email": attendee_email,
                    "response_status": response_status,
                },
            )
        )
        ev = self.events.setdefault(event_id, {"id": event_id, "attendees": []})
        for a in ev.get("attendees", []):
            if a.get("email") == attendee_email:
                a["responseStatus"] = response_status
                break
        else:
            ev.setdefault("attendees", []).append(
                {
                    "email": attendee_email,
                    "responseStatus": response_status,
                    "self": True,
                }
            )
        return ev

    def create_event(
        self,
        *,
        calendar_id: str = "primary",
        summary: str,
        start: Dict[str, str],
        end: Dict[str, str],
        attendees: Optional[Iterable[str]] = None,
        location: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.calls.append(
            (
                "create_event",
                {
                    "calendar_id": calendar_id,
                    "summary": summary,
                    "start": start,
                    "end": end,
                    "attendees": list(attendees) if attendees else [],
                    "location": location,
                    "description": description,
                },
            )
        )
        evt_id = f"evt_{uuid.uuid4().hex[:8]}"
        ev: Dict[str, Any] = {
            "id": evt_id,
            "summary": summary,
            "start": start,
            "end": end,
        }
        if attendees:
            ev["attendees"] = [{"email": a} for a in attendees]
        if location:
            ev["location"] = location
        if description:
            ev["description"] = description
        self.events[evt_id] = ev
        return ev


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_RELATIVE_UNIT_SECONDS = {
    "h": 3600,
    "d": 86_400,
    "w": 7 * 86_400,
    "m": 30 * 86_400,
    "y": 365 * 86_400,
}


def _relative_window_seconds(value: str) -> Optional[int]:
    """Parse a Gmail relative window like ``1d`` / ``2w`` / ``3m`` into seconds."""
    m = re.fullmatch(r"(\d+)\s*([hdwmy])", value.strip().lower())
    if not m:
        return None
    return int(m.group(1)) * _RELATIVE_UNIT_SECONDS[m.group(2)]


def _absolute_date_epoch(value: str) -> Optional[float]:
    """Parse a normalized ``YYYY/MM/DD`` Gmail date into an epoch (UTC midnight)."""
    m = re.fullmatch(r"(\d{4})/(\d{1,2})/(\d{1,2})", value.strip())
    if not m:
        return None
    try:
        dt = datetime(
            int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc
        )
    except ValueError:
        return None
    return dt.timestamp()


def _msg_epoch(msg: Dict[str, Any]) -> float:
    """Message receipt time in epoch seconds from Gmail's millis ``internalDate``."""
    try:
        return int(msg.get("internalDate", "0")) / 1000.0
    except (TypeError, ValueError):
        return 0.0


def _date_operator_matches(
    token: str, msg: Dict[str, Any], now: float
) -> Optional[bool]:
    """Evaluate a date operator token against ``msg``.

    Returns True/False if ``token`` is a recognized date operator, else None so
    the caller falls through to its other token handling. Models the operators
    the email agent emits — ``newer_than``/``older_than`` (relative windows) and
    the absolute ``after``/``before``/``newer``/``older`` (``YYYY/MM/DD``, the
    form ``normalize_gmail_date_operators`` produces).
    """
    when = _msg_epoch(msg)
    if token.startswith(("newer_than:", "older_than:")):
        op, _, val = token.partition(":")
        window = _relative_window_seconds(val)
        if window is None:
            return None
        if op == "newer_than":
            return when >= now - window
        return when <= now - window
    for op in ("after", "before", "newer", "older"):
        prefix = op + ":"
        if token.startswith(prefix):
            epoch = _absolute_date_epoch(token[len(prefix) :])
            if epoch is None:
                return None
            if op in ("after", "newer"):
                return when >= epoch
            return when < epoch
    return None


def _query_matches(query: str, msg: Dict[str, Any]) -> bool:
    """Tiny subset of Gmail's query DSL.

    ``is:unread``, ``from:``, ``subject:`` and the date operators
    (``newer_than``/``older_than``/``after``/``before``/``newer``/``older``) are
    honored — enough for the eval-harness scenarios and the #2406 same-day
    search coverage.
    """
    headers = {
        (h.get("name") or "").lower(): h.get("value", "")
        for h in (msg.get("payload") or {}).get("headers", [])
    }
    label_ids = set(msg.get("labelIds", []))
    now = datetime.now(timezone.utc).timestamp()
    for token in query.split():
        date_verdict = _date_operator_matches(token, msg, now)
        if date_verdict is not None:
            if not date_verdict:
                return False
            continue
        if token == "is:unread":
            if "UNREAD" not in label_ids:
                return False
        elif token.startswith("from:"):
            needle = token[len("from:") :]
            if needle not in headers.get("from", "").lower():
                return False
        elif token.startswith("subject:"):
            needle = token[len("subject:") :]
            if needle not in headers.get("subject", "").lower():
                return False
        else:
            # Free-text — match against subject + snippet.
            if (
                token not in headers.get("subject", "").lower()
                and token not in (msg.get("snippet") or "").lower()
            ):
                return False
    return True


_DEFAULT_SYSTEM_LABELS: list[Dict[str, Any]] = [
    {"id": "INBOX", "name": "INBOX", "type": "system"},
    {"id": "SENT", "name": "SENT", "type": "system"},
    {"id": "DRAFT", "name": "DRAFT", "type": "system"},
    {"id": "SPAM", "name": "SPAM", "type": "system"},
    {"id": "TRASH", "name": "TRASH", "type": "system"},
    {"id": "UNREAD", "name": "UNREAD", "type": "system"},
    {"id": "STARRED", "name": "STARRED", "type": "system"},
    {"id": "IMPORTANT", "name": "IMPORTANT", "type": "system"},
    {"id": "CATEGORY_PROMOTIONS", "name": "CATEGORY_PROMOTIONS", "type": "system"},
    {"id": "CATEGORY_UPDATES", "name": "CATEGORY_UPDATES", "type": "system"},
    {"id": "CATEGORY_SOCIAL", "name": "CATEGORY_SOCIAL", "type": "system"},
    {"id": "CATEGORY_FORUMS", "name": "CATEGORY_FORUMS", "type": "system"},
    {"id": "CATEGORY_PERSONAL", "name": "CATEGORY_PERSONAL", "type": "system"},
]
