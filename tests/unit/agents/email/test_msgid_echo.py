# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for message_id echo in the triage response (issue #1539).

The EmailTriageResult echoes the identifying id of the triaged input so
consuming apps can correlate results and deduplicate across polling loops.

AC coverage:
- test_single_triage_echoes_message_id: single email input → result.message_id
  matches input message.message_id (heuristic engine AND llm engine).
- test_thread_triage_echoes_id: thread input → result.message_id echoes the
  thread's identifying id (ThreadInput.thread_id, which the contract requires).
- test_contract_backward_compatible: EmailTriageResult still validates WITHOUT
  message_id (optional field — existing callers not broken).
- Schema tests live in test_contract_schema.py; backward compat assertions here
  are a superset to keep this file self-contained.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("fastapi")

from gaia.agents.email.contract import (  # noqa: E402
    EmailAddress,
    EmailMessage,
    EmailTriageRequest,
    EmailTriageResponse,
    EmailTriageResult,
    SingleEmailInput,
    ThreadInput,
)

# ---------------------------------------------------------------------------
# Chat double for engine=llm tests (no live Lemonade)
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeChat:
    """Returns canned JSON for classify and a plain summary for summarize."""

    def __init__(
        self,
        category: str = "actionable",
        summary: str = "Needs your reply.",
    ) -> None:
        self._category = category
        self._summary = summary

    def send_messages(self, messages, system_prompt=None, **kwargs):
        if system_prompt and "Respond with a single JSON" in system_prompt:
            cat = self._category
            return _Resp(
                f'{{"category": "{cat}", "confidence": 0.82, "reasoning": "stub"}}'
            )
        return _Resp(self._summary)


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_PRINCIPAL = EmailAddress(email="alice@example.com")
_SENDER = EmailAddress(name="Bob Smith", email="bob@company.com")


def _single_request(message_id: str) -> EmailTriageRequest:
    return EmailTriageRequest(
        payload=SingleEmailInput(
            principal=_PRINCIPAL,
            message=EmailMessage(
                message_id=message_id,
                from_=_SENDER,
                to=[_PRINCIPAL],
                subject="Project update",
                body="Please review the attached proposal when you get a chance.",
            ),
        )
    )


def _thread_request(
    thread_id: str,
    msg_ids: list[str],
) -> EmailTriageRequest:
    messages = [
        EmailMessage(
            message_id=mid,
            thread_id=thread_id,
            from_=(_SENDER if i % 2 == 0 else _PRINCIPAL),
            to=[_PRINCIPAL if i % 2 == 0 else _SENDER],
            subject="Contract renewal",
            body=f"Message {i + 1} body.",
        )
        for i, mid in enumerate(msg_ids)
    ]
    return EmailTriageRequest(
        payload=ThreadInput(
            principal=_PRINCIPAL,
            thread_id=thread_id,
            messages=messages,
        )
    )


# ---------------------------------------------------------------------------
# AC1: single email echoes message_id
# ---------------------------------------------------------------------------


class TestSingleTriageEchoesMessageId:
    """result.message_id == input message.message_id for both engines."""

    def test_heuristic_engine_echoes_message_id(self):
        from gaia.api.email_routes import EmailTriageService

        service = EmailTriageService()
        response = service.triage_request(_single_request("m-42"))

        assert isinstance(response, EmailTriageResponse)
        assert response.result.message_id == "m-42"

    def test_llm_engine_echoes_message_id(self):
        from gaia.api.email_routes import EmailTriageService

        service = EmailTriageService()
        response = service.triage_request(
            _single_request("m-99"),
            engine="llm",
            chat=_FakeChat(),
        )

        assert isinstance(response, EmailTriageResponse)
        assert response.result.message_id == "m-99"

    def test_different_message_ids_echo_correctly(self):
        """Each request echoes its own id — ids don't bleed across calls."""
        from gaia.api.email_routes import EmailTriageService

        service = EmailTriageService()
        for mid in ("abc-1", "xyz-999", ""):
            resp = service.triage_request(_single_request(mid))
            assert resp.result.message_id == mid


# ---------------------------------------------------------------------------
# AC2: thread echoes the identifying id
# ---------------------------------------------------------------------------


class TestThreadTriageEchoesId:
    """Thread result echoes thread_id (the thread's canonical identifier)."""

    def test_thread_echoes_thread_id(self):
        """ThreadInput.thread_id is the canonical identifier for a thread."""
        from gaia.api.email_routes import EmailTriageService

        service = EmailTriageService()
        response = service.triage_request(
            _thread_request("t-77", ["m-1", "m-2", "m-3"])
        )

        assert isinstance(response, EmailTriageResponse)
        assert response.result.message_id == "t-77"

    def test_thread_llm_engine_echoes_thread_id(self):
        from gaia.api.email_routes import EmailTriageService

        service = EmailTriageService()
        response = service.triage_request(
            _thread_request("t-100", ["m-a", "m-b"]),
            engine="llm",
            chat=_FakeChat(),
        )

        assert response.result.message_id == "t-100"

    def test_thread_with_different_id_echoes_correctly(self):
        from gaia.api.email_routes import EmailTriageService

        service = EmailTriageService()
        response = service.triage_request(
            _thread_request("thread-abc-123", ["m-x", "m-y"])
        )
        assert response.result.message_id == "thread-abc-123"


# ---------------------------------------------------------------------------
# AC3: backward compatibility — message_id is optional
# ---------------------------------------------------------------------------


class TestContractBackwardCompatible:
    """EmailTriageResult still validates WITHOUT message_id (optional field)."""

    def test_result_validates_without_message_id(self):
        """A result dict without message_id validates successfully."""
        result = EmailTriageResult.model_validate(
            {
                "category": "actionable",
                "is_spam": False,
                "is_phishing": False,
                "summary": "Existing result without message_id.",
                "action_items": [],
                "draft": None,
            }
        )
        assert result.message_id is None

    def test_result_validates_with_none_message_id(self):
        result = EmailTriageResult.model_validate(
            {
                "category": "informational",
                "is_spam": False,
                "is_phishing": False,
                "summary": "Status update.",
                "action_items": [],
                "draft": None,
                "message_id": None,
            }
        )
        assert result.message_id is None

    def test_result_validates_with_string_message_id(self):
        result = EmailTriageResult.model_validate(
            {
                "category": "urgent",
                "is_spam": False,
                "is_phishing": False,
                "summary": "Needs immediate action.",
                "action_items": [],
                "draft": None,
                "message_id": "msg-abc-123",
            }
        )
        assert result.message_id == "msg-abc-123"

    def test_existing_sample_payloads_still_parse(self):
        """Sample payloads from test_contract_schema.py still validate."""
        from gaia.agents.email.contract import SCHEMA_VERSION, EmailTriageResponse

        # The frozen sample response from test_contract_schema — no message_id field.
        sample = {
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
        response = EmailTriageResponse.model_validate(sample)
        assert response.result.message_id is None

    def test_thread_sample_payload_still_parses(self):
        from gaia.agents.email.contract import SCHEMA_VERSION, EmailTriageResponse

        sample = {
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
        response = EmailTriageResponse.model_validate(sample)
        assert response.result.message_id is None
