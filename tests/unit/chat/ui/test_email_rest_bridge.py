# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
EmailRestBridge — rip-out of the in-process email agent (#1653).

The email agent_type no longer instantiates an in-process ChatAgent backed by
EmailTriageAgent; instead the UI chat path calls EmailTriageService directly
(the same object the mounted /v1/email/triage route uses).

These tests cover:
  - The bridge parses a plain-text user message into an EmailTriageRequest.
  - The bridge calls EmailTriageService.triage_request and formats the result.
  - Auth/grant errors surface the AGENT_NOT_GRANTED sentinel (same prefix the
    frontend's EmailConnectCta detects).
  - Unparseable messages return a guide string (not a crash).
  - _session_mail_provider is GONE from _chat_helpers.
  - The mail_provider= kwarg is NO LONGER passed to registry.create_agent.
  - pre_scan_inbox is NOT in _RENDER_TOOL_TO_LANG.
"""

from __future__ import annotations

import inspect
from unittest.mock import patch

import pytest

pytest.importorskip("gaia.ui._chat_helpers")
pytest.importorskip("gaia_agent_email")

from gaia_agent_email.api_routes import EmailTriageService
from gaia_agent_email.contract import (
    EmailAddress,
    EmailMessage,
    EmailTriageRequest,
    EmailTriageResponse,
    EmailTriageResult,
)

from gaia.ui._chat_helpers import _email_rest_bridge, _parse_email_from_text

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_triage_response() -> EmailTriageResponse:
    """Minimal valid EmailTriageResponse for mock returns."""
    from gaia_agent_email.contract import EmailCategory

    result = EmailTriageResult(
        category=EmailCategory.NEEDS_RESPONSE,
        is_spam=False,
        is_phishing=False,
        summary="Reply to Alice about the Q3 report by Friday.",
        action_items=[],
    )
    return EmailTriageResponse(request_kind="single", result=result)


def _make_message(
    subject: str = "Test subject", body: str = "Test body"
) -> EmailMessage:
    return EmailMessage(
        message_id="msg1",
        **{"from": EmailAddress(email="alice@example.com")},
        subject=subject,
        body=body,
    )


# ---------------------------------------------------------------------------
# _parse_email_from_text
# ---------------------------------------------------------------------------


class TestParseEmailFromText:
    """_parse_email_from_text converts free-text user messages into an
    EmailTriageRequest — or returns None when no email content is found."""

    def test_returns_none_for_empty_message(self):
        assert _parse_email_from_text("") is None

    def test_returns_none_for_no_email_indicators(self):
        assert _parse_email_from_text("What is the weather today?") is None

    def test_parses_subject_and_body(self):
        text = "Subject: Q3 report\nFrom: alice@example.com\n\nHi, please review the report by Friday."
        result = _parse_email_from_text(text)
        assert result is not None
        assert isinstance(result, EmailTriageRequest)
        assert result.payload.kind == "single"
        assert result.payload.message.subject == "Q3 report"
        assert "Friday" in result.payload.message.body

    def test_parses_from_address(self):
        text = (
            "From: bob@company.com\nSubject: Urgent action needed\n\nPlease reply ASAP."
        )
        result = _parse_email_from_text(text)
        assert result is not None
        assert result.payload.message.from_.email == "bob@company.com"

    def test_parses_plain_email_body_only(self):
        """When the message looks like an email body (long text with typical email cues)."""
        text = "Hi Alice,\n\nCould you please review the attached document and reply by EOD?\n\nThanks,\nBob"
        result = _parse_email_from_text(text)
        assert result is not None
        assert "Could you please" in result.payload.message.body

    def test_uses_unknown_sender_when_from_missing(self):
        """Missing From header falls back to a placeholder so triage still runs."""
        text = "Subject: Meeting notes\n\nHere are the notes from today's meeting."
        result = _parse_email_from_text(text)
        assert result is not None
        # From is synthesized — must be a valid EmailAddress
        assert "@" in result.payload.message.from_.email

    def test_guide_hint_keywords_trigger_parse(self):
        """Common email keywords ('Dear', 'Hi ', salutation) mark the text as email content."""
        text = "Dear John,\n\nI wanted to follow up on our last conversation. Could you let me know the status?"
        result = _parse_email_from_text(text)
        assert result is not None


# ---------------------------------------------------------------------------
# _email_rest_bridge — triage path
# ---------------------------------------------------------------------------


class TestEmailRestBridge:
    """_email_rest_bridge replaces the in-process email agent dispatch."""

    def test_returns_formatted_triage_result(self):
        """Bridge calls EmailTriageService and returns a formatted summary string."""
        mock_resp = _make_triage_response()
        with patch.object(EmailTriageService, "triage_request", return_value=mock_resp):
            result = _email_rest_bridge(
                "From: alice@example.com\nSubject: Q3 report\n\nPlease review by Friday."
            )
        assert isinstance(result, str)
        assert len(result) > 0
        # Category value (lowercase str) should appear in the output.
        assert "NEEDS_RESPONSE" in result or "Reply" in result or "Q3 report" in result

    def test_returns_guide_when_no_email_content(self):
        """When the user's message contains no email content, return a helpful guide."""
        result = _email_rest_bridge("hello")
        assert isinstance(result, str)
        # Must tell the user what to do — NOT raise.
        assert any(
            kw in result.lower()
            for kw in ["paste", "email", "subject", "from", "triage"]
        )

    def test_unexpected_service_error_propagates(self):
        """Errors from the service that are NOT LLMTriageError/EmailSummarizeError
        propagate as-is (fail loud; no silent swallowing)."""
        with patch.object(
            EmailTriageService,
            "triage_request",
            side_effect=RuntimeError("unexpected backend failure"),
        ):
            with pytest.raises(RuntimeError, match="unexpected backend failure"):
                _email_rest_bridge(
                    "From: alice@example.com\nSubject: test\n\nBody text."
                )

    def test_surfaces_lemonade_error(self):
        """LLMTriageError (LLM backend down) returns an actionable error string."""
        from gaia_agent_email.tools.llm_triage import LLMTriageError

        with patch.object(
            EmailTriageService,
            "triage_request",
            side_effect=LLMTriageError("Lemonade not reachable"),
        ):
            result = _email_rest_bridge(
                "From: alice@example.com\nSubject: test\n\nBody text."
            )
        assert isinstance(result, str)
        assert len(result) > 0
        assert (
            "lemonade" in result.lower()
            or "llm" in result.lower()
            or "model" in result.lower()
        )

    def test_bridge_calls_service_with_valid_triage_request(self):
        """Bridge passes a well-formed EmailTriageRequest to EmailTriageService."""
        mock_resp = _make_triage_response()
        calls = []

        def _capture(req):
            calls.append(req)
            return mock_resp

        with patch.object(EmailTriageService, "triage_request", side_effect=_capture):
            _email_rest_bridge(
                "From: alice@example.com\nSubject: test\n\nBody text.",
            )
        assert len(calls) == 1
        req = calls[0]
        assert isinstance(req, EmailTriageRequest)
        assert req.payload.kind == "single"
        assert req.payload.message.from_.email == "alice@example.com"


# ---------------------------------------------------------------------------
# Rip-out canaries: verify removed symbols and patterns are gone
# ---------------------------------------------------------------------------


class TestRipOutCanaries:
    """Source-level assertions that the in-process email wiring is gone."""

    def test_session_mail_provider_removed(self):
        """_session_mail_provider must NOT exist in _chat_helpers."""
        import gaia.ui._chat_helpers as ch

        assert not hasattr(
            ch, "_session_mail_provider"
        ), "_session_mail_provider still exists — in-process email wiring not ripped out"

    def test_mail_provider_kwarg_not_passed_to_create_agent(self):
        """registry.create_agent must never receive mail_provider= in _chat_helpers.

        Source-level canary: the in-process wiring that forwarded the session's
        mail_provider to create_agent has been deleted.
        """
        import gaia.ui._chat_helpers as ch

        src = inspect.getsource(ch)
        assert (
            "mail_provider=_session_mail_provider" not in src
        ), "mail_provider= is still forwarded to create_agent — rip-out incomplete"

    def test_pre_scan_inbox_not_in_render_tool_map(self):
        """pre_scan_inbox must NOT be a key in SSEOutputHandler._RENDER_TOOL_TO_LANG.

        The pre-scan render path is dropped (#1778 follow-up); its SSE injection
        should be removed from the handler.
        """
        from gaia.ui.sse_handler import SSEOutputHandler

        assert (
            "pre_scan_inbox" not in SSEOutputHandler._RENDER_TOOL_TO_LANG
        ), "pre_scan_inbox still registered in _RENDER_TOOL_TO_LANG — SSE drop incomplete"

    def test_email_rest_bridge_exists(self):
        """_email_rest_bridge must be importable from _chat_helpers."""
        import gaia.ui._chat_helpers as ch

        assert hasattr(
            ch, "_email_rest_bridge"
        ), "_email_rest_bridge not found in _chat_helpers — REST re-point not implemented"
