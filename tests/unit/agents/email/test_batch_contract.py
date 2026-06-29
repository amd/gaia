# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Batch triage contract tests for issue #1887 (ADDITIVE redesign).

These tests define the batch contract that lives BESIDE the single-email
contract.  They FAIL until:
  - BatchItemError, BatchItemResult, BatchTriageRequest, BatchTriageResponse,
    and MAX_BATCH_SIZE exist in gaia_agent_email.contract
  - Batch stays additive — it adds no required field to the existing triage
    shapes and forces no MAJOR bump (the contract sits at 2.1, a same-major
    additive revision over 2.0).
  - The original EmailTriageRequest (payload) / EmailTriageResponse
    (request_kind/result) are UNCHANGED.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip when a framework-only env lacks it.
pytest.importorskip("gaia_agent_email")

from gaia_agent_email.contract import (  # noqa: E402
    MAX_BATCH_SIZE,
    SCHEMA_VERSION,
    BatchItemError,
    BatchItemResult,
    BatchTriageRequest,
    BatchTriageResponse,
    EmailTriageRequest,
    EmailTriageResponse,
    EmailTriageResult,
)

# ---------------------------------------------------------------------------
# Minimal builders
# ---------------------------------------------------------------------------


def _single_item() -> dict:
    """A SingleEmailInput dict for use inside a `items` list."""
    return {
        "kind": "single",
        "principal": {"email": "alice@example.com"},
        "message": {
            "message_id": "msg-1",
            "from": {"name": "Bob", "email": "bob@vendor.com"},
            "to": [{"email": "alice@example.com"}],
            "subject": "Q2 invoice",
            "body": "Please review by Friday.",
        },
    }


def _thread_item() -> dict:
    """A ThreadInput dict for use inside a `items` list."""
    return {
        "kind": "thread",
        "principal": {"email": "alice@example.com"},
        "thread_id": "thread-42",
        "messages": [
            {
                "message_id": "msg-2",
                "from": {"email": "bob@vendor.com"},
                "subject": "Renewal call",
                "body": "Can we hop on a call?",
            }
        ],
    }


def _minimal_triage_result() -> EmailTriageResult:
    return EmailTriageResult.model_validate(
        {"category": "NEEDS_RESPONSE", "summary": "test summary"}
    )


# ---------------------------------------------------------------------------
# 1a — Batch is additive: it must not force a MAJOR bump (contract is 2.x)
# ---------------------------------------------------------------------------


def test_schema_version_stays_same_major():
    """Batch is additive, so the contract MAJOR must stay 2 (no breaking bump).

    The contract sits at 2.1 — a same-major additive revision over 2.0 — because
    the 2.1 REST surfaces (search / prescan / archive / quarantine / calendar)
    are bundled alongside batch. Batch itself adds no required field to the
    existing triage shapes, so it never forces a MAJOR bump.
    """
    major = SCHEMA_VERSION.split(".")[0]
    assert (
        major == "2"
    ), f"SCHEMA_VERSION is {SCHEMA_VERSION!r}; an additive change must not bump MAJOR"


# ---------------------------------------------------------------------------
# 1b — MAX_BATCH_SIZE is 100
# ---------------------------------------------------------------------------


def test_max_batch_size_is_100():
    assert MAX_BATCH_SIZE == 100


# ---------------------------------------------------------------------------
# 1c — BatchTriageRequest with items list validates and round-trips
# ---------------------------------------------------------------------------


def test_batch_triage_request_validates():
    """BatchTriageRequest accepts an `items` list of EmailInput."""
    req = BatchTriageRequest.model_validate({"items": [_single_item(), _thread_item()]})
    assert len(req.items) == 2
    assert req.items[0].kind == "single"
    assert req.items[1].kind == "thread"
    assert req.schema_version == SCHEMA_VERSION


def test_batch_triage_request_round_trips():
    req = BatchTriageRequest.model_validate({"items": [_single_item(), _thread_item()]})
    dumped = req.model_dump(by_alias=True, mode="json")
    again = BatchTriageRequest.model_validate(dumped)
    assert again == req


def test_batch_triage_request_with_context():
    req = BatchTriageRequest.model_validate(
        {
            "items": [_single_item()],
            "context": {
                "people": ["Boss"],
                "projects": ["Apollo"],
                "tone": "concise",
            },
        }
    )
    assert req.context is not None
    assert req.context.people == ["Boss"]


# ---------------------------------------------------------------------------
# 1d — BatchTriageResponse with results list validates and round-trips
# ---------------------------------------------------------------------------


def test_batch_triage_response_validates():
    """BatchTriageResponse accepts a `results` list of BatchItemResult."""
    result = _minimal_triage_result()
    item_result = BatchItemResult(index=0, result=result)
    resp = BatchTriageResponse(results=[item_result])
    assert len(resp.results) == 1
    assert resp.results[0].index == 0
    assert resp.results[0].result is not None
    assert resp.schema_version == SCHEMA_VERSION


def test_batch_triage_response_round_trips():
    result = _minimal_triage_result()
    resp = BatchTriageResponse(results=[BatchItemResult(index=0, result=result)])
    dumped = resp.model_dump(mode="json")
    again = BatchTriageResponse.model_validate(dumped)
    assert again == resp


# ---------------------------------------------------------------------------
# 1e — BatchItemResult: exactly-one constraint
# ---------------------------------------------------------------------------


def test_batch_item_result_rejects_both_result_and_error():
    """A BatchItemResult cannot have both result and error set."""
    result = _minimal_triage_result()
    with pytest.raises((ValueError, ValidationError)):
        BatchItemResult(
            index=0,
            result=result,
            error=BatchItemError(message="something went wrong"),
        )


def test_batch_item_result_rejects_neither_result_nor_error():
    """A BatchItemResult must have exactly one of result or error."""
    with pytest.raises((ValueError, ValidationError)):
        BatchItemResult(index=0)


def test_batch_item_result_with_result_only_validates():
    result = _minimal_triage_result()
    item = BatchItemResult(index=0, result=result)
    assert item.result is not None
    assert item.error is None
    assert item.index == 0


def test_batch_item_result_with_error_only_validates():
    item = BatchItemResult(index=0, error=BatchItemError(message="LLM unavailable"))
    assert item.error is not None
    assert item.result is None
    assert item.error.message == "LLM unavailable"


# ---------------------------------------------------------------------------
# 1f — BatchTriageRequest rejects empty items
# ---------------------------------------------------------------------------


def test_batch_triage_request_rejects_empty_items():
    with pytest.raises((ValueError, ValidationError)):
        BatchTriageRequest.model_validate({"items": []})


# ---------------------------------------------------------------------------
# 1g — BatchTriageRequest rejects over MAX_BATCH_SIZE
# ---------------------------------------------------------------------------


def test_batch_triage_request_rejects_over_max_batch_size():
    items = [_single_item() for _ in range(MAX_BATCH_SIZE + 1)]
    with pytest.raises((ValueError, ValidationError)):
        BatchTriageRequest.model_validate({"items": items})


# ---------------------------------------------------------------------------
# 1h — BatchTriageRequest rejects unknown fields (extra="forbid")
# ---------------------------------------------------------------------------


def test_batch_triage_request_rejects_unknown_field():
    with pytest.raises((ValueError, ValidationError)):
        BatchTriageRequest.model_validate(
            {"items": [_single_item()], "bogus_field": "surprise"}
        )


# ---------------------------------------------------------------------------
# 1i — ADDITIVE GUARANTEE: original EmailTriageRequest and EmailTriageResponse
#      are unchanged (payload, request_kind, result still exist)
# ---------------------------------------------------------------------------


def test_email_triage_request_still_has_payload_field():
    """Additive guarantee: the single-email EmailTriageRequest.payload is unchanged."""
    assert (
        "payload" in EmailTriageRequest.model_fields
    ), "EmailTriageRequest lost its 'payload' field — the additive contract was broken"


def test_email_triage_response_still_has_request_kind():
    """Additive guarantee: EmailTriageResponse.request_kind is unchanged."""
    assert (
        "request_kind" in EmailTriageResponse.model_fields
    ), "EmailTriageResponse lost 'request_kind' — the additive contract was broken"


def test_email_triage_response_still_has_result():
    """Additive guarantee: EmailTriageResponse.result is unchanged."""
    assert (
        "result" in EmailTriageResponse.model_fields
    ), "EmailTriageResponse lost 'result' — the additive contract was broken"
