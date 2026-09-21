# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Read-only Gmail mailbox client for the flagship agent's email tools.

Same five reads as the Graph backend next door, emitting the same
provider-neutral summary shape, so neither the skill nor the model learns
which mailbox is connected.

This is a reimplementation of the read half of
``gaia_agent_email.gmail_backend``, not a relocation of it: the flagship must
not depend on the standalone email package it is meant to replace, and that
backend returns raw Gmail resources — its flattening lives in the sidecar's
tool layer, which does not come along. The wire contract (Gmail API v1) is the
shared truth, not the Python.
"""

from __future__ import annotations

import base64
import binascii
import html
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import getaddresses
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

import httpx

from gaia.agents.tools._email.errors import MailboxAuthError, MailboxError

logger = logging.getLogger(__name__)

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"

# Gmail's list returns id stubs only, so every message costs a `messages.get`.
# 100 matches the tool layer's own ceiling; Graph's 999 would be 999 fetches.
_GMAIL_MAX_LIMIT = 100

# Fanned out over a bounded pool: 25 sequential round-trips would spend most of
# a tool's timeout waiting. Small enough to stay under Gmail's per-user
# concurrency limit.
_FETCH_CONCURRENCY = 5

# `metadataHeaders` is only honoured alongside `format=metadata`; sending
# either alone silently returns the wrong thing.
_METADATA_HEADERS = ("Subject", "From", "To", "Cc", "Date")

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

_GMAIL_ENABLE_URL = (
    "https://console.cloud.google.com/apis/library/gmail.googleapis.com"
)


def _error_detail(response: httpx.Response) -> tuple:
    """Gmail's structured error fields — a reason token and its remedy link.

    Deliberately never the raw response body: that is upstream-controlled text
    and it ends up in front of a user.
    """
    try:
        err = (response.json() or {}).get("error") or {}
    except ValueError:
        return "", ""
    errors = err.get("errors") or [{}]
    first = errors[0] if isinstance(errors[0], dict) else {}
    reason = str(first.get("reason") or err.get("status") or "")
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
    }
    if include_body:
        summary["body"], summary["body_content_type"] = _select_body(payload)
    return summary


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
                "has expired or been revoked. Reconnect it with `gaia "
                "connectors connect google` (or Settings -> Connectors in the "
                f"Agent UI), then retry. (request: {where})"
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
                "The connected Google account has not granted this agent a "
                "gmail read scope. Re-grant it with `gaia connectors grants "
                "grant google installed:gaia --scopes <scope>`, or reconnect "
                f"Google, then retry. (request: {where}; reason: "
                f"{reason or 'forbidden'})"
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
                "state — reconnect Google with `gaia connectors connect google`."
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

    def _fan_out(self, paths: Sequence[str], params: Optional[dict] = None) -> List[Any]:
        """One GET per path over a bounded pool, under one minted token."""
        token = self._access_token_fn()

        def fetch(path: str) -> Any:
            return self._get(path, params=params, token=token)

        with ThreadPoolExecutor(max_workers=min(_FETCH_CONCURRENCY, len(paths))) as pool:
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
        """Metadata for every id. `metadataHeaders` needs `format=metadata`."""
        if not ids:
            return []
        label_names = self._label_map()
        messages = self._fan_out(
            [f"/users/me/messages/{mid}" for mid in ids],
            {"format": "metadata", "metadataHeaders": list(_METADATA_HEADERS)},
        )
        return [message_summary(m, label_names=label_names) for m in messages]

    def list_inbox(
        self, *, limit: int = 25, unread_only: bool = False
    ) -> List[Dict[str, Any]]:
        """Newest-first inbox messages, with metadata but no bodies."""
        label_ids = ["INBOX", "UNREAD"] if unread_only else ["INBOX"]
        return self._fetch_summaries(
            self._list_ids(label_ids=label_ids, limit=limit)
        )

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
        data = self._get(
            f"/users/me/messages/{message_id}", params={"format": "full"}
        )
        return message_summary(
            data, label_names=self._label_map(), include_body=True
        )

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


__all__ = [
    "GMAIL_API_BASE",
    "GmailReadBackend",
    "message_summary",
]
