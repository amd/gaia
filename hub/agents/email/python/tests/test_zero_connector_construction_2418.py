# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Zero-connector construction path (#2418).

With NO mailbox connected and NO injected fake backend, ``EmailTriageAgent``
must still CONSTRUCT so the LLM loop runs and conversational, no-mailbox
questions can be answered — instead of raising during ``__init__`` (which the
REST layer turns into an HTTP 502 before any tool can run).

Every other test injects a fake backend via the eval seam, which makes
``resolve_mail_backends()`` succeed and hides this path entirely — so this test
constructs with the real zero-connector code path (patched to report nothing
connected).
"""

import json
from unittest.mock import MagicMock, patch

import pytest


def _build_zero_connector_agent(tmp_path, monkeypatch):
    """Construct with NO injected backend and nothing connected."""
    from gaia_agent_email.agent import EmailTriageAgent
    from gaia_agent_email.config import EmailAgentConfig

    # Real zero-connector path: no injected backend, keyring reports nothing.
    monkeypatch.setattr(
        "gaia_agent_email.config.connected_mailbox_providers", lambda: []
    )
    cfg = EmailAgentConfig(
        # Explicit model id so construction never probes Lemonade.
        model_id="test-model",
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        debug=False,
    )
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
        mock_sdk.return_value = MagicMock()
        return EmailTriageAgent(config=cfg)


def test_construction_succeeds_with_zero_connectors(tmp_path, monkeypatch):
    """The agent constructs even when no mailbox is connected (#2418)."""
    agent = _build_zero_connector_agent(tmp_path, monkeypatch)

    # No live backends; the primary is a deferred-error placeholder.
    assert agent._backends == {}
    # Tools registered fine — the LLM loop can run and answer no-mailbox
    # questions instead of 502-ing at construction.
    assert "list_inbox" in agent._tools_registry


def test_read_tool_fails_loudly_with_zero_connectors(tmp_path, monkeypatch):
    """Operational read tools return the actionable empty-state error, not a
    ZeroDivisionError, when nothing is connected."""
    agent = _build_zero_connector_agent(tmp_path, monkeypatch)

    list_inbox = agent._tools_registry["list_inbox"]["function"]
    envelope = json.loads(list_inbox(max_results=5))

    assert envelope["ok"] is False
    assert "No mailbox connected" in envelope["error"]


def test_send_backend_fails_loudly_with_zero_connectors(tmp_path, monkeypatch):
    """The primary backend raises the actionable ConfigurationError on use, so
    send-from-scratch fails loudly per call (AC-5 of #2401 unchanged)."""
    from gaia_agent_email.config import ConfigurationError

    agent = _build_zero_connector_agent(tmp_path, monkeypatch)

    backend = agent._send_backend()
    with pytest.raises(ConfigurationError, match="No mailbox connected"):
        backend.send_message(to="x@example.com", subject="s", body="b")
