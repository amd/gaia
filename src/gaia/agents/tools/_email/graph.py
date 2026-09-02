# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Read-only Microsoft Graph mailbox client for the flagship agent's email tools.

Scope is deliberately narrow: list, search, read, and folder enumeration. There
is no mutating verb here, so there is no action ledger to keep consistent and
nothing to undo. Writes arrive in a later phase together with the ledger that
makes them reversible.

This is a lean reimplementation of the read half of
``gaia_agent_email.outlook_backend``, not an import of it: the flagship must not
depend on the standalone email package it is meant to replace. The wire
contract (Graph ``message`` resource) is the shared truth, not the Python.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Iterable, List, Optional

import httpx

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

# Every field the read tools and the triage signals need, and nothing heavier.
# ``body`` is excluded — it is the one large field, and only ``read_email``
# fetches it. ``bodyPreview`` is Graph's snippet equivalent and is enough for
# scanning.
_LIST_SELECT = (
    "id,conversationId,subject,from,toRecipients,ccRecipients,"
    "receivedDateTime,isRead,isDraft,flag,categories,bodyPreview"
)
_FULL_SELECT = _LIST_SELECT + ",body"

# Graph caps $top at 999; asking for more is a 400, not a truncation.
_MAX_TOP = 999


class MailboxError(RuntimeError):
    """A mailbox request failed in a way the caller should surface verbatim."""


class MailboxAuthError(MailboxError):
    """The mailbox rejected our credentials or refused the requested scope."""


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


def message_summary(
    msg: Dict[str, Any], *, include_body: bool = False
) -> Dict[str, Any]:
    """Flatten a Graph ``message`` into the shape the agent's tools return.

    Provider-neutral on purpose: a Gmail backend added later emits this same
    shape, so the tools and the skill never learn which mailbox they are on.
    """
    flag = (msg.get("flag") or {}).get("flagStatus")
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
    }
    if include_body:
        body = msg.get("body") or {}
        summary["body"] = body.get("content") or ""
        summary["body_content_type"] = (body.get("contentType") or "text").lower()
    return summary


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
        # Built from status + truncated body only. Never from a wrapper
        # exception, which can carry the Authorization header into a log.
        detail = response.text[:300]
        if response.status_code == 401:
            raise MailboxAuthError(
                "Microsoft Graph rejected the access token (401). The Outlook "
                "connection has expired or been revoked. Reconnect it with "
                "`gaia connectors` (or Settings → Connectors in the Agent UI), "
                f"then retry. (request: {where})"
            )
        if response.status_code == 403:
            raise MailboxAuthError(
                "Microsoft Graph refused the request (403 — insufficient "
                "permissions). The connected account has not granted "
                "Mail.ReadWrite to this agent. Reconnect Microsoft with "
                "`gaia connectors` and approve mail access. "
                f"(request: {where}; detail: {detail})"
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
            f"{response.status_code}. Detail: {detail}"
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
                "connection is in an unusable state — reconnect Microsoft with "
                "`gaia connectors`."
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
            "$orderby": "receivedDateTime desc",
        }
        if unread_only:
            params["$filter"] = "isRead eq false"
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
            "$search": f'"{query}"',
        }
        data = self._get("/me/messages", params=params)
        return [message_summary(m) for m in data.get("value", [])]

    def get_message(self, message_id: str) -> Dict[str, Any]:
        """One message, body included."""
        if not message_id or not message_id.strip():
            raise ValueError("message_id must be a non-empty message id")
        data = self._get(f"/me/messages/{message_id}", params={"$select": _FULL_SELECT})
        return message_summary(data, include_body=True)

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


__all__ = [
    "GRAPH_API_BASE",
    "MailboxAuthError",
    "MailboxError",
    "OutlookReadBackend",
    "message_summary",
]
