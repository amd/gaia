# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A relayed Agent UI turn tells the flagship sidecar it is the Agent UI.

The sidecar answers the TUI and the Agent UI alike, so without being told it
cannot point a user at the Agent UI's Scheduled tasks page.
"""

from __future__ import annotations

import pytest

import gaia.ui._chat_helpers as chat_helpers
from gaia.ui.email_sidecar import daemon_client
from gaia.ui.email_sidecar.profiles import (
    EMAIL_PROFILE,
    GAIA_PROFILE,
    surface_supported,
)
from gaia.ui.models import ChatRequest
from gaia.ui.sse_handler import SSEOutputHandler


@pytest.mark.parametrize(
    "version, expected", [("2.19", True), ("3.0", True), ("2.18", False), (None, False)]
)
def test_only_a_flagship_at_2_19_takes_the_surface(version, expected):
    assert surface_supported(GAIA_PROFILE, version) is expected


def test_email_never_takes_the_surface():
    assert surface_supported(EMAIL_PROFILE, "9.9") is False


class _Proxy:
    agent_id = "gaia"

    def __init__(self):
        self.bodies = []

    def init(self):
        return 200, {}

    def query_stream(self, body, read_timeout, on_response):
        self.bodies.append(body)
        yield {"type": "final", "answer": "ok"}


def _dispatch(monkeypatch, version):
    proxy = _Proxy()

    class _Handle:
        api_version = version

        def proxy(self):
            return proxy

    monkeypatch.setattr(daemon_client, "acquire_handle", lambda _id: _Handle())
    chat_helpers._dispatch_sidecar_query(
        SSEOutputHandler(),
        ChatRequest(session_id="s", message="schedule a report", agent_type="gaia"),
        [],
        None,
        "gaia",
    )
    [body] = proxy.bodies
    return body


def test_the_relay_says_the_turn_is_from_the_agent_ui(monkeypatch):
    assert _dispatch(monkeypatch, "2.19")["surface"] == "agent_ui"


def test_an_older_sidecar_is_not_sent_a_field_it_would_422(monkeypatch):
    assert "surface" not in _dispatch(monkeypatch, "2.18")
