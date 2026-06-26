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
# 2.1 adds the read-only inbox-search surface (#1781) — additive, no triage
# shape change.
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
# INBOX SEARCH — read-only mailbox search (#1781)
# ---------------------------------------------------------------------------


class EmailSearchRequest(_Strict):
    """Search the connected mailbox for messages by Gmail-style query / labels.

    Read-only: this lists messages already in the mailbox; it never sends,
    modifies, or triages. It restores the agent's in-loop inbox-search tool
    (``search_messages``) on the REST contract so the Agent UI can drive it
    through the package (#1781).

    Both ``query`` and ``labels`` are optional — with neither, the backend
    lists the INBOX (bounded by ``max_results``).
    """

    schema_version: str = Field(
        default=SCHEMA_VERSION,
        description="Contract version. Mismatch lets a consumer fail loudly.",
    )
    query: Optional[str] = Field(
        default=None,
        description=(
            "Gmail-style search query (e.g. 'from:alice is:unread'). None lists "
            "the inbox without a text filter."
        ),
    )
    labels: Optional[List[str]] = Field(
        default=None,
        description=(
            "Label ids to filter by (e.g. ['INBOX', 'UNREAD']). None defaults to "
            "the INBOX label."
        ),
    )
    max_results: int = Field(
        default=25,
        ge=1,
        le=100,
        description=(
            "Max messages to return (1-100). Each match is hydrated with a "
            "per-message fetch, so the count is capped to bound that fan-out."
        ),
    )
    page_token: Optional[str] = Field(
        default=None,
        description=(
            "Opaque pagination cursor from a previous response's "
            "``next_page_token``. None fetches the first page."
        ),
    )


class EmailSearchResultItem(_Strict):
    """One message in a search result — inbox-list metadata, not the full body.

    Header values (``from_``/``to``/``date``) are the raw, provider-decoded
    header strings exactly as the agent's read tools surface them — not parsed
    into ``EmailAddress`` objects, because list headers can carry multiple or
    malformed addresses a strict parse would reject. Fetch the full message via
    the triage path when you need the body.
    """

    id: str = Field(..., description="Provider message id (opaque).")
    thread_id: Optional[str] = Field(
        default=None, description="Provider thread id this message belongs to."
    )
    subject: str = Field(default="", description="Subject line.")
    from_: str = Field(
        default="",
        alias="from",
        description="Raw 'From' header string. Aliased to 'from' on the wire.",
    )
    to: str = Field(default="", description="Raw 'To' header string.")
    date: str = Field(default="", description="Raw 'Date' header string.")
    snippet: str = Field(
        default="", description="Provider-supplied short preview of the body."
    )
    label_ids: List[str] = Field(
        default_factory=list, description="Label ids on the message."
    )

    # Accept both the wire alias ('from') and the python name ('from_').
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class EmailSearchResponse(_Strict):
    """Top-level inbox-search response envelope (#1781)."""

    schema_version: str = Field(
        default=SCHEMA_VERSION, description="Echoes the contract version."
    )
    query: Optional[str] = Field(
        default=None, description="Echoes the request query (None when unset)."
    )
    count: int = Field(..., description="Number of messages returned.")
    messages: List[EmailSearchResultItem] = Field(
        default_factory=list, description="Matching messages (newest-first)."
    )
    next_page_token: Optional[str] = Field(
        default=None,
        description="Opaque token to fetch the next page, or null when no more.",
    )


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
    "EmailSearchRequest",
    "EmailSearchResultItem",
    "EmailSearchResponse",
    "parse_request",
    "parse_response",
]
