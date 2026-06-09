# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
AC3 local-only integration gate (#1452).

Asserts that the ``engine=llm`` path in ``EmailTriageService`` never emits
outbound HTTP requests to cloud LLM endpoints during a triage run.

The test mocks egress at the ``requests`` adapter level (the chokepoint
every ``requests``-based call passes through) so it runs without a live
Lemonade backend.  The ``chat`` client is also injected as a stub so no
network I/O is needed — the test purely verifies the *routing contract*, not
actual LLM output quality.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("fastapi")

from gaia.agents.email.contract import (  # noqa: E402
    EmailAddress,
    EmailMessage,
    EmailTriageRequest,
    SingleEmailInput,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Cloud-LLM hostnames that must NEVER receive a request during email triage.
_CLOUD_LLM_HOSTS = (
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "huggingface.co",
)


def _make_request() -> EmailTriageRequest:
    return EmailTriageRequest(
        payload=SingleEmailInput(
            principal=EmailAddress(email="alice@example.com"),
            message=EmailMessage(
                message_id="msg-local-only",
                from_=EmailAddress(name="Bob", email="bob@company.com"),
                subject="Can you review this?",
                body="Hi Alice, please review the attached doc when you get a chance.",
            ),
        )
    )


# ---------------------------------------------------------------------------
# Egress detection tests
# ---------------------------------------------------------------------------


class _EgressDetector:
    """Records all URLs that would be sent by requests.adapters.HTTPAdapter."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, adapter_self, request, *args, **kwargs):
        self.calls.append(request.url)
        raise RuntimeError(
            f"Unexpected real HTTP request during test: {request.url}. "
            "email_routes engine=llm must use the injected chat stub, not "
            "real network I/O."
        )


class TestNoCloudEgressDuringTriage:
    """The engine=llm path must not emit any HTTP calls to cloud LLM endpoints."""

    def test_engine_llm_with_injected_chat_makes_no_cloud_requests(self):
        from gaia.api.email_routes import EmailTriageService

        # Stub that answers classify and summarize without network calls.
        class _StubChat:
            def send_messages(self, messages, system_prompt=None, **kwargs):
                class _R:
                    text = '{"category": "actionable", "confidence": 0.9, "reasoning": "stub"}'

                if system_prompt and "Respond with a single JSON" in system_prompt:
                    return _R()

                class _S:
                    text = (
                        "Alice should review the document at her earliest convenience."
                    )

                return _S()

        service = EmailTriageService()
        detector = _EgressDetector()

        with patch("requests.adapters.HTTPAdapter.send", detector):
            # Inject the stub chat — no network I/O needed.
            response = service.triage_request(
                _make_request(), engine="llm", chat=_StubChat()
            )

        # No HTTP calls should have been made at all during the triage.
        assert (
            detector.calls == []
        ), f"engine=llm made unexpected HTTP calls: {detector.calls}"
        assert response.result.category.value in (
            "actionable",
            "urgent",
            "informational",
            "low priority",
        )

    def test_build_llm_chat_rejects_cloud_base_url_before_any_connection(self):
        """_build_llm_chat raises BEFORE any HTTP connection attempt."""
        from gaia.agents.email.config import ConfigurationError
        from gaia.api.email_routes import EmailTriageService

        service = EmailTriageService()
        detector = _EgressDetector()

        with patch("requests.adapters.HTTPAdapter.send", detector):
            with pytest.raises(
                (ConfigurationError, ValueError, RuntimeError)
            ) as exc_info:
                service._build_llm_chat(base_url="https://api.openai.com/v1")

        # The rejection must happen BEFORE any network call.
        assert detector.calls == [], (
            "A cloud URL check must raise BEFORE making any HTTP request. "
            f"Calls made: {detector.calls}"
        )
        msg = str(exc_info.value)
        # Error is actionable: names the bad host.
        assert "openai" in msg.lower() or "AC3" in msg or "cloud" in msg.lower()

    def test_heuristic_engine_makes_no_http_calls(self):
        """The heuristic (default) path must never touch the network."""
        from gaia.api.email_routes import EmailTriageService

        service = EmailTriageService()
        detector = _EgressDetector()

        with patch("requests.adapters.HTTPAdapter.send", detector):
            response = service.triage_request(_make_request())  # engine=heuristic

        assert (
            detector.calls == []
        ), f"engine=heuristic made unexpected HTTP calls: {detector.calls}"
        assert response is not None
