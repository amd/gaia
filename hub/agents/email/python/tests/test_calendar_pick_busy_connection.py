# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The calendar provider pick must not ask for a grant the user already gave.

While another GAIA operation is saving a connection, the connection listing
returns it as a busy row with no scopes. Read as "no calendar scope", that
sends the user off to grant permissions they already have.
"""

from __future__ import annotations

import pytest
from gaia_agent_email import config as config_mod
from gaia_agent_email.config import ConfigurationError, EmailAgentConfig

from gaia.connectors.errors import ConnectionBusyError


def _connected(monkeypatch, row):
    monkeypatch.setattr(config_mod, "connected_mailbox_providers", lambda: ["google"])
    monkeypatch.setattr(config_mod, "get_connection", lambda _provider: row)


def test_a_busy_connection_is_reported_as_busy_not_as_a_missing_grant(monkeypatch):
    _connected(monkeypatch, {"provider": "google", "scopes": [], "error": "busy"})

    with pytest.raises(ConnectionBusyError):
        EmailAgentConfig().resolve_calendar_backend()


def test_a_connection_without_calendar_scope_still_asks_for_the_grant(monkeypatch):
    _connected(monkeypatch, {"provider": "google", "scopes": ["openid"]})

    with pytest.raises(ConfigurationError, match="no calendar scope"):
        EmailAgentConfig().resolve_calendar_backend()


class _FakeLiveCalendar:
    def __init__(self, _token_fn):
        pass

    def list_events(self, **_kwargs):
        return [{"id": "evt-1"}]


def test_an_agent_built_while_busy_gets_its_calendar_once_the_save_finishes(
    tmp_path, monkeypatch
):
    """A session lasts hours and the calendar tools keep the backend they were
    built with, so a busy connection at start must not break them for good."""
    from gaia_agent_email import calendar_backend
    from gaia_agent_email.agent import EmailTriageAgent
    from gaia_agent_email.scopes import CALENDAR_SCOPES

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(calendar_backend, "LiveCalendarBackend", _FakeLiveCalendar)
    row = {"provider": "google", "scopes": [], "error": "busy"}
    _connected(monkeypatch, row)
    agent = EmailTriageAgent(
        config=EmailAgentConfig(
            gmail_backend=object(),
            db_path=str(tmp_path / "s.db"),
            silent_mode=True,
        )
    )
    try:
        with pytest.raises(ConnectionBusyError):
            agent._calendar.list_events(time_min="a", time_max="b")

        # The other operation finishes saving; nothing restarts the agent.
        row.clear()
        row.update({"provider": "google", "scopes": [sorted(CALENDAR_SCOPES)[0]]})

        assert agent._calendar.list_events(time_min="a", time_max="b") == [
            {"id": "evt-1"}
        ]
    finally:
        agent.close_db()
