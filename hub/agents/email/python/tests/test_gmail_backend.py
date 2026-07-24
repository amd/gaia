# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Spec for LiveGmailBackend's 401 remediation message (issue #2159 defect 2).

At long sidecar uptime a forwarded token can go stale and Gmail returns 401. The
old single message told the user to "Reconnect Google" — but in the daemon
(forwarded) deployment the connection is valid host-side; reconnecting is a dead
end while the daemon's re-forward timer fixes it automatically. The message must
be mode-aware: forwarded mode → "no reconnect needed, daemon re-forwards";
standalone mode → keep the reconnect guidance. Never leak the bearer token.
"""

from __future__ import annotations

import httpx
import pytest

from gaia_agent_email import forwarded_credentials
from gaia_agent_email.gmail_backend import LiveGmailBackend

from gaia.connectors.errors import ConnectorsError


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv(forwarded_credentials.FORWARDED_MODE_ENV_VAR, raising=False)
    forwarded_credentials.reset()
    yield
    forwarded_credentials.reset()


def _backend_returning_401():
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Invalid Credentials")

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    return LiveGmailBackend(
        access_token_fn=lambda: "forwarded-access-token", http_client=client
    )


def test_401_in_forwarded_mode_says_no_reconnect_needed(monkeypatch):
    monkeypatch.setenv(forwarded_credentials.FORWARDED_MODE_ENV_VAR, "1")
    backend = _backend_returning_401()
    with pytest.raises(ConnectorsError) as exc:
        backend.list_messages(max_results=1)
    msg = str(exc.value)
    assert "no reconnect" in msg.lower()
    assert "re-forward" in msg.lower()
    # Never leak the token.
    assert "forwarded-access-token" not in msg


def test_401_in_standalone_mode_says_reconnect(monkeypatch):
    monkeypatch.delenv(forwarded_credentials.FORWARDED_MODE_ENV_VAR, raising=False)
    backend = _backend_returning_401()
    with pytest.raises(ConnectorsError) as exc:
        backend.list_messages(max_results=1)
    msg = str(exc.value)
    assert "Reconnect Google" in msg
    assert "forwarded-access-token" not in msg
