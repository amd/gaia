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


def test_a_live_measurement_reaches_the_meter_before_the_answer():
    """Contract 2.28: the sidecar measures at turn start; the relay forwards it.

    The relay's own cuts are turns the sidecar never saw, so they are added to
    its count here, exactly as the ``final`` copy does.
    """
    h = SSEOutputHandler()
    h.relay_hidden_turns = 2

    relay._dispatch_one(
        h,
        {
            "type": "context_usage",
            "window": 65536,
            "used": 900,
            "turns_in_view": 3,
            "turns_hidden": 0,
        },
        GAIA_PROFILE,
    )

    [meter] = _events(h)
    assert meter == {
        "type": "context_usage",
        "window": 65536,
        "used": 900,
        "turns_in_view": 3,
        "turns_hidden": 2,
    }
    assert h.relay_context["turns_hidden"] == 2


def test_a_failed_final_does_not_leave_the_live_guess_in_the_meter():
    """A pre-answer estimate is not the failed turn's usage; ``final`` has none.

    The live event itself stands (it was true when streamed), but the turn's
    recorded meter must not show the guess as its result.
    """
    h = SSEOutputHandler()

    relay._dispatch_one(
        h,
        {"type": "context_usage", "window": 65536, "used": 900, "turns_in_view": 3},
        GAIA_PROFILE,
    )
    relay._dispatch_one(
        h,
        {"type": "final", "answer": "The model server went away.", "failed": True},
        GAIA_PROFILE,
    )

    events = _events(h)
    assert [e["type"] for e in events] == ["context_usage", "answer"]
    assert events[1]["failed"] is True
    assert h.relay_context is None


def test_a_live_estimate_does_not_survive_a_context_error():
    """A live hint is not the turn's result: a failed measurement wins.

    The sequence is the one the reviewer reported: ``context_usage`` first,
    then ``final`` carrying ``context_error``. The estimate must not outrank
    the explanation when the turn's meter is recorded.
    """
    h = SSEOutputHandler()

    relay._dispatch_one(
        h,
        {"type": "context_usage", "window": 65536, "used": 900, "turns_in_view": 3},
        GAIA_PROFILE,
    )
    relay._dispatch_one(
        h,
        {"type": "final", "answer": "Paris.", "context_error": "tokenizer exploded"},
        GAIA_PROFILE,
    )

    assert [e["type"] for e in _events(h)] == ["context_usage", "answer"]
    assert h.relay_context is None
    assert h.relay_context_error == "tokenizer exploded"
    helpers.record_relayed_context(
        "s8096",
        h.relay_context,
        helpers.relayed_context_reason(GAIA_PROFILE, "2.28", h),
    )
    assert helpers.relayed_context("s8096") == {"reason": "tokenizer exploded"}


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


def test_turns_the_relay_left_out_count_as_hidden():
    """The sidecar measures only the turns it was sent; the rest are hidden too.

    In v0.26.0-rc18 a 71-turn chat lost turns 1-22 from the model's view at
    turn 35 while the meter said none were hidden, so the model denied facts
    the user gave in turn 1 and nothing on screen explained why.
    """
    h = SSEOutputHandler()
    h.relay_hidden_turns = 22
    measured = {**_USAGE, "turns_in_view": 13, "turns_hidden": 0}

    relay._dispatch_one(
        h, {"type": "final", "answer": "Paris.", "context": measured}, GAIA_PROFILE
    )

    [meter] = [e for e in _events(h) if e["type"] == "context_usage"]
    assert meter["turns_hidden"] == 22
    assert meter["turns_in_view"] == 13
    assert h.relay_context["turns_hidden"] == 22


def _turn(i: int, chars: int) -> list:
    return [
        {"role": "user", "content": f"question {i}"},
        {"role": "assistant", "content": "x" * chars},
    ]


def test_the_relay_records_how_many_turns_it_left_out(monkeypatch):
    from gaia.agents.base import history

    turns = [_turn(i, 4000) for i in range(10)]
    # Exactly the cost of the five newest turns: they fit, a sixth does not.
    budget = sum(history.turn_cost(t) for t in turns[5:])
    monkeypatch.setattr(history, "relayed_history_budget", lambda *a, **k: budget)
    monkeypatch.setattr(helpers, "_ui_cloud_provider", lambda model: None)
    h = SSEOutputHandler()

    kept = helpers._relayed_turns(turns, "m", "q", h, None)

    assert h.relay_hidden_turns == len(turns) - len(kept) > 0
    cut = [e for e in _events(h) if e.get("status") == "history_cut"]
    assert cut and cut[0]["turns"] == h.relay_hidden_turns


def test_the_text_history_relay_records_how_many_turns_it_left_out(monkeypatch):
    """An agent on contract 2.21-2.24 measures context but takes text pairs."""
    from gaia.agents.base import history

    pairs = [(f"question {i}", "x" * 4000) for i in range(10)]
    budget = sum(history.turn_cost(_turn(i, 4000)) for i in range(5))
    monkeypatch.setattr(history, "relayed_history_budget", lambda *a, **k: budget)
    monkeypatch.setattr(helpers, "_ui_cloud_provider", lambda model: None)
    h = SSEOutputHandler()

    context = helpers._relayed_history(pairs, "m", "q", h, None)

    assert h.relay_hidden_turns == len(pairs) - len(context) // 2 > 0


@pytest.mark.parametrize("fits", [10, 5])
def test_the_relay_tells_the_model_only_when_it_left_turns_out(monkeypatch, fits):
    """rc18: the chat said older messages were out of view; the model denied them."""
    from gaia.agents.base import history

    turns = [_turn(i, 4000) for i in range(10)]
    stored = [[dict(m) for m in turn] for turn in turns]
    budget = sum(history.turn_cost(t) for t in turns[:fits])
    monkeypatch.setattr(history, "relayed_history_budget", lambda *a, **k: budget)
    monkeypatch.setattr(helpers, "_ui_cloud_provider", lambda model: None)
    h, h_text = SSEOutputHandler(), SSEOutputHandler()

    kept = helpers._relayed_turns(turns, "m", "q", h, None)
    pairs = [(turn[0]["content"], turn[1]["content"]) for turn in turns]
    context = helpers._relayed_history(pairs, "m", "q", h_text, None)

    hidden = len(turns) - len(kept)
    assert (hidden > 0) == (fits < len(turns))
    assert h.relay_hidden_turns == h_text.relay_hidden_turns == hidden
    sent = [m["content"] for turn in kept for m in turn]
    for texts in (sent, [m["content"] for m in context]):
        noted = [t for t in texts if history.HIDDEN_TURNS_NOTE in t]
        assert len(noted) == (1 if hidden else 0)
        if hidden:
            assert texts[0] == f"{history.HIDDEN_TURNS_NOTE}\n\nquestion {hidden}"
    assert turns == stored


def test_history_exactly_at_budget_hides_nothing():
    from gaia.agents.base import history

    turns = [_turn(i, 4000) for i in range(6)]
    budget = sum(history.turn_cost(t) for t in turns)

    assert history.fit_turns(turns, budget) == turns
    # One token over evicts down to half the budget, oldest first.
    over = history.fit_turns(turns, budget - 1)
    assert over[1:] == turns[len(turns) - len(over) + 1 :]
    assert over[0][1] == turns[len(turns) - len(over)][1]
    assert over[0][0]["content"].startswith(history.HIDDEN_TURNS_NOTE)
    assert sum(history.turn_cost(t) for t in over) <= (budget - 1) // 2


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


def test_turns_both_sides_left_out_add_up_once():
    """The sidecar's own cuts and the relay's are different turns; both count."""
    h = SSEOutputHandler()
    h.relay_hidden_turns = 22
    measured = {**_USAGE, "turns_in_view": 10, "turns_hidden": 3}

    relay._dispatch_one(
        h, {"type": "final", "answer": "Paris.", "context": measured}, GAIA_PROFILE
    )

    [meter] = [e for e in _events(h) if e["type"] == "context_usage"]
    assert meter["turns_hidden"] == 25
    assert measured["turns_hidden"] == 3  # the sidecar's event is not mutated


def test_a_relay_that_left_nothing_out_passes_the_measurement_through():
    h = SSEOutputHandler()
    measured = {**_USAGE, "turns_in_view": 4, "turns_hidden": 1}

    relay._dispatch_one(
        h, {"type": "final", "answer": "Paris.", "context": measured}, GAIA_PROFILE
    )

    assert h.relay_context == measured
