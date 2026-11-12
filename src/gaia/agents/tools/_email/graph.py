# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Microsoft Graph mailbox clients for the flagship agent's email tools.

The read client lists, searches and reads, under the narrowest scope the
connection carries. The write client adds drafts and sending, and is built only
when a write is asked for. Neither organizes or deletes received mail.

This is a lean reimplementation of ``gaia_agent_email.outlook_backend``, not an
import of it: the flagship must not
depend on the standalone email package it is meant to replace. The wire
contract (Graph ``message`` resource) is the shared truth, not the Python.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import quote

import httpx

from gaia.agents.tools._email.errors import (
    MailboxAuthError,
    MailboxError,
    MailboxOutcomeUnknown,
)
from gaia.agents.tools._email.outgoing import (
    OutgoingMessage,
    Recipient,
    Recipients,
    SourceMessage,
)
from gaia.agents.tools._email.scopes import MICROSOFT_CONNECTOR_ID

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

# Every field the read tools and the triage signals need, and nothing heavier.
# ``body`` is excluded — it is the one large field, and only ``read_email``
# fetches it. ``bodyPreview`` is Graph's snippet equivalent and is enough for
# scanning.
_LIST_SELECT = (
    "id,conversationId,subject,from,toRecipients,ccRecipients,"
    "receivedDateTime,isRead,isDraft,flag,categories,bodyPreview,hasAttachments"
)
# Attachment metadata rides on the same request. The inner $select is what
# keeps `contentBytes` (the file itself) off the wire.
_ATTACHMENT_EXPAND = "attachments($select=name,contentType,size,isInline)"
# Graph only needs the header collection when reading a full conversation;
# keeping it off list/search calls avoids asking those endpoints for data the
# caller does not use.
_FULL_SELECT = _LIST_SELECT + ",internetMessageHeaders,body"

_DRAFT_SELECT = (
    "id,conversationId,subject,toRecipients,ccRecipients,bccRecipients,body,"
    "isDraft,internetMessageId,bodyPreview"
)
_SOURCE_SELECT = (
    "id,conversationId,subject,from,replyTo,toRecipients,ccRecipients,body,"
    "internetMessageId,receivedDateTime"
)

# Graph caps $top at 999; asking for more is a 400, not a truncation.
_MAX_TOP = 999

# One page of a conversation. Longer threads are reported truncated, not cut.
_THREAD_TOP = 100

# Well-known folder names Graph accepts in place of a folder id.
_FOLDER_NAMES = {"inbox": "inbox", "sent": "sentitems"}

# The shape Graph issues for a message id: standard base64, so `+`, `/` and the
# `=` padding are all legitimate, as are base64url's `-` and `_`.
_MESSAGE_ID_RE = re.compile(r"[A-Za-z0-9+/=_-]{1,512}")

# The shape Graph documents for `error.code` — a short token like
# `ErrorAccessDenied`. Anything else means the body isn't the documented
# error shape (an HTML error page, a proxy notice) and gets dropped.
_ERROR_CODE_RE = re.compile(r"[A-Za-z0-9_.]{1,64}")


def _error_detail(response: httpx.Response) -> str:
    """Graph's structured `error.code` field, or "" if the body isn't that shape.

    Deliberately never `error.message` or the raw body: both are
    upstream-controlled prose and echoing them puts arbitrary text (or an
    HTML error page) in front of the user.
    """
    try:
        parsed = response.json()
    except ValueError:
        return ""
    # A parsed body or its `error` field can be any JSON type, not just an object.
    err = parsed.get("error") if isinstance(parsed, dict) else None
    if not isinstance(err, dict):
        return ""
    code = str(err.get("code") or "")
    return code if _ERROR_CODE_RE.fullmatch(code) else ""


def _address(entity: Optional[Dict[str, Any]]) -> str:
    """Render a Graph recipient as ``Name <addr>``, or the bare address."""
    if not entity:
        return ""
    email = entity.get("emailAddress") or {}
    name = (email.get("name") or "").strip()
    addr = (email.get("address") or "").strip()
    if name and addr and name.lower() != addr.lower():
        return f"{name} <{addr}>"
    return addr or name


def _address_list(entities: Optional[Iterable[Dict[str, Any]]]) -> str:
    return ", ".join(p for p in (_address(e) for e in (entities or [])) if p)


def _attachments(msg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Name, type and size of each real attachment; inline images excluded."""
    return [
        {
            "name": att.get("name") or "",
            "mime_type": (att.get("contentType") or "").lower(),
            "size": int(att.get("size") or 0),
        }
        for att in msg.get("attachments") or []
        if not att.get("isInline")
    ]


def message_summary(
    msg: Dict[str, Any], *, include_body: bool = False
) -> Dict[str, Any]:
    """Flatten a Graph ``message`` into the shape the agent's tools return.

    Provider-neutral on purpose: a Gmail backend added later emits this same
    shape, so the tools and the skill never learn which mailbox they are on.
    """
    flag = (msg.get("flag") or {}).get("flagStatus")
    headers = {
        str(header.get("name") or "").lower(): str(header.get("value") or "")
        for header in msg.get("internetMessageHeaders") or []
        if header.get("name")
    }
    attachments = _attachments(msg)
    summary: Dict[str, Any] = {
        "id": msg.get("id"),
        "thread_id": msg.get("conversationId") or msg.get("id"),
        "subject": msg.get("subject") or "(no subject)",
        "from": _address(msg.get("from")),
        "to": _address_list(msg.get("toRecipients")),
        "cc": _address_list(msg.get("ccRecipients")),
        "received": msg.get("receivedDateTime") or "",
        "unread": not msg.get("isRead", True),
        "flagged": flag in ("flagged", "complete"),
        "categories": list(msg.get("categories") or []),
        "preview": (msg.get("bodyPreview") or "").strip(),
        # Graph's hasAttachments already ignores inline images.
        "has_attachments": bool(attachments) or bool(msg.get("hasAttachments")),
        "attachments": attachments,
        # Provider-neutral metadata consumed by the waiting-on-you detector.
        "auto_submitted": headers.get("auto-submitted", ""),
        "x_autoreply": headers.get("x-autoreply", ""),
    }
    if include_body:
        body = msg.get("body") or {}
        summary["body"] = body.get("content") or ""
        summary["body_content_type"] = (body.get("contentType") or "text").lower()
    return summary


def _recipient(entity: Dict[str, Any]) -> Recipient:
    email = entity.get("emailAddress") or {}
    return Recipient(
        (email.get("name") or "").strip(), (email.get("address") or "").strip()
    )


def _recipients(entities: Optional[Iterable[Dict[str, Any]]]) -> Recipients:
    found = (_recipient(e) for e in entities or [])
    return tuple(r for r in found if r.address)


def _graph_recipients(recipients: Recipients) -> List[Dict[str, Any]]:
    return [
        (
            {"emailAddress": {"name": r.name, "address": r.address}}
            if r.name
            else {"emailAddress": {"address": r.address}}
        )
        for r in recipients
    ]


def graph_message(message: OutgoingMessage) -> Dict[str, Any]:
    """*message* as a Graph ``message`` resource.

    Threading headers are absent on purpose: Graph answers 400 to a standard
    header in ``internetMessageHeaders``. A reply threads through ``createReply``.
    """
    return {
        "subject": message.subject,
        "body": {
            "contentType": "HTML" if message.body_type == "html" else "Text",
            "content": message.body,
        },
        "toRecipients": _graph_recipients(message.to),
        "ccRecipients": _graph_recipients(message.cc),
        "bccRecipients": _graph_recipients(message.bcc),
    }


def _graph_id(value: str, kind: str, source: str) -> str:
    # Ids arrive from the model. Quoted whole: a base64 `/` is data, not a path.
    if not value or not _MESSAGE_ID_RE.fullmatch(value):
        raise ValueError(
            f"{kind} {value!r} is not a Microsoft Graph id. Use one from {source}."
        )
    return quote(value, safe="")


class OutlookReadBackend:
    """Read-only Microsoft Graph client for one connected Outlook mailbox."""

    def __init__(
        self,
        access_token_fn: Callable[[], str],
        *,
        http_client: Optional[httpx.Client] = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._access_token_fn = access_token_fn
        # Tests inject an httpx.MockTransport-backed client so no test ever
        # needs the network or a real token.
        self._client = http_client or httpx.Client(timeout=timeout_seconds)

    # -- HTTP ---------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        # Re-minted per request. A cached token would let a mid-scan revoke
        # look like success on the pages already fetched.
        return {"Authorization": f"Bearer {self._access_token_fn()}"}

    def _raise(self, response: httpx.Response, where: str) -> None:
        # Built from status + the documented `error.code` field only. Never
        # from a wrapper exception (can carry the Authorization header into a
        # log) or the raw body (upstream-controlled — an HTML error page or
        # proxy notice would land verbatim in front of the user).
        code = _error_detail(response)
        if response.status_code == 401:
            raise MailboxAuthError(
                "Microsoft Graph rejected the access token (401). The Outlook "
                "connection has expired or been revoked and needs reconnecting. "
                f"(request: {where})",
                connect=(MICROSOFT_CONNECTOR_ID,),
                reconnect=(MICROSOFT_CONNECTOR_ID,),
            )
        if response.status_code == 403:
            raise MailboxAuthError(
                "Microsoft Graph refused the request (403 — insufficient "
                "permissions). The connected account has not granted "
                "Mail.ReadWrite to this agent, so it needs reconnecting with "
                "mail access approved. "
                f"(request: {where}; code: {code or 'forbidden'})",
                connect=(MICROSOFT_CONNECTOR_ID,),
                reconnect=(MICROSOFT_CONNECTOR_ID,),
            )
        if response.status_code == 404:
            raise MailboxError(
                "Microsoft Graph has no such message or conversation (404). "
                "The id may be mistyped, or the mail was deleted. Use an id "
                f"returned by list_inbox or search_email. (request: {where})"
            )
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "unknown")
            raise MailboxError(
                "Microsoft Graph is rate-limiting this mailbox (429). Retry "
                f"after {retry_after}s. Reduce `limit` if this repeats. "
                f"(request: {where})"
            )
        raise MailboxError(
            f"Microsoft Graph request failed: {where} returned "
            f"{response.status_code}" + (f" ({code})" if code else "") + ". "
            "Retry; if it persists, check Microsoft 365 service status and "
            "reconnect Microsoft under Settings > Connectors."
        )

    def _get(self, path: str, *, params: Optional[dict] = None) -> Any:
        try:
            resp = self._client.get(
                f"{GRAPH_API_BASE}{path}", headers=self._headers(), params=params
            )
        except httpx.HTTPError as exc:
            raise MailboxError(
                f"Could not reach Microsoft Graph for {path}: {exc}. Check "
                "network connectivity, then retry."
            ) from exc
        if resp.status_code != 200:
            self._raise(resp, f"GET {path}")
        return resp.json()

    # -- Reads --------------------------------------------------------------

    def get_user_email(self) -> str:
        """The connected mailbox's address."""
        data = self._get("/me", params={"$select": "mail,userPrincipalName"})
        # Personal accounts frequently have a null `mail` and carry the address
        # on userPrincipalName instead.
        address = data.get("mail") or data.get("userPrincipalName") or ""
        if not address:
            raise MailboxError(
                "Microsoft Graph returned no address for the connected account "
                "(both `mail` and `userPrincipalName` were empty). The "
                "connection is in an unusable state and needs reconnecting.",
                connect=(MICROSOFT_CONNECTOR_ID,),
                reconnect=(MICROSOFT_CONNECTOR_ID,),
            )
        return address

    def _clamp(self, limit: int) -> int:
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}")
        return min(limit, _MAX_TOP)

    def list_inbox(
        self, *, limit: int = 25, unread_only: bool = False
    ) -> List[Dict[str, Any]]:
        """Newest-first inbox messages, with metadata but no bodies."""
        params: Dict[str, Any] = {
            "$top": self._clamp(limit),
            "$select": _LIST_SELECT,
            "$expand": _ATTACHMENT_EXPAND,
            "$orderby": "receivedDateTime desc",
        }
        if unread_only:
            # Graph requires every ordered property to lead $filter; without this
            # broad lower bound it rejects an unread-only inbox with
            # InefficientFilter.
            params["$filter"] = (
                "receivedDateTime ge 1900-01-01T00:00:00Z and isRead eq false"
            )
        data = self._get("/me/mailFolders/inbox/messages", params=params)
        return [message_summary(m) for m in data.get("value", [])]

    def search(self, query: str, *, limit: int = 25) -> List[Dict[str, Any]]:
        """Full-mailbox keyword search.

        Uses Graph's ``$search`` (KQL). Graph forbids combining ``$search``
        with ``$orderby``, so results come back in relevance order, not date
        order — the tool docstring says so, because a caller that assumes
        newest-first would silently misreport.
        """
        if not query or not query.strip():
            raise ValueError("query must be a non-empty search string")
        params = {
            "$top": self._clamp(limit),
            "$select": _LIST_SELECT,
            "$expand": _ATTACHMENT_EXPAND,
            "$search": f'"{query}"',
        }
        data = self._get("/me/messages", params=params)
        return [message_summary(m) for m in data.get("value", [])]

    def get_message(self, message_id: str) -> Dict[str, Any]:
        """One message, body included."""
        if not message_id or not message_id.strip():
            raise ValueError("message_id must be a non-empty message id")
        # Ids arrive from the model; accept only the shape Graph issues.
        if not _MESSAGE_ID_RE.fullmatch(message_id):
            raise ValueError(
                f"message_id {message_id!r} is not a Microsoft Graph message "
                "id. Use an id returned by list_inbox or search."
            )
        # Quoted whole: a base64 id's `/` is data, never a path separator.
        path = f"/me/messages/{quote(message_id, safe='')}"
        data = self._get(
            path, params={"$select": _FULL_SELECT, "$expand": _ATTACHMENT_EXPAND}
        )
        return message_summary(data, include_body=True)

    def list_thread_ids(self, folder: str, *, limit: int = 50) -> Dict[str, Any]:
        """Distinct conversation ids in ``folder`` (``inbox`` or ``sent``), newest first.

        ``truncated`` means older mail exists past ``limit``: Graph returned a
        next page, or the listing filled the whole budget.
        """
        name = _FOLDER_NAMES.get(folder)
        if name is None:
            raise ValueError(
                f"folder must be one of {sorted(_FOLDER_NAMES)}, got {folder!r}"
            )
        top = self._clamp(limit)
        data = self._get(
            f"/me/mailFolders/{name}/messages",
            params={
                "$top": top,
                "$select": "id,conversationId",
                "$orderby": "receivedDateTime desc",
            },
        )
        rows = data.get("value", [])
        thread_ids: List[str] = []
        for row in rows:
            tid = row.get("conversationId") or row.get("id")
            if tid and tid not in thread_ids:
                thread_ids.append(tid)
        return {
            "thread_ids": thread_ids,
            "messages_listed": len(rows),
            "truncated": bool(data.get("@odata.nextLink")) or len(rows) >= top,
        }

    def get_thread(self, thread_id: str) -> Dict[str, Any]:
        """Every message in one conversation, oldest first, bodies included.

        Graph has no thread resource, so this filters messages on
        ``conversationId``. Combining that filter with ``$orderby`` trips
        Graph's InefficientFilter, so the sort happens here instead.
        """
        # The id lands inside an OData string literal; the alphabet check is
        # what keeps a quote out of it.
        if not thread_id or not _MESSAGE_ID_RE.fullmatch(thread_id):
            raise ValueError(
                f"thread_id {thread_id!r} is not a Microsoft Graph conversation "
                "id. Use the thread_id from a list_inbox or search_email result."
            )
        data = self._get(
            "/me/messages",
            params={
                "$filter": f"conversationId eq '{thread_id}'",
                "$select": _FULL_SELECT,
                "$expand": _ATTACHMENT_EXPAND,
                "$top": _THREAD_TOP,
            },
        )
        raw = sorted(
            data.get("value", []), key=lambda m: m.get("receivedDateTime") or ""
        )
        messages = []
        for msg in raw:
            summary = message_summary(msg, include_body=True)
            summary["draft"] = bool(msg.get("isDraft"))
            summary["_label_ids"] = []
            messages.append(summary)
        return {
            "thread_id": thread_id,
            "messages": messages,
            "truncated": bool(data.get("@odata.nextLink")),
        }

    def list_folders(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        """Mail folders with their unread and total counts."""
        data = self._get(
            "/me/mailFolders",
            params={
                "$top": self._clamp(limit),
                "$select": "id,displayName,unreadItemCount,totalItemCount",
            },
        )
        return [
            {
                "id": f.get("id"),
                "name": f.get("displayName") or "",
                "unread": f.get("unreadItemCount", 0),
                "total": f.get("totalItemCount", 0),
            }
            for f in data.get("value", [])
        ]

    def get_draft(self, draft_id: str) -> OutgoingMessage:
        """One draft, exactly as Graph would send it now.

        A Graph draft id is a message id, so this refuses a message that is not
        a draft: discarding or rewriting received mail must never start here.
        """
        path = _graph_id(draft_id, "draft_id", "create_draft or list_drafts")
        data = self._get(
            f"/me/messages/{path}",
            params={
                "$select": _DRAFT_SELECT,
                "$expand": "attachments($select=name)",
            },
        )
        if not data.get("isDraft"):
            raise ValueError(
                f"Message {draft_id!r} is not a draft. Use an id from create_draft "
                "or list_drafts."
            )
        body = data.get("body") or {}
        return OutgoingMessage(
            to=_recipients(data.get("toRecipients")),
            cc=_recipients(data.get("ccRecipients")),
            bcc=_recipients(data.get("bccRecipients")),
            subject=data.get("subject") or "",
            body=body.get("content") or "",
            body_type=(body.get("contentType") or "text").lower(),
            thread_id=data.get("conversationId"),
            attachments=tuple(
                a.get("name") or "" for a in data.get("attachments") or []
            ),
        )

    def list_drafts(self, *, limit: int = 25) -> List[Dict[str, Any]]:
        """Saved drafts with their recipients and subject, not their bodies."""
        data = self._get(
            "/me/mailFolders/drafts/messages",
            params={
                "$top": self._clamp(limit),
                "$select": "id,conversationId,subject,toRecipients,bodyPreview",
            },
        )
        return [
            {
                "draft_id": m.get("id"),
                "thread_id": m.get("conversationId"),
                "to": _address_list(m.get("toRecipients")),
                "subject": m.get("subject") or "(no subject)",
                "preview": (m.get("bodyPreview") or "").strip(),
            }
            for m in data.get("value", [])
        ]


class OutlookWriteBackend(OutlookReadBackend):
    """Drafting and sending. Each call is made once; a failure is reported, not retried."""

    # A PATCH touches only the fields it names, so a draft's files stay.
    edit_keeps_attachments = True

    def _write(
        self, method: str, path: str, *, payload: Optional[dict] = None
    ) -> Dict[str, Any]:
        where = f"{method} {path}"
        headers = self._headers()
        try:
            resp = self._client.request(
                method,
                f"{GRAPH_API_BASE}{path}",
                headers=headers,
                json=payload,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise MailboxError(
                f"Could not reach Microsoft Graph for {where} "
                f"({type(exc).__name__}), so the request never went out. Check "
                "network connectivity, then retry."
            ) from exc
        except httpx.HTTPError as exc:
            raise MailboxOutcomeUnknown(
                f"The connection to Microsoft Graph failed during {where} "
                f"({type(exc).__name__}), so GAIA can't tell whether it took "
                "effect. Check Sent Items and Drafts before trying again."
            ) from exc
        if resp.status_code == 403:
            raise MailboxAuthError(
                "Microsoft Graph refused this because the connection can't write "
                "mail (403). Reconnect Microsoft in Settings -> Connectors and "
                "approve reading and sending mail. (request: "
                f"{where}; code: {_error_detail(resp) or 'forbidden'})"
            )
        if resp.status_code >= 500:
            raise MailboxOutcomeUnknown(
                f"Microsoft Graph answered {resp.status_code} during {where}, so "
                "GAIA can't tell whether it took effect. Check Sent Items and Drafts "
                "before trying again."
            )
        if resp.status_code not in (200, 201, 202, 204):
            self._raise(resp, where)
        try:
            return resp.json() if resp.content else {}
        except ValueError as exc:
            raise MailboxOutcomeUnknown(
                f"Microsoft Graph accepted {where} but its answer was unreadable, so "
                "GAIA can't confirm the result. Check Sent Items and Drafts before "
                "trying again."
            ) from exc

    def get_source_message(self, message_id: str) -> SourceMessage:
        """What a reply or forward is built from."""
        path = _graph_id(message_id, "message_id", "list_inbox or search_email")
        data = self._get(
            f"/me/messages/{path}",
            params={
                "$select": _SOURCE_SELECT,
                "$expand": "attachments($select=name)",
            },
        )
        sender = _recipient(data["from"]) if data.get("from") else None
        body = data.get("body") or {}
        return SourceMessage(
            id=message_id,
            sender=sender if sender and sender.address else None,
            reply_to=_recipients(data.get("replyTo")),
            to=_recipients(data.get("toRecipients")),
            cc=_recipients(data.get("ccRecipients")),
            subject=data.get("subject") or "",
            body=body.get("content") or "",
            body_type=(body.get("contentType") or "text").lower(),
            thread_id=data.get("conversationId"),
            internet_message_id=data.get("internetMessageId"),
            references=None,
            received=data.get("receivedDateTime") or "",
            attachments=tuple(
                a.get("name") or "" for a in data.get("attachments") or []
            ),
        )

    def _new_reply_draft(self, message: OutgoingMessage) -> str:
        """A threaded draft whose every field is *message*'s, not Graph's defaults."""
        source = quote(message.reply_to_id, safe="")
        draft_id = self._write("POST", f"/me/messages/{source}/createReply")["id"]
        try:
            self.update_draft(draft_id, message)
        except MailboxError as exc:
            raise MailboxError(
                f"Graph started a reply draft ({draft_id}) but could not fill it "
                f"in, so nothing was sent. Discard that draft in Outlook. {exc}"
            ) from exc
        return draft_id

    def create_draft(self, message: OutgoingMessage) -> str:
        if message.reply_to_id:
            return self._new_reply_draft(message)
        return self._write("POST", "/me/messages", payload=graph_message(message))["id"]

    def update_draft(self, draft_id: str, message: OutgoingMessage) -> None:
        path = _graph_id(draft_id, "draft_id", "create_draft or list_drafts")
        self._write("PATCH", f"/me/messages/{path}", payload=graph_message(message))

    def discard_draft(self, draft_id: str) -> None:
        path = _graph_id(draft_id, "draft_id", "create_draft or list_drafts")
        self._write("DELETE", f"/me/messages/{path}")

    def send_draft(self, draft_id: str) -> Dict[str, Any]:
        path = _graph_id(draft_id, "draft_id", "create_draft or list_drafts")
        self._write("POST", f"/me/messages/{path}/send")
        # Graph answers 202 with no body, so no sent id exists to report.
        return {"message_id": None, "thread_id": None}

    def send(self, message: OutgoingMessage) -> Dict[str, Any]:
        if message.reply_to_id:
            return self.send_draft(self._new_reply_draft(message))
        self._write(
            "POST",
            "/me/sendMail",
            payload={"message": graph_message(message), "saveToSentItems": True},
        )
        return {"message_id": None, "thread_id": None}


__all__ = [
    "GRAPH_API_BASE",
    "MailboxAuthError",
    "MailboxError",
    "OutlookReadBackend",
    "OutlookWriteBackend",
    "graph_message",
    "message_summary",
]
