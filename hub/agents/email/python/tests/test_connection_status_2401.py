# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Live mailbox connection-status tool tests for EmailTriageAgent (#2401).

Acceptance criteria covered:
- With Google connected, ``list_connected_mailboxes`` names the actual account
  (provider + account_email), not a generic capability description.
- With multiple connected, it lists all of them.
- With none connected, the tool reports ``connected=False`` with an actionable
  "connect in Settings → Connectors" message.
- Disconnect → reconnect (no restart) is reflected on the next call — the tool
  reads live state per call rather than caching.

Backends are injected fakes; the connectors layer (``get_connection`` /
``available_mailbox_providers``) is stubbed so the tests run hermetically
without a live keyring or Lemonade.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# parents[0] = tests/, [1] = email/, [2] = python/, [3] = agents/,
# [4] = hub/, [5] = repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.agent import EmailTriageAgent  # noqa: E402
from gaia_agent_email.config import EmailAgentConfig  # noqa: E402


class _MinimalMailBackend:
    pass


class _MinimalCalendarBackend:
    pass


def _build_agent(tmp_path: Path) -> EmailTriageAgent:
    """Build EmailTriageAgent with injected fakes, memory forced off.

    Memory off keeps the live registry to the mixin tools only (no embedder /
    Lemonade needed) — the connection tool doesn't touch memory.
    """
    cfg = EmailAgentConfig(
        gmail_backend=_MinimalMailBackend(),
        calendar_backend=_MinimalCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        debug=False,
    )
    with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
        mock_sdk.return_value = MagicMock()
        return EmailTriageAgent(config=cfg)


def _invoke(agent: EmailTriageAgent) -> dict:
    from gaia.agents.base.tools import _TOOL_REGISTRY

    entry = _TOOL_REGISTRY.get("list_connected_mailboxes")
    assert entry is not None, "list_connected_mailboxes tool not registered"
    return json.loads(entry["function"]())


def test_tool_registered(tmp_path, monkeypatch):
    from gaia.agents.base.tools import _TOOL_REGISTRY

    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    agent = _build_agent(tmp_path)
    try:
        assert "list_connected_mailboxes" in _TOOL_REGISTRY
    finally:
        agent.close_db()


def test_names_connected_google_account(tmp_path, monkeypatch):
    """AC1: with Google connected, the tool names the actual account."""
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    agent = _build_agent(tmp_path)
    try:
        with (
            patch.object(
                agent.config,
                "available_mailbox_providers",
                return_value=["google"],
            ),
            patch(
                "gaia.connectors.api.get_connection",
                return_value={"account_email": "tomasz.iniewicz@gmail.com"},
            ),
        ):
            result = _invoke(agent)
        assert result["ok"] is True
        data = result["data"]
        assert data["connected"] is True
        assert data["mailboxes"] == [
            {"provider": "google", "account_email": "tomasz.iniewicz@gmail.com"}
        ]
    finally:
        agent.close_db()


def test_lists_all_connected(tmp_path, monkeypatch):
    """AC2: with multiple connected, all are listed."""
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    agent = _build_agent(tmp_path)
    emails = {
        "google": {"account_email": "user@gmail.com"},
        "microsoft": {"account_email": "user@outlook.com"},
    }
    try:
        with (
            patch.object(
                agent.config,
                "available_mailbox_providers",
                return_value=["google", "microsoft"],
            ),
            patch(
                "gaia.connectors.api.get_connection",
                side_effect=lambda p: emails.get(p),
            ),
        ):
            result = _invoke(agent)
        providers = {
            m["provider"]: m["account_email"] for m in result["data"]["mailboxes"]
        }
        assert providers == {
            "google": "user@gmail.com",
            "microsoft": "user@outlook.com",
        }
    finally:
        agent.close_db()


def test_none_connected_is_actionable(tmp_path, monkeypatch):
    """AC3: with nothing connected, the tool says so with an actionable hint."""
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    agent = _build_agent(tmp_path)
    try:
        with patch.object(agent.config, "available_mailbox_providers", return_value=[]):
            result = _invoke(agent)
        assert result["ok"] is True
        data = result["data"]
        assert data["connected"] is False
        assert data["mailboxes"] == []
        assert "Settings → Connectors" in data["message"]
    finally:
        agent.close_db()


def test_default_account_sentinel_maps_to_none(tmp_path, monkeypatch):
    """The store's DEFAULT_ACCOUNT no-email sentinel never leaks to the user."""
    from gaia.connectors.store import DEFAULT_ACCOUNT

    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    agent = _build_agent(tmp_path)
    try:
        with (
            patch.object(
                agent.config,
                "available_mailbox_providers",
                return_value=["google"],
            ),
            patch(
                "gaia.connectors.api.get_connection",
                return_value={"account_email": DEFAULT_ACCOUNT},
            ),
        ):
            result = _invoke(agent)
        assert result["data"]["mailboxes"][0]["account_email"] is None
    finally:
        agent.close_db()


def test_reads_live_state_each_call(tmp_path, monkeypatch):
    """AC4: disconnect → reconnect (no restart) is reflected on the next call."""
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    agent = _build_agent(tmp_path)
    try:
        # First call: Google connected.
        with (
            patch.object(
                agent.config,
                "available_mailbox_providers",
                return_value=["google"],
            ),
            patch(
                "gaia.connectors.api.get_connection",
                return_value={"account_email": "user@gmail.com"},
            ),
        ):
            first = _invoke(agent)
        assert first["data"]["connected"] is True

        # Everything disconnected before the second call — no agent rebuild.
        with patch.object(agent.config, "available_mailbox_providers", return_value=[]):
            second = _invoke(agent)
        assert second["data"]["connected"] is False
    finally:
        agent.close_db()
