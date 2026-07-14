# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Contract-schema tests for the Email Triage Agent (issue #1262).

These tests freeze the request/response contract shared by the REST surface
(#1229) and the MCP stdio interface (#1104). They assert that:

- A valid single-email request validates and round-trips.
- A valid full-thread request validates and round-trips.
- Valid responses (single + thread) validate.
- Invalid payloads are rejected LOUDLY (pydantic ValidationError / ValueError),
  never silently coerced.

The schema lives in ``gaia_agent_email.contract`` — dependency-light (pydantic
only) so both API surfaces can import it without dragging Gmail backends in.
"""

from __future__ import annotations

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip when a framework-only env lacks it.
import pytest  # noqa: E402
from pydantic import ValidationError

pytest.importorskip("gaia_agent_email")  # noqa: E402
from gaia_agent_email.contract import (
    SCHEMA_VERSION,
    ActionItem,
    DraftReply,
    EmailAddress,
    EmailCategory,
    EmailTriageRequest,
    EmailTriageResponse,
    EmailTriageResult,
    TriageContext,
    TriageUsage,
    parse_request,
)
from gaia_agent_email.tools.triage_heuristics import ALL_CATEGORIES

# ---------------------------------------------------------------------------
# Sample payloads (the frozen contract examples — kept in sync with the .mdx)
# ---------------------------------------------------------------------------


def _single_email_request() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "payload": {
            "kind": "single",
            "principal": {"name": "Alice Example", "email": "alice@example.com"},
            "message": {
                "message_id": "msg-1",
                "thread_id": "thread-1",
                "from_": {"name": "Bob Sender", "email": "bob@vendor.com"},
                "to": [{"name": "Alice Example", "email": "alice@example.com"}],
                "cc": [],
                "date": "2026-05-30T09:00:00Z",
                "subject": "Q2 invoice attached",
                "body": "Hi Alice, please review the attached invoice by Friday.",
            },
        },
    }


def _thread_request() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "payload": {
            "kind": "thread",
            "principal": {"name": "Alice Example", "email": "alice@example.com"},
            "thread_id": "thread-42",
            "messages": [
                {
                    "message_id": "msg-1",
                    "thread_id": "thread-42",
                    "from_": {"name": "Bob", "email": "bob@vendor.com"},
                    "to": [{"name": "Alice", "email": "alice@example.com"}],
                    "date": "2026-05-30T09:00:00Z",
                    "subject": "Contract renewal",
                    "body": "Can we hop on a call about the renewal?",
                },
                {
                    "message_id": "msg-2",
                    "thread_id": "thread-42",
                    "from_": {"name": "Alice", "email": "alice@example.com"},
                    "to": [{"name": "Bob", "email": "bob@vendor.com"}],
                    "date": "2026-05-30T10:00:00Z",
                    "subject": "Re: Contract renewal",
                    "body": "Sure, does Thursday 2pm work?",
                },
            ],
        },
    }


def _single_response() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "request_kind": "single",
        "result": {
            "category": "NEEDS_RESPONSE",
            "is_spam": False,
            "is_phishing": False,
            "summary": "Vendor invoice needs review by Friday.",
            "action_items": [
                {"description": "Review the Q2 invoice", "due_hint": "Friday"}
            ],
            "draft": {
                "to": [{"name": "Bob Sender", "email": "bob@vendor.com"}],
                "subject": "Re: Q2 invoice attached",
            },
        },
    }


def _thread_response() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "request_kind": "thread",
        "result": {
            "category": "NEEDS_RESPONSE",
            "is_spam": False,
            "is_phishing": False,
            "summary": "Bob wants a renewal call; Alice proposed Thursday 2pm.",
            "action_items": [{"description": "Confirm Thursday 2pm call"}],
            "draft": None,
        },
    }


# ---------------------------------------------------------------------------
# Valid payloads
# ---------------------------------------------------------------------------


def test_contract_import_is_backend_free():
    # The REST surface (#1229) and MCP stdio interface (#1104) must be able to
    # import the contract without dragging Gmail / connector backends into the
    # process. Importing it in a fresh subprocess proves no backend module is
    # loaded as a side effect.
    import subprocess
    import sys

    code = (
        "import sys, gaia_agent_email.contract;"
        "heavy=[m for m in sys.modules if 'gmail_backend' in m "
        "or 'connectors' in m or 'calendar_backend' in m "
        "or m=='gaia_agent_email.agent'];"
        "assert not heavy, heavy;"
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_schema_version_is_pinned_and_nonempty():
    assert isinstance(SCHEMA_VERSION, str)
    assert SCHEMA_VERSION


def test_categories_match_agent_taxonomy():
    # AC4 guard: the contract's categories must not drift from the agent's
    # frozen four-bucket taxonomy in triage_heuristics.
    assert {c.value for c in EmailCategory} == set(ALL_CATEGORIES)


def test_valid_single_email_request_validates():
    req = EmailTriageRequest.model_validate(_single_email_request())
    assert req.payload.kind == "single"
    assert req.payload.principal.email == "alice@example.com"
    assert req.payload.message.subject == "Q2 invoice attached"


def test_valid_thread_request_validates():
    req = EmailTriageRequest.model_validate(_thread_request())
    assert req.payload.kind == "thread"
    assert len(req.payload.messages) == 2
    assert req.payload.thread_id == "thread-42"


def test_parse_request_helper_returns_model():
    req = parse_request(_thread_request())
    assert isinstance(req, EmailTriageRequest)
    assert req.payload.kind == "thread"


def test_request_round_trips_through_dump():
    req = EmailTriageRequest.model_validate(_single_email_request())
    dumped = req.model_dump(by_alias=True)
    again = EmailTriageRequest.model_validate(dumped)
    assert again == req


def test_valid_single_response_validates():
    resp = EmailTriageResponse.model_validate(_single_response())
    assert resp.request_kind == "single"
    assert resp.result.category == EmailCategory.NEEDS_RESPONSE
    # schema 2.3: the triage draft is a DraftScaffold (to + subject, no body).
    assert resp.result.draft is not None
    assert set(resp.result.draft.model_dump()) == {"to", "subject"}
    assert resp.result.action_items[0].due_hint == "Friday"


def test_valid_thread_response_validates_with_null_draft():
    resp = EmailTriageResponse.model_validate(_thread_response())
    assert resp.request_kind == "thread"
    assert resp.result.draft is None


def test_email_address_accepts_optional_name():
    addr = EmailAddress.model_validate({"email": "x@y.com"})
    assert addr.name is None
    assert addr.email == "x@y.com"


def test_action_item_due_hint_optional():
    item = ActionItem.model_validate({"description": "do thing"})
    assert item.due_hint is None


def test_wire_alias_from_validates():
    # The docs / REST + MCP consumers send the RFC-822 wire key "from"
    # (not the Python field name "from_"). Both must validate.
    payload = _single_email_request()
    msg = payload["payload"]["message"]
    msg["from"] = msg.pop("from_")
    req = EmailTriageRequest.model_validate(payload)
    assert req.payload.message.from_.email == "bob@vendor.com"
    # And the canonical dump uses the wire alias.
    assert "from" in req.payload.message.model_dump(by_alias=True)


# ---------------------------------------------------------------------------
# Invalid payloads — must be rejected LOUDLY
# ---------------------------------------------------------------------------


def test_missing_principal_rejected():
    payload = _single_email_request()
    del payload["payload"]["principal"]
    with pytest.raises(ValidationError):
        EmailTriageRequest.model_validate(payload)


def test_empty_thread_rejected():
    payload = _thread_request()
    payload["payload"]["messages"] = []
    with pytest.raises(ValidationError):
        EmailTriageRequest.model_validate(payload)


def test_unknown_kind_rejected():
    payload = _single_email_request()
    payload["payload"]["kind"] = "digest"
    with pytest.raises(ValidationError):
        EmailTriageRequest.model_validate(payload)


def test_bad_category_rejected():
    payload = _single_response()
    payload["result"]["category"] = "actionable"  # old 4-bucket taxonomy value
    with pytest.raises(ValidationError):
        EmailTriageResponse.model_validate(payload)


def test_unknown_field_rejected_loudly():
    payload = _single_email_request()
    payload["payload"]["message"]["totally_new_field"] = "surprise"
    with pytest.raises(ValidationError):
        EmailTriageRequest.model_validate(payload)


def test_malformed_address_rejected():
    payload = _single_email_request()
    payload["payload"]["message"]["from_"] = {"name": "X", "email": "not-an-email"}
    with pytest.raises(ValidationError):
        EmailTriageRequest.model_validate(payload)


def test_empty_address_rejected():
    with pytest.raises(ValidationError):
        EmailAddress.model_validate({"email": ""})


def test_thread_payload_missing_messages_rejected():
    payload = _thread_request()
    del payload["payload"]["messages"]
    with pytest.raises(ValidationError):
        EmailTriageRequest.model_validate(payload)


def test_single_payload_missing_message_rejected():
    payload = _single_email_request()
    del payload["payload"]["message"]
    with pytest.raises(ValidationError):
        EmailTriageRequest.model_validate(payload)


def test_parse_request_raises_loudly_on_garbage():
    with pytest.raises((ValidationError, ValueError)):
        parse_request({"not": "a request"})


def test_response_unknown_field_rejected():
    payload = _single_response()
    payload["result"]["extra_secret"] = "leak"
    with pytest.raises(ValidationError):
        EmailTriageResponse.model_validate(payload)


def test_draft_requires_recipient():
    with pytest.raises(ValidationError):
        DraftReply.model_validate({"subject": "hi", "body": "x", "to": []})


def test_result_summary_required():
    payload = _single_response()
    del payload["result"]["summary"]
    with pytest.raises(ValidationError):
        EmailTriageResult.model_validate(payload["result"])


# ---------------------------------------------------------------------------
# Phase 2 (#1603) contract freeze guard — the multi-inbox 'mailbox' tag lives
# on the INTERNAL agent tool-result dicts, NOT on the frozen REST schema.
# ---------------------------------------------------------------------------


def test_schema_version_unchanged_by_multi_inbox():
    """Schema 2.4 is the current frozen contract version.

    2.1 was additive over the 2.0 5-bucket taxonomy (no triage shape change):
    it added inbox search (#1781), mailbox actions (#1779), the calendar
    surface (#1780), and inbox pre-scan (#1778). 2.2 is additive over 2.1:
    attachment handling (#1542) — read/triage exposes attachment metadata,
    draft/send accept attachments. 2.3 is a BREAKING triage-shape change:
    EmailTriageResult.draft is now a DraftScaffold (recipient + subject only,
    no body) instead of a DraftReply. 2.4 is additive over 2.3 (#2016): the
    streaming agent-loop surface (POST /v1/email/query + cancel) — no existing
    shape changed. If this fails, someone changed the version unexpectedly;
    that requires an explicit version negotiation, not a drive-by edit.
    """
    assert SCHEMA_VERSION == "2.4"


def test_triage_result_gained_no_new_required_field():
    """Every EmailTriageResult field beyond the original required set must be
    optional — a new REQUIRED field would break every existing consumer."""
    required = {
        name
        for name, field in EmailTriageResult.model_fields.items()
        if field.is_required()
    }
    assert required == {"category", "summary"}, (
        f"EmailTriageResult required fields changed: {sorted(required)}. "
        "Adding a required field is a breaking contract change — it needs a "
        "SCHEMA_VERSION bump and a migration plan, not a drive-by edit."
    )


def test_suggested_action_defaults_to_none():
    """suggested_action defaults to 'none' when not provided."""
    result = EmailTriageResult.model_validate(
        {
            "category": "FYI",
            "summary": "Just an update.",
        }
    )
    assert result.suggested_action == "none"


def test_suggested_action_accepts_valid_literals():
    """suggested_action accepts reply, none, and archive."""
    for action in ("reply", "none", "archive"):
        result = EmailTriageResult.model_validate(
            {
                "category": "URGENT",
                "summary": "Critical issue.",
                "suggested_action": action,
            }
        )
        assert result.suggested_action == action


def test_suggested_action_rejects_invalid_value():
    """suggested_action rejects values outside reply/none/archive."""
    with pytest.raises(ValidationError):
        EmailTriageResult.model_validate(
            {
                "category": "URGENT",
                "summary": "Critical issue.",
                "suggested_action": "forward",
            }
        )


def test_triage_result_has_no_mailbox_field():
    """The 'mailbox' tag is internal to the agent tools; the frozen REST result
    must not grow one implicitly."""
    assert "mailbox" not in EmailTriageResult.model_fields


# ---------------------------------------------------------------------------
# ActionItem type discriminator (#1538)
# ---------------------------------------------------------------------------


def test_action_item_defaults_to_text():
    """ActionItem without a type field defaults to 'text' and url is None."""
    item = ActionItem.model_validate({"description": "Reply to Alice"})
    assert item.type == "text"
    assert item.url is None


def test_action_item_due_hint_still_optional():
    """Existing due_hint behaviour is unchanged."""
    item = ActionItem.model_validate({"description": "do thing"})
    assert item.due_hint is None


def test_action_item_link_requires_url():
    """type='link' without a url raises ValidationError."""
    with pytest.raises(ValidationError):
        ActionItem.model_validate({"description": "Visit site", "type": "link"})


def test_action_item_link_with_empty_url_rejected():
    """type='link' with an empty url string raises ValidationError."""
    with pytest.raises(ValidationError):
        ActionItem.model_validate(
            {"description": "Visit site", "type": "link", "url": ""}
        )


def test_action_item_text_rejects_url():
    """type='text' (or default) with a url raises ValidationError."""
    with pytest.raises(ValidationError):
        ActionItem.model_validate(
            {"description": "Do thing", "type": "text", "url": "https://example.com"}
        )


def test_action_item_link_valid():
    """A valid link action item with description and url validates."""
    item = ActionItem.model_validate(
        {
            "description": "Check the report",
            "type": "link",
            "url": "https://example.com/report",
        }
    )
    assert item.type == "link"
    assert item.url == "https://example.com/report"
    assert item.description == "Check the report"


# ---------------------------------------------------------------------------
# _extract_action_items URL detection (#1538)
# ---------------------------------------------------------------------------


def test_extract_action_items_detects_link():
    """A sentence with an https URL in an action-cue context yields a link item."""
    pytest.importorskip("gaia_agent_email.api_routes")
    from gaia_agent_email.api_routes import EmailTriageService

    svc = EmailTriageService()
    body = "Please review the report at https://example.com/report by Friday."
    items = svc._extract_action_items(body)
    link_items = [i for i in items if i.type == "link"]
    assert link_items, "expected at least one link action item"
    assert link_items[0].url == "https://example.com/report"


def test_extract_action_items_preserves_matched_parens_in_url():
    """A URL whose own path contains a matched () keeps its closing paren —
    only true trailing punctuation is trimmed (#1696 review: Wikipedia-style)."""
    pytest.importorskip("gaia_agent_email.api_routes")
    from gaia_agent_email.api_routes import EmailTriageService

    svc = EmailTriageService()
    body = (
        "Please read https://en.wikipedia.org/wiki/Python_(programming_language) "
        "before the review."
    )
    items = svc._extract_action_items(body)
    link_items = [i for i in items if i.type == "link"]
    assert link_items, "expected a link action item"
    assert (
        link_items[0].url
        == "https://en.wikipedia.org/wiki/Python_(programming_language)"
    )


def test_extract_action_items_trims_unmatched_trailing_paren():
    """A trailing ')' that does NOT close a '(' inside the URL is still trimmed."""
    pytest.importorskip("gaia_agent_email.api_routes")
    from gaia_agent_email.api_routes import EmailTriageService

    svc = EmailTriageService()
    body = "Review the doc (see https://example.com/report) before Friday."
    items = svc._extract_action_items(body)
    link_items = [i for i in items if i.type == "link"]
    assert link_items
    assert link_items[0].url == "https://example.com/report"


def test_extract_action_items_plain_imperative_is_text():
    """A plain imperative sentence without a URL yields a text item."""
    pytest.importorskip("gaia_agent_email.api_routes")
    from gaia_agent_email.api_routes import EmailTriageService

    svc = EmailTriageService()
    body = "Please confirm the meeting time by Monday."
    items = svc._extract_action_items(body)
    assert items
    assert all(i.type == "text" for i in items)
    assert all(i.url is None for i in items)


def test_extract_action_items_link_strips_trailing_punctuation():
    """A URL ending a sentence keeps no trailing punctuation in the link."""
    pytest.importorskip("gaia_agent_email.api_routes")
    from gaia_agent_email.api_routes import EmailTriageService

    svc = EmailTriageService()
    body = "Please review the doc at https://example.com/report."
    link_items = [i for i in svc._extract_action_items(body) if i.type == "link"]
    assert link_items
    assert link_items[0].url == "https://example.com/report"


# ---------------------------------------------------------------------------
# TriageUsage / EmailTriageResult.usage (#1540)
# ---------------------------------------------------------------------------


def test_triage_usage_defaults_are_zero():
    """TriageUsage validates with zero defaults."""
    usage = TriageUsage()
    assert usage.prompt_tokens == 0
    assert usage.total_tokens == 0
    assert usage.tokens_per_second == 0.0


def test_triage_usage_populated_round_trips():
    """A populated TriageUsage validates and round-trips."""
    usage = TriageUsage.model_validate(
        {"prompt_tokens": 120, "total_tokens": 200, "tokens_per_second": 42.5}
    )
    assert usage.prompt_tokens == 120
    assert usage.total_tokens == 200
    assert usage.tokens_per_second == 42.5
    again = TriageUsage.model_validate(usage.model_dump())
    assert again == usage


def test_triage_usage_rejects_unknown_field():
    """TriageUsage forbids extra fields (strict)."""
    with pytest.raises(ValidationError):
        TriageUsage.model_validate({"prompt_tokens": 1, "bogus": 2})


def test_triage_result_usage_defaults_to_none():
    """EmailTriageResult.usage defaults to None (heuristic-only path)."""
    result = EmailTriageResult.model_validate(
        {"category": "FYI", "summary": "Just an update."}
    )
    assert result.usage is None


def test_triage_result_accepts_usage():
    """EmailTriageResult accepts a populated usage object and round-trips."""
    result = EmailTriageResult.model_validate(
        {
            "category": "URGENT",
            "summary": "Critical issue.",
            "usage": {
                "prompt_tokens": 50,
                "total_tokens": 90,
                "tokens_per_second": 30.0,
            },
        }
    )
    assert result.usage is not None
    assert result.usage.prompt_tokens == 50
    assert result.usage.total_tokens == 90
    assert result.usage.tokens_per_second == 30.0


def test_usage_is_not_a_required_field():
    """usage must not become a required field (required set guard)."""
    required = {
        name
        for name, field in EmailTriageResult.model_fields.items()
        if field.is_required()
    }
    assert "usage" not in required


# ---------------------------------------------------------------------------
# TriageContext / EmailTriageRequest.context (#1541)
# ---------------------------------------------------------------------------


def test_triage_context_defaults_are_empty():
    """TriageContext validates with empty defaults."""
    ctx = TriageContext()
    assert ctx.people == []
    assert ctx.projects == []
    assert ctx.tone is None
    assert ctx.self_email is None


def test_triage_context_populated_validates():
    """A populated TriageContext validates and round-trips."""
    ctx = TriageContext.model_validate(
        {
            "people": ["Boss", "Alice"],
            "projects": ["Apollo"],
            "tone": "concise",
            "self_email": "me@example.com",
        }
    )
    assert ctx.people == ["Boss", "Alice"]
    assert ctx.projects == ["Apollo"]
    assert ctx.tone == "concise"
    assert ctx.self_email == "me@example.com"
    again = TriageContext.model_validate(ctx.model_dump())
    assert again == ctx


def test_triage_context_rejects_unknown_field():
    """TriageContext forbids extra fields (strict)."""
    with pytest.raises(ValidationError):
        TriageContext.model_validate({"people": [], "unknown_field": "x"})


def test_request_without_context_validates():
    """A request with no context validates and context is None (unchanged)."""
    req = EmailTriageRequest.model_validate(_single_email_request())
    assert req.context is None


def test_request_with_context_validates():
    """A request carrying a populated context validates."""
    payload = _single_email_request()
    payload["context"] = {
        "people": ["Boss"],
        "projects": ["Apollo"],
        "tone": "friendly",
        "self_email": "alice@example.com",
    }
    req = EmailTriageRequest.model_validate(payload)
    assert req.context is not None
    assert req.context.people == ["Boss"]
    assert req.context.tone == "friendly"


def test_request_context_unknown_subfield_rejected():
    """An unknown sub-field of context is rejected loudly (extra='forbid')."""
    payload = _single_email_request()
    payload["context"] = {"people": ["Boss"], "bogus": True}
    with pytest.raises(ValidationError):
        EmailTriageRequest.model_validate(payload)
