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

The schema lives in ``gaia.agents.email.contract`` — dependency-light (pydantic
only) so both API surfaces can import it without dragging Gmail backends in.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from gaia.agents.email.contract import (
    SCHEMA_VERSION,
    ActionItem,
    DraftReply,
    EmailAddress,
    EmailCategory,
    EmailTriageRequest,
    EmailTriageResponse,
    EmailTriageResult,
    parse_request,
)
from gaia.agents.email.tools.triage_heuristics import ALL_CATEGORIES

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
            "category": "actionable",
            "is_spam": False,
            "is_phishing": False,
            "summary": "Vendor invoice needs review by Friday.",
            "action_items": [
                {"description": "Review the Q2 invoice", "due_hint": "Friday"}
            ],
            "draft": {
                "to": [{"name": "Bob Sender", "email": "bob@vendor.com"}],
                "subject": "Re: Q2 invoice attached",
                "body": "Thanks Bob, I'll review and confirm by Friday.",
            },
        },
    }


def _thread_response() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "request_kind": "thread",
        "result": {
            "category": "actionable",
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
        "import sys, gaia.agents.email.contract;"
        "heavy=[m for m in sys.modules if 'gmail_backend' in m "
        "or 'connectors' in m or 'calendar_backend' in m "
        "or m=='gaia.agents.email.agent'];"
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
    assert resp.result.category == EmailCategory.ACTIONABLE
    assert resp.result.draft is not None
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
    payload["result"]["category"] = "NEEDS_RESPONSE"  # old PR#916 taxonomy
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
# message_id field (#1539) — optional, backward-compatible amendment
# ---------------------------------------------------------------------------


def test_result_message_id_is_optional():
    """message_id has a default of None — existing payloads validate unchanged."""
    payload = _single_response()
    assert "message_id" not in payload["result"]  # existing sample has no field
    result = EmailTriageResult.model_validate(payload["result"])
    assert result.message_id is None


def test_result_message_id_accepted_when_present():
    """A result with message_id validates and round-trips."""
    payload = _single_response()
    payload["result"]["message_id"] = "msg-abc-001"
    result = EmailTriageResult.model_validate(payload["result"])
    assert result.message_id == "msg-abc-001"


def test_result_message_id_null_accepted():
    """Explicit null (None) is accepted."""
    payload = _single_response()
    payload["result"]["message_id"] = None
    result = EmailTriageResult.model_validate(payload["result"])
    assert result.message_id is None


def test_result_message_id_included_in_dump():
    """message_id is serialized in model_dump output (consumers see it)."""
    payload = _single_response()
    payload["result"]["message_id"] = "t-42"
    result = EmailTriageResult.model_validate(payload["result"])
    dumped = result.model_dump()
    assert "message_id" in dumped
    assert dumped["message_id"] == "t-42"


def test_response_with_message_id_round_trips():
    """Full response envelope with message_id round-trips through dump/validate."""
    payload = _single_response()
    payload["result"]["message_id"] = "m-round-trip"
    response = EmailTriageResponse.model_validate(payload)
    dumped = response.model_dump()
    again = EmailTriageResponse.model_validate(dumped)
    assert again.result.message_id == "m-round-trip"
