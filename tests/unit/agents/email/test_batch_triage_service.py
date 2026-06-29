# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Batch triage service tests for issue #1887 (ADDITIVE redesign).

These tests verify the NEW triage_batch method and POST /v1/email/triage/batch
endpoint, added BESIDE the unchanged single-email path.

They FAIL until:
  - EmailTriageService.triage_batch(request: BatchTriageRequest) is added
  - POST /v1/email/triage/batch route is added
  - The single POST /v1/email/triage route still works (regression)
"""

from __future__ import annotations

import json as _json
import types as types_mod

import pytest

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip when a framework-only env lacks it.
pytest.importorskip("gaia_agent_email")

from gaia_agent_email.api_routes import (  # noqa: E402
    EmailTriageService,
    LLMTriageError,
)
from gaia_agent_email.contract import (  # noqa: E402
    BatchItemResult,
    BatchTriageRequest,
)

# ---------------------------------------------------------------------------
# _CountingFakeChat — counts send_messages calls, injects failure on demand
# ---------------------------------------------------------------------------


class _CountingFakeChat:
    """Minimal chat stub that counts calls and can inject a failure at a
    specific call number.

    Each item in the batch makes 2 LLM calls:
      call 1+2  → item index 0  (classify + summarize)
      call 3+4  → item index 1
      call 5+6  → item index 2
      …
    Pass fail_on_call=N to raise LLMTriageError on the Nth call.
    """

    def __init__(self, fail_on_call=None):
        self._call = 0
        self._fail_on = fail_on_call

    def send_messages(self, messages, system_prompt="", **kwargs):
        self._call += 1
        if self._fail_on is not None and self._call == self._fail_on:
            raise LLMTriageError("injected failure")
        resp = types_mod.SimpleNamespace()
        first = messages[0].get("content", "") if messages else ""
        resp.text = (
            _json.dumps(
                {"category": "NEEDS_RESPONSE", "confidence": 0.9, "reasoning": "t"}
            )
            if "Classify" in first
            else "summary"
        )
        return resp


# ---------------------------------------------------------------------------
# Minimal request builders
# ---------------------------------------------------------------------------


def _single_item_dict() -> dict:
    return {
        "kind": "single",
        "principal": {"email": "alice@example.com"},
        "message": {
            "message_id": "msg-1",
            "from": {"name": "Bob", "email": "bob@vendor.com"},
            "to": [{"email": "alice@example.com"}],
            "subject": "Q2 invoice",
            "body": "Please review the attached invoice by Friday.",
        },
    }


def _thread_item_dict() -> dict:
    return {
        "kind": "thread",
        "principal": {"email": "alice@example.com"},
        "thread_id": "thread-42",
        "messages": [
            {
                "message_id": "msg-2",
                "from": {"email": "bob@vendor.com"},
                "subject": "Renewal",
                "body": "Can we hop on a call about the renewal?",
            }
        ],
    }


def _batch_request(*item_dicts) -> BatchTriageRequest:
    return BatchTriageRequest.model_validate({"items": list(item_dicts)})


# ---------------------------------------------------------------------------
# Item 3a — 3-item array: all results populated, indices match, no errors
# ---------------------------------------------------------------------------


def test_three_item_array_all_succeed():
    """A 3-item request returns 3 BatchItemResults, each with a result set."""
    req = _batch_request(_single_item_dict(), _thread_item_dict(), _single_item_dict())
    fake = _CountingFakeChat()
    service = EmailTriageService()
    response = service.triage_batch(req, chat=fake)

    results = response.results
    assert len(results) == 3, f"expected 3 results, got {len(results)}"

    for i, item_result in enumerate(results):
        assert isinstance(item_result, BatchItemResult)
        assert item_result.index == i, f"item {i}: wrong index {item_result.index}"
        assert item_result.result is not None, f"item {i}: result is None"
        assert (
            item_result.error is None
        ), f"item {i}: unexpected error {item_result.error}"


# ---------------------------------------------------------------------------
# Item 3b — per-item isolation: item 1 fails, items 0 and 2 still succeed
# ---------------------------------------------------------------------------


def test_per_item_isolation_failure_at_item_1():
    """A failure on item index 1 sets its error; items 0 and 2 still have results."""
    req = _batch_request(_single_item_dict(), _single_item_dict(), _single_item_dict())
    # Each item makes 2 calls: item0→calls1+2, item1→calls3+4, item2→calls5+6.
    # Failing call 3 triggers an error on item index 1.
    fake = _CountingFakeChat(fail_on_call=3)
    service = EmailTriageService()
    response = service.triage_batch(req, chat=fake)

    results = response.results
    assert len(results) == 3

    assert results[0].result is not None, "item 0 should have succeeded"
    assert results[0].error is None, "item 0 should have no error"

    assert results[1].error is not None, "item 1 should have an error"
    assert results[1].result is None, "item 1 error result should be None"

    assert results[2].result is not None, "item 2 should have succeeded"
    assert results[2].error is None, "item 2 should have no error"


# ---------------------------------------------------------------------------
# Item 3c — empty items → 422 via TestClient on /triage/batch
# ---------------------------------------------------------------------------


def test_empty_items_returns_422():
    """POST {"items": []} to /triage/batch is rejected with HTTP 422."""
    from fastapi.testclient import TestClient
    from gaia_agent_email.export_openapi import build_app

    client = TestClient(build_app())
    resp = client.post("/v1/email/triage/batch", json={"items": []})
    assert (
        resp.status_code == 422
    ), f"expected 422 for empty items list, got {resp.status_code}"


# ---------------------------------------------------------------------------
# Item 3d — old single-endpoint `payload` shape → 422 on /triage/batch
# ---------------------------------------------------------------------------


def test_old_payload_shape_on_batch_returns_422():
    """A schema-2.0 {payload: …} body POSTed to /triage/batch is rejected with 422."""
    from fastapi.testclient import TestClient
    from gaia_agent_email.export_openapi import build_app

    client = TestClient(build_app())
    old_shape = {
        "payload": {
            "kind": "single",
            "principal": {"email": "alice@example.com"},
            "message": {
                "message_id": "msg-x",
                "from": {"email": "bob@vendor.com"},
                "subject": "old shape",
                "body": "This uses the single-email payload field.",
            },
        }
    }
    resp = client.post("/v1/email/triage/batch", json=old_shape)
    assert (
        resp.status_code == 422
    ), f"expected 422 for 'payload' shape on batch endpoint, got {resp.status_code}"


# ---------------------------------------------------------------------------
# Item 3e — over MAX_BATCH_SIZE (101 items) → 422 via TestClient
# ---------------------------------------------------------------------------


def test_over_max_batch_size_returns_422():
    """POST with 101 items to /triage/batch is rejected with HTTP 422."""
    from fastapi.testclient import TestClient
    from gaia_agent_email.export_openapi import build_app

    client = TestClient(build_app())
    items = [_single_item_dict() for _ in range(101)]
    resp = client.post("/v1/email/triage/batch", json={"items": items})
    assert (
        resp.status_code == 422
    ), f"expected 422 for 101 items (over MAX_BATCH_SIZE), got {resp.status_code}"


# ---------------------------------------------------------------------------
# Item 3f — Lemonade unreachable → whole-request 502 / LLMTriageError
# ---------------------------------------------------------------------------


def test_lemonade_unreachable_raises_llm_triage_error(monkeypatch):
    """When Lemonade is unreachable (no chat arg), triage_batch raises LLMTriageError."""
    import requests as requests_mod

    def _fake_get(url, *args, **kwargs):
        raise requests_mod.exceptions.ConnectionError("Connection refused")

    monkeypatch.setattr(requests_mod, "get", _fake_get)

    req = _batch_request(_single_item_dict())
    service = EmailTriageService()

    with pytest.raises(LLMTriageError):
        service.triage_batch(req)


def test_lemonade_unreachable_returns_502_via_test_client(monkeypatch):
    """The /v1/email/triage/batch endpoint surfaces a Lemonade failure as HTTP 502."""
    import requests as requests_mod
    from fastapi.testclient import TestClient
    from gaia_agent_email.export_openapi import build_app

    def _fake_get(url, *args, **kwargs):
        raise requests_mod.exceptions.ConnectionError("Connection refused")

    monkeypatch.setattr(requests_mod, "get", _fake_get)

    client = TestClient(build_app(), raise_server_exceptions=False)
    resp = client.post(
        "/v1/email/triage/batch",
        json={"items": [_single_item_dict()]},
    )
    assert (
        resp.status_code == 502
    ), f"expected 502 for unreachable Lemonade, got {resp.status_code}"


# ---------------------------------------------------------------------------
# REGRESSION — single-endpoint still returns schema-2.0 {request_kind, result}
# ---------------------------------------------------------------------------


def test_single_triage_endpoint_unchanged_regression(monkeypatch):
    """REGRESSION: POST to /v1/email/triage still returns {request_kind, result}.

    This proves the additive guarantee: the existing single-email endpoint
    is untouched by the batch addition.
    """
    from fastapi.testclient import TestClient

    # Override triage_request on the service to return a fixed response, so the
    # TestClient exercises the route shape without a running Lemonade server.
    from gaia_agent_email import api_routes as ar_mod
    from gaia_agent_email.contract import (
        EmailCategory,
        EmailTriageResponse,
        EmailTriageResult,
    )
    from gaia_agent_email.export_openapi import build_app

    _fake_result = EmailTriageResult(
        category=EmailCategory.NEEDS_RESPONSE,
        summary="regression test summary",
        is_spam=False,
        is_phishing=False,
    )
    _fake_response = EmailTriageResponse(
        request_kind="single",
        result=_fake_result,
    )

    original_triage_request = ar_mod.EmailTriageService.triage_request

    def _patched_triage_request(self, request, chat=None):
        return _fake_response

    monkeypatch.setattr(
        ar_mod.EmailTriageService, "triage_request", _patched_triage_request
    )

    client = TestClient(build_app())
    payload = {
        "schema_version": "2.0",
        "payload": {
            "kind": "single",
            "principal": {"email": "alice@example.com"},
            "message": {
                "message_id": "msg-reg",
                "from": {"email": "bob@vendor.com"},
                "subject": "regression",
                "body": "Check the contract is unchanged.",
            },
        },
    }
    resp = client.post("/v1/email/triage", json=payload)
    assert (
        resp.status_code == 200
    ), f"single /triage endpoint returned {resp.status_code}: {resp.text}"
    body = resp.json()
    # Must have request_kind and result — the schema-2.0 shape
    assert (
        "request_kind" in body
    ), f"'request_kind' missing from /triage response: {body}"
    assert "result" in body, f"'result' missing from /triage response: {body}"
    # Must NOT have a 'results' key (that's the batch shape)
    assert (
        "results" not in body
    ), f"single /triage returned batch 'results' key — endpoint was broken: {body}"
