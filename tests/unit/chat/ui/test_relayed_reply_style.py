# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A voice turn's ``reply_style`` reaches the relayed flagship (2.28)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import gaia.ui._chat_helpers as helpers
from gaia.ui.email_sidecar import daemon_client
from gaia.ui.email_sidecar.profiles import (
    EMAIL_PROFILE,
    GAIA_PROFILE,
    reply_style_supported,
)
from gaia.ui.models import ChatRequest
from gaia.ui.sse_handler import SSEOutputHandler


class _Proxy:
    agent_id = "gaia"

    def __init__(self):
        self.bodies: list = []

    def init(self):
        return 200, {}

    def query_stream(self, body, read_timeout, on_response):
        self.bodies.append(body)
        yield {"type": "final", "answer": "<spoken>Done.</spoken> Detail."}

    def cancel_query(self, _rid):
        pass

    def set_bypass(self, _session_id, _on):
        pass


def _dispatch(monkeypatch, version, reply_style):
    proxy = _Proxy()
    handle = SimpleNamespace(api_version=version, proxy=lambda: proxy)
    monkeypatch.setattr(daemon_client, "acquire_handle", lambda _id: handle)
    monkeypatch.setattr(helpers, "_replay_cloud_key_for_relay", lambda *_a: None)
    handler = SSEOutputHandler()
    helpers._dispatch_sidecar_query(
        handler,
        ChatRequest(
            session_id="s",
            message="what's on today",
            agent_type="gaia",
            reply_style=reply_style,
        ),
        [],
        None,
        "gaia",
    )
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    return proxy.bodies, events


@pytest.mark.parametrize(
    "version, expected", [("2.28", True), ("3.0", True), ("2.27", False), (None, False)]
)
def test_only_a_flagship_at_2_28_takes_reply_style(version, expected):
    assert reply_style_supported(GAIA_PROFILE, version) is expected
    assert reply_style_supported(EMAIL_PROFILE, "9.9") is False


def test_a_voice_turn_sends_reply_style(monkeypatch):
    bodies, events = _dispatch(monkeypatch, "2.28", "voice")
    [body] = bodies
    assert body["reply_style"] == "voice"
    assert not [e for e in events if e["type"] == "agent_error"]


def test_a_text_turn_sends_nothing(monkeypatch):
    bodies, _ = _dispatch(monkeypatch, "2.28", "text")
    assert "reply_style" not in bodies[0]


def test_a_voice_turn_stops_before_an_old_flagship(monkeypatch):
    bodies, events = _dispatch(monkeypatch, "2.27", "voice")
    assert bodies == []
    [error] = [e for e in events if e["type"] == "agent_error"]
    assert "newer GAIA agent" in error["content"]


def test_chat_request_refuses_unknown_style():
    with pytest.raises(ValueError):
        ChatRequest(session_id="s", message="x", reply_style="sung")
