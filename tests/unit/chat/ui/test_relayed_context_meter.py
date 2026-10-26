# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Context panel and composer meter for chats relayed to the GAIA sidecar.

The sidecar measures its own agent and sends it on ``final.context``
(contract 2.21); the Agent UI streams it to the meter and serves it between
turns from ``GET /api/chat/context``.
"""

from __future__ import annotations

import asyncio

import pytest

import gaia.ui._chat_helpers as helpers
from gaia.ui import turn_insights
from gaia.ui.email_sidecar import relay
from gaia.ui.email_sidecar.profiles import GAIA_PROFILE, context_supported
from gaia.ui.routers.chat import get_context_usage
from gaia.ui.sse_handler import SSEOutputHandler

_USAGE = {"window": 65536, "used": 1234, "model": "Qwen3.6-35B-A3B-GGUF"}


def _events(handler):
    out = []
    while not handler.event_queue.empty():
        out.append(handler.event_queue.get_nowait())
    return out


@pytest.fixture(autouse=True)
def _clean():
    helpers._relayed_contexts.clear()
    yield
    helpers._relayed_contexts.clear()


def test_final_context_reaches_the_meter_before_the_answer():
    h = SSEOutputHandler()

    relay._dispatch_one(
        h, {"type": "final", "answer": "Paris.", "context": _USAGE}, GAIA_PROFILE
    )

    events = _events(h)
    assert [e["type"] for e in events] == ["context_usage", "answer"]
    assert events[0] == {"type": "context_usage", **_USAGE}
    assert h.relay_context == _USAGE


def test_a_context_error_is_kept_not_streamed():
    h = SSEOutputHandler()

    relay._dispatch_one(
        h,
        {"type": "final", "answer": "Paris.", "context_error": "tokenizer exploded"},
        GAIA_PROFILE,
    )

    assert [e["type"] for e in _events(h)] == ["answer"]
    assert h.relay_context is None
    assert h.relay_context_error == "tokenizer exploded"


def test_an_old_agents_final_changes_nothing():
    h = SSEOutputHandler()

    relay._dispatch_one(h, {"type": "final", "answer": "Paris."}, GAIA_PROFILE)

    assert [e["type"] for e in _events(h)] == ["answer"]
    assert h.relay_context is None and h.relay_context_error is None


@pytest.mark.parametrize(
    "version, expected",
    [("2.21", True), ("2.22", True), ("2.20", False), (None, False), ("junk", False)],
)
def test_context_floor_is_2_21(version, expected):
    assert context_supported(GAIA_PROFILE, version) is expected


def test_a_turn_without_a_measurement_keeps_the_last_one():
    helpers.record_relayed_context("s1", _USAGE, None)
    helpers.record_relayed_context("s1", None, None)  # an error or a Stop

    assert helpers.relayed_context("s1") == {"usage": _USAGE}


def test_a_reason_replaces_a_missing_measurement_and_forget_clears_it():
    helpers.record_relayed_context("s1", None, "too old")
    assert helpers.relayed_context("s1") == {"reason": "too old"}

    helpers.forget_relayed_context("s1")
    assert helpers.relayed_context("s1") is None


class _DB:
    def __init__(self, answered=True):
        self.answered = answered

    def get_session(self, session_id):
        return {"id": session_id}

    def latest_message_id(self, session_id, role=None):
        return 7 if self.answered else None


def _get(session_id, db=None):
    return asyncio.run(get_context_usage(session_id, db or _DB()))


def test_the_endpoint_serves_a_relayed_chats_measurement(monkeypatch):
    monkeypatch.setattr(helpers, "cached_session_agent", lambda sid: None)
    helpers.record_relayed_context("s1", _USAGE, None)

    assert _get("s1") == {"available": True, **_USAGE}


def test_the_endpoint_names_an_old_agent_instead_of_a_restart(monkeypatch):
    monkeypatch.setattr(helpers, "cached_session_agent", lambda sid: None)
    helpers.record_relayed_context("s1", None, GAIA_PROFILE.context_upgrade_message)

    body = _get("s1")

    assert body["available"] is False
    assert body["reason"] == GAIA_PROFILE.context_upgrade_message
    assert "restarted" not in body["reason"]


def test_the_sidecar_payload_has_every_field_the_panel_reads(monkeypatch):
    """The real measurement on a bare agent: the shape the sidecar sends."""

    class _Agent:
        system_prompt = "You are GAIA."
        _openai_tools = [{"type": "function", "function": {"name": "read_file"}}]
        _tools_registry = {"read_file": object(), "write_file": object()}
        model_id = "Qwen3.6-35B-A3B-GGUF"

    def no_server(base_url, headers):
        raise ConnectionError("no Lemonade in unit tests")

    monkeypatch.setattr(turn_insights, "_lemonade", lambda: ("http://127.0.0.1:1", {}))
    monkeypatch.setattr(turn_insights, "_loaded_chat_model", no_server)
    turns = [
        [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello!"}]
    ]

    usage = turn_insights.panel_context(_Agent(), turns, _Agent.model_id)

    for key in (
        "window",
        "used",
        "system",
        "tools",
        "history",
        "categories",
        "tool_loading",
        "model",
    ):
        assert key in usage, key
    assert usage["model"] == "Qwen3.6-35B-A3B-GGUF"
    assert usage["window"] > 0
    assert usage["turns_in_view"] == 1
    assert sum(c["tokens"] for c in usage["categories"]) == usage["used"]
    assert usage["tokenizer"]["exact"] is False


@pytest.mark.parametrize(
    "api_version, error, expected",
    [
        ("2.20", None, GAIA_PROFILE.context_upgrade_message),
        ("2.20", "tokenizer exploded", GAIA_PROFILE.context_upgrade_message),
        ("2.21", None, None),
        ("2.21", "tokenizer exploded", "tokenizer exploded"),
    ],
)
def test_the_dispatch_names_an_old_agent_or_the_agents_own_error(
    api_version, error, expected
):
    """The gate _dispatch_sidecar_query records with (#5746 review)."""
    h = SSEOutputHandler()
    h.relay_context_error = error

    assert helpers.relayed_context_reason(GAIA_PROFILE, api_version, h) == expected


def test_the_tokenizer_count_stops_at_its_deadline(monkeypatch):
    """An abandoned exact count sends nothing more to a busy server."""
    import time

    sent = []

    class _Client:
        def __init__(self, *a, **k):
            pass

        def post(self, url, json=None, timeout=None):
            sent.append(timeout)
            raise AssertionError("no request may go out after the deadline")

        def close(self):
            pass

    import httpx

    monkeypatch.setattr(httpx, "Client", _Client)
    counter = turn_insights.ModelTokenCounter(
        "http://x", {}, deadline=time.monotonic() - 1
    )

    with pytest.raises(TimeoutError):
        counter("some text")
    assert sent == []
