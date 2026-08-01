# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""#2643 AC5 — raise the default per-call scan size from 25 to 50.

Two DIFFERENT numbers govern the pre_scan_inbox / triage_inbox tools, and
this issue only touches one of them:

- The tool's OWN Python default (``max_messages: int = 25`` on the
  registered ``@tool`` function) — what the agent gets when it calls the
  tool WITHOUT specifying a count. This is what #2643 raises, to 50.
- The hard ceiling (``config.default_inbox_scan_ceiling()``, 100 by
  default, overridable via ``GAIA_EMAIL_TRIAGE_MAX_MESSAGES``) — the most
  the agent can ask for even when it explicitly requests more. This was
  ALREADY 100 before #2643 and is unchanged by it.

These tests pin both: the default moves, the ceiling doesn't.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.agent import EmailTriageAgent  # noqa: E402
from gaia_agent_email.config import EmailAgentConfig, default_inbox_scan_ceiling  # noqa: E402

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


def _make_agent(tmp_path):
    cfg = EmailAgentConfig(
        gmail_backend=FakeGmailBackend(),
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


def _registered_tool(name: str):
    from gaia.agents.base.tools import _TOOL_REGISTRY

    return _TOOL_REGISTRY[name]["function"]


class TestDefaultCeilingRaisedTo50:
    def test_pre_scan_inbox_tool_default_is_50(self, tmp_path):
        agent = _make_agent(tmp_path)
        try:
            fn = _registered_tool("pre_scan_inbox")
            default = inspect.signature(fn).parameters["max_messages"].default
        finally:
            agent.close_db()
        assert default == 50, (
            f"pre_scan_inbox's own default must be 50 (was 25 pre-#2643), "
            f"got {default!r}"
        )

    def test_triage_inbox_tool_default_is_50(self, tmp_path):
        agent = _make_agent(tmp_path)
        try:
            fn = _registered_tool("triage_inbox")
            default = inspect.signature(fn).parameters["max_messages"].default
        finally:
            agent.close_db()
        assert default == 50, (
            f"triage_inbox's own default must be 50 (was 25 pre-#2643), "
            f"got {default!r}"
        )


class TestHardCeilingUnchangedByThisIssue:
    def test_default_inbox_scan_ceiling_is_still_100(self, monkeypatch):
        monkeypatch.delenv("GAIA_EMAIL_TRIAGE_MAX_MESSAGES", raising=False)
        assert default_inbox_scan_ceiling() == 100, (
            "the hard ceiling was already 100 before #2643 (config.py's "
            "DEFAULT_INBOX_SCAN_CEILING) -- #2643 raises the tool's OWN "
            "default (25->50), never this ceiling"
        )

    def test_pre_scan_inbox_can_still_be_asked_for_up_to_100(self, tmp_path):
        gmail = FakeGmailBackend()
        for i in range(120):
            gmail.add_message(
                {
                    "id": f"m{i}",
                    "threadId": f"t{i}",
                    "labelIds": ["INBOX", "CATEGORY_UPDATES"],
                    "snippet": "hello",
                    "internalDate": str(1_700_000_000_000 + i),
                    "payload": {
                        "mimeType": "text/plain",
                        "headers": [
                            {"name": "Subject", "value": f"Update {i}"},
                            {"name": "From", "value": "svc@example.com"},
                        ],
                        "body": {"size": 5, "data": ""},
                    },
                }
            )
        cfg = EmailAgentConfig(
            gmail_backend=gmail,
            db_path=str(tmp_path / "state.db"),
            silent_mode=True,
        )
        with (
            patch("gaia.llm.lemonade_manager.LemonadeManager.ensure_ready"),
            patch("gaia.agents.base.agent.AgentSDK") as mock_sdk,
        ):
            mock_sdk.return_value = MagicMock()
            agent = EmailTriageAgent(config=cfg)
        try:
            import json

            fn = _registered_tool("pre_scan_inbox")
            envelope = json.loads(fn(max_messages=100))
            assert envelope["ok"] is True
            assert envelope["data"]["scanned"] == 100, envelope["data"]["scanned"]
        finally:
            agent.close_db()
