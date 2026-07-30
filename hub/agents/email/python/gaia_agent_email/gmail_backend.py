# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Gmail backend Protocol + ``LiveGmailBackend`` REST implementation.

Architecture seam: every Gmail-touching tool in the email agent calls
methods on a ``GmailBackend`` Protocol, NOT on a concrete class. This
lets the eval harness inject ``FakeGmailBackend(mbox_path)`` against the
synthetic dataset (#848) without going anywhere near OAuth, while
production binds to ``LiveGmailBackend(_get_gmail_token)`` and hits the
real API.

Why semantic verbs (``archive_message``, ``mark_read``) instead of a
generic ``modify_labels``: the Protocol is the future seam for #963
(Outlook/Exchange). Outlook has no concept of "labels" — it moves
between folders. Exposing ``modify_labels`` would force the Outlook
backend to either rename or write a leaky shim. The semantic verbs are
provider-agnostic; the Gmail-specific label list/add-remove translation
stays a *private* implementation detail of ``LiveGmailBackend``.

Token lifecycle: the ``access_token_fn`` callable is invoked on EVERY
HTTP request, not cached for the duration of a tool call. This way a
long paginated ``list_messages`` cannot leak a stale token at page
boundaries (the connectors token cache handles refresh efficiently).

No silent fallbacks: every non-2xx response raises ``ConnectorsError``
with an actionable message. The error string is constructed from
``response.status_code`` + ``response.text[:300]`` ONLY — never from a
wrapper exception that might leak the ``Authorization: Bearer ...``
request header.
"""

from __future__ import annotations

import base64
import email as _email_parser
import json
import re
import uuid
from html.parser import HTMLParser
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Protocol,
    Tuple,
    runtime_checkable,
)

import httpx
from gaia_agent_email.scopes import (
    AGENT_NAMESPACED_ID,
    GMAIL_SCOPES,
    SCOPE_GMAIL_FULL_MAILBOX,
)

from gaia_agent_email.google_errors import (
    access_not_configured_message,
    access_not_configured_url,
)

from gaia.connectors.api import get_access_token_sync
from gaia.connectors.errors import ConnectorsError, ScopeMismatchError
from gaia.logger import get_logger

log = get_logger(__name__)


GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
GMAIL_BATCH_URL = "https://gmail.googleapis.com/batch/gmail/v1"
GMAIL_LABEL_INBOX = "INBOX"
GMAIL_LABEL_UNREAD = "UNREAD"
GMAIL_LABEL_STARRED = "STARRED"

# Headers requested on a ``format="metadata"`` fetch (#2643) — exactly what
# the metadata-first triage scan needs: Subject/From for the category
# heuristic, List-Unsubscribe for the RFC 2369 bulk-mail signal. A fixed,
# narrow list (rather than every header a message carries) keeps the
# metadata response small regardless of how many headers the sender added.
# ``FakeGmailBackend`` filters to this same set so a hermetic run measures
# the same shape live Gmail would return.
METADATA_SCAN_HEADERS: Tuple[str, ...] = ("Subject", "From", "To", "Date", "List-Unsubscribe")

# Gmail's documented per-batch subrequest ceiling. A caller passing more ids
# than this to ``get_messages_batch`` is chunked into multiple batch calls,
# never a single oversized request.
_BATCH_MAX_SUBREQUESTS = 100


# ---------------------------------------------------------------------------
# Protocol — the eval seam
# ---------------------------------------------------------------------------


@runtime_checkable
class GmailBackend(Protocol):
    """Structural protocol every Gmail backend implementation satisfies.

    Returned data shape MUST match Gmail API v1's JSON envelope on every
    call (see https://developers.google.com/gmail/api/reference/rest).
    The fake-vs-live shape parity is enforced by
    ``tests/unit/email/test_fake_gmail_shape_contract.py``.
    """

    def get_user_email(self) -> str:
        """Return the authenticated user's primary email address."""
        ...

    def list_messages(
        self,
        *,
        query: Optional[str] = None,
        label_ids: Optional[Iterable[str]] = None,
        max_results: int = 25,
        page_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List message ids matching the query/labels.

        Returns ``{"messages": [{"id": ..., "threadId": ...}], "nextPageToken": str?}``.
        """
        ...

    def get_message(self, message_id: str, *, format: str = "full") -> Dict[str, Any]:
        """Fetch a single message in Gmail API v1 shape.

        ``format="full"`` (the default) is byte-identical to every call site
        that predates #2643 — the full decoded body is present. ``format=
        "metadata"`` returns headers (``METADATA_SCAN_HEADERS``) + labelIds +
        snippet + internalDate, with NO body — cheap enough to run over an
        entire scan, but callers must fetch ``format="full"`` again before
        reading ``payload`` for actual content (metadata-mode ``payload`` has
        no ``parts``/``body.data`` to decode).

        NOTE: ``get_messages_batch`` (#2643 lever 2 — fetch many messages in
        one round-trip) is intentionally NOT part of this Protocol. It is a
        duck-typed, opt-in capability some backends provide (``LiveGmailBackend``,
        ``FakeGmailBackend``) — declaring it here would force EVERY backend
        (including ``LiveOutlookBackend``, which doesn't implement it) to grow
        it just to keep satisfying ``isinstance(x, GmailBackend)``, which
        several tests rely on. Callers detect it via
        ``getattr(gmail, "get_messages_batch", None)`` and fall back to a
        per-id ``get_message`` loop when it's absent — see
        ``read_tools._fetch_messages``.
        """
        ...

    def get_thread(self, thread_id: str) -> Dict[str, Any]:
        """Fetch a thread (all messages in conversation)."""
        ...

    def list_labels(self) -> List[Dict[str, Any]]:
        """List all labels in the user's mailbox."""
        ...

    def get_label(self, label_id: str) -> Dict[str, Any]:
        """Fetch one label with its exact message/thread counts (#2584).

        ``list_labels()`` returns Gmail's MINIMAL label form (id/name/type
        only, no counts) — this is the only call that returns the label
        resource's ``messagesTotal`` / ``messagesUnread`` fields, which are
        exact integers (unlike ``list_messages``'s ``resultSizeEstimate``,
        which Google documents as approximate and can be off by 2x+ on a
        real mailbox). Used to report accurate scan-coverage denominators,
        not per-message — one call per scan, not per message.
        """
        ...

    def archive_message(self, message_id: str) -> Dict[str, Any]:
        """Remove the INBOX label."""
        ...

    def mark_read(self, message_id: str) -> Dict[str, Any]:
        """Remove the UNREAD label."""
        ...

    def mark_unread(self, message_id: str) -> Dict[str, Any]:
        """Add the UNREAD label."""
        ...

    def add_star(self, message_id: str) -> Dict[str, Any]:
        """Add the STARRED label."""
        ...

    def remove_star(self, message_id: str) -> Dict[str, Any]:
        """Remove the STARRED label."""
        ...

    def add_label(self, message_id: str, label_id: str) -> Dict[str, Any]:
        """Add a (system or user-defined) label by id."""
        ...

    def remove_label(self, message_id: str, label_id: str) -> Dict[str, Any]:
        """Remove a label by id."""
        ...

    def trash_message(self, message_id: str) -> Dict[str, Any]:
        """Move to TRASH (recoverable for 30 days by Gmail)."""
        ...

    def untrash_message(self, message_id: str) -> Dict[str, Any]:
        """Restore from TRASH."""
        ...

    def unarchive_message(
        self, message_id: str, prior_labels: List[str]
    ) -> Dict[str, Any]:
        """Reverse an archive: restore the message to the inbox.

        ``message_id`` is the id valid NOW (post-archive for folder-based
        backends like Outlook, where archive changed the id). Returns the
        message resource; the id may change again for folder backends.
        """
        ...

    def permanent_delete(self, message_id: str) -> None:
        """Permanently delete (DELETE not recoverable). Use sparingly.

        ``LiveGmailBackend`` never actually issues this call (#2533) — Gmail
        gates it behind a full-mailbox scope GAIA does not request, so it
        fails loud with an actionable error instead. Kept on the Protocol
        for backend parity (Outlook's scope already covers it) and so a
        fake/test backend can still implement it directly.
        """
        ...

    def create_draft(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        headers: Optional[Dict[str, str]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Create a draft and return its id.

        ``attachments``: optional list of dicts, each with ``filename`` (str),
        ``mime_type`` (str), and ``content`` (raw bytes) — #1542.
        """
        ...

    def send_draft(self, draft_id: str) -> Dict[str, Any]:
        """Send a previously-created draft."""
        ...

    def send_message(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        headers: Optional[Dict[str, str]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Send a one-shot message (no draft step). ``attachments`` as in
        :meth:`create_draft`."""
        ...

    def create_label(
        self, *, name: str, label_list_visibility: str = "labelShow"
    ) -> Dict[str, Any]:
        """Create a new user-defined label."""
        ...


# ---------------------------------------------------------------------------
# MIME body decoder — used by both Live and Fake backends
# ---------------------------------------------------------------------------


class _HTMLStripper(HTMLParser):
    """Tag-stripper that drops <script> and <style> bodies entirely.

    The plain-text fallback that goes into the LLM prompt MUST NOT
    contain CSS rules ("a {color:red}") — they bloat the context and,
    more importantly, would let an attacker inject what looks like
    natural language inside a ``<style>`` block. We discard the entire
    body of those tags.
    """

    _DROP_TAGS = {"script", "style", "head", "meta"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._suppress_depth = 0

    def handle_starttag(self, tag, attrs):  # noqa: D401 — HTMLParser API
        if tag.lower() in self._DROP_TAGS:
            self._suppress_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in self._DROP_TAGS and self._suppress_depth > 0:
            self._suppress_depth -= 1

    def handle_data(self, data):
        if self._suppress_depth == 0:
            self._chunks.append(data)

    def get_text(self) -> str:
        return " ".join(part.strip() for part in self._chunks if part.strip())


def _decode_charset(
    payload: bytes, declared_charset: Optional[str]
) -> Tuple[str, bool]:
    """Decode bytes with a charset fallback chain.

    Returns ``(text, fell_back)``. ``fell_back=True`` means the declared
    charset was missing/invalid OR the body declared an encoding it
    didn't actually use (cp1252 declared as us-ascii is common). The
    caller can attach ``charset_fallback: True`` to the attachment
    descriptor so the agent flags low-confidence body content.
    """
    candidates: list[str] = []
    if declared_charset:
        candidates.append(declared_charset)
    if "utf-8" not in {c.lower() for c in candidates}:
        candidates.append("utf-8")
    candidates.extend(["latin-1", "cp1252"])

    for idx, charset in enumerate(candidates):
        try:
            return payload.decode(charset), idx > 0
        except (UnicodeDecodeError, LookupError):
            continue
    # Last-resort: latin-1 with replace can never raise — every byte maps.
    return payload.decode("latin-1", errors="replace"), True


def decode_message_body(payload: dict) -> Tuple[str, List[Dict[str, Any]]]:
    """Recursively extract plain text from a Gmail API v1 ``payload`` dict.

    Operates on the ``payload`` object as returned by ``messages.get``.
    Walks ``parts`` recursively, prefers ``text/plain`` over
    ``text/html``, descends into ``message/rfc822`` (forwarded mail)
    and emits the inner body — NOT the raw RFC 2822 wrapper headers.

    HTML is converted to plain text via :class:`_HTMLStripper`. The
    LLM never sees raw HTML — context bloat and an indirect-prompt-
    injection vector via crafted ``<style>``/``<script>`` blocks.

    Header values are NOT re-decoded: the Gmail API already decodes
    encoded-word headers (RFC 2047) before returning them to us. The
    fake backend (which reads raw mbox) is responsible for decoding
    encoded-word headers into Unicode before constructing the
    Gmail-API-shape payload.

    Returns:
        (plain_text_body, attachment_descriptors)

        Each attachment descriptor is a dict::

            {
                "filename": str,
                "mime_type": str,
                "size_bytes": int,
                "attachment_id": str | None,  # body.attachmentId from Gmail
                "charset_fallback": bool,     # only set on text parts
            }
    """
    attachments: List[Dict[str, Any]] = []
    body_text = _walk_parts(payload, attachments)
    return body_text.strip(), attachments


def _walk_parts(
    part: dict,
    attachments: List[Dict[str, Any]],
) -> str:
    mime_type = (part.get("mimeType") or "").lower()
    body = part.get("body") or {}
    parts = part.get("parts") or []

    # Container types — recurse.
    if mime_type.startswith("multipart/"):
        # multipart/alternative: prefer text/plain over text/html. Other
        # multipart kinds: concatenate non-attachment children.
        if mime_type == "multipart/alternative":
            plain_text = ""
            html_text = ""
            for child in parts:
                child_mime = (child.get("mimeType") or "").lower()
                if child_mime == "text/plain":
                    plain_text = _walk_parts(child, attachments)
                elif child_mime == "text/html":
                    html_text = _walk_parts(child, attachments)
                elif child_mime.startswith("multipart/"):
                    # Nested multipart inside alternative; pick whichever
                    # comes back first.
                    nested = _walk_parts(child, attachments)
                    plain_text = plain_text or nested
            return plain_text or html_text
        else:
            chunks: list[str] = []
            for child in parts:
                chunks.append(_walk_parts(child, attachments))
            return "\n".join(c for c in chunks if c)

    # message/rfc822 — the forwarded body lives in ``parts[0].body``.
    if mime_type == "message/rfc822" and parts:
        return _walk_parts(parts[0], attachments)

    # Leaf content.
    if mime_type in ("text/plain", "text/html"):
        raw_b64 = body.get("data")
        if not raw_b64:
            return ""
        raw_bytes = base64.urlsafe_b64decode(_pad_b64(raw_b64))
        charset = _extract_charset(part.get("headers") or [])
        text, fell_back = _decode_charset(raw_bytes, charset)
        if mime_type == "text/html":
            stripper = _HTMLStripper()
            stripper.feed(text)
            text = stripper.get_text()
        if fell_back:
            attachments.append(
                {
                    "filename": "<inline-text>",
                    "mime_type": mime_type,
                    "size_bytes": len(raw_bytes),
                    "attachment_id": None,
                    "charset_fallback": True,
                }
            )
        return text

    # Anything else with a filename is an attachment.
    filename = part.get("filename") or ""
    if filename:
        attachments.append(
            {
                "filename": filename,
                "mime_type": mime_type or "application/octet-stream",
                "size_bytes": int(body.get("size", 0)),
                "attachment_id": body.get("attachmentId"),
            }
        )
    return ""


def _pad_b64(data: str) -> str:
    """Re-pad URL-safe base64 (Gmail strips ``=`` padding)."""
    pad = (-len(data)) % 4
    return data + ("=" * pad)


def _extract_charset(headers: List[Dict[str, str]]) -> Optional[str]:
    for h in headers:
        if (h.get("name") or "").lower() == "content-type":
            value = h.get("value") or ""
            for token in value.split(";"):
                token = token.strip()
                if token.lower().startswith("charset="):
                    return token.split("=", 1)[1].strip().strip('"').strip("'")
    return None


# ---------------------------------------------------------------------------
# Gmail batch HTTP (#2643 lever 2) — hand-rolled multipart/mixed request and
# response (de)serialization, mirroring the wire format Google's own
# api-client libraries use: each subrequest/subresponse is one embedded raw
# HTTP message inside a ``Content-Type: application/http`` MIME part.
# Correlation between a request and its response is by that part's own
# ``Content-ID`` — Google's docs do not promise response order matches
# request order — never by position.
# ---------------------------------------------------------------------------


def _build_batch_request_body(ids: List[str], *, format: str, boundary: str) -> bytes:
    """Build a multipart/mixed batch request body: one ``GET .../messages/{id}``
    subrequest per id, each tagged ``Content-ID: <item{index}>``."""
    query = f"format={format}"
    if format == "metadata":
        for h in METADATA_SCAN_HEADERS:
            query += f"&metadataHeaders={h}"
    parts: List[str] = []
    for i, mid in enumerate(ids):
        parts.append(
            f"--{boundary}\r\n"
            "Content-Type: application/http\r\n"
            f"Content-ID: <item{i}>\r\n"
            "\r\n"
            f"GET /gmail/v1/users/me/messages/{mid}?{query} HTTP/1.1\r\n"
            "\r\n"
        )
    parts.append(f"--{boundary}--\r\n")
    return "".join(parts).encode("utf-8")


_CONTENT_ID_INDEX_RE = re.compile(r"item(\d+)")
_HTTP_STATUS_LINE_RE = re.compile(rb"^HTTP/\d(?:\.\d)?\s+(\d+)")


def _parse_embedded_http_response(raw: bytes) -> Tuple[int, bytes]:
    """Parse one embedded raw HTTP/1.1 response: status line + headers +
    blank line + body. Returns ``(status_code, body_bytes)`` — header lines
    are discarded, since every batch response body here is JSON and the
    caller only needs the status and the payload.
    """
    raw = raw.lstrip(b"\r\n")
    line_end = raw.find(b"\n")
    status_line = raw[:line_end] if line_end != -1 else raw
    match = _HTTP_STATUS_LINE_RE.match(status_line.rstrip(b"\r"))
    if not match:
        raise ValueError(
            f"no parseable HTTP status line in batch response part: "
            f"{status_line[:120]!r}"
        )
    status = int(match.group(1))
    sep = raw.find(b"\r\n\r\n")
    sep_len = 4
    if sep == -1:
        sep = raw.find(b"\n\n")
        sep_len = 2
    body = raw[sep + sep_len :] if sep != -1 else b""
    return status, body


def _parse_batch_response(
    content_type: str, body: bytes
) -> List[Tuple[int, int, bytes]]:
    """Parse a Gmail batch multipart/mixed response.

    Returns ``[(item_index, http_status, json_body_bytes), ...]`` — the
    index is parsed from each part's own ``Content-ID`` (``response-item{N}``
    or any string containing ``item{N}``), never assumed from the part's
    position in the response, so a reordered response still correlates
    correctly to the original request.
    """
    synthetic = (
        b"Content-Type: "
        + content_type.encode("ascii", errors="replace")
        + b"\r\n\r\n"
        + body
    )
    parsed = _email_parser.message_from_bytes(synthetic)
    if not parsed.is_multipart():
        raise ValueError(
            f"batch response was not multipart (Content-Type: {content_type!r})"
        )
    out: List[Tuple[int, int, bytes]] = []
    for part in parsed.get_payload():
        content_id = part.get("Content-ID", "") or ""
        id_match = _CONTENT_ID_INDEX_RE.search(content_id)
        if not id_match:
            raise ValueError(
                f"batch response part had no parseable Content-ID: {content_id!r}"
            )
        idx = int(id_match.group(1))
        raw = part.get_payload(decode=True)
        if raw is None:
            payload_text = part.get_payload()
            raw = (
                payload_text.encode("utf-8")
                if isinstance(payload_text, str)
                else b""
            )
        status, json_body = _parse_embedded_http_response(raw)
        out.append((idx, status, json_body))
    return out


# ---------------------------------------------------------------------------
# LiveGmailBackend
# ---------------------------------------------------------------------------


class LiveGmailBackend:
    """Concrete ``GmailBackend`` that hits the real Gmail REST API."""

    def __init__(
        self,
        access_token_fn: Callable[[], str],
        *,
        http_client: Optional[httpx.Client] = None,
        timeout_seconds: float = 15.0,
    ):
        self._access_token_fn = access_token_fn
        # Allow tests to inject a transport without touching network. The
        # test fixture passes an ``httpx.MockTransport``-backed client.
        self._client = http_client or httpx.Client(timeout=timeout_seconds)

    # -- HTTP helpers -------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        # Re-fetch on every request — the connectors token cache makes this
        # cheap, but we MUST re-check every time so a mid-paginated revoke
        # surfaces as AUTH_REQUIRED, not as a stale-token 401.
        token = self._access_token_fn()
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _unauthorized_message(where: str) -> str:
        """401 remediation, mode-aware (#2159).

        In forwarded mode the connection is valid host-side — the daemon owns
        the refresh token and re-forwards fresh access tokens automatically — so
        a 401 means the forwarded token went stale, NOT that the user must
        reconnect. Telling them to reconnect (as the single old message did)
        sends them down a dead end while the daemon's re-forward timer fixes it
        on its own. Standalone mode keeps the reconnect guidance because there
        the sidecar owns its own OAuth.
        """
        from gaia_agent_email import forwarded_credentials

        if forwarded_credentials.is_forwarding_enabled():
            return (
                "Gmail API returned 401 with a daemon-forwarded token. The "
                "connection is still valid host-side — the daemon re-forwards a "
                "fresh access token automatically, so no reconnect is needed; "
                "retry in a moment. If it persists, the Google connection may "
                "have been revoked — check Settings → Connectors. (where: "
                + where
                + ")"
            )
        return (
            "Gmail API returned 401. The access token may have expired or "
            "scopes were revoked. Reconnect Google in Settings → Connectors. "
            "(where: " + where + ")"
        )

    def _raise_http(self, response: httpx.Response, where: str) -> None:
        # Construct the error message from status + truncated body ONLY.
        # Never from the wrapper exception, which would expose the
        # Authorization header.
        if response.status_code == 401:
            raise ConnectorsError(self._unauthorized_message(where))
        enable_url = access_not_configured_url(response)
        if enable_url:
            raise ConnectorsError(
                access_not_configured_message("Gmail API", enable_url)
            )
        raise ConnectorsError(
            f"Gmail API {where} returned {response.status_code}: "
            f"{response.text[:300]}"
        )

    def _get(self, path: str, *, params: Optional[dict] = None) -> Any:
        resp = self._client.get(
            f"{GMAIL_API_BASE}{path}", headers=self._headers(), params=params
        )
        if resp.status_code != 200:
            self._raise_http(resp, f"GET {path}")
        return resp.json()

    def _post(self, path: str, *, json_body: Optional[dict] = None) -> Any:
        resp = self._client.post(
            f"{GMAIL_API_BASE}{path}", headers=self._headers(), json=json_body
        )
        if resp.status_code not in (200, 201, 204):
            self._raise_http(resp, f"POST {path}")
        return resp.json() if resp.text else {}

    def _delete(self, path: str) -> None:
        resp = self._client.delete(f"{GMAIL_API_BASE}{path}", headers=self._headers())
        if resp.status_code not in (200, 204):
            self._raise_http(resp, f"DELETE {path}")

    # -- Read APIs ----------------------------------------------------------

    def get_user_email(self) -> str:
        data = self._get("/profile")
        return data.get("emailAddress", "")

    def list_messages(
        self,
        *,
        query: Optional[str] = None,
        label_ids: Optional[Iterable[str]] = None,
        max_results: int = 25,
        page_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"maxResults": max_results}
        if query:
            params["q"] = query
        if label_ids:
            params["labelIds"] = list(label_ids)
        if page_token:
            params["pageToken"] = page_token
        data = self._get("/messages", params=params)
        # Gmail returns no "messages" key on empty inbox — normalize.
        return {
            "messages": data.get("messages", []),
            "nextPageToken": data.get("nextPageToken"),
            "resultSizeEstimate": data.get("resultSizeEstimate", 0),
        }

    def get_message(self, message_id: str, *, format: str = "full") -> Dict[str, Any]:
        params: Dict[str, Any] = {"format": format}
        if format == "metadata":
            # Explicit, narrow header list -- Gmail's metadataHeaders filter
            # (#2643) so the response never carries more than the scan needs.
            params["metadataHeaders"] = list(METADATA_SCAN_HEADERS)
        return self._get(f"/messages/{message_id}", params=params)

    def get_messages_batch(
        self, message_ids: Iterable[str], *, format: str = "full"
    ) -> Dict[str, Dict[str, Any]]:
        """Fetch multiple messages via Gmail's batch HTTP endpoint (#2643).

        Chunks at ``_BATCH_MAX_SUBREQUESTS`` (Gmail's documented per-batch
        subrequest ceiling). A single id skips the batch endpoint entirely —
        multipart framing overhead is pure waste for one message — and calls
        ``get_message`` directly. Every requested id is guaranteed a
        corresponding entry in the returned map or the call raises
        ``ConnectorsError``; a batch response with fewer parts than requested,
        an unparseable part, or a non-2xx embedded response is never silently
        dropped or substituted with a partial result.
        """
        ids = list(message_ids)
        if not ids:
            return {}
        if len(ids) == 1:
            return {ids[0]: self.get_message(ids[0], format=format)}
        out: Dict[str, Dict[str, Any]] = {}
        for start in range(0, len(ids), _BATCH_MAX_SUBREQUESTS):
            chunk = ids[start : start + _BATCH_MAX_SUBREQUESTS]
            out.update(self._batch_get_messages(chunk, format=format))
        return out

    def _batch_get_messages(
        self, ids: List[str], *, format: str
    ) -> Dict[str, Dict[str, Any]]:
        boundary = f"batch_{uuid.uuid4().hex}"
        body = _build_batch_request_body(ids, format=format, boundary=boundary)
        headers = self._headers()
        headers["Content-Type"] = f'multipart/mixed; boundary="{boundary}"'
        resp = self._client.post(GMAIL_BATCH_URL, headers=headers, content=body)
        if resp.status_code != 200:
            self._raise_http(resp, "POST /batch/gmail/v1")
        try:
            parts = _parse_batch_response(
                resp.headers.get("content-type", ""), resp.content
            )
        except ValueError as exc:
            raise ConnectorsError(
                f"Gmail batch response for {len(ids)} message(s) could not be "
                f"parsed: {exc}"
            ) from exc
        by_index = {idx: (status, payload) for idx, status, payload in parts}
        out: Dict[str, Dict[str, Any]] = {}
        for i, mid in enumerate(ids):
            if i not in by_index:
                raise ConnectorsError(
                    f"Gmail batch response had no part for message {mid!r} "
                    f"(request index {i}) — {len(parts)} of {len(ids)} parts "
                    "returned"
                )
            status, payload = by_index[i]
            if status != 200:
                raise ConnectorsError(
                    f"Gmail batch GET for message {mid!r} returned {status}: "
                    f"{payload[:300].decode('utf-8', errors='replace')!r}"
                )
            try:
                out[mid] = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise ConnectorsError(
                    f"Gmail batch response for message {mid!r} was not valid "
                    f"JSON: {exc}"
                ) from exc
        return out

    def get_thread(self, thread_id: str) -> Dict[str, Any]:
        return self._get(f"/threads/{thread_id}", params={"format": "full"})

    def list_labels(self) -> List[Dict[str, Any]]:
        data = self._get("/labels")
        return data.get("labels", [])

    def get_label(self, label_id: str) -> Dict[str, Any]:
        # labels().get (NOT list — list returns the minimal form with no
        # counts) — the label resource's messagesTotal/messagesUnread are
        # exact integers, unlike list_messages's resultSizeEstimate (#2584).
        return self._get(f"/labels/{label_id}")

    # -- Mutate APIs --------------------------------------------------------

    def _modify_labels(
        self,
        message_id: str,
        *,
        add: Optional[Iterable[str]] = None,
        remove: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {}
        if add:
            body["addLabelIds"] = list(add)
        if remove:
            body["removeLabelIds"] = list(remove)
        return self._post(f"/messages/{message_id}/modify", json_body=body)

    def archive_message(self, message_id: str) -> Dict[str, Any]:
        return self._modify_labels(message_id, remove=[GMAIL_LABEL_INBOX])

    def mark_read(self, message_id: str) -> Dict[str, Any]:
        return self._modify_labels(message_id, remove=[GMAIL_LABEL_UNREAD])

    def mark_unread(self, message_id: str) -> Dict[str, Any]:
        return self._modify_labels(message_id, add=[GMAIL_LABEL_UNREAD])

    def add_star(self, message_id: str) -> Dict[str, Any]:
        return self._modify_labels(message_id, add=[GMAIL_LABEL_STARRED])

    def remove_star(self, message_id: str) -> Dict[str, Any]:
        return self._modify_labels(message_id, remove=[GMAIL_LABEL_STARRED])

    def add_label(self, message_id: str, label_id: str) -> Dict[str, Any]:
        return self._modify_labels(message_id, add=[label_id])

    def remove_label(self, message_id: str, label_id: str) -> Dict[str, Any]:
        return self._modify_labels(message_id, remove=[label_id])

    def trash_message(self, message_id: str) -> Dict[str, Any]:
        return self._post(f"/messages/{message_id}/trash")

    def untrash_message(self, message_id: str) -> Dict[str, Any]:
        # Gmail untrash clears TRASH but does not re-add INBOX, so a
        # quarantine/soft-delete undo would land in All Mail. Restore the inbox
        # view so undo returns the message to its original state.
        self._post(f"/messages/{message_id}/untrash")
        return self._modify_labels(message_id, add=[GMAIL_LABEL_INBOX])

    def unarchive_message(
        self, message_id: str, prior_labels: List[str]
    ) -> Dict[str, Any]:
        # Archive removes only the INBOX label, so its exact inverse re-adds
        # only INBOX (idempotent if already present). ``prior_labels`` is unused
        # — kept for Protocol parity: the message still carries every other
        # label, and re-applying immutable system labels like SENT/DRAFT is
        # rejected by Gmail's modify API ("Invalid label: SENT").
        return self._modify_labels(message_id, add=[GMAIL_LABEL_INBOX])

    def permanent_delete(self, message_id: str) -> None:
        # #2533: DELETE /messages/{id} requires the https://mail.google.com/
        # full-mailbox scope, which GAIA deliberately never requests (see
        # scopes.SCOPE_GMAIL_FULL_MAILBOX) — every call would 403
        # ACCESS_TOKEN_SCOPE_INSUFFICIENT. Fail loud here, before any HTTP
        # call, with an actionable message naming the missing scope — never
        # let the user hit a raw 403 after already confirming an
        # "irreversible" action. No agent tool calls this today (the
        # ``permanent_delete`` tool was removed from the agent's registered
        # capabilities for the same reason), but the Protocol method must
        # still fail safely for any direct caller.
        raise ScopeMismatchError(
            required=[SCOPE_GMAIL_FULL_MAILBOX],
            granted=list(GMAIL_SCOPES),
            provider="google",
            message=(
                "Permanently deleting a Gmail message requires the "
                f"{SCOPE_GMAIL_FULL_MAILBOX!r} scope, which GAIA does not "
                "request — it would grant full-mailbox delete access for "
                "every GAIA agent. Move the message to Trash instead "
                "(trash_message); Gmail keeps it recoverable there for 30 "
                "days and it can be restored any time with "
                "restore_trashed_message."
            ),
        )

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
        raw = _build_rfc822(
            to=to,
            subject=subject,
            body=body,
            extra_headers=headers,
            attachments=attachments,
        )
        return self._post(
            "/drafts",
            json_body={"message": {"raw": raw}},
        )

    def send_draft(self, draft_id: str) -> Dict[str, Any]:
        return self._post("/drafts/send", json_body={"id": draft_id})

    def send_message(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        headers: Optional[Dict[str, str]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        raw = _build_rfc822(
            to=to,
            subject=subject,
            body=body,
            extra_headers=headers,
            attachments=attachments,
        )
        return self._post(
            "/messages/send",
            json_body={"raw": raw},
        )

    def create_label(
        self,
        *,
        name: str,
        label_list_visibility: str = "labelShow",
    ) -> Dict[str, Any]:
        return self._post(
            "/labels",
            json_body={
                "name": name,
                "labelListVisibility": label_list_visibility,
                "messageListVisibility": "show",
            },
        )


def _build_rfc822(
    *,
    to: str,
    subject: str,
    body: str,
    extra_headers: Optional[Dict[str, str]] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Build an RFC 2822 message wrapped in URL-safe base64.

    Used for ``drafts.create``, ``drafts.send``, ``messages.send``. We
    construct manually instead of using ``email.message.EmailMessage`` to
    avoid double-encoding edge cases — Gmail expects the raw bytes
    base64url-encoded in a single ``raw`` field.

    With ``attachments`` (dicts of ``filename``/``mime_type``/``content``
    bytes, #1542) the message becomes ``multipart/mixed``: the text body
    first, then one base64 part per attachment. Without them the output is
    byte-identical to the pre-2.2 single-part shape.
    """
    headers = {
        "To": to,
        "Subject": subject,
        "Content-Type": 'text/plain; charset="utf-8"',
    }
    if extra_headers:
        headers.update(extra_headers)
    boundary = ""
    if attachments:
        boundary = f"=_gaia_{uuid.uuid4().hex}"
        headers["MIME-Version"] = "1.0"
        headers["Content-Type"] = f'multipart/mixed; boundary="{boundary}"'
    # Defense-in-depth against CRLF header injection. ``to`` and
    # ``subject`` can be LLM-decided or lifted from inbound mail (e.g.
    # ``forward_message_impl`` passes the original Subject verbatim).
    # A CRLF in any header value terminates the current header and starts
    # a new one, which an attacker could use to inject ``Bcc:``, ``Cc:``,
    # or body-prefix lines. Attachment filenames land in part headers, so
    # they get the same check.
    for k, v in headers.items():
        if "\r" in v or "\n" in v:
            raise ValueError(
                f"refusing to send: header {k!r} contains a newline — "
                f"possible CRLF injection attempt"
            )
    for att in attachments or []:
        fname = att["filename"]
        if any(c in fname for c in ("\r", "\n", '"')):
            raise ValueError(
                f"refusing to send: attachment filename {fname!r} contains a "
                f"newline or quote — possible header injection attempt"
            )
    header_block = "\r\n".join(f"{k}: {v}" for k, v in headers.items())
    if not attachments:
        rfc822 = f"{header_block}\r\n\r\n{body}"
    else:
        parts = [
            f"--{boundary}\r\n"
            f'Content-Type: text/plain; charset="utf-8"\r\n'
            f"\r\n"
            f"{body}"
        ]
        for att in attachments:
            content_b64 = base64.b64encode(att["content"]).decode("ascii")
            # RFC 2045 wants encoded lines capped at 76 chars.
            wrapped = "\r\n".join(
                content_b64[i : i + 76] for i in range(0, len(content_b64), 76)
            )
            parts.append(
                f"--{boundary}\r\n"
                f'Content-Type: {att["mime_type"]}; name="{att["filename"]}"\r\n'
                f'Content-Disposition: attachment; filename="{att["filename"]}"\r\n'
                f"Content-Transfer-Encoding: base64\r\n"
                f"\r\n"
                f"{wrapped}"
            )
        mime_body = "\r\n".join(parts) + f"\r\n--{boundary}--"
        rfc822 = f"{header_block}\r\n\r\n{mime_body}"
    return base64.urlsafe_b64encode(rfc822.encode("utf-8")).decode("ascii").rstrip("=")


# ---------------------------------------------------------------------------
# Module-level token resolver
# ---------------------------------------------------------------------------


def _get_gmail_token() -> str:
    """Return a Gmail access token, honoring the sidecar's runtime mode.

    Forwarded mode (daemon deployment, #2154): the daemon-forwarded access token
    — the sidecar never reads the keyring/grants store. Standalone mode: the
    normal grant-checked connectors path (``get_access_token_sync``). Both raise
    loudly on no-grant / missing-scope / expiry — never a silent empty token.

    Module-level (not a method) so it mirrors ``connectors_demo`` and can be
    unit-tested without instantiating the agent. Requests ``GMAIL_SCOPES`` (a
    NARROWER set than ``ALL_SCOPES``), so a user who declines calendar can still
    use Gmail tools.
    """
    from gaia_agent_email import forwarded_credentials

    return forwarded_credentials.resolve_access_token(
        "google",
        list(GMAIL_SCOPES),
        live_fetch=lambda: get_access_token_sync(
            provider="google",
            agent_id=AGENT_NAMESPACED_ID,
            scopes=list(GMAIL_SCOPES),
        ),
    )


__all__ = [
    "GMAIL_API_BASE",
    "GMAIL_BATCH_URL",
    "GMAIL_LABEL_INBOX",
    "GMAIL_LABEL_STARRED",
    "GMAIL_LABEL_UNREAD",
    "METADATA_SCAN_HEADERS",
    "GmailBackend",
    "LiveGmailBackend",
    "_get_gmail_token",
    "decode_message_body",
]
