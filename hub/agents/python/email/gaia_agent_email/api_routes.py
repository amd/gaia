# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
REST surface for the Email Triage Agent (#1229).

A consuming application invokes the email agent over HTTP through these
endpoints, mounted on the existing OpenAI-compatible FastAPI app
(``gaia.api.openai_server``):

    POST /v1/email/triage  — single email or full thread in (the FROZEN
                             #1262 contract), structured triage result out
                             (category, summary, action items, optional
                             draft proposal).
    POST /v1/email/draft   — propose a reply and obtain a single-use
                             confirmation token bound to the exact
                             ``(to, subject, body)`` payload.
    POST /v1/email/send    — send a reply. REJECTED with a 4xx unless a
                             valid confirmation token for *this* payload is
                             supplied. This is the send-confirmation gate
                             (#1264) translated to the API boundary — the
                             same rule the agent enforces via
                             ``TOOLS_REQUIRING_CONFIRMATION`` /
                             ``console.confirm_tool_execution``.

Design commitments
------------------
- **Reuse the frozen contract.** ``/v1/email/triage`` takes and returns the
  exact pydantic models from ``gaia_agent_email.contract`` — no parallel
  schema. A consuming app that drifts from the contract gets a 422.
- **Reuse the agent's real categorizer.** Category / spam / phishing come
  from ``triage_heuristics.classify_category_heuristic`` — the same
  deterministic fast-path the agent runs pre-LLM — not a reimplementation.
- **Fail loudly, never auto-confirm.** A send without a valid, payload-bound
  confirmation token raises a 403 with an actionable message. The token is
  single-use and bound to the payload, so a stale token cannot authorize a
  different message (no bait-and-switch).
- **No live mail in tests.** The send backend is a FastAPI dependency
  (:func:`get_send_backend`) so tests inject ``FakeGmailBackend`` via
  ``app.dependency_overrides``; production resolves the live Gmail backend
  and fails loudly if it is unavailable.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from gaia_agent_email.contract import (
    ActionItem,
    DraftReply,
    EmailAddress,
    EmailCategory,
    EmailMessage,
    EmailTriageRequest,
    EmailTriageResponse,
    EmailTriageResult,
    SingleEmailInput,
    ThreadInput,
)
from gaia_agent_email.tools.triage_heuristics import classify_category_heuristic
from gaia.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/v1/email", tags=["email"])


# ---------------------------------------------------------------------------
# Deterministic triage service
# ---------------------------------------------------------------------------

# Phrases that mark a line as an action the principal must take. Deliberately
# small and explicit — this is a deterministic extractor, not an LLM. The
# agent's LLM path produces richer action items; this fast-path covers the
# obvious imperative cues so the REST surface returns useful structure
# without a model round-trip.
_ACTION_CUES = (
    "please ",
    "can you ",
    "could you ",
    "kindly ",
    "let me know",
    "reply by",
    "respond by",
    "rsvp",
    "action required",
    "follow up",
    "follow-up",
    "review the",
    "send me",
    "send the",
    "confirm ",
)

# Crude due-date hints surfaced verbatim (never parsed — the contract's
# ``due_hint`` is explicitly free-text).
_DUE_HINT_RE = re.compile(
    r"\b(by\s+(?:eod|cob|tomorrow|today|monday|tuesday|wednesday|thursday|"
    r"friday|saturday|sunday|next\s+\w+|\d{1,2}(?::\d{2})?\s*(?:am|pm)?))",
    re.IGNORECASE,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_MAX_SUMMARY_CHARS = 300


def _split_sentences(text: str) -> List[str]:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]


class EmailTriageService:
    """Convert contract inputs (or raw Gmail-API messages) into a contract
    :class:`EmailTriageResult` deterministically.

    No LLM is invoked: category comes from the agent's heuristic
    categorizer, and the summary / action items / draft proposal are derived
    from the message text with explicit rules. This keeps the REST surface
    fast, offline-testable, and aligned with the agent's pre-LLM fast path.
    """

    # -- Public: contract path ---------------------------------------------

    def triage_request(self, request: EmailTriageRequest) -> EmailTriageResponse:
        """Triage a contract request envelope into a contract response."""
        payload = request.payload
        if isinstance(payload, SingleEmailInput):
            result = self._triage_single(payload)
            kind = "single"
        elif isinstance(payload, ThreadInput):
            result = self._triage_thread(payload)
            kind = "thread"
        else:  # pragma: no cover - discriminated union guarantees one of the two
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported payload kind: {getattr(payload, 'kind', '?')!r}",
            )
        return EmailTriageResponse(request_kind=kind, result=result)

    def triage_gmail_message(
        self, msg: dict, *, principal_email: str
    ) -> EmailTriageResult:
        """Triage a Gmail-API-shaped message (e.g. from ``get_message_impl``).

        Used by the agent-pipeline e2e to assert the REST surface's
        summarizer agrees with what the agent's read tools fetch.
        """
        from gaia_agent_email.gmail_backend import decode_message_body

        payload = msg.get("payload") or {}
        headers = {
            (h.get("name") or "").lower(): h.get("value", "")
            for h in payload.get("headers", [])
        }
        body, _attachments = decode_message_body(payload)
        subject = headers.get("subject", "")
        sender = headers.get("from", "")
        label_ids = list(msg.get("labelIds", []))
        principal = EmailAddress(email=principal_email)
        return self._build_result(
            subject=subject,
            sender_raw=sender,
            body=body,
            label_ids=label_ids,
            principal=principal,
            reply_to=_parse_address(sender),
        )

    # -- Internal -----------------------------------------------------------

    def _triage_single(self, payload: SingleEmailInput) -> EmailTriageResult:
        msg = payload.message
        return self._build_result(
            subject=msg.subject,
            sender_raw=_format_address(msg.from_),
            body=msg.body,
            label_ids=[],
            principal=payload.principal,
            reply_to=msg.from_,
        )

    def _triage_thread(self, payload: ThreadInput) -> EmailTriageResult:
        # Summarize the whole thread; categorize on the LAST inbound message
        # (the one awaiting the principal's attention), reply to its sender.
        messages: List[EmailMessage] = payload.messages
        last = messages[-1]
        combined_body = "\n\n".join(
            f"{_format_address(m.from_)}: {m.body}" for m in messages
        )
        result = self._build_result(
            subject=last.subject,
            sender_raw=_format_address(last.from_),
            body=combined_body,
            label_ids=[],
            principal=payload.principal,
            reply_to=last.from_,
            summary_prefix=f"Thread of {len(messages)} messages. ",
        )
        return result

    def _build_result(
        self,
        *,
        subject: str,
        sender_raw: str,
        body: str,
        label_ids: List[str],
        principal: EmailAddress,
        reply_to: Optional[EmailAddress],
        summary_prefix: str = "",
    ) -> EmailTriageResult:
        heuristic = classify_category_heuristic(
            subject=subject, sender=sender_raw, label_ids=label_ids
        )
        category = EmailCategory(heuristic.category)
        summary = summary_prefix + self._summarize(subject, body)
        action_items = self._extract_action_items(body)
        draft = self._build_draft(
            subject=subject,
            reply_to=reply_to,
            principal=principal,
            is_spam=heuristic.is_spam,
            is_phishing=heuristic.is_phishing,
        )
        return EmailTriageResult(
            category=category,
            is_spam=heuristic.is_spam,
            is_phishing=heuristic.is_phishing,
            summary=summary,
            action_items=action_items,
            draft=draft,
        )

    def _summarize(self, subject: str, body: str) -> str:
        sentences = _split_sentences(body)
        lead = " ".join(sentences[:2]) if sentences else ""
        if subject and lead:
            summary = f"{subject.strip()} — {lead}"
        elif subject:
            summary = subject.strip()
        elif lead:
            summary = lead
        else:
            summary = "(no content)"
        if len(summary) > _MAX_SUMMARY_CHARS:
            summary = summary[: _MAX_SUMMARY_CHARS - 1].rstrip() + "…"
        return summary

    def _extract_action_items(self, body: str) -> List[ActionItem]:
        items: List[ActionItem] = []
        seen: set[str] = set()
        for sentence in _split_sentences(body):
            low = sentence.lower()
            if not any(cue in low for cue in _ACTION_CUES):
                continue
            normalized = sentence.strip()
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            due_match = _DUE_HINT_RE.search(sentence)
            due_hint = due_match.group(1) if due_match else None
            items.append(ActionItem(description=normalized, due_hint=due_hint))
        return items

    def _build_draft(
        self,
        *,
        subject: str,
        reply_to: Optional[EmailAddress],
        principal: EmailAddress,
        is_spam: bool,
        is_phishing: bool,
    ) -> Optional[DraftReply]:
        # Never propose a reply to spam/phishing — surfacing a draft would
        # nudge the user toward engaging with a hostile sender.
        if is_spam or is_phishing or reply_to is None:
            return None
        # Don't propose replying to yourself (e.g. a thread whose last message
        # the principal sent) — there is no one to reply to.
        if reply_to.email.lower() == principal.email.lower():
            return None
        reply_subject = subject.strip()
        if reply_subject and not reply_subject.lower().startswith("re:"):
            reply_subject = f"Re: {reply_subject}"
        elif not reply_subject:
            reply_subject = "Re:"
        return DraftReply(
            to=[reply_to],
            subject=reply_subject,
            body="",  # proposal scaffold; the user/agent fills the body in
        )


def _format_address(addr: EmailAddress) -> str:
    if addr.name:
        return f"{addr.name} <{addr.email}>"
    return addr.email


def _parse_address(raw: str) -> Optional[EmailAddress]:
    """Parse a raw ``From`` header (``"Alice <a@b.com>"``) into an
    :class:`EmailAddress`. Returns None when no plausible address is found.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    m = re.search(r"<([^>]+)>", raw)
    if m:
        email = m.group(1).strip()
        name = raw[: m.start()].strip().strip('"') or None
    else:
        email = raw
        name = None
    try:
        return EmailAddress(name=name, email=email)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Confirmation-token store (the send-confirmation gate)
# ---------------------------------------------------------------------------


def _payload_fingerprint(to: List[EmailAddress], subject: str, body: str) -> str:
    """A stable fingerprint of the exact message a token authorizes.

    Binding the token to the payload means a token issued for one message
    cannot be replayed to send different content.
    """
    recipients = ",".join(sorted(a.email.lower() for a in to))
    material = "\x1f".join([recipients, subject, body])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class ConfirmationStore:
    """Single-use confirmation tokens bound to a message fingerprint.

    A token is minted by the draft endpoint and consumed by the send
    endpoint. Consuming a token removes it (single-use). The server-side
    secret makes tokens unforgeable; the fingerprint makes them payload-
    specific.
    """

    def __init__(self, secret: Optional[bytes] = None):
        self._secret = secret or secrets.token_bytes(32)
        self._lock = threading.Lock()
        # token -> fingerprint it authorizes
        self._tokens: dict[str, str] = {}

    def issue(self, fingerprint: str) -> str:
        token = hmac.new(
            self._secret, (fingerprint + secrets.token_hex(8)).encode("utf-8"), "sha256"
        ).hexdigest()
        with self._lock:
            self._tokens[token] = fingerprint
        return token

    def consume(self, token: str, fingerprint: str) -> bool:
        """Validate and consume a token for ``fingerprint``.

        Returns True only when the token was issued for exactly this
        payload. The token is removed on a successful match (single-use).
        A blank/unknown token, or a token issued for a different payload,
        returns False and is NOT consumed.
        """
        if not token:
            return False
        with self._lock:
            expected = self._tokens.get(token)
            if expected is None:
                return False
            if not hmac.compare_digest(expected, fingerprint):
                # Right token, wrong payload — do not consume; reject.
                return False
            del self._tokens[token]
            return True


# Process-wide store. Tokens live only for the life of the server process —
# acceptable for a confirmation handshake (draft then send within a session).
confirmation_store = ConfirmationStore()


# ---------------------------------------------------------------------------
# Send-backend dependency (injectable for tests; fail-loud in production)
# ---------------------------------------------------------------------------


def get_send_backend():
    """Resolve the Gmail backend used by the send endpoint.

    Production builds the live backend and fails loudly if Google
    credentials are not connected — never silently no-ops a send. Tests
    override this via ``app.dependency_overrides[get_send_backend]`` to
    inject ``FakeGmailBackend`` so no live mail is touched.

    IMPORTANT: this is invoked from the send handler *after* the
    confirmation gate, NOT as a FastAPI ``Depends`` — a request that lacks a
    valid confirmation token must be rejected with a 4xx regardless of
    backend health, so the gate is always evaluated first. (Resolving the
    backend as a ``Depends`` would let a backend-unavailable 503 preempt the
    403, masking the missing-confirmation rejection.)
    """
    from gaia_agent_email.gmail_backend import LiveGmailBackend, _get_gmail_token

    try:
        return LiveGmailBackend(_get_gmail_token)
    except Exception as exc:  # noqa: BLE001 - boundary translation, re-raised
        raise HTTPException(
            status_code=503,
            detail=(
                "Email send backend unavailable: Google account is not "
                "connected. Connect Google via the connectors flow before "
                f"sending. ({type(exc).__name__}: {exc})"
            ),
        ) from exc


# Module-level indirection the send handler calls after the gate. Tests swap
# this (e.g. ``monkeypatch.setattr(email_routes, "resolve_send_backend",
# lambda: FakeGmailBackend())``) to inject a fake without touching live mail.
# Default is the fail-loud live resolver above.
resolve_send_backend = get_send_backend


# ---------------------------------------------------------------------------
# Send / draft request & response models (LOCAL — contract.py is frozen and
# triage-only; the send handshake is not part of the #1262 contract).
# ---------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmailDraftRequest(_Strict):
    """Propose a reply and obtain a confirmation token for it."""

    to: List[EmailAddress] = Field(
        ..., min_length=1, description="Proposed recipients (non-empty)."
    )
    subject: str = Field(..., description="Proposed subject line.")
    body: str = Field(..., description="Proposed reply body.")


class EmailDraftResponse(_Strict):
    """The proposed reply plus a single-use confirmation token bound to it."""

    draft: DraftReply = Field(
        ..., description="The proposed reply (to / subject / body)."
    )
    confirmation_token: str = Field(
        ...,
        description=(
            "Echo this back to POST /v1/email/send to authorize sending "
            "exactly this payload. Single-use; bound to (to, subject, body)."
        ),
    )


class EmailSendRequest(_Strict):
    """Send a reply. Requires a valid confirmation token for this payload."""

    to: List[EmailAddress] = Field(
        ..., min_length=1, description="Recipients (non-empty)."
    )
    subject: str = Field(..., description="Subject line.")
    body: str = Field(..., description="Reply body.")
    confirmation_token: Optional[str] = Field(
        default=None,
        description=(
            "Confirmation token from POST /v1/email/draft. A send without a "
            "valid token for this exact payload is rejected (403)."
        ),
    )


class EmailSendResponse(_Strict):
    sent_id: str = Field(..., description="Provider message id of the sent email.")
    to: List[EmailAddress] = Field(
        ..., description="Recipients the message was sent to."
    )
    subject: str = Field(..., description="Subject of the sent message.")
    sent: bool = Field(default=True, description="Always true on success.")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

_service = EmailTriageService()


@router.post("/triage", response_model=EmailTriageResponse)
async def triage_email(request: EmailTriageRequest) -> EmailTriageResponse:
    """Triage a single email or a full thread.

    Accepts the FROZEN #1262 ``EmailTriageRequest`` (a single-email or
    thread payload, discriminated on ``kind``) and returns the structured
    ``EmailTriageResponse`` — category, spam/phishing signals, a plain-text
    summary, extracted action items, and an optional draft-reply proposal.
    No mail is read or sent; this analyzes only the payload in the request.
    """
    return _service.triage_request(request)


@router.post("/draft", response_model=EmailDraftResponse)
async def draft_reply(request: EmailDraftRequest) -> EmailDraftResponse:
    """Propose a reply and mint a confirmation token bound to its payload.

    The returned token must be echoed to :func:`send_email` to authorize
    sending exactly this ``(to, subject, body)``. This is the explicit
    user-confirmation step — the consuming app surfaces the draft to the
    user, and only a user-approved send echoes the token back.
    """
    fingerprint = _payload_fingerprint(request.to, request.subject, request.body)
    token = confirmation_store.issue(fingerprint)
    draft = DraftReply(to=request.to, subject=request.subject, body=request.body)
    return EmailDraftResponse(draft=draft, confirmation_token=token)


@router.post("/send", response_model=EmailSendResponse)
async def send_email(request: EmailSendRequest) -> EmailSendResponse:
    """Send a reply — gated on explicit confirmation (#1264).

    The confirmation gate is enforced FIRST: a request without a valid,
    payload-bound confirmation token is rejected with HTTP 403 before any
    backend call (or even backend resolution). This mirrors the agent's
    ``TOOLS_REQUIRING_CONFIRMATION`` guard, translated to the API boundary,
    and guarantees the gate fires regardless of backend health. Never
    auto-confirms.
    """
    fingerprint = _payload_fingerprint(request.to, request.subject, request.body)
    if not confirmation_store.consume(request.confirmation_token or "", fingerprint):
        raise HTTPException(
            status_code=403,
            detail=(
                "Send rejected: missing or invalid confirmation token for this "
                "message. Call POST /v1/email/draft to obtain a confirmation "
                "token bound to this exact (to, subject, body), then echo it in "
                "'confirmation_token'. Emails are never sent without explicit "
                "confirmation."
            ),
        )

    # Gate passed — resolve the backend now (AFTER the gate) and send. Gmail's
    # send takes a single 'to' header string.
    backend = resolve_send_backend()
    to_header = ", ".join(_format_address(a) for a in request.to)
    result = backend.send_message(
        to=to_header, subject=request.subject, body=request.body
    )
    sent_id = result.get("id") or ""
    if not sent_id:
        raise HTTPException(
            status_code=502,
            detail="Email backend did not return a message id for the send.",
        )
    logger.info("email send: id=%s to=%s", sent_id, to_header)
    return EmailSendResponse(sent_id=sent_id, to=request.to, subject=request.subject)


@router.get("/spec", response_class=HTMLResponse, include_in_schema=False)
async def email_spec() -> HTMLResponse:
    """Serve the self-contained HTML endpoint spec page.

    Not included in the OpenAPI schema — this is a human-readable convenience
    page, not a machine-readable contract endpoint.
    """
    from gaia_agent_email.spec_html import render_endpoint_spec_html

    return HTMLResponse(content=render_endpoint_spec_html())


__all__ = [
    "router",
    "EmailTriageService",
    "ConfirmationStore",
    "confirmation_store",
    "get_send_backend",
    "EmailDraftRequest",
    "EmailDraftResponse",
    "EmailSendRequest",
    "EmailSendResponse",
    # Shared formatting helpers reused by the MCP surface.
    "_format_address",
    "_payload_fingerprint",
]
