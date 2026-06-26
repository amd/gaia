# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Frozen request/response contract for the Email Triage Agent (issue #1262).

This module is the **single source of truth** for the payloads exchanged with
the email agent. It is shared, intentionally, by:

- the REST surface (#1229) — used directly as FastAPI request/response bodies;
- the MCP stdio interface (#1104) — used to validate the same operation.

GAIA owns this contract (#1261 Q2 RESOLVED: GAIA defines the payloads; the
consuming application conforms). Freeze it here; build the endpoints against it.

Design notes
------------
- **pydantic v2** (already a hard dependency, `setup.py`: ``pydantic>=2.9.2``)
  matches `gaia.api.schemas` and `gaia.ui.models`. No new dependency.
- **Dependency-light:** this module imports ONLY pydantic. It must never import
  Gmail / connector backends — or even ``triage_heuristics``, which transitively
  drags the agent package ``__init__`` (and thus the live backends) in — so both
  API surfaces can import it without pulling live-mail machinery into process.
- **Fail loudly:** every model sets ``extra="forbid"`` so an unknown field is a
  ``ValidationError``, never silently dropped — a consuming app that drifts from
  the contract finds out immediately.
- **Single email vs full thread** is a discriminated union on ``kind`` so a
  consumer branches deterministically and an empty/`messages`-less thread is
  rejected at validation time.
- **Categories** mirror the agent's five-bucket taxonomy. The strings are
  duplicated here (not imported) to keep this module backend-free; the contract
  tests assert byte-for-byte equality against
  ``triage_heuristics.ALL_CATEGORIES`` so drift is caught at test time, not by
  coupling the import graph.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Literal, Optional, Union, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Category strings — kept in sync with ``triage_heuristics.ALL_CATEGORIES`` by
# ``test_contract_schema.test_categories_match_agent_taxonomy``. Duplicated
# (not imported) so this module stays free of the email package's heavy
# ``__init__`` import chain.
CATEGORY_URGENT = "URGENT"
CATEGORY_NEEDS_RESPONSE = "NEEDS_RESPONSE"
CATEGORY_FYI = "FYI"
CATEGORY_PROMOTIONAL = "PROMOTIONAL"
CATEGORY_PERSONAL = "PERSONAL"

# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------

# Bump on ANY breaking change to the shapes below. Echoed in both request and
# response so a consumer can detect a mismatch loudly instead of silently
# mis-parsing. The first frozen revision is "1.0".
#
# 2.1 (#1780): adds the calendar surface (view / create / respond) to the REST
# contract. Additive — the triage/draft/send shapes are unchanged — so a 2.0
# consumer keeps working; the bump signals new endpoints are available.
SCHEMA_VERSION = "2.1"


class _Strict(BaseModel):
    """Base for every contract model: reject unknown fields loudly."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Category taxonomy (must agree with triage_heuristics — AC4)
# ---------------------------------------------------------------------------


class EmailCategory(str, Enum):
    """The five-bucket triage taxonomy (schema 2.0, #1615). Values MUST match
    ``triage_heuristics.ALL_CATEGORIES`` — the contract tests assert this.
    """

    URGENT = CATEGORY_URGENT
    NEEDS_RESPONSE = CATEGORY_NEEDS_RESPONSE
    FYI = CATEGORY_FYI
    PROMOTIONAL = CATEGORY_PROMOTIONAL
    PERSONAL = CATEGORY_PERSONAL


# ---------------------------------------------------------------------------
# Shared value objects
# ---------------------------------------------------------------------------


class EmailAddress(_Strict):
    """A single email participant. ``name`` is optional (many headers carry a
    bare address); ``email`` is required and must look like an address.
    """

    name: Optional[str] = Field(
        default=None, description="Display name, e.g. 'Alice Example'."
    )
    email: str = Field(..., description="Bare email address, e.g. 'a@b.com'.")

    @field_validator("email")
    @classmethod
    def _email_must_be_plausible(cls, v: str) -> str:
        v = (v or "").strip()
        # Minimal, non-silent validation: an address has a local part, an '@',
        # and a domain with a dot. We deliberately do not pull in a full RFC-822
        # validator — the contract only needs to reject obvious garbage loudly.
        if not v or "@" not in v:
            raise ValueError(f"not a valid email address: {v!r}")
        local, _, domain = v.partition("@")
        if not local or not domain or "." not in domain:
            raise ValueError(f"not a valid email address: {v!r}")
        return v


# ---------------------------------------------------------------------------
# INPUT — AC1: principal recipient, other participants, body
# ---------------------------------------------------------------------------


class EmailMessage(_Strict):
    """One email message. Shared by the single-email and thread inputs."""

    message_id: str = Field(..., description="Provider message id (opaque).")
    thread_id: Optional[str] = Field(
        default=None, description="Provider thread id this message belongs to."
    )
    from_: EmailAddress = Field(
        ...,
        alias="from",
        description="Sender. Aliased to 'from' (a Python keyword) on the wire.",
    )
    to: List[EmailAddress] = Field(
        default_factory=list, description="Primary recipients (the 'To' line)."
    )
    cc: List[EmailAddress] = Field(
        default_factory=list, description="Carbon-copy recipients."
    )
    bcc: List[EmailAddress] = Field(
        default_factory=list, description="Blind-carbon-copy recipients."
    )
    date: Optional[str] = Field(
        default=None, description="ISO-8601 timestamp of the message."
    )
    subject: str = Field(default="", description="Subject line.")
    body: str = Field(..., description="Plain-text message body to analyze.")

    # Accept both the wire alias ('from') and the python name ('from_').
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class _BaseInput(_Strict):
    """Common fields for both input shapes."""

    principal: EmailAddress = Field(
        ...,
        description=(
            "The recipient the agent acts on behalf of — whose inbox this is. "
            "Distinct from per-message 'to': the principal is the account "
            "owner, not necessarily a recipient of every message in a thread."
        ),
    )


class SingleEmailInput(_BaseInput):
    """A single email to triage (AC3 — single-email path)."""

    kind: Literal["single"] = "single"
    message: EmailMessage = Field(..., description="The one message to analyze.")


class ThreadInput(_BaseInput):
    """A full conversation thread to triage (AC3 — thread path)."""

    kind: Literal["thread"] = "thread"
    thread_id: str = Field(..., description="Provider thread id for the conversation.")
    messages: List[EmailMessage] = Field(
        ...,
        min_length=1,
        description="Every message in the thread, oldest-first. Non-empty.",
    )


# Discriminated union: ``kind`` selects the concrete shape. An unknown kind or a
# shape missing its required fields is rejected loudly.
EmailInput = Union[SingleEmailInput, ThreadInput]


class TriageContext(_Strict):
    """Optional caller-supplied context that biases categorization/summary
    (#1541). Absent → behavior is identical to today. Coexists with gaia
    memory (#1114) without requiring it.
    """

    people: List[str] = Field(
        default_factory=list,
        description="Important people whose mail should weigh higher.",
    )
    projects: List[str] = Field(
        default_factory=list,
        description="Active projects the principal cares about.",
    )
    tone: Optional[str] = Field(
        default=None, description="Preferred summary tone, e.g. 'concise'."
    )
    self_email: Optional[str] = Field(
        default=None,
        description="The principal's own address, so the model knows who 'I' is.",
    )


class EmailTriageRequest(_Strict):
    """Top-level request envelope shared by REST (#1229) and MCP stdio (#1104)."""

    schema_version: str = Field(
        default=SCHEMA_VERSION,
        description="Contract version. Mismatch lets a consumer fail loudly.",
    )
    payload: EmailInput = Field(
        ...,
        discriminator="kind",
        description="The single-email or full-thread input.",
    )
    context: Optional[TriageContext] = Field(
        default=None,
        description=(
            "Optional context (people/projects/tone/self-email) that biases "
            "categorization and summary. Absent → behavior unchanged."
        ),
    )


# ---------------------------------------------------------------------------
# OUTPUT — AC2: category, summary, draft, action items
# ---------------------------------------------------------------------------


class ActionItem(_Strict):
    """A single extracted action the principal should take."""

    description: str = Field(..., description="Imperative action, e.g. 'Reply to Bob'.")
    due_hint: Optional[str] = Field(
        default=None,
        description="Free-text due hint as written ('Friday', 'EOD'); not parsed.",
    )
    type: Literal["text", "link"] = Field(
        default="text",
        description=(
            "Discriminator: 'text' for a plain imperative action; 'link' when the "
            "action involves following a URL (url is then required and non-empty)."
        ),
    )
    url: Optional[str] = Field(
        default=None,
        description="The URL to follow for a 'link' action item; None for 'text'.",
    )

    @field_validator("description")
    @classmethod
    def _description_nonempty(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("action item description must be non-empty")
        return v

    @model_validator(mode="after")
    def _url_consistent_with_type(self) -> "ActionItem":
        if self.type == "link":
            if not (self.url or "").strip():
                raise ValueError(
                    "url is required and must be non-empty when type='link'"
                )
        else:
            if self.url is not None:
                raise ValueError("url must be None when type='text'")
        return self


class DraftReply(_Strict):
    """A drafted reply the agent proposes. Never sent without confirmation
    (#1264) — this is a proposal, not an action.
    """

    to: List[EmailAddress] = Field(
        ..., min_length=1, description="Proposed recipients (non-empty)."
    )
    subject: str = Field(..., description="Proposed subject line.")
    body: str = Field(..., description="Proposed reply body.")


class TriageUsage(_Strict):
    """LLM usage metrics for a triage, aggregated across the classify +
    summarize calls. Reuses the existing ``AgentResponse.stats`` measurement
    (#1277/#1278) — no new measurement path. ``None`` on the heuristic-only
    path where no LLM call is made.
    """

    prompt_tokens: int = Field(
        default=0, description="Sum of input tokens across the LLM calls."
    )
    completion_tokens: int = Field(
        default=0, description="Sum of output (completion) tokens across the LLM calls."
    )
    total_tokens: int = Field(
        default=0, description="Sum of input + output tokens across the LLM calls."
    )
    tokens_per_second: float = Field(
        default=0.0,
        description="Aggregate decode throughput (total output tokens / total decode time).",
    )


class EmailTriageResult(_Strict):
    """The structured analysis of a single email or thread."""

    category: EmailCategory = Field(
        ..., description="One of the five taxonomy buckets (schema 2.0)."
    )
    is_spam: bool = Field(default=False, description="Spam signal (independent).")
    is_phishing: bool = Field(
        default=False, description="Phishing signal (independent of spam)."
    )
    summary: str = Field(..., description="Plain-text summary of the email/thread.")
    action_items: List[ActionItem] = Field(
        default_factory=list, description="Extracted actions (may be empty)."
    )
    draft: Optional[DraftReply] = Field(
        default=None, description="Proposed reply, or null when none is suggested."
    )
    suggested_action: Literal["reply", "none", "archive"] = Field(
        default="none",
        description=(
            "Suggested next action: reply (URGENT/NEEDS_RESPONSE), "
            "archive (PROMOTIONAL), or none (FYI/PERSONAL). Derived by "
            "precedence rule -- never required so existing consumers are unaffected."
        ),
    )
    message_id: Optional[str] = Field(
        default=None,
        description=(
            "Echoes the provider message-id from the request (SingleEmailInput.message "
            "or ThreadInput.thread_id). Null when the result was produced from a "
            "raw Gmail-API message (no contract message_id available)."
        ),
    )
    usage: Optional[TriageUsage] = Field(
        default=None,
        description=(
            "LLM usage metrics (tokens + aggregate TPS) for this triage. Null on "
            "the heuristic-only path where no LLM call was made."
        ),
    )


class EmailTriageResponse(_Strict):
    """Top-level response envelope shared by REST (#1229) and MCP stdio (#1104)."""

    schema_version: str = Field(
        default=SCHEMA_VERSION, description="Echoes the contract version."
    )
    request_kind: Literal["single", "thread"] = Field(
        ..., description="Which input shape produced this result."
    )
    result: EmailTriageResult = Field(..., description="The structured analysis.")


# ---------------------------------------------------------------------------
# CALENDAR — view / create / respond (schema 2.1, #1780)
# ---------------------------------------------------------------------------
#
# Restores the agent's calendar capability on the REST contract so the Agent UI
# can view, create, and RSVP to events through the packaged sidecar. The Google
# and Microsoft (Outlook) calendar backends both satisfy the same backend
# Protocol, so a single contract reaches either connected provider.


class CalendarEventDateTime(_Strict):
    """One endpoint (start or end) of a calendar event.

    Provide EXACTLY ONE of:
      - ``date_time`` — a timed event, RFC 3339 (e.g. ``2026-07-01T14:00:00Z``
        or ``2026-07-01T14:00:00-07:00``); or
      - ``date`` — an all-day event, ``YYYY-MM-DD``.

    ``time_zone`` is an optional IANA name (e.g. ``America/Los_Angeles``).
    Note: when ``time_zone`` is omitted on a timed event, the Microsoft Graph
    (Outlook) calendar backend attaches a default of ``UTC`` (Graph rejects a
    bare ``dateTime`` without a paired time zone). For Google, supply either a
    UTC offset inside ``date_time`` or an explicit ``time_zone``.
    """

    date_time: Optional[str] = Field(
        default=None,
        description="Timed-event instant, RFC 3339. Mutually exclusive with 'date'.",
    )
    date: Optional[str] = Field(
        default=None,
        description="All-day date, 'YYYY-MM-DD'. Mutually exclusive with 'date_time'.",
    )
    time_zone: Optional[str] = Field(
        default=None,
        description=(
            "IANA time zone (e.g. 'America/Los_Angeles'). Optional; the Outlook "
            "backend defaults a missing time zone to 'UTC' for timed events."
        ),
    )

    @model_validator(mode="after")
    def _exactly_one_endpoint(self) -> "CalendarEventDateTime":
        has_dt = bool((self.date_time or "").strip())
        has_date = bool((self.date or "").strip())
        if has_dt == has_date:
            raise ValueError(
                "provide exactly one of 'date_time' (timed) or 'date' (all-day)"
            )
        return self


class CalendarEvent(_Strict):
    """A calendar event as returned by the view endpoint (display-flattened).

    ``start`` / ``end`` are the raw provider strings (an RFC 3339 ``dateTime``
    for timed events or a ``YYYY-MM-DD`` ``date`` for all-day events) — flattened
    for display rather than re-modeled, matching what the agent's calendar tools
    surface.
    """

    id: Optional[str] = Field(default=None, description="Provider event id (opaque).")
    summary: str = Field(default="", description="Event title / summary.")
    start: Optional[str] = Field(
        default=None, description="Start instant ('dateTime') or all-day 'date'."
    )
    end: Optional[str] = Field(
        default=None, description="End instant ('dateTime') or all-day 'date'."
    )
    location: Optional[str] = Field(
        default=None, description="Free-text location, or null when none."
    )
    organizer: Optional[str] = Field(
        default=None, description="Organizer email, or null when not reported."
    )


class CalendarEventsResponse(_Strict):
    """Result of GET /v1/email/calendar/events (read-only view)."""

    schema_version: str = Field(
        default=SCHEMA_VERSION, description="Echoes the contract version."
    )
    events: List[CalendarEvent] = Field(
        default_factory=list, description="Matching events, ordered by start time."
    )


class CalendarCreateEventRequest(_Strict):
    """Create a calendar event. Shared by the preview (token-mint) and create
    (token-consume) endpoints.

    Creating an event is a Tier-2 (externally visible) mutation, so it is gated
    by the same single-use confirmation-token handshake as ``/v1/email/send``:
    POST this to ``/calendar/events/preview`` to mint a token bound to the exact
    event, then echo the token in ``confirmation_token`` to ``/calendar/events``.
    """

    schema_version: str = Field(
        default=SCHEMA_VERSION,
        description="Contract version. Mismatch lets a consumer fail loudly.",
    )
    summary: str = Field(..., description="Event title / summary (non-empty).")
    start: CalendarEventDateTime = Field(..., description="Event start.")
    end: CalendarEventDateTime = Field(..., description="Event end (after start).")
    attendees: List[str] = Field(
        default_factory=list,
        description="Attendee email addresses to invite (may be empty).",
    )
    location: Optional[str] = Field(
        default=None, description="Optional free-text location."
    )
    description: Optional[str] = Field(
        default=None, description="Optional event description / body."
    )
    provider: Optional[str] = Field(
        default=None,
        description=(
            "Optional provider binding ('google' or 'microsoft'). When set on "
            "preview, the confirmation token binds to this provider so the create "
            "routes to the right calendar even with multiple accounts connected."
        ),
    )
    confirmation_token: Optional[str] = Field(
        default=None,
        description=(
            "Confirmation token from POST /v1/email/calendar/events/preview. "
            "Ignored by preview; required by create — a create without a valid "
            "token bound to this exact event is rejected (403)."
        ),
    )

    @field_validator("summary")
    @classmethod
    def _summary_nonempty(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("event summary must be non-empty")
        return v


class CalendarEventPreviewResponse(_Strict):
    """The normalized event echo plus a single-use confirmation token bound to
    it. Returned by POST /v1/email/calendar/events/preview.
    """

    schema_version: str = Field(
        default=SCHEMA_VERSION, description="Echoes the contract version."
    )
    summary: str = Field(..., description="The event title to be created.")
    start: CalendarEventDateTime = Field(..., description="Event start.")
    end: CalendarEventDateTime = Field(..., description="Event end.")
    attendees: List[str] = Field(
        default_factory=list, description="Attendees to invite."
    )
    location: Optional[str] = Field(default=None, description="Optional location.")
    description: Optional[str] = Field(
        default=None, description="Optional description."
    )
    confirmation_token: str = Field(
        ...,
        description=(
            "Echo this back to POST /v1/email/calendar/events to authorize "
            "creating exactly this event. Single-use; bound to the event payload."
        ),
    )


class CalendarEventResponse(_Strict):
    """Result of POST /v1/email/calendar/events (create — confirmation-gated)."""

    schema_version: str = Field(
        default=SCHEMA_VERSION, description="Echoes the contract version."
    )
    event_id: str = Field(..., description="Provider id of the created event.")
    summary: str = Field(..., description="Title of the created event.")
    created: bool = Field(default=True, description="Always true on success.")


class CalendarRespondRequest(_Strict):
    """RSVP to an existing calendar invite (POST /v1/email/calendar/events/respond).

    Responding to an invite is an explicit, user-initiated action surfaced by the
    UI's accept/decline controls, so it is not separately token-gated. ``status``
    is the RSVP verb; ``attendee_email`` is the principal's own address (used by
    the Google backend to locate the attendee row; ignored by the Outlook backend,
    which RSVPs on the authenticated ``/me`` calendar).
    """

    schema_version: str = Field(
        default=SCHEMA_VERSION,
        description="Contract version. Mismatch lets a consumer fail loudly.",
    )
    event_id: str = Field(..., description="Provider event id to RSVP to.")
    status: Literal["accepted", "declined", "tentative"] = Field(
        ..., description="RSVP response: accept, decline, or tentatively accept."
    )
    attendee_email: str = Field(
        ...,
        description=(
            "The principal's own email (the attendee responding). Used by the "
            "Google backend; ignored by Outlook (RSVPs on /me)."
        ),
    )
    provider: Optional[str] = Field(
        default=None,
        description="Optional provider binding ('google' or 'microsoft').",
    )

    @field_validator("attendee_email")
    @classmethod
    def _attendee_email_plausible(cls, v: str) -> str:
        v = (v or "").strip()
        if not v or "@" not in v:
            raise ValueError(f"not a valid email address: {v!r}")
        return v


class CalendarRespondResponse(_Strict):
    """Result of POST /v1/email/calendar/events/respond (RSVP)."""

    schema_version: str = Field(
        default=SCHEMA_VERSION, description="Echoes the contract version."
    )
    event_id: str = Field(..., description="The event that was responded to.")
    status: Literal["accepted", "declined", "tentative"] = Field(
        ..., description="The RSVP response that was recorded."
    )
    responded: bool = Field(default=True, description="Always true on success.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_request(data: dict) -> EmailTriageRequest:
    """Validate a raw request dict into an :class:`EmailTriageRequest`.

    Raises ``pydantic.ValidationError`` (a ``ValueError`` subclass) on any
    contract violation — never returns a partial/coerced object. Use this at the
    REST and MCP boundaries so a malformed payload fails loudly with an
    actionable message naming the offending field.
    """
    return cast(EmailTriageRequest, EmailTriageRequest.model_validate(data))


def parse_response(data: dict) -> EmailTriageResponse:
    """Validate a raw response dict into an :class:`EmailTriageResponse`.

    Same fail-loudly contract as :func:`parse_request`.
    """
    return cast(EmailTriageResponse, EmailTriageResponse.model_validate(data))


__all__ = [
    "SCHEMA_VERSION",
    "EmailCategory",
    "EmailAddress",
    "EmailMessage",
    "SingleEmailInput",
    "ThreadInput",
    "EmailInput",
    "TriageContext",
    "EmailTriageRequest",
    "ActionItem",
    "DraftReply",
    "TriageUsage",
    "EmailTriageResult",
    "EmailTriageResponse",
    # Calendar surface (schema 2.1, #1780).
    "CalendarEventDateTime",
    "CalendarEvent",
    "CalendarEventsResponse",
    "CalendarCreateEventRequest",
    "CalendarEventPreviewResponse",
    "CalendarEventResponse",
    "CalendarRespondRequest",
    "CalendarRespondResponse",
    "parse_request",
    "parse_response",
]
