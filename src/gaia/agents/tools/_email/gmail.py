# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Gmail mailbox clients for the flagship agent's email tools.

Same reads and writes as the Graph backend next door, emitting the same
provider-neutral shapes, so neither the skill nor the model learns which mailbox
is connected. Reads run under the narrowest scope the connection carries, so
writes live in their own subclass, built only when a write is asked for.

This is a reimplementation of ``gaia_agent_email.gmail_backend``, not a
relocation of it: the flagship must not depend on the standalone email package
it is meant to replace, and that backend returns raw Gmail resources — its
flattening lives in the sidecar's tool layer, which does not come along. The
wire contract (Gmail API v1) is the shared truth, not the Python.
"""

from __future__ import annotations

import base64
import binascii
import html
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.headerregistry import Address
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import getaddresses
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

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
from gaia.agents.tools._email.scopes import GOOGLE_CONNECTOR_ID

logger = logging.getLogger(__name__)

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"

# Gmail's list returns id stubs only, so every message costs a `messages.get`.
# 100 matches the tool layer's own ceiling; Graph's 999 would be 999 fetches.
_GMAIL_MAX_LIMIT = 100

# Fanned out over a bounded pool: 25 sequential round-trips would spend most of
# a tool's timeout waiting. Small enough to stay under Gmail's per-user
# concurrency limit.
_FETCH_CONCURRENCY = 5

# `format=metadata` drops `payload.parts`, which is where attachments live, so
# listings ask for `full` and use a partial-response mask to leave every
# body's `data` off the wire. Gmail's `fields` syntax has no recursion, so the
# part tree is spelled out to a depth no real message reaches.
_PART_TREE_DEPTH = 8


def _part_fields(depth: int) -> str:
    fields = "mimeType,filename,headers,body/size"
    if depth:
        fields += f",parts({_part_fields(depth - 1)})"
    return fields


_SUMMARY_FIELDS = (
    "id,threadId,labelIds,snippet,internalDate,"
    f"payload({_part_fields(_PART_TREE_DEPTH)})"
)

# Gmail label IDs that are mailbox state, not user tags. `categories` must
# carry only what a user would recognise as a label they created.
_RESERVED_LABEL_IDS = frozenset(
    {
        "INBOX",
        "SENT",
        "DRAFT",
        "SPAM",
        "TRASH",
        "UNREAD",
        "STARRED",
        "IMPORTANT",
        "CHAT",
    }
)
_RESERVED_LABEL_PREFIX = "CATEGORY_"

_GMAIL_ENABLE_URL = "https://console.cloud.google.com/apis/library/gmail.googleapis.com"

# The shape Gmail issues for a message id. Thread ids share it.
_MESSAGE_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")

# The folder names the scans use, mapped to Gmail's system label ids.
_FOLDER_LABELS = {"inbox": "INBOX", "sent": "SENT"}

# The shape Gmail documents for `errors[].reason` (`accessNotConfigured`) and
# for `error.status` (`PERMISSION_DENIED`). Anything else means the body isn't
# the documented error shape and gets dropped.
_ERROR_REASON_RE = re.compile(r"[A-Za-z0-9_.]{1,64}")


def _error_detail(response: httpx.Response) -> tuple:
    """Gmail's structured error fields — a reason token and its remedy link.

    Deliberately never the raw response body: that is upstream-controlled text
    and it ends up in front of a user.
    """
    try:
        parsed = response.json()
    except ValueError:
        return "", ""
    # The parsed body, its `error` field, and that field's `errors` list can
    # each come back as any JSON type, not just the documented one.
    err = parsed.get("error") if isinstance(parsed, dict) else None
    if not isinstance(err, dict):
        return "", ""
    errors = err.get("errors")
    first = errors[0] if isinstance(errors, list) and errors else None
    if not isinstance(first, dict):
        first = {}
    reason = str(first.get("reason") or err.get("status") or "")
    if not _ERROR_REASON_RE.fullmatch(reason):
        reason = ""
    help_url = str(first.get("extendedHelp") or "")
    if not help_url.startswith("https://"):
        help_url = ""
    return reason, help_url


def _decode(raw: Optional[str]) -> str:
    """Decode RFC 2047 encoded words. Idempotent on already-plain text."""
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except (UnicodeDecodeError, LookupError, ValueError):
        return raw


def _header_map(payload: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for header in payload.get("headers") or []:
        name = (header.get("name") or "").lower()
        if name and name not in out:
            out[name] = header.get("value") or ""
    return out


def _address_list(raw: Optional[str]) -> str:
    """Render one RFC 5322 address header as Graph's ``Name <addr>`` list.

    Split on commas and ``"Doe, Jane" <j@x>`` becomes two broken addresses.
    """
    parts: List[str] = []
    for name, addr in getaddresses([_decode(raw)]):
        name, addr = name.strip(), addr.strip()
        if name and addr and name.lower() != addr.lower():
            parts.append(f"{name} <{addr}>")
        elif addr or name:
            parts.append(addr or name)
    return ", ".join(parts)


def _received(internal_date: Any) -> str:
    """``internalDate`` is epoch millis as a string; Graph emits UTC ISO."""
    try:
        millis = int(internal_date)
    except (TypeError, ValueError):
        return ""
    stamp = datetime.fromtimestamp(millis / 1000, tz=timezone.utc)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def _find_part(part: Dict[str, Any], mime_type: str) -> Optional[Dict[str, Any]]:
    if (part.get("mimeType") or "").lower() == mime_type and not part.get("filename"):
        return part
    for child in part.get("parts") or []:
        found = _find_part(child, mime_type)
        if found is not None:
            return found
    return None


def _decode_part(part: Dict[str, Any]) -> str:
    data = (part.get("body") or {}).get("data")
    if not data:
        return ""
    try:
        raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except (binascii.Error, ValueError) as exc:
        raise MailboxError(
            "Gmail returned a message body that is not valid base64, so it "
            "cannot be read. Open the message in Gmail directly, and report "
            "this at https://github.com/amd/gaia/issues if it repeats."
        ) from exc
    return raw.decode("utf-8", errors="replace")


def _select_body(payload: Dict[str, Any]) -> tuple:
    """The readable body and its type. Binary parts are never decoded."""
    for mime_type, kind in (("text/plain", "text"), ("text/html", "html")):
        part = _find_part(payload, mime_type)
        if part is not None:
            return _decode_part(part), kind
    return "", "text"


def _is_inline(part: Dict[str, Any]) -> bool:
    """An inline image (signature logo, ``cid:`` reference), not a file.

    Apple Mail marks PDFs and other files ``inline`` too, so only images count.
    """
    if not (part.get("mimeType") or "").lower().startswith("image/"):
        return False
    headers = _header_map(part)
    disposition = headers.get("content-disposition", "").strip().lower()
    if disposition.startswith("attachment"):
        return False
    return disposition.startswith("inline") or bool(headers.get("content-id"))


def _attachments(part: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Name, type and size of each attached file. Content is never read."""
    found: List[Dict[str, Any]] = []
    filename = part.get("filename") or ""
    if filename and not _is_inline(part):
        found.append(
            {
                "name": _decode(filename),
                "mime_type": (part.get("mimeType") or "").lower(),
                "size": int((part.get("body") or {}).get("size") or 0),
            }
        )
    for child in part.get("parts") or []:
        found.extend(_attachments(child))
    return found


def _categories(
    label_ids: Optional[Iterable[str]], label_names: Optional[Dict[str, str]]
) -> List[str]:
    """User-applied labels only — Gmail's own state and tabs are not tags."""
    names = label_names or {}
    return [
        names.get(lid, lid)
        for lid in label_ids or []
        if lid not in _RESERVED_LABEL_IDS and not lid.startswith(_RESERVED_LABEL_PREFIX)
    ]


def message_summary(
    msg: Dict[str, Any],
    *,
    label_names: Optional[Dict[str, str]] = None,
    include_body: bool = False,
) -> Dict[str, Any]:
    """Flatten a Gmail ``message`` into the shape the agent's tools return.

    Byte-identical in shape to ``graph.message_summary`` — that parity is what
    lets the tools and the skill stay unaware of which mailbox answered.
    """
    payload = msg.get("payload") or {}
    headers = _header_map(payload)
    labels = set(msg.get("labelIds") or [])
    attachments = _attachments(payload)
    summary: Dict[str, Any] = {
        "id": msg.get("id"),
        "thread_id": msg.get("threadId") or msg.get("id"),
        "subject": _decode(headers.get("subject")) or "(no subject)",
        "from": _address_list(headers.get("from")),
        "to": _address_list(headers.get("to")),
        "cc": _address_list(headers.get("cc")),
        "received": _received(msg.get("internalDate")),
        "unread": "UNREAD" in labels,
        "flagged": "STARRED" in labels,
        "categories": _categories(msg.get("labelIds"), label_names),
        # Gmail entity-encodes the snippet where Graph's bodyPreview is plain.
        "preview": html.unescape(msg.get("snippet") or "").strip(),
        "has_attachments": bool(attachments),
        "attachments": attachments,
        # Kept for the waiting-on-you detector; not shown as user-facing mail
        # content.
        "auto_submitted": _decode(headers.get("auto-submitted")),
        "x_autoreply": _decode(headers.get("x-autoreply")),
    }
    if include_body:
        summary["body"], summary["body_content_type"] = _select_body(payload)
    return summary


def _recipients(raw: Optional[str]) -> Recipients:
    """One address header as recipients; names are decoded after the split."""
    return tuple(
        Recipient(_decode(name).strip(), addr.strip())
        for name, addr in getaddresses([raw or ""])
        if addr.strip()
    )


def _attachment_names(part: Dict[str, Any]) -> List[str]:
    names = [part["filename"]] if part.get("filename") else []
    for child in part.get("parts") or []:
        names.extend(_attachment_names(child))
    return names


def _normalized_body(text: str) -> str:
    """A body as written: MIME's CRLFs and closing newline are not content."""
    return text.replace("\r\n", "\n").removesuffix("\n")


def _validated_id(value: str, kind: str, source: str) -> str:
    # Ids arrive from the model; accept only the shape Gmail issues.
    if not value or not _MESSAGE_ID_RE.fullmatch(value):
        raise ValueError(f"{kind} {value!r} is not a Gmail id. Use one from {source}.")
    return value


def _draft_message(draft: Dict[str, Any]) -> OutgoingMessage:
    message = draft.get("message") or {}
    payload = message.get("payload") or {}
    headers = _header_map(payload)
    body, body_type = _select_body(payload)
    html_part = _find_part(payload, "text/html") if body_type == "text" else None
    return OutgoingMessage(
        to=_recipients(headers.get("to")),
        cc=_recipients(headers.get("cc")),
        bcc=_recipients(headers.get("bcc")),
        subject=_decode(headers.get("subject")),
        body=_normalized_body(body),
        body_type=body_type,
        thread_id=message.get("threadId"),
        in_reply_to=headers.get("in-reply-to") or None,
        references=headers.get("references") or None,
        attachments=tuple(_attachment_names(payload)),
        html_alternative=(
            _normalized_body(_decode_part(html_part)) if html_part else None
        ),
    )


def raw_mime(message: OutgoingMessage) -> str:
    """*message* as the base64url RFC 5322 text Gmail's ``raw`` field takes.

    ``EmailMessage`` encodes non-ASCII headers and refuses a line break in any
    of them, which is what stops a subject lifted from inbound mail from
    injecting a ``Bcc:``.
    """
    mime = EmailMessage(policy=SMTP)
    for header, recipients in (
        ("To", message.to),
        ("Cc", message.cc),
        ("Bcc", message.bcc),
    ):
        if recipients:
            mime[header] = [Address(r.name, addr_spec=r.address) for r in recipients]
    mime["Subject"] = message.subject
    if message.in_reply_to:
        mime["In-Reply-To"] = message.in_reply_to
    if message.references:
        mime["References"] = message.references
    mime.set_content(
        message.body, subtype="html" if message.body_type == "html" else "plain"
    )
    if message.html_alternative:
        mime.add_alternative(message.html_alternative, subtype="html")
    return base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii").rstrip("=")


def _message_resource(message: OutgoingMessage) -> Dict[str, Any]:
    resource: Dict[str, Any] = {"raw": raw_mime(message)}
    if message.thread_id:
        resource["threadId"] = message.thread_id
    return resource


class GmailReadBackend:
    """Read-only Gmail API v1 client for one connected Google mailbox."""

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
        self._label_cache: Optional[List[Dict[str, Any]]] = None

    # -- HTTP ---------------------------------------------------------------

    def _raise(self, response: httpx.Response, where: str) -> None:
        reason, help_url = _error_detail(response)
        code = response.status_code
        if code == 401:
            raise MailboxAuthError(
                "Gmail rejected the access token (401). The Google connection "
                "has expired or been revoked and needs reconnecting. "
                f"(request: {where})",
                connect=(GOOGLE_CONNECTOR_ID,),
                reconnect=(GOOGLE_CONNECTOR_ID,),
            )
        if code == 403 and reason == "accessNotConfigured":
            raise MailboxAuthError(
                "The Gmail API is not enabled for the Google Cloud project "
                "behind this connection (403 accessNotConfigured), so no "
                "mailbox can be read. Enable it, wait a minute, then retry: "
                f"{help_url or _GMAIL_ENABLE_URL} (request: {where})"
            )
        if code == 403:
            raise MailboxAuthError(
                "Gmail refused the request (403 — insufficient permissions). "
                "The connected Google account has not given GAIA permission "
                "to read mail, so it needs reconnecting with mail access "
                f"ticked. (request: {where}; reason: "
                f"{reason or 'forbidden'})",
                connect=(GOOGLE_CONNECTOR_ID,),
                reconnect=(GOOGLE_CONNECTOR_ID,),
            )
        if code == 404:
            raise MailboxError(
                "Gmail has no such message or thread (404). The id may be "
                "mistyped, or the mail was deleted. Use an id returned by "
                f"list_inbox or search_email. (request: {where})"
            )
        if code == 429:
            retry_after = response.headers.get("Retry-After", "unknown")
            raise MailboxError(
                "Gmail is rate-limiting this mailbox (429). Retry after "
                f"{retry_after}s. Reduce `limit` if this repeats. "
                f"(request: {where})"
            )
        raise MailboxError(
            f"Gmail request failed: {where} returned {code}"
            + (f" ({reason})" if reason else "")
            + ". Retry, and check https://www.google.com/appsstatus if it "
            "persists."
        )

    def _get(
        self, path: str, *, params: Optional[dict] = None, token: Optional[str] = None
    ) -> Any:
        # Re-minted per request unless a caller already minted one for a fan-out
        # it owns: a cached token would let a mid-scan revoke look like success.
        bearer = token or self._access_token_fn()
        try:
            resp = self._client.get(
                f"{GMAIL_API_BASE}{path}",
                headers={"Authorization": f"Bearer {bearer}"},
                params=params,
            )
        except httpx.HTTPError as exc:
            raise MailboxError(
                f"Could not reach Gmail for {path}: {type(exc).__name__}. Check "
                "network connectivity, then retry."
            ) from exc
        if resp.status_code != 200:
            self._raise(resp, f"GET {path}")
        return resp.json()

    # -- Reads --------------------------------------------------------------

    def get_user_email(self) -> str:
        """The connected mailbox's address."""
        data = self._get("/users/me/profile")
        address = (data.get("emailAddress") or "").strip()
        if not address:
            raise MailboxError(
                "Gmail returned no address for the connected account "
                "(`emailAddress` was empty). The connection is in an unusable "
                "state and needs reconnecting.",
                connect=(GOOGLE_CONNECTOR_ID,),
                reconnect=(GOOGLE_CONNECTOR_ID,),
            )
        return address

    def _clamp(self, limit: int) -> int:
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}")
        return min(limit, _GMAIL_MAX_LIMIT)

    def _labels(self) -> List[Dict[str, Any]]:
        if self._label_cache is None:
            data = self._get("/users/me/labels")
            self._label_cache = [
                lab for lab in data.get("labels") or [] if lab.get("id")
            ]
        return self._label_cache

    def _label_map(self) -> Dict[str, str]:
        """Label id -> display name. Gmail's message resource carries only
        opaque ids, and `labels.list` is the only way to name them."""
        return {lab["id"]: lab.get("name") or lab["id"] for lab in self._labels()}

    def _fan_out(
        self, paths: Sequence[str], params: Optional[dict] = None
    ) -> List[Any]:
        """One GET per path over a bounded pool, under one minted token.

        The pool shares this instance's ``httpx.Client``, whose connection
        pool guards its own state with a thread lock. Do not swap in a client
        without that guarantee, or hand each worker its own.
        """
        token = self._access_token_fn()

        def fetch(path: str) -> Any:
            return self._get(path, params=params, token=token)

        with ThreadPoolExecutor(
            max_workers=min(_FETCH_CONCURRENCY, len(paths))
        ) as pool:
            # list() forces every result, so a failed subrequest raises here
            # rather than shortening the listing into a smaller-looking inbox.
            return list(pool.map(fetch, paths))

    def _list_ids(
        self,
        *,
        label_ids: Optional[Sequence[str]] = None,
        query: Optional[str] = None,
        limit: int = 25,
    ) -> List[str]:
        params: Dict[str, Any] = {"maxResults": self._clamp(limit)}
        if label_ids:
            params["labelIds"] = list(label_ids)
        if query:
            params["q"] = query
        data = self._get("/users/me/messages", params=params)
        # The key is absent, not empty, when nothing matches.
        return [m["id"] for m in (data.get("messages") or []) if m.get("id")]

    def _fetch_summaries(self, ids: Sequence[str]) -> List[Dict[str, Any]]:
        """Metadata and attachment names for every id, without bodies."""
        if not ids:
            return []
        label_names = self._label_map()
        messages = self._fan_out(
            [f"/users/me/messages/{mid}" for mid in ids],
            {"format": "full", "fields": _SUMMARY_FIELDS},
        )
        return [message_summary(m, label_names=label_names) for m in messages]

    def list_inbox(
        self, *, limit: int = 25, unread_only: bool = False
    ) -> List[Dict[str, Any]]:
        """Newest-first inbox messages, with metadata but no bodies."""
        label_ids = ["INBOX", "UNREAD"] if unread_only else ["INBOX"]
        return self._fetch_summaries(self._list_ids(label_ids=label_ids, limit=limit))

    def search(self, query: str, *, limit: int = 25) -> List[Dict[str, Any]]:
        """Full-mailbox keyword search.

        Gmail returns hits in relevance order, not date order — the tool
        docstring says so, because a caller assuming newest-first would
        silently misreport.
        """
        if not query or not query.strip():
            raise ValueError("query must be a non-empty search string")
        return self._fetch_summaries(self._list_ids(query=query, limit=limit))

    def get_message(self, message_id: str) -> Dict[str, Any]:
        """One message, body included."""
        if not message_id or not message_id.strip():
            raise ValueError("message_id must be a non-empty message id")
        # Ids arrive from the model; accept only the shape Gmail issues.
        if not _MESSAGE_ID_RE.fullmatch(message_id):
            raise ValueError(
                f"message_id {message_id!r} is not a Gmail message id. Use an "
                "id returned by list_inbox or search."
            )
        data = self._get(f"/users/me/messages/{message_id}", params={"format": "full"})
        return message_summary(data, label_names=self._label_map(), include_body=True)

    def list_thread_ids(self, folder: str, *, limit: int = 50) -> Dict[str, Any]:
        """Distinct thread ids in ``folder`` (``inbox`` or ``sent``), newest first.

        ``truncated`` means older mail exists past ``limit``: Gmail returned
        another page, or the listing filled the whole budget.
        """
        label = _FOLDER_LABELS.get(folder)
        if label is None:
            raise ValueError(
                f"folder must be one of {sorted(_FOLDER_LABELS)}, got {folder!r}"
            )
        wanted = self._clamp(limit)
        data = self._get(
            "/users/me/messages",
            params={"labelIds": [label], "maxResults": wanted},
        )
        stubs = data.get("messages") or []
        thread_ids: List[str] = []
        for stub in stubs:
            tid = stub.get("threadId")
            if not tid:
                raise MailboxError(
                    f"Gmail listed message {stub.get('id')!r} without a "
                    "threadId, so it cannot be grouped into a conversation. "
                    "Retry; report it at https://github.com/amd/gaia/issues "
                    "if it repeats."
                )
            if tid not in thread_ids:
                thread_ids.append(tid)
        return {
            "thread_ids": thread_ids,
            "messages_listed": len(stubs),
            "truncated": bool(data.get("nextPageToken")) or len(stubs) >= wanted,
        }

    def get_thread(self, thread_id: str) -> Dict[str, Any]:
        """Every message in one conversation, oldest first, bodies included.

        Gmail does not promise thread order, so it is sorted here. Each message
        also carries its raw ``_label_ids`` for the waiting-on-you veto; the
        tool layer strips underscore keys before the model sees them.
        """
        if not thread_id or not _MESSAGE_ID_RE.fullmatch(thread_id):
            raise ValueError(
                f"thread_id {thread_id!r} is not a Gmail thread id. Use the "
                "thread_id from a list_inbox or search_email result."
            )
        data = self._get(f"/users/me/threads/{thread_id}", params={"format": "full"})
        raw = sorted(
            data.get("messages") or [], key=lambda m: int(m.get("internalDate") or 0)
        )
        label_names = self._label_map()
        messages = []
        for msg in raw:
            summary = message_summary(msg, label_names=label_names, include_body=True)
            summary["draft"] = "DRAFT" in (msg.get("labelIds") or [])
            summary["_label_ids"] = list(msg.get("labelIds") or [])
            messages.append(summary)
        return {"thread_id": thread_id, "messages": messages, "truncated": False}

    def list_folders(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        """Labels with their unread and total counts.

        `labels.list` omits the counts, so each one costs a `labels.get`.
        """
        ids = [lab["id"] for lab in self._labels()][: self._clamp(limit)]
        if not ids:
            return []
        return [
            {
                "id": detail.get("id"),
                "name": detail.get("name") or detail.get("id") or "",
                "unread": detail.get("messagesUnread", 0),
                "total": detail.get("messagesTotal", 0),
            }
            for detail in self._fan_out([f"/users/me/labels/{lid}" for lid in ids])
        ]

    def get_draft(self, draft_id: str) -> OutgoingMessage:
        """One draft, exactly as Gmail would send it now."""
        _validated_id(draft_id, "draft_id", "create_draft or list_drafts")
        return _draft_message(
            self._get(f"/users/me/drafts/{draft_id}", params={"format": "full"})
        )

    def list_drafts(self, *, limit: int = 25) -> List[Dict[str, Any]]:
        """Saved drafts with their recipients and subject, not their bodies."""
        data = self._get("/users/me/drafts", params={"maxResults": self._clamp(limit)})
        ids = [d["id"] for d in data.get("drafts") or [] if d.get("id")]
        if not ids:
            return []
        rows = []
        for draft in self._fan_out(
            [f"/users/me/drafts/{did}" for did in ids], {"format": "metadata"}
        ):
            message = draft.get("message") or {}
            headers = _header_map(message.get("payload") or {})
            rows.append(
                {
                    "draft_id": draft.get("id"),
                    "thread_id": message.get("threadId"),
                    "to": _address_list(headers.get("to")),
                    "subject": _decode(headers.get("subject")) or "(no subject)",
                    "preview": html.unescape(message.get("snippet") or "").strip(),
                }
            )
        return rows


class GmailWriteBackend(GmailReadBackend):
    """Drafting and sending. Each call is made once; a failure is reported, not retried."""

    # Gmail replaces a whole draft, and this rewrite carries no files.
    edit_keeps_attachments = False

    def _write(
        self, method: str, path: str, *, payload: Optional[dict] = None
    ) -> Dict[str, Any]:
        where = f"{method} {path}"
        token = self._access_token_fn()
        try:
            resp = self._client.request(
                method,
                f"{GMAIL_API_BASE}{path}",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise MailboxError(
                f"Could not reach Gmail for {where} ({type(exc).__name__}), so the "
                "request never went out. Check network connectivity, then retry."
            ) from exc
        except httpx.HTTPError as exc:
            raise MailboxOutcomeUnknown(
                f"The connection to Gmail failed during {where} "
                f"({type(exc).__name__}), so GAIA can't tell whether it took "
                "effect. Check Sent and Drafts in Gmail before trying again."
            ) from exc
        if resp.status_code == 403:
            reason, _ = _error_detail(resp)
            if reason in ("insufficientPermissions", "PERMISSION_DENIED"):
                raise MailboxAuthError(
                    "Gmail refused this because the connection can't write mail "
                    "(403). Reconnect Google in Settings -> Connectors and approve "
                    f"reading, composing and sending. (request: {where})"
                )
        if resp.status_code >= 500:
            raise MailboxOutcomeUnknown(
                f"Gmail answered {resp.status_code} during {where}, so GAIA can't "
                "tell whether it took effect. Check Sent and Drafts in Gmail before "
                "trying again."
            )
        if resp.status_code not in (200, 204):
            self._raise(resp, where)
        try:
            return resp.json() if resp.content else {}
        except ValueError as exc:
            raise MailboxOutcomeUnknown(
                f"Gmail accepted {where} but its answer was unreadable, so GAIA "
                "can't confirm the result. Check Sent and Drafts in Gmail before "
                "trying again."
            ) from exc

    def get_source_message(self, message_id: str) -> SourceMessage:
        """What a reply or forward is built from, threading headers included."""
        _validated_id(message_id, "message_id", "list_inbox or search_email")
        data = self._get(f"/users/me/messages/{message_id}", params={"format": "full"})
        payload = data.get("payload") or {}
        headers = _header_map(payload)
        senders = _recipients(headers.get("from"))
        body, body_type = _select_body(payload)
        return SourceMessage(
            id=message_id,
            sender=senders[0] if senders else None,
            reply_to=_recipients(headers.get("reply-to")),
            to=_recipients(headers.get("to")),
            cc=_recipients(headers.get("cc")),
            subject=_decode(headers.get("subject")),
            body=_normalized_body(body),
            body_type=body_type,
            thread_id=data.get("threadId"),
            internet_message_id=headers.get("message-id") or None,
            references=headers.get("references") or None,
            received=_received(data.get("internalDate")),
            attachments=tuple(_attachment_names(payload)),
        )

    def create_draft(self, message: OutgoingMessage) -> str:
        created = self._write(
            "POST", "/users/me/drafts", payload={"message": _message_resource(message)}
        )
        return created["id"]

    def update_draft(self, draft_id: str, message: OutgoingMessage) -> None:
        _validated_id(draft_id, "draft_id", "create_draft or list_drafts")
        self._write(
            "PUT",
            f"/users/me/drafts/{draft_id}",
            payload={"message": _message_resource(message)},
        )

    def discard_draft(self, draft_id: str) -> None:
        _validated_id(draft_id, "draft_id", "create_draft or list_drafts")
        self._write("DELETE", f"/users/me/drafts/{draft_id}")

    def send_draft(self, draft_id: str) -> Dict[str, Any]:
        _validated_id(draft_id, "draft_id", "create_draft or list_drafts")
        sent = self._write("POST", "/users/me/drafts/send", payload={"id": draft_id})
        return {"message_id": sent.get("id"), "thread_id": sent.get("threadId")}

    def send(self, message: OutgoingMessage) -> Dict[str, Any]:
        sent = self._write(
            "POST", "/users/me/messages/send", payload=_message_resource(message)
        )
        return {"message_id": sent.get("id"), "thread_id": sent.get("threadId")}


__all__ = [
    "GMAIL_API_BASE",
    "GmailReadBackend",
    "GmailWriteBackend",
    "raw_mime",
    "message_summary",
]
