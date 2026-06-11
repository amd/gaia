# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Regression tests for the split exception handlers in the email summarize tools.

CRITICAL 1 fix (#1592 review): EmailSummarizeError was previously caught in the
same branch as ConnectorsError and routed through format_connector_error, which
mapped it to "UNEXPECTED_ERROR: EmailSummarizeError: …" instead of the plain
str(exc) users and the LLM see.  These tests guard against regression.

Coverage:
- ``summarize_message`` tool: EmailSummarizeError → plain str(exc), no CTA prefix
- ``summarize_message`` tool: ConnectorsError/AuthRequiredError → AGENT_NOT_GRANTED: prefix
- ``summarize_thread`` tool: same split behavior
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.tools.summarize_tools import EmailSummarizeError  # noqa: E402

from gaia.connectors.errors import AuthRequiredError  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _make_email_agent(tmp_path):
    """Construct an EmailTriageAgent with all tool mixins registered."""
    from gaia_agent_email.agent import EmailTriageAgent
    from gaia_agent_email.config import EmailAgentConfig

    cfg = EmailAgentConfig(
        gmail_backend=MagicMock(),
        calendar_backend=MagicMock(),
        db_path=str(tmp_path / "state.db"),
        silent_mode=True,
    )
    with (
        patch("gaia.llm.lemonade_manager.LemonadeManager.ensure_ready"),
        patch("gaia.agents.base.agent.AgentSDK") as mock_sdk,
    ):
        mock_sdk.return_value = MagicMock()
        agent = EmailTriageAgent(config=cfg)
    return agent


def _get_tool(name: str):
    from gaia.agents.base.tools import _TOOL_REGISTRY

    return _TOOL_REGISTRY[name]["function"]


class TestSummarizeMessageErrorHandlerSplit:
    """summarize_message tool must split ConnectorsError and EmailSummarizeError."""

    @pytest.fixture(autouse=True)
    def _agent(self, tmp_path):
        agent = _make_email_agent(tmp_path)
        yield agent
        agent.close_db()

    def test_email_summarize_error_yields_plain_str_no_prefix(self):
        """EmailSummarizeError must produce the plain error string, NOT UNEXPECTED_ERROR."""
        exc_msg = "No usable summary produced for message-id=abc123 (empty LLM output)"
        with patch(
            "gaia_agent_email.tools.summarize_tools.summarize_message_impl",
            side_effect=EmailSummarizeError(exc_msg),
        ):
            result = _get_tool("summarize_message")("abc123")

        envelope = json.loads(result)
        assert envelope["ok"] is False
        error = envelope["error"]
        # Must carry the plain exception message.
        assert exc_msg in error
        # Must NOT carry any connector-error prefix.
        assert not error.startswith("UNEXPECTED_ERROR:")
        assert not error.startswith("AGENT_NOT_GRANTED:")
        assert not error.startswith("NOT_CONNECTED:")
        assert not error.startswith("AUTH_REQUIRED:")

    def test_connectors_error_yields_agent_not_granted_prefix(self):
        """ConnectorsError/AuthRequiredError must still produce the AGENT_NOT_GRANTED prefix."""
        auth_exc = AuthRequiredError(
            AuthRequiredError.Reason.AGENT_NOT_GRANTED,
            provider="google",
            agent_id="installed:email",
            missing_scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        )
        with patch(
            "gaia_agent_email.tools.summarize_tools.summarize_message_impl",
            side_effect=auth_exc,
        ):
            result = _get_tool("summarize_message")("abc123")

        envelope = json.loads(result)
        assert envelope["ok"] is False
        error = envelope["error"]
        # Must carry the connector-error prefix or the installed:email override message.
        assert "AGENT_NOT_GRANTED:" in error or "Email agent needs additional" in error


class TestSummarizeThreadErrorHandlerSplit:
    """summarize_thread tool (read_tools) must split ConnectorsError and EmailSummarizeError."""

    @pytest.fixture(autouse=True)
    def _agent(self, tmp_path):
        agent = _make_email_agent(tmp_path)
        yield agent
        agent.close_db()

    def test_email_summarize_error_yields_plain_str_no_prefix(self):
        """EmailSummarizeError from summarize_thread must not be wrapped in UNEXPECTED_ERROR."""
        exc_msg = "No usable summary produced for thread-id=t99 (empty LLM output)"
        with patch(
            "gaia_agent_email.tools.read_tools.summarize_thread_impl",
            side_effect=EmailSummarizeError(exc_msg),
        ):
            result = _get_tool("summarize_thread")("t99")

        envelope = json.loads(result)
        assert envelope["ok"] is False
        error = envelope["error"]
        assert exc_msg in error
        assert not error.startswith("UNEXPECTED_ERROR:")
        assert not error.startswith("AGENT_NOT_GRANTED:")
        assert not error.startswith("NOT_CONNECTED:")

    def test_connectors_error_yields_agent_not_granted_prefix(self):
        """ConnectorsError from summarize_thread must still produce the expected prefix."""
        auth_exc = AuthRequiredError(
            AuthRequiredError.Reason.AGENT_NOT_GRANTED,
            provider="google",
            agent_id="installed:email",
            missing_scopes=["https://www.googleapis.com/auth/gmail.modify"],
        )
        with patch(
            "gaia_agent_email.tools.read_tools.summarize_thread_impl",
            side_effect=auth_exc,
        ):
            result = _get_tool("summarize_thread")("t99")

        envelope = json.loads(result)
        assert envelope["ok"] is False
        error = envelope["error"]
        assert "AGENT_NOT_GRANTED:" in error or "Email agent needs additional" in error
