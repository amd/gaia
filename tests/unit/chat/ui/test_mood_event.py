# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The model's mood crosses every hop to the Agent UI as one ``mood`` event.

An agent only names a mood when asked (``/query``'s ``mood`` field), so an
older Agent UI never receives an event it would show as "unsupported".
"""

from __future__ import annotations

import pytest

import gaia.ui._chat_helpers as chat_helpers
from gaia.ui.models import ChatRequest
from gaia.ui.sidecar import daemon_client, relay
from gaia.ui.sidecar.profiles import _NO_PROFILE, GAIA_PROFILE, mood_supported
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import CanonicalTranslator


def _events(handler):
    out = []
    while not handler.event_queue.empty():
        out.append(handler.event_queue.get_nowait())
    return out


def test_an_in_process_turn_emits_the_mood():
    handler = SSEOutputHandler()
    handler.print_mood("curious")
    assert _events(handler) == [{"type": "mood", "mood": "curious"}]


def test_the_sidecar_stream_carries_it_unchanged():
    translator = CanonicalTranslator(run_id="run-1", agent_id="gaia")
    assert translator.translate({"type": "mood", "mood": "sad"}) == [
        {"type": "mood", "mood": "sad"}
    ]


def test_the_relay_hands_it_to_the_agent_ui():
    handler = SSEOutputHandler()
    relay._dispatch_one(handler, {"type": "mood", "mood": "proud"}, GAIA_PROFILE)
    assert _events(handler) == [{"type": "mood", "mood": "proud"}]


@pytest.mark.parametrize(
    "version, expected", [("2.31", True), ("3.0", True), ("2.30", False), (None, False)]
)
def test_only_a_flagship_at_2_31_is_asked_for_its_mood(version, expected):
    assert mood_supported(GAIA_PROFILE, version) is expected


def test_a_profile_without_the_floor_is_never_asked_for_a_mood():
    assert mood_supported(_NO_PROFILE, "9.9") is False


class _Proxy:
    agent_id = "gaia"

    def __init__(self):
        self.bodies = []

    def init(self):
        return 200, {}

    def query_stream(self, body, read_timeout, on_response):
        self.bodies.append(body)
        yield {"type": "mood", "mood": "happy"}
        yield {"type": "final", "answer": "You're welcome!"}


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
        ChatRequest(session_id="s", message="Thanks!", agent_type="gaia"),
        [],
        None,
        "gaia",
    )
    [body] = proxy.bodies
    return body, [e for e in _events(handler) if e]


def test_a_new_enough_agent_is_asked_and_its_mood_reaches_the_ui(monkeypatch):
    body, events = _dispatch(monkeypatch, "2.31")

    assert body["mood"] is True
    assert {"type": "mood", "mood": "happy"} in events


def test_an_older_agent_is_not_sent_a_field_it_would_422(monkeypatch):
    body, _ = _dispatch(monkeypatch, "2.30")

    assert "mood" not in body


def test_in_process_agent_ui_chats_ask_for_the_mood():
    kwargs = chat_helpers._session_agent_kwargs(
        rag_file_paths=[], library_paths=[], allowed=[], session_id="s"
    )
    assert kwargs["express_mood"] is True
