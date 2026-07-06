# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
HTML spec generator for the Email Triage Agent REST endpoints (issue #1263).

``render_endpoint_spec_html()`` returns a single self-contained HTML page
documenting the email REST endpoints (triage, search, draft, send) and the
frozen #1262 contract request/response shapes. It derives field rows directly
from the contract pydantic models so the spec stays in sync automatically.

No external assets — inline CSS only. No LLM, no network calls.
"""

from __future__ import annotations

import html as _html_lib
import webbrowser
from pathlib import Path
from typing import (
    Annotated,
    Any,
    List,
    Optional,
    Tuple,
    Type,
    Union,
    get_args,
    get_origin,
)

from gaia_agent_email.contract import (
    SCHEMA_VERSION,
    ActionItem,
    AttachmentMeta,
    BatchItemError,
    BatchItemResult,
    BatchTriageRequest,
    BatchTriageResponse,
    CalendarCreateEventRequest,
    CalendarEvent,
    CalendarEventDateTime,
    CalendarEventPreviewResponse,
    CalendarEventResponse,
    CalendarEventsResponse,
    CalendarRespondRequest,
    CalendarRespondResponse,
    DraftReply,
    EmailActionConfirmRequest,
    EmailActionConfirmResponse,
    EmailAddress,
    EmailArchiveRequest,
    EmailArchiveResponse,
    EmailCategory,
    EmailMessage,
    EmailPreScanRequest,
    EmailPreScanResponse,
    EmailPreScanResult,
    EmailQuarantineRequest,
    EmailQuarantineResponse,
    EmailSearchRequest,
    EmailSearchResponse,
    EmailSearchResultItem,
    EmailTriageRequest,
    EmailTriageResponse,
    EmailTriageResult,
    EmailUnarchiveRequest,
    EmailUnarchiveResponse,
    EmailUnquarantineRequest,
    EmailUnquarantineResponse,
    OutgoingAttachment,
    PreScanItem,
    SingleEmailInput,
    ThreadInput,
)
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# The runtime type of ``None`` — used to drop the NoneType arm of Optional[X]
# unions when labelling a field's type. ``type(None)`` is the canonical way to
# obtain it; bound to a constant so the comparison reads ``is not _NONE_TYPE``.
_NONE_TYPE = type(None)

_INLINE_CSS = """
body {
  font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
               Helvetica, Arial, sans-serif;
  background: #0a0a0b;
  color: #f0f0ee;
  margin: 0;
  padding: 2rem;
  line-height: 1.6;
}
h1 {
  color: #e2a33e;
  font-size: 2rem;
  margin-bottom: 0.25rem;
  letter-spacing: -0.01em;
}
.subtitle {
  color: #8e8e92;
  font-size: 0.95rem;
  margin-bottom: 2.5rem;
}
h2 {
  color: #f0f0ee;
  margin-top: 2.5rem;
  margin-bottom: 0.5rem;
  font-size: 1.4rem;
}
h3 {
  color: #f0f0ee;
  margin-top: 1.5rem;
  margin-bottom: 0.4rem;
  font-size: 1.1rem;
}
.endpoint-block {
  background: #111113;
  border: 1px solid #1f1f22;
  border-radius: 12px;
  padding: 1.5rem;
  margin-bottom: 1.5rem;
}
.method-badge {
  display: inline-block;
  background: #e2a33e;
  color: #0a0a0b;
  font-size: 0.78rem;
  font-weight: 700;
  padding: 0.2rem 0.6rem;
  border-radius: 5px;
  letter-spacing: 0.05em;
  margin-right: 0.75rem;
  vertical-align: middle;
}
.path {
  font-family: "JetBrains Mono", "SF Mono", ui-monospace, Menlo, monospace;
  font-size: 1.05rem;
  color: #e2a33e;
  vertical-align: middle;
}
.desc {
  color: #8e8e92;
  font-size: 0.93rem;
  margin-top: 0.5rem;
}
table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 0.75rem;
  font-size: 0.9rem;
}
th {
  text-align: left;
  color: #8e8e92;
  font-weight: 600;
  border-bottom: 1px solid #1f1f22;
  padding: 0.4rem 0.6rem;
}
td {
  padding: 0.4rem 0.6rem;
  border-bottom: 1px solid #1f1f22;
  vertical-align: top;
}
td:first-child {
  font-family: "JetBrains Mono", "SF Mono", ui-monospace, Menlo, monospace;
  color: #e2a33e;
  white-space: nowrap;
}
td:nth-child(2) {
  color: #c9c9c6;
}
td:nth-child(3) {
  color: #8e8e92;
}
.required-badge {
  display: inline-block;
  font-size: 0.72rem;
  background: rgba(232, 122, 122, 0.14);
  color: #e87a7a;
  padding: 0.05rem 0.4rem;
  border-radius: 3px;
  margin-left: 0.4rem;
  vertical-align: middle;
}
.optional-badge {
  display: inline-block;
  font-size: 0.72rem;
  background: #1f1f22;
  color: #8e8e92;
  padding: 0.05rem 0.4rem;
  border-radius: 3px;
  margin-left: 0.4rem;
  vertical-align: middle;
}
.version-badge {
  display: inline-block;
  background: rgba(226, 163, 62, 0.12);
  color: #e2a33e;
  border: 1px solid rgba(226, 163, 62, 0.35);
  font-size: 0.8rem;
  padding: 0.2rem 0.75rem;
  border-radius: 999px;
  margin-left: 1rem;
  vertical-align: middle;
}
.category-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-top: 0.5rem;
}
.category-tag {
  background: rgba(226, 163, 62, 0.12);
  color: #e2a33e;
  border: 1px solid rgba(226, 163, 62, 0.35);
  border-radius: 999px;
  padding: 0.2rem 0.75rem;
  font-family: "JetBrains Mono", "SF Mono", ui-monospace, Menlo, monospace;
  font-size: 0.85rem;
}
.model-section {
  background: #0d0d0f;
  border: 1px solid #1f1f22;
  border-radius: 10px;
  padding: 1rem 1.25rem;
  margin-top: 1rem;
}
.footer {
  margin-top: 3rem;
  color: #8e8e92;
  font-size: 0.8rem;
  border-top: 1px solid #1f1f22;
  padding-top: 1rem;
}
"""


def _esc(text: str) -> str:
    return _html_lib.escape(str(text))


def _annotation_label(annotation: Any) -> str:
    """Render a typing annotation into a compact human-readable label.

    Recurses through Optional / List / Union so generics like
    ``Optional[List[EmailAddress]]`` render as ``list[EmailAddress]``
    rather than the bare outer name. NoneType arms (from Optional) are
    dropped so the label names the value type, not ``| None``.
    ``Annotated[X, …]`` (e.g. a discriminated-union list element) is unwrapped
    to ``X`` so the label names the value type, not the ``Annotated`` wrapper.
    """
    origin = get_origin(annotation)
    # Unwrap Annotated[X, metadata…] → X before any other handling, so a
    # discriminated-union element renders as the union, not 'Annotated[...'.
    if origin is Annotated:
        return _annotation_label(get_args(annotation)[0])
    if origin is None:
        # A concrete class (str, bool, EmailAddress, …) or a bare name.
        return getattr(annotation, "__name__", None) or str(annotation)

    args = [a for a in get_args(annotation) if a is not _NONE_TYPE]
    if origin is Union:
        # Optional[X] collapses to X; a real multi-arm Union joins with ' | '.
        if len(args) == 1:
            return _annotation_label(args[0])
        return " | ".join(_annotation_label(a) for a in args)
    if origin in (list, List):
        inner = _annotation_label(args[0]) if args else "any"
        return f"list[{inner}]"

    # Other generics (e.g. Literal): show the origin name with its args.
    origin_name = getattr(origin, "__name__", None) or str(origin)
    if args:
        inner = ", ".join(_annotation_label(a) for a in args)
        return f"{origin_name}[{inner}]"
    return origin_name


def _type_label(field_info: Any) -> str:
    """Best-effort human-readable type label from a pydantic FieldInfo."""
    annotation = getattr(field_info, "annotation", None)
    if annotation is None:
        return "any"
    return _annotation_label(annotation)


def _required_badge(field_info: Any) -> str:
    # pydantic v2's authoritative required check: a field with no default and
    # no default_factory has ``is_required()`` True. ``default`` is the
    # ``PydanticUndefined`` sentinel for required fields (NOT None), so testing
    # ``default is not None`` would mislabel every required field as optional.
    if field_info.is_required():
        return '<span class="required-badge">required</span>'
    return '<span class="optional-badge">optional</span>'


def _model_table(model: Type[BaseModel], title: str) -> str:
    rows: List[str] = []
    for name, info in model.model_fields.items():
        desc = (info.description or "").strip()
        type_label = _type_label(info)
        badge = _required_badge(info)
        rows.append(
            f"<tr>"
            f"<td>{_esc(name)}{badge}</td>"
            f"<td>{_esc(type_label)}</td>"
            f"<td>{_esc(desc)}</td>"
            f"</tr>"
        )

    table_html = (
        "<table>"
        "<thead><tr>"
        "<th>Field</th><th>Type</th><th>Description</th>"
        "</tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody>"
        "</table>"
    )
    return f'<div class="model-section"><h3>{_esc(title)}</h3>{table_html}</div>'


def _category_list_html() -> str:
    tags = "".join(
        f'<span class="category-tag">{_esc(cat.value)}</span>' for cat in EmailCategory
    )
    return f'<div class="category-list">{tags}</div>'


def _endpoint_block(
    path: str,
    description: str,
    request_sections: List[Tuple[str, Type[BaseModel]]],
    response_sections: List[Tuple[str, Type[BaseModel]]],
    extra_html: str = "",
    method: str = "POST",
) -> str:
    req_html = "".join(_model_table(m, t) for t, m in request_sections)
    resp_html = "".join(_model_table(m, t) for t, m in response_sections)
    # A GET (read-only) endpoint has no request body — show query params (if any)
    # via the request_sections heading text instead of a "Request body" header.
    req_heading = "Query parameters" if method.upper() == "GET" else "Request body"
    req_block = f"<h3>{req_heading}</h3>{req_html}" if req_html else ""
    return (
        f'<div class="endpoint-block">'
        f'<span class="method-badge">{_esc(method.upper())}</span>'
        f'<span class="path">{_esc(path)}</span>'
        f'<p class="desc">{_esc(description)}</p>'
        f"{extra_html}"
        f"{req_block}"
        f"<h3>Response body</h3>{resp_html}"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_endpoint_spec_html() -> str:
    """Return a self-contained HTML page documenting the Email Triage API.

    The page is built entirely from the frozen #1262 contract models —
    field names and descriptions are derived at call time so the spec
    stays in sync with contract changes automatically. No external assets;
    all CSS is inlined.
    """
    triage_extra = (
        "<p class='desc'>"
        "<strong>Payload discriminator:</strong> set <code>payload.kind</code> "
        'to <code>"single"</code> for a single message or '
        '<code>"thread"</code> for a conversation thread.</p>'
        "<h3>Category values (EmailCategory)</h3>"
        + _category_list_html()
        + _model_table(SingleEmailInput, "SingleEmailInput (kind: single)")
        + _model_table(ThreadInput, "ThreadInput (kind: thread)")
        + _model_table(EmailMessage, "EmailMessage")
        + _model_table(EmailAddress, "EmailAddress")
        + _model_table(AttachmentMeta, "AttachmentMeta (schema 2.2, #1542)")
    )

    triage_block = (
        f'<div class="endpoint-block">'
        f'<span class="method-badge">POST</span>'
        f'<span class="path">/v1/email/triage</span>'
        f'<p class="desc">Triage a single email or a full thread. '
        f"Accepts the frozen #1262 EmailTriageRequest and returns "
        f"a structured EmailTriageResponse — category, spam/phishing signals, "
        f"a plain-text summary, extracted action items, and an optional draft reply. "
        f"No mail is read or sent; this analyses only the payload in the request.</p>"
        f"<h3>Request envelope</h3>"
        f"{_model_table(EmailTriageRequest, 'EmailTriageRequest')}"
        f"<h3>Payload shapes</h3>"
        f"{triage_extra}"
        f"<h3>Response envelope</h3>"
        f"{_model_table(EmailTriageResponse, 'EmailTriageResponse')}"
        f"{_model_table(EmailTriageResult, 'EmailTriageResult')}"
        f"{_model_table(DraftReply, 'DraftReply (optional)')}"
        f"{_model_table(ActionItem, 'ActionItem')}"
        f"</div>"
    )

    batch_block = (
        f'<div class="endpoint-block">'
        f'<span class="method-badge">POST</span>'
        f'<span class="path">/v1/email/triage/batch</span>'
        f'<p class="desc">Triage a batch of emails or threads in one request (#1887). '
        f"Accepts a BatchTriageRequest (an <code>items</code> array of 1–100 "
        f"single-email / thread inputs) and returns a BatchTriageResponse — one "
        f"BatchItemResult per item, order-preserved. This is additive: the single "
        f"<code>/v1/email/triage</code> endpoint above is unchanged.</p>"
        f"<p class='desc'><strong>Per-item isolation:</strong> a failure on one "
        f"item sets that entry's <code>error</code> and the rest still run. "
        f"<strong>HTTP 200 with every item errored is valid</strong> — inspect each "
        f"<code>results[].error</code>, not just the status. A 502 means the local "
        f"LLM was unreachable before any item was processed (the whole batch fails).</p>"
        f"<h3>Request envelope</h3>"
        f"{_model_table(BatchTriageRequest, 'BatchTriageRequest')}"
        f"<h3>Response envelope</h3>"
        f"{_model_table(BatchTriageResponse, 'BatchTriageResponse')}"
        f"{_model_table(BatchItemResult, 'BatchItemResult (exactly one of result / error)')}"
        f"{_model_table(BatchItemError, 'BatchItemError')}"
        f"</div>"
    )

    prescan_block = _endpoint_block(
        path="/v1/email/prescan",
        description=(
            "Inbox pre-scan (#1778). Lists the most-recent inbox messages from "
            "the connected mailbox and returns the aggregate triage-card "
            "envelope the Agent UI renders — top urgent / actionable rows, an "
            "informational count, and suggested archives, each with a heuristic "
            "reason. Read-only: nothing is archived, marked, or sent. "
            "Classification reuses the agent's pre_scan_inbox path. Fails loudly "
            "when no mailbox is connected (503) or 2+ are (400)."
        ),
        request_sections=[("EmailPreScanRequest", EmailPreScanRequest)],
        response_sections=[
            ("EmailPreScanResponse", EmailPreScanResponse),
            ("EmailPreScanResult", EmailPreScanResult),
            ("PreScanItem", PreScanItem),
        ],
    )

    # /draft and /send are derived from the REST route models (the same
    # pydantic classes the endpoints actually use) via _endpoint_block, so the
    # tables cannot drift from the live request/response shapes. Imported
    # lazily here to keep this module's load surface free of FastAPI and to
    # avoid any import-order coupling with email_routes (which imports this
    # module lazily for its GET /spec page).
    from gaia_agent_email.api_routes import (
        EmailBriefingResponse,
        EmailDraftRequest,
        EmailDraftResponse,
        EmailSendRequest,
        EmailSendResponse,
    )

    briefing_block = _endpoint_block(
        path="/v1/email/briefing",
        method="GET",
        description=(
            "Latest scheduled daily inbox briefing (#1608). The email sidecar "
            "generates the pre-scan envelope on a configurable daily schedule "
            "— off by default; enable with GAIA_EMAIL_BRIEFING_ENABLED=true "
            "(fire time via GAIA_EMAIL_BRIEFING_TIME, 24h local HH:MM, "
            "default 08:00) — and this endpoint returns the most recent run. "
            "The briefing payload is the same email_pre_scan envelope as "
            "POST /v1/email/prescan, produced by the agent's own "
            "pre_scan_inbox path. 404 until a scheduled run has happened."
        ),
        request_sections=[],
        response_sections=[
            ("EmailBriefingResponse", EmailBriefingResponse),
            ("EmailPreScanResult", EmailPreScanResult),
            ("PreScanItem", PreScanItem),
        ],
    )

    search_block = _endpoint_block(
        path="/v1/email/search",
        description=(
            "Search the connected mailbox (read-only, #1781) by Gmail-style "
            "query / labels. Returns inbox-list metadata (id, thread, subject, "
            "from, snippet, labels) for each match — not the message body, and "
            "nothing is sent or modified. The mailbox is the one connected in "
            "GAIA; an ambiguous count fails loud (0 -> 503, 2+ -> 400)."
        ),
        request_sections=[("EmailSearchRequest", EmailSearchRequest)],
        response_sections=[
            ("EmailSearchResponse", EmailSearchResponse),
            ("EmailSearchResultItem", EmailSearchResultItem),
        ],
    )

    draft_block = _endpoint_block(
        path="/v1/email/draft",
        description=(
            "Propose a reply and obtain a single-use confirmation token bound "
            "to the exact (to, subject, body, attachments) payload — "
            "attachment binding covers filename, MIME type, and content digest "
            "(schema 2.2, #1542). Echo the token to POST /v1/email/send to "
            "authorize sending."
        ),
        request_sections=[
            ("EmailDraftRequest", EmailDraftRequest),
            ("OutgoingAttachment", OutgoingAttachment),
        ],
        response_sections=[("EmailDraftResponse", EmailDraftResponse)],
    )

    send_block = _endpoint_block(
        path="/v1/email/send",
        description=(
            "Send a reply — gated on explicit confirmation (#1264). The "
            "confirmation gate fires FIRST: a request without a valid, "
            "payload-bound confirmation token is rejected with HTTP 403 before "
            "any backend call. Attachments (schema 2.2) must exactly match the "
            "confirmed draft's — a swapped or smuggled file is rejected. "
            "Emails are never sent without explicit confirmation."
        ),
        request_sections=[("EmailSendRequest", EmailSendRequest)],
        response_sections=[("EmailSendResponse", EmailSendResponse)],
    )

    # Mailbox actions — archive / quarantine + reversal (schema 2.1, #1779).
    # Built from the contract models so the tables track the live shapes.
    confirm_block = _endpoint_block(
        path="/v1/email/confirm",
        description=(
            "Mint a single-use confirmation token for a destructive mailbox "
            "action (archive / quarantine), bound to that exact (action, "
            "message_id). The action analogue of /v1/email/draft — nothing "
            "mutates here. Echo the token to /archive or /quarantine."
        ),
        request_sections=[("EmailActionConfirmRequest", EmailActionConfirmRequest)],
        response_sections=[("EmailActionConfirmResponse", EmailActionConfirmResponse)],
    )

    archive_block = _endpoint_block(
        path="/v1/email/archive",
        description=(
            "Archive a message — gated on confirmation, reversible for 30s. The "
            "gate fires FIRST: no valid token for this (action='archive', "
            "message_id) is rejected with HTTP 403 before any backend call. "
            "Returns a batch_id undo handle and the post_archive_id (the id a "
            "folder-based backend like Outlook mints on the move, #1738)."
        ),
        request_sections=[("EmailArchiveRequest", EmailArchiveRequest)],
        response_sections=[("EmailArchiveResponse", EmailArchiveResponse)],
    )

    unarchive_block = _endpoint_block(
        path="/v1/email/unarchive",
        description=(
            "Reverse an archive within the undo window. NOT gated — it restores. "
            "Routes by the mailbox recorded at archive time and uses the "
            "post_archive_id so Outlook can find the moved message. Fails loudly "
            "with HTTP 409 when the window has expired or the batch_id is unknown."
        ),
        request_sections=[("EmailUnarchiveRequest", EmailUnarchiveRequest)],
        response_sections=[("EmailUnarchiveResponse", EmailUnarchiveResponse)],
    )

    quarantine_block = _endpoint_block(
        path="/v1/email/quarantine",
        description=(
            "Quarantine a phishing message — gated on confirmation, reversible "
            "for 30s. Applies the GAIA_PHISHING_QUARANTINE label and removes the "
            "message from the inbox. The gate fires FIRST (HTTP 403 without a "
            "valid token). Refuses is_phishing=false with HTTP 400."
        ),
        request_sections=[("EmailQuarantineRequest", EmailQuarantineRequest)],
        response_sections=[("EmailQuarantineResponse", EmailQuarantineResponse)],
    )

    unquarantine_block = _endpoint_block(
        path="/v1/email/unquarantine",
        description=(
            "Reverse a quarantine within the undo window. NOT gated — it restores "
            "the exact prior label set and removes the quarantine label. Fails "
            "loudly with HTTP 409 when the window has expired or the action_id is "
            "unknown/already undone."
        ),
        request_sections=[("EmailUnquarantineRequest", EmailUnquarantineRequest)],
        response_sections=[("EmailUnquarantineResponse", EmailUnquarantineResponse)],
    )

    # Calendar surface (schema 2.1, #1780) — view / preview / create / respond.
    # Reaches either the Google or Microsoft calendar backend through one contract.
    calendar_view_block = _endpoint_block(
        path="/v1/email/calendar/events",
        method="GET",
        description=(
            "View events on the primary calendar (read-only). Optional RFC 3339 "
            "query params time_min / time_max bound the window; provider "
            "(google|microsoft) is required only when more than one account is "
            "connected. Fails loudly (403 + reconnect CTA) if the calendar scope "
            "is missing."
        ),
        request_sections=[],
        response_sections=[
            ("CalendarEventsResponse", CalendarEventsResponse),
            ("CalendarEvent", CalendarEvent),
        ],
    )

    calendar_preview_block = _endpoint_block(
        path="/v1/email/calendar/events/preview",
        description=(
            "Mint a single-use confirmation token bound to a proposed event — the "
            "calendar analogue of /v1/email/draft. Creates nothing; echo the "
            "returned confirmation_token to POST /v1/email/calendar/events."
        ),
        request_sections=[("CalendarCreateEventRequest", CalendarCreateEventRequest)],
        response_sections=[
            ("CalendarEventPreviewResponse", CalendarEventPreviewResponse),
            ("CalendarEventDateTime", CalendarEventDateTime),
        ],
    )

    calendar_create_block = _endpoint_block(
        path="/v1/email/calendar/events",
        description=(
            "Create a calendar event — gated on explicit confirmation (#1780). "
            "Like /send, the gate fires FIRST: a request without a valid, "
            "payload-bound confirmation token (from .../preview) is rejected with "
            "HTTP 403 before any backend call. Events are externally visible to "
            "attendees, so they are never created without confirmation."
        ),
        request_sections=[("CalendarCreateEventRequest", CalendarCreateEventRequest)],
        response_sections=[("CalendarEventResponse", CalendarEventResponse)],
    )

    calendar_respond_block = _endpoint_block(
        path="/v1/email/calendar/events/respond",
        description=(
            "RSVP accept / decline / tentative to an existing invite. An explicit, "
            "user-initiated action (the UI's accept/decline controls), so it is not "
            "separately token-gated. attendee_email is the principal's own address "
            "(used by Google; ignored by Outlook, which RSVPs on /me)."
        ),
        request_sections=[("CalendarRespondRequest", CalendarRespondRequest)],
        response_sections=[("CalendarRespondResponse", CalendarRespondResponse)],
    )

    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Email Triage Agent — Endpoint Spec</title>
<style>
{_INLINE_CSS}
</style>
</head>
<body>
<h1>Email Triage Agent
  <span class="version-badge">Contract schema_version: {_esc(SCHEMA_VERSION)}</span>
</h1>
<p class="subtitle">
  REST endpoint specification derived from the frozen #1262 contract
  (<code>gaia_agent_email.contract</code>).
  Field descriptions are sourced directly from the pydantic models and stay
  in sync with the contract automatically.
</p>

<h2>Endpoints</h2>

{triage_block}

{batch_block}

{prescan_block}

{briefing_block}

{search_block}

{draft_block}

{send_block}

<h2>Mailbox actions — archive &amp; quarantine (schema 2.1)</h2>
<p class="subtitle">
  Reversible mailbox mutations exposed on the contract (#1779). Each acts on the
  mailbox connected in GAIA on the host and is gated on a single-use confirmation
  token from <code>/v1/email/confirm</code> — the same explicit-confirmation rule as
  <code>/v1/email/send</code>. Both are reversible within a 30-second undo window via
  the ungated <code>/unarchive</code> · <code>/unquarantine</code>.
</p>

{confirm_block}

{archive_block}

{unarchive_block}

{quarantine_block}

{unquarantine_block}

<h2>Calendar</h2>
<p class="subtitle">
  View, create, and RSVP to calendar events through the same contract — reaching
  whichever calendar (Google or Microsoft) the user connected. Added in
  schema_version 2.1 (#1780).
</p>

{calendar_view_block}

{calendar_preview_block}

{calendar_create_block}

{calendar_respond_block}

<h2>Convenience pages</h2>

<div class="endpoint-block">
  <span class="method-badge">GET</span>
  <span class="path">/v1/email/spec</span>
  <p class="desc">This page — a human-readable rendering of the contract above.
    Not part of the OpenAPI schema.</p>
</div>

<div class="endpoint-block">
  <span class="method-badge">GET</span>
  <span class="path">/v1/email/playground</span>
  <p class="desc">A self-contained, localhost-only playground: a stack-health
    check plus live triage/draft against this sidecar. Served same-origin with a
    <code>Content-Security-Policy: connect-src 'self'</code> header, so the page
    can only reach this sidecar and email content never leaves the machine. Not
    part of the OpenAPI schema.</p>
</div>

<div class="footer">
  GAIA Email Triage Agent &mdash; schema_version {_esc(SCHEMA_VERSION)} &mdash; amd-gaia.ai
</div>
</body>
</html>"""
    return body


# Default location for the generated spec when no explicit path is given.
DEFAULT_SPEC_PATH = Path.home() / ".gaia" / "email" / "endpoint-spec.html"


def write_and_open_spec(output_path: Optional[str] = None) -> Path:
    """Render the spec, write it to disk, and open it in a browser.

    Shared by every ``gaia email --spec`` entry point so the write/open
    behavior lives in one place. ``output_path`` overrides the default
    ``~/.gaia/email/endpoint-spec.html``. Returns the resolved destination
    path (already written) so callers can print it.
    """
    if output_path:
        dest = Path(output_path).expanduser().resolve()
    else:
        dest = DEFAULT_SPEC_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render_endpoint_spec_html(), encoding="utf-8")
    webbrowser.open(dest.as_uri())
    return dest


__all__ = [
    "render_endpoint_spec_html",
    "write_and_open_spec",
    "DEFAULT_SPEC_PATH",
]
