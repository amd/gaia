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
