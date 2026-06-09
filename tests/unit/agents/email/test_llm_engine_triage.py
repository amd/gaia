# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for the engine=llm triage path in POST /v1/email/triage (#1452).

All four acceptance criteria are covered here, without a live Lemonade
backend — the chat client is mocked throughout.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("fastapi")

from gaia.agents.email.config import ConfigurationError  # noqa: E402
from gaia.agents.email.contract import (  # noqa: E402
    EmailAddress,
    EmailCategory,
    EmailMessage,
    EmailTriageRequest,
    EmailTriageResponse,
    EmailTriageResult,
    SingleEmailInput,
)

# ---------------------------------------------------------------------------
# Chat doubles
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeChat:
    """Returns canned JSON for classify and a plain summary for summarize."""

    def __init__(
        self, category: str = "actionable", summary: str = "Needs your reply."
    ) -> None:
        self._category = category
        self._summary = summary
        self.calls: list[str] = []

    def send_messages(self, messages, system_prompt=None, **kwargs):
        # Distinguish classify vs summarize by the system_prompt content.
        if system_prompt and "Respond with a single JSON" in system_prompt:
            self.calls.append("classify")
            return _Resp(
                f'{{"category": "{self._category}", "confidence": 0.82, "reasoning": "stub"}}'
            )
        self.calls.append("summarize")
        return _Resp(self._summary)


# A low-heuristic-confidence payload: a personal human sender with no labels —
# the heuristic returns confident=False and falls through to LLM.
_LOW_CONFIDENCE_REQUEST = EmailTriageRequest(
    payload=SingleEmailInput(
        principal=EmailAddress(email="alice@example.com"),
        message=EmailMessage(
            message_id="msg-42",
            from_=EmailAddress(name="Bob Smith", email="bob@company.com"),
            to=[EmailAddress(email="alice@example.com")],
            subject="Quick question on the proposal",
            body="Hi Alice, can you review the Q3 proposal when you get a chance?",
        ),
    )
)


# ---------------------------------------------------------------------------
# AC1: engine=llm returns LLM category + summary on low-confidence input
# ---------------------------------------------------------------------------


class TestTriageEngineLLM:
    """AC1 — engine=llm escalates to LLM and returns its category/summary."""

    def test_llm_category_and_summary_returned(self):
        from gaia.api.email_routes import EmailTriageService

        chat = _FakeChat(category="actionable", summary="Bob wants a proposal review.")
        service = EmailTriageService()
        response = service.triage_request(
            _LOW_CONFIDENCE_REQUEST, engine="llm", chat=chat
        )

        assert isinstance(response, EmailTriageResponse)
        result = response.result
        # Category from LLM, not heuristic fallback
        assert result.category == EmailCategory.ACTIONABLE
        # Summary from LLM summarizer
        assert "proposal" in result.summary.lower() or "bob" in result.summary.lower()
        # At least one classify call was made
        assert "classify" in chat.calls
        assert "summarize" in chat.calls

    def test_contract_shape_preserved_on_llm_engine(self):
        """The response shape is the exact frozen #1262 contract."""
        from gaia.api.email_routes import EmailTriageService

        chat = _FakeChat(category="urgent", summary="Urgent: system down.")
        service = EmailTriageService()
        response = service.triage_request(
            _LOW_CONFIDENCE_REQUEST, engine="llm", chat=chat
        )

        assert isinstance(response, EmailTriageResponse)
        assert isinstance(response.result, EmailTriageResult)
        # Frozen contract fields
        assert hasattr(response.result, "category")
        assert hasattr(response.result, "is_spam")
        assert hasattr(response.result, "is_phishing")
        assert hasattr(response.result, "summary")
        assert hasattr(response.result, "action_items")
        assert hasattr(response.result, "draft")

    def test_high_confidence_heuristic_skips_llm_classify(self):
        """Spam label → heuristic is confident → LLM classify not called."""
        from gaia.agents.email.tools.triage_heuristics import HeuristicResult
        from gaia.api.email_routes import EmailTriageService

        chat = _FakeChat(category="low priority", summary="spam.")
        service = EmailTriageService()

        spam_req = EmailTriageRequest(
            payload=SingleEmailInput(
                principal=EmailAddress(email="alice@example.com"),
                message=EmailMessage(
                    message_id="msg-spam",
                    from_=EmailAddress(name="Promo", email="deals@promo.com"),
                    subject="Buy now! 50% off",
                    body="Limited time offer!",
                ),
            )
        )

        with patch(
            "gaia.api.email_routes.classify_category_heuristic",
            return_value=HeuristicResult(
                category="low priority",
                is_spam=True,
                confident=True,
                reason="SPAM label",
            ),
        ):
            response = service.triage_request(spam_req, engine="llm", chat=chat)

        # Heuristic was confident — LLM should not classify
        assert "classify" not in chat.calls
        assert response.result.is_spam is True


# ---------------------------------------------------------------------------
# AC2: default engine=heuristic is byte-unchanged; no LLM call
# ---------------------------------------------------------------------------


class TestTriageDefaultEngineHeuristic:
    """AC2 — default (no engine param) and engine=heuristic do not call the LLM."""

    def _build_service_with_spy(self):
        from gaia.api.email_routes import EmailTriageService

        chat = _FakeChat()
        service = EmailTriageService()
        return service, chat

    def test_no_engine_param_uses_heuristic(self):
        service, chat = self._build_service_with_spy()
        # Call with no engine kwarg — defaults to heuristic
        response = service.triage_request(_LOW_CONFIDENCE_REQUEST)

        assert isinstance(response, EmailTriageResponse)
        assert chat.calls == []  # no LLM calls

    def test_engine_heuristic_explicit_uses_heuristic(self):
        service, chat = self._build_service_with_spy()
        response = service.triage_request(
            _LOW_CONFIDENCE_REQUEST, engine="heuristic", chat=chat
        )
        assert isinstance(response, EmailTriageResponse)
        assert chat.calls == []  # no LLM calls

    def test_heuristic_result_matches_baseline(self):
        """The heuristic result with no engine is identical to pre-change behaviour."""
        from gaia.api.email_routes import EmailTriageService

        service_old = EmailTriageService()
        service_new = EmailTriageService()

        result_old = service_old.triage_request(_LOW_CONFIDENCE_REQUEST)
        result_new = service_new.triage_request(
            _LOW_CONFIDENCE_REQUEST, engine="heuristic"
        )

        assert result_old.result.category == result_new.result.category
        assert result_old.result.is_spam == result_new.result.is_spam
        assert result_old.result.is_phishing == result_new.result.is_phishing
        assert result_old.result.summary == result_new.result.summary

    def test_invalid_engine_raises(self):
        from gaia.api.email_routes import EmailTriageService

        service = EmailTriageService()
        with pytest.raises((ValueError, Exception)):
            service.triage_request(_LOW_CONFIDENCE_REQUEST, engine="magic")


# ---------------------------------------------------------------------------
# AC3: engine=llm rejects cloud base_url loudly (no silent fallback)
# ---------------------------------------------------------------------------


class TestTriageCloudBaseUrlRejected:
    """AC3 — constructing a triage chat client against a cloud base_url raises."""

    @pytest.mark.parametrize(
        "cloud_url",
        [
            "https://api.openai.com/v1",
            "https://api.anthropic.com/v1",
            "https://generativelanguage.googleapis.com",
            "https://example.com/llm",
        ],
    )
    def test_cloud_base_url_raises_configuration_error(self, cloud_url):
        from gaia.agents.email.config import EmailAgentConfig

        cfg = EmailAgentConfig(base_url=cloud_url)
        with pytest.raises(ConfigurationError) as exc_info:
            cfg.validate()
        msg = str(exc_info.value)
        # Error must name what failed, tell how to fix, and cite AC3
        assert "AC3" in msg
        assert "local" in msg.lower() or "allowlist" in msg.lower()

    def test_cloud_base_url_error_is_loud_not_silent_fallback(self):
        """There is NO silent downgrade to heuristic on a bad base_url."""
        from gaia.agents.email.config import ConfigurationError
        from gaia.api.email_routes import EmailTriageService

        service = EmailTriageService()

        # Verify that _build_llm_chat raises rather than returning None
        with pytest.raises((ConfigurationError, ValueError, RuntimeError)) as exc_info:
            service._build_llm_chat(base_url="https://api.openai.com/v1")
        msg = str(exc_info.value)
        # Must not be a generic "something went wrong" — must cite AC3 and the
        # local-only requirement. Asserting on the AC3/local markers (not a host
        # substring) avoids CodeQL's URL-substring-sanitization false positive.
        assert "AC3" in msg and ("local" in msg.lower() or "allowlist" in msg.lower())

    def test_local_base_url_does_not_raise(self):
        from gaia.agents.email.config import EmailAgentConfig

        for local_url in [
            "http://localhost:13305/api/v1",
            "http://127.0.0.1:13305",
        ]:
            cfg = EmailAgentConfig(base_url=local_url)
            cfg.validate()  # Must not raise


# ---------------------------------------------------------------------------
# AC4: enforcement artifacts (static lint gate + integration egress check)
# ---------------------------------------------------------------------------


class TestEnforcementArtifacts:
    """AC4 — util/check_email_agent_local_only.py must exist and be importable;
    the integration test that mocks egress detection must also exist.
    """

    def test_static_lint_gate_module_is_importable(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "check_email_agent_local_only",
            _REPO_ROOT / "util" / "check_email_agent_local_only.py",
        )
        assert spec is not None, (
            "util/check_email_agent_local_only.py missing — AC3 static lint gate "
            "must be codified (not just a docstring reference)."
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        # The gate must expose a callable (the check function).
        assert callable(getattr(mod, "check", None)) or callable(
            getattr(mod, "main", None)
        ), "check_email_agent_local_only.py must define a check() or main() entry point"

    def test_integration_local_only_test_exists(self):
        path = _REPO_ROOT / "tests" / "integration" / "test_email_agent_local_only.py"
        assert path.exists(), (
            "tests/integration/test_email_agent_local_only.py missing — "
            "AC3 egress detection integration test must be codified."
        )
