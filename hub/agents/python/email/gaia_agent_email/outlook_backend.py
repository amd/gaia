# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Microsoft Graph mail backend — ``LiveOutlookBackend`` (#1275).

Connects the Email Triage Agent to a personal Outlook.com / Hotmail / Live
mailbox via the Microsoft OAuth provider (#1105), alongside the shipped Gmail
connector.

Architecture seam (the one ``gmail_backend.py`` explicitly anticipated for
"#963 Outlook/Exchange"): this backend satisfies the SAME ``GmailBackend``
Protocol as ``LiveGmailBackend`` by **translating Microsoft Graph JSON into the
Gmail API v1 envelope** on read, and Gmail's semantic verbs
(``archive_message``, ``mark_read``, ``add_star`` …) into MS Graph mutations on
write. The email agent's tools call methods on a ``GmailBackend`` and consume
Gmail-shaped dicts — so they operate on Outlook and Gmail interchangeably,
without a single tool change.

Translation summary (Graph -> Gmail shape):

- ``message`` -> ``{id, threadId(=conversationId), labelIds, snippet,
  payload:{headers:[{name,value}], body:{data:<b64url>, ...}}}``.
- ``isRead==false`` -> ``UNREAD`` label; ``flag.flagStatus=="flagged"`` ->
  ``STARRED``; in-inbox -> ``INBOX``; ``categories[]`` -> extra labelIds.
- ``body.contentType`` ("html"/"text") -> ``payload.mimeType`` so the shared
  ``decode_message_body`` strips HTML / drops ``<style>`` (prompt-injection
  defense) exactly as it does for Gmail.

Semantic verbs (Gmail -> Graph):

- ``archive_message`` -> move to the ``archive`` well-known folder (mirrors
  Gmail's "remove INBOX label"); ``trash``/``untrash`` -> move to
  ``deleteditems``/``inbox``; ``permanent_delete`` -> ``DELETE``.
- ``mark_read``/``mark_unread`` -> ``PATCH {isRead}``; ``add_star``/
  ``remove_star`` -> ``PATCH {flag:{flagStatus}}``; ``add_label``/
  ``remove_label`` -> ``PATCH {categories}`` (Outlook's label analogue).
- ``create_draft`` -> ``POST /me/messages``; ``send_draft`` ->
  ``POST /me/messages/{id}/send``; ``send_message`` -> ``POST /me/sendMail``.

Token lifecycle, error hygiene, and the no-silent-fallback contract mirror
``gmail_backend.py`` exactly:

- ``access_token_fn`` is invoked on EVERY request (the connectors token cache
  makes this cheap) so a mid-paginated revoke surfaces as a 401, not a stale
  token.
- Every non-2xx raises ``ConnectorsError`` built from ``status_code`` +
  truncated body ONLY — never from a wrapper exception that could leak the
  ``Authorization: Bearer ...`` header. An empty/no-access result is NEVER
  swallowed into an empty list.
"""

from __future__ import annotations

import base64
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
)

import httpx
from gaia_agent_email.outlook_scopes import OUTLOOK_MAIL_SCOPES
from gaia_agent_email.scopes import AGENT_NAMESPACED_ID

from gaia.connectors.errors import ConnectorsError
from gaia.connectors.handler import get_credential_sync
from gaia.logger import get_logger

log = get_logger(__name__)


GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

# Well-known Outlook folder names accepted as a ``move`` destinationId.
_FOLDER_INBOX = "inbox"
_FOLDER_ARCHIVE = "archive"
_FOLDER_DELETED = "deleteditems"

# Gmail system label ids we synthesize from MS Graph message flags.
_LABEL_INBOX = "INBOX"
_LABEL_UNREAD = "UNREAD"
_LABEL_STARRED = "STARRED"
_LABEL_SENT = "SENT"

# ``$select`` for ``get_message`` — pull exactly the fields the Gmail-shape
# translation needs (Graph returns a large default projection otherwise).
_MESSAGE_SELECT = (
    "id,conversationId,subject,from,toRecipients,ccRecipients,"
    "receivedDateTime,sentDateTime,isRead,isDraft,flag,categories,"
    "bodyPreview,body,parentFolderId"
)


# ---------------------------------------------------------------------------
# Graph message -> Gmail API v1 shape translation
# ---------------------------------------------------------------------------


def _format_address(entity: Optional[Dict[str, Any]]) -> str:
    """Render a Graph ``recipient`` (``{emailAddress:{name,address}}``) as an
    RFC-5322-style ``Name <addr>`` header value (or bare address)."""
    if not entity:
        return ""
    email = entity.get("emailAddress") or {}
    name = (email.get("name") or "").strip()
    addr = (email.get("address") or "").strip()
    if name and addr and name.lower() != addr.lower():
        return f"{name} <{addr}>"
    return addr or name


def _format_recipient_list(entities: Optional[Iterable[Dict[str, Any]]]) -> str:
    parts = [_format_address(e) for e in (entities or [])]
    return ", ".join(p for p in parts if p)


def _b64url(text: str) -> str:
    """URL-safe base64 with stripped padding — matches Gmail's wire format so
    the shared ``decode_message_body`` (which re-pads) round-trips."""
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def graph_message_to_gmail(msg: Dict[str, Any]) -> Dict[str, Any]:
    """Translate a Microsoft Graph ``message`` resource into a Gmail API v1
    ``messages.get?format=full`` shaped dict.

    The returned ``payload`` is a single leaf part (Graph hands us the body
    already assembled as one ``text``/``html`` blob — there is no MIME tree to
    walk), with the headers the read tools read (``Subject``/``From``/``To``/
    ``Date``) reconstructed from the structured Graph fields.
    """
    body = msg.get("body") or {}
    content_type = (body.get("contentType") or "text").lower()
    mime_type = "text/html" if content_type == "html" else "text/plain"
    raw_content = body.get("content") or ""

    headers = [
        {"name": "Subject", "value": msg.get("subject") or ""},
        {"name": "From", "value": _format_address(msg.get("from"))},
        {"name": "To", "value": _format_recipient_list(msg.get("toRecipients"))},
        {"name": "Date", "value": msg.get("receivedDateTime") or ""},
    ]
    cc = _format_recipient_list(msg.get("ccRecipients"))
    if cc:
        headers.append({"name": "Cc", "value": cc})

    payload = {
        "mimeType": mime_type,
        "headers": headers,
        "body": {"size": len(raw_content), "data": _b64url(raw_content)},
    }

    return {
        "id": msg.get("id"),
        "threadId": msg.get("conversationId") or msg.get("id"),
        "labelIds": _derive_label_ids(msg),
        "snippet": msg.get("bodyPreview") or "",
        "internalDate": msg.get("receivedDateTime") or "",
        "payload": payload,
    }


def _derive_label_ids(msg: Dict[str, Any]) -> List[str]:
    """Map MS Graph message flags onto Gmail-style label ids the tools expect."""
    labels: List[str] = []
    # A message returned from a mailbox read is in the inbox unless it has been
    # moved; Graph does not return a folder name on the message, only
    # ``parentFolderId`` (an opaque id). We treat any non-draft message as
    # INBOX-resident for triage purposes — the read tools only key off INBOX /
    # UNREAD / STARRED, and list calls already scope to the inbox folder.
    if not msg.get("isDraft"):
        labels.append(_LABEL_INBOX)
    if msg.get("isRead") is False:
        labels.append(_LABEL_UNREAD)
    if ((msg.get("flag") or {}).get("flagStatus") or "") == "flagged":
        labels.append(_LABEL_STARRED)
    for category in msg.get("categories") or []:
        if category:
            labels.append(category)
    return labels


def _validate_no_crlf(field: str, value: str) -> None:
    """Reject CRLF in header-bound values — defense-in-depth against header
    injection (``to``/``subject`` may be LLM-decided or lifted from inbound
    mail). Mirrors ``gmail_backend._build_rfc822``'s guard."""
    if "\r" in value or "\n" in value:
        raise ValueError(
            f"refusing to send: {field!r} contains a newline — "
            "possible CRLF injection attempt"
        )


def _recipients(addresses: str) -> List[Dict[str, Any]]:
    """Build a Graph ``recipient`` array from a comma-separated address string."""
    out: List[Dict[str, Any]] = []
    for addr in addresses.split(","):
        addr = addr.strip()
        if addr:
            out.append({"emailAddress": {"address": addr}})
    return out


# Graph's simple-attach path (attachments inline on the message resource) caps
# each file at 3 MB; larger files need an uploadSession, which this backend
# does not implement. Enforced loudly — never silently truncated.
_GRAPH_SIMPLE_ATTACH_MAX_BYTES = 3 * 1024 * 1024


class AttachmentTooLargeError(ValueError):
    """An attachment passed contract validation (<=25 MB) but exceeds this
    backend's own limit (Outlook's 3 MB Graph simple-attach cap).

    Distinct from the bare ``ValueError`` CRLF-injection guards in this
    module so the API layer can map exactly this condition to HTTP 413
    without catching unrelated validation failures.
    """


def _build_graph_message(
    *,
    to: str,
    subject: str,
    body: str,
    headers: Optional[Dict[str, str]] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build a Graph ``message`` resource for draft creation / sendMail.

    ``internetMessageHeaders`` on Graph accepts ONLY custom headers whose name
    begins with ``x-`` — sending a standard header (e.g. ``In-Reply-To``)
    there returns HTTP 400. The reply tools pass RFC threading headers
    (``In-Reply-To``/``References``); those are dropped here (Outlook threads by
    ``conversationId``/subject, so a reply still threads). Only ``x-`` headers
    are forwarded, after CRLF validation.

    ``attachments`` (dicts of ``filename``/``mime_type``/``content`` bytes,
    #1542) map to Graph ``fileAttachment`` resources. Files over the 3 MB
    simple-attach limit are rejected loudly.
    """
    _validate_no_crlf("to", to)
    _validate_no_crlf("subject", subject)
    custom_headers: List[Dict[str, str]] = []
    for k, v in (headers or {}).items():
        _validate_no_crlf(k, v)
        if k.lower().startswith("x-"):
            custom_headers.append({"name": k, "value": v})
    message: Dict[str, Any] = {
        "subject": subject,
        "body": {"contentType": "text", "content": body},
        "toRecipients": _recipients(to),
    }
    if custom_headers:
        message["internetMessageHeaders"] = custom_headers
    if attachments:
        graph_attachments: List[Dict[str, Any]] = []
        for att in attachments:
            content: bytes = att["content"]
            if len(content) > _GRAPH_SIMPLE_ATTACH_MAX_BYTES:
                raise AttachmentTooLargeError(
                    f"attachment {att['filename']!r} is {len(content)} bytes; "
                    f"the Outlook backend supports at most "
                    f"{_GRAPH_SIMPLE_ATTACH_MAX_BYTES} bytes (3 MB) per "
                    f"attachment. Send it from a Gmail mailbox or shrink the file."
                )
            graph_attachments.append(
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": att["filename"],
                    "contentType": att["mime_type"],
                    "contentBytes": base64.b64encode(content).decode("ascii"),
                }
            )
        message["attachments"] = graph_attachments
    return message


# ---------------------------------------------------------------------------
# LiveOutlookBackend
# ---------------------------------------------------------------------------


class LiveOutlookBackend:
    """Concrete ``GmailBackend`` that hits Microsoft Graph for a personal
    Outlook.com mailbox.

    Satisfies the ``GmailBackend`` structural Protocol so the email agent's
    tools use it interchangeably with ``LiveGmailBackend``.
    """

    def __init__(
        self,
        access_token_fn: Callable[[], str],
        *,
        http_client: Optional[httpx.Client] = None,
        timeout_seconds: float = 15.0,
    ):
        self._access_token_fn = access_token_fn
        # Allow tests to inject an ``httpx.MockTransport``-backed client without
        # touching the network.
        self._client = http_client or httpx.Client(timeout=timeout_seconds)

    # -- HTTP helpers -------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        # Re-fetch on every request — cheap via the connectors token cache, but
        # mandatory so a mid-paginated revoke surfaces as 401 (AUTH_REQUIRED),
        # not a stale-token success.
        token = self._access_token_fn()
        return {"Authorization": f"Bearer {token}"}

    def _raise_http(self, response: httpx.Response, where: str) -> None:
        # Build the error from status + truncated body ONLY. NEVER from a
        # wrapper exception, which would expose the Authorization header.
        if response.status_code == 401:
            raise ConnectorsError(
                "Microsoft Graph returned 401. The Outlook access token may "
                "have expired or been revoked. Reconnect Microsoft in "
                f"Settings -> Connections. (where: {where})"
            )
        if response.status_code == 403:
            raise ConnectorsError(
                "Microsoft Graph returned 403 (insufficient permissions). The "
                "connected Microsoft account did not grant the mail scopes this "
                "agent needs (Mail.ReadWrite / Mail.Send). Reconnect Microsoft "
                "in Settings -> Connections and approve mail access. "
                f"(where: {where}; detail: {response.text[:300]})"
            )
        raise ConnectorsError(
            f"Microsoft Graph {where} returned {response.status_code}: "
            f"{response.text[:300]}"
        )

    def _get(self, path: str, *, params: Optional[dict] = None) -> Any:
        resp = self._client.get(
            f"{GRAPH_API_BASE}{path}", headers=self._headers(), params=params
        )
        if resp.status_code != 200:
            self._raise_http(resp, f"GET {path}")
        return resp.json()

    def _get_url(self, url: str) -> Any:
        """GET an absolute URL (used to follow a Graph ``@odata.nextLink``).

        The nextLink already encodes the query (``$top``/``$filter``/skiptoken),
        so we send it verbatim rather than re-deriving params — re-deriving
        would silently drop the server-side paging cursor.
        """
        resp = self._client.get(url, headers=self._headers())
        if resp.status_code != 200:
            self._raise_http(resp, "GET (nextLink)")
        return resp.json()

    def _post(self, path: str, *, json_body: Optional[dict] = None) -> Any:
        resp = self._client.post(
            f"{GRAPH_API_BASE}{path}", headers=self._headers(), json=json_body
        )
        # Graph returns 200/201 (move, create draft), 202 (send) or 204.
        if resp.status_code not in (200, 201, 202, 204):
            self._raise_http(resp, f"POST {path}")
        return resp.json() if resp.text else {}

    def _patch(self, path: str, *, json_body: dict) -> Any:
        resp = self._client.patch(
            f"{GRAPH_API_BASE}{path}", headers=self._headers(), json=json_body
        )
        if resp.status_code not in (200, 204):
            self._raise_http(resp, f"PATCH {path}")
        return resp.json() if resp.text else {}

    def _delete(self, path: str) -> None:
        resp = self._client.delete(f"{GRAPH_API_BASE}{path}", headers=self._headers())
        if resp.status_code not in (200, 204):
            self._raise_http(resp, f"DELETE {path}")

    # -- Read APIs ----------------------------------------------------------

    def get_user_email(self) -> str:
        data = self._get("/me", params={"$select": "mail,userPrincipalName"})
        # Personal accounts often have a null ``mail`` and carry the address in
        # ``userPrincipalName`` instead.
        return data.get("mail") or data.get("userPrincipalName") or ""

    def list_messages(
        self,
        *,
        query: Optional[str] = None,
        label_ids: Optional[Iterable[str]] = None,
        max_results: int = 25,
        page_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        # A non-None ``page_token`` is a Graph ``@odata.nextLink`` (absolute URL
        # with the server-side paging cursor baked in). Follow it verbatim — do
        # NOT re-derive params, which would silently restart at page 1.
        if page_token:
            data = self._get_url(page_token)
        else:
            labels = set(label_ids or [])
            params: Dict[str, Any] = {
                "$top": max_results,
                "$select": "id,conversationId",
            }

            # A free-text query maps to $search (Graph KQL). $search cannot be
            # combined with $filter/$orderby, so it takes precedence and runs
            # against the whole mailbox.
            if query:
                params["$search"] = f'"{query}"'
                path = "/me/messages"
            elif _LABEL_UNREAD in labels:
                params["$filter"] = "isRead eq false"
                params["$orderby"] = "receivedDateTime desc"
                path = "/me/mailFolders/inbox/messages"
            elif _LABEL_SENT in labels:
                # Sent-folder scan (follow-up tracking, #1606) — falling
                # through to the inbox would silently scan the wrong mail.
                params["$orderby"] = "receivedDateTime desc"
                path = "/me/mailFolders/sentitems/messages"
            else:
                # Default (INBOX or unspecified) -> inbox folder, newest first.
                params["$orderby"] = "receivedDateTime desc"
                path = "/me/mailFolders/inbox/messages"

            data = self._get(path, params=params)
        messages = [
            {"id": m.get("id"), "threadId": m.get("conversationId") or m.get("id")}
            for m in data.get("value", [])
        ]
        return {
            "messages": messages,
            # Carry the @odata.nextLink so paginated callers keep working;
            # Gmail callers treat any truthy token as "more pages".
            "nextPageToken": data.get("@odata.nextLink"),
            "resultSizeEstimate": len(messages),
        }

    def get_message(self, message_id: str) -> Dict[str, Any]:
        data = self._get(
            f"/me/messages/{message_id}", params={"$select": _MESSAGE_SELECT}
        )
        return graph_message_to_gmail(data)

    def get_thread(self, thread_id: str) -> Dict[str, Any]:
        # Graph has no thread-get; fetch every message in the conversation.
        # Combining $filter=conversationId with $orderby=receivedDateTime trips
        # Graph's InefficientFilter (the two properties aren't co-indexed), so
        # we filter server-side and sort ascending client-side instead.
        data = self._get(
            "/me/messages",
            params={
                "$filter": f"conversationId eq '{thread_id}'",
                "$select": _MESSAGE_SELECT,
                "$top": 100,
            },
        )
        messages = [graph_message_to_gmail(m) for m in data.get("value", [])]
        messages.sort(key=lambda m: m.get("internalDate") or "")
        return {"id": thread_id, "messages": messages}

    def list_labels(self) -> List[Dict[str, Any]]:
        # Outlook's closest analogue to Gmail labels is master categories.
        data = self._get("/me/outlook/masterCategories")
        out: List[Dict[str, Any]] = []
        for cat in data.get("value", []):
            name = cat.get("displayName")
            if name:
                out.append({"id": name, "name": name, "type": "user"})
        return out

    # -- Mutate APIs --------------------------------------------------------

    def _move(self, message_id: str, destination: str) -> Dict[str, Any]:
        return self._post(
            f"/me/messages/{message_id}/move",
            json_body={"destinationId": destination},
        )

    def _patch_categories(
        self, message_id: str, categories: List[str]
    ) -> Dict[str, Any]:
        return self._patch(
            f"/me/messages/{message_id}", json_body={"categories": categories}
        )

    def archive_message(self, message_id: str) -> Dict[str, Any]:
        # Mirrors Gmail "remove INBOX label" — move out of the inbox.
        return self._move(message_id, _FOLDER_ARCHIVE)

    def mark_read(self, message_id: str) -> Dict[str, Any]:
        return self._patch(f"/me/messages/{message_id}", json_body={"isRead": True})

    def mark_unread(self, message_id: str) -> Dict[str, Any]:
        return self._patch(f"/me/messages/{message_id}", json_body={"isRead": False})

    def add_star(self, message_id: str) -> Dict[str, Any]:
        return self._patch(
            f"/me/messages/{message_id}",
            json_body={"flag": {"flagStatus": "flagged"}},
        )

    def remove_star(self, message_id: str) -> Dict[str, Any]:
        return self._patch(
            f"/me/messages/{message_id}",
            json_body={"flag": {"flagStatus": "notFlagged"}},
        )

    def add_label(self, message_id: str, label_id: str) -> Dict[str, Any]:
        # Graph replaces the whole ``categories`` array on PATCH, so read the
        # current set first and PATCH the union (idempotent).
        current = self._get(
            f"/me/messages/{message_id}", params={"$select": "categories"}
        )
        categories = list(current.get("categories") or [])
        if label_id not in categories:
            categories.append(label_id)
        return self._patch_categories(message_id, categories)

    def remove_label(self, message_id: str, label_id: str) -> Dict[str, Any]:
        current = self._get(
            f"/me/messages/{message_id}", params={"$select": "categories"}
        )
        categories = [c for c in (current.get("categories") or []) if c != label_id]
        return self._patch_categories(message_id, categories)

    def trash_message(self, message_id: str) -> Dict[str, Any]:
        # Recoverable: moves to Deleted Items (restore via untrash_message).
        return self._move(message_id, _FOLDER_DELETED)

    def untrash_message(self, message_id: str) -> Dict[str, Any]:
        return self._move(message_id, _FOLDER_INBOX)

    def unarchive_message(
        self, message_id: str, prior_labels: List[str]
    ) -> Dict[str, Any]:
        # Move back to the inbox folder; prior_labels unused (categories survive
        # a folder move — kept for Protocol parity with LiveGmailBackend).
        return self._move(message_id, _FOLDER_INBOX)

    def permanent_delete(self, message_id: str) -> None:
        self._delete(f"/me/messages/{message_id}")

    # -- Send APIs ----------------------------------------------------------

    def create_draft(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        headers: Optional[Dict[str, str]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        message = _build_graph_message(
            to=to, subject=subject, body=body, headers=headers, attachments=attachments
        )
        # Returns the created draft message resource; ``id`` is the draft id.
        return self._post("/me/messages", json_body=message)

    def send_draft(self, draft_id: str) -> Dict[str, Any]:
        # POST /send takes no body and returns 202 with no content. Return the
        # draft id as the sent id so the reply-tool envelope stays populated
        # (Graph does not echo a new id from this endpoint).
        self._post(f"/me/messages/{draft_id}/send")
        return {"id": draft_id}

    def send_message(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        headers: Optional[Dict[str, str]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        message = _build_graph_message(
            to=to, subject=subject, body=body, headers=headers, attachments=attachments
        )
        self._post(
            "/me/sendMail",
            json_body={"message": message, "saveToSentItems": True},
        )
        # Graph sendMail returns HTTP 202 with no body — no id is echoed back.
        # "sent": True signals success to the REST handler without inventing a fake id.
        return {"id": "", "sent": True, "to": to, "subject": subject}

    def create_label(  # pylint: disable=unused-argument
        self, *, name: str, label_list_visibility: str = "labelShow"
    ) -> Dict[str, Any]:
        # Master categories require a ``color`` (preset enum); ``preset0`` is a
        # safe default. ``label_list_visibility`` has no Outlook analogue and is
        # ignored — kept in the signature for Protocol parity with Gmail.
        # pylint: disable=unused-argument
        created = self._post(
            "/me/outlook/masterCategories",
            json_body={"displayName": name, "color": "preset0"},
        )
        cat_name = created.get("displayName", name)
        return {"id": cat_name, "name": cat_name, "type": "user"}


# ---------------------------------------------------------------------------
# Module-level token resolver
# ---------------------------------------------------------------------------


def _get_outlook_token() -> str:
    """Return an MS Graph access token via the grant-checked connector path.

    Uses the ``microsoft`` connector + ``oauth_pkce`` handler seam from #1105:
    ``get_credential(spec, required_scopes=[...])`` -> ``{"access_token": ...}``.
    The grant dispatcher raises ``AuthRequiredError`` (no grant / missing
    scopes) BEFORE any network round-trip; we let it propagate so the agent can
    prompt the user — never swallowed into an empty token / empty inbox.

    Module-level (not a method) so it mirrors ``_get_gmail_token`` and can be
    unit-tested without instantiating the agent.
    """
    cred = get_credential_sync(
        "microsoft",
        agent_id=AGENT_NAMESPACED_ID,
        required_scopes=list(OUTLOOK_MAIL_SCOPES),
    )
    return cred["access_token"]


__all__ = [
    "GRAPH_API_BASE",
    "LiveOutlookBackend",
    "_get_outlook_token",
    "graph_message_to_gmail",
]
