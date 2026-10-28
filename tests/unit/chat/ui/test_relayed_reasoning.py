# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A relayed GAIA turn shows the model's reasoning, as an in-process one does.

Over the relay the reasoning used to arrive folded into status lines the Agent
UI drops, so an installed GAIA never showed any.
"""

from __future__ import annotations

import pytest

import gaia.ui._chat_helpers as chat_helpers
from gaia.ui.email_sidecar import daemon_client, relay
from gaia.ui.email_sidecar.profiles import (
    EMAIL_PROFILE,
    GAIA_PROFILE,
    reasoning_supported,
)
from gaia.ui.models import ChatRequest
from gaia.ui.sse_handler import SSEOutputHandler


@pytest.mark.parametrize(
    "version, expected", [("2.22", True), ("3.0", True), ("2.21", False), (None, False)]
)
def test_only_a_flagship_at_2_22_takes_reasoning(version, expected):
    assert reasoning_supported(GAIA_PROFILE, version) is expected


def test_email_is_never_asked_for_reasoning():
    assert reasoning_supported(EMAIL_PROFILE, "9.9") is False


class _Proxy:
    agent_id = "gaia"

    def __init__(self):
        self.bodies = []

    def init(self):
        return 200, {}

    def query_stream(self, body, read_timeout, on_response):
        self.bodies.append(body)
        yield {"type": "reasoning", "delta": "Weigh the "}
        yield {"type": "reasoning", "delta": "forecast."}
        yield {"type": "final", "answer": "Bring a coat."}


def _dispatch(monkeypatch, version):
    proxy = _Proxy()

    class _Handle:
        api_version = version

        def proxy(self):
            return proxy

    monkeypatch.setattr(daemon_client, "acquire_handle", lambda _id: _Handle())
    handler = SSEOutputHandler()
    chat_helpers._dispatch_sidecar_query(
        handler,
        ChatRequest(session_id="s", message="Do I need a coat?", agent_type="gaia"),
        [],
        None,
        "gaia",
    )
    [body] = proxy.bodies
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    return body, events


def test_a_new_enough_agent_is_asked_for_its_reasoning(monkeypatch):
    body, events = _dispatch(monkeypatch, "2.22")

    assert body["reasoning"] is True
    thinking = [e["content"] for e in events if e and e.get("type") == "thinking"]
    assert "".join(thinking) == "Weigh the forecast."


def test_an_older_agent_is_not_sent_a_field_it_would_422(monkeypatch):
    body, _ = _dispatch(monkeypatch, "2.21")

    assert "reasoning" not in body


def test_an_empty_piece_shows_nothing():
    handler = SSEOutputHandler()
    relay._dispatch_one(handler, {"type": "reasoning", "delta": ""}, GAIA_PROFILE)

    assert handler.event_queue.empty()
