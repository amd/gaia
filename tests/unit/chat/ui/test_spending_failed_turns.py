# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A cloud turn that failed before any reply is not counted as spending.

A model id that doesn't exist (HTTP 404 on every message) used to add a turn
to the month for each attempt. A stopped answer is still counted, since the
provider may bill it.
"""

import asyncio
import json
import threading
from unittest.mock import AsyncMock, patch

import pytest

from gaia.ui import _chat_helpers as helpers
from gaia.ui import spending
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest
from gaia.ui.run_manager import RunManager

FLASH = "fireworks.accounts/fireworks/models/deepseek-v4p1-flash"
MISSING = "fireworks.no-such-model-violet-otter"


@pytest.fixture
def db():
    database = ChatDatabase(":memory:")
    yield database
    database.close()


def _month(db):
    return spending.month_report(db)


def test_a_failed_turn_with_no_usage_is_not_recorded(db):
    spending.record_turn(db, MISSING, {}, failed=True)
    spending.record_turn(
        db, FLASH, {"input_tokens": 0, "output_tokens": 0}, failed=True
    )
    report = _month(db)
    assert report["turns"] == 0
    assert report["unpriced_turns"] == 0
    assert report["unreported_turns"] == 0


def test_a_failed_turn_that_reported_usage_still_counts(db):
    """It failed after the provider answered, so the tokens were billed."""
    spending.record_turn(
        db, FLASH, {"input_tokens": 100_000, "output_tokens": 500}, failed=True
    )
    report = _month(db)
    assert report["turns"] == 1
    assert report["total_usd"] > 0


@pytest.mark.parametrize(
    "stats, counted",
    [
        # The prompt reached the provider, so its input was billed.
        ({"input_tokens": 1_000, "output_tokens": 0}, True),
        # Half a usage report can't be priced, and nothing says it was billed.
        ({"input_tokens": None, "output_tokens": 50}, False),
        ({"input_tokens": "1000", "output_tokens": "5"}, False),
    ],
)
def test_a_failed_turn_counts_only_on_a_full_nonzero_usage_report(db, stats, counted):
    spending.record_turn(db, FLASH, stats, failed=True)
    assert _month(db)["turns"] == (1 if counted else 0)


def test_a_failed_local_turn_records_nothing(db):
    spending.record_turn(
        db, "Gemma-4-E4B-it-GGUF", {"input_tokens": 9, "output_tokens": 9}, failed=True
    )
    assert _month(db)["turns"] == 0


def test_a_stopped_turn_with_no_usage_is_still_counted_apart(db):
    spending.record_turn(db, FLASH, {})
    assert _month(db)["unreported_turns"] == 1


class _StreamAgent:
    """Cached agent whose turn may stream some text, be stopped, or fail."""

    def __init__(self, model_id, say="", stop=False, fail=True, failed_answer=""):
        self.model_id = model_id
        self.conversation_history = []
        self.indexed_files = set()
        self._cancel_event = None
        self.console = None
        self.say = say
        self.stop = stop
        self.fail = fail
        self.failed_answer = failed_answer
        self.calls = 0

    def _register_tools(self):
        pass

    def process_query(self, message):
        self.calls += 1
        # The SSE handler is the agent's console on a cache hit.
        if self.say:
            self.console._emit({"type": "chunk", "content": self.say})
        if self.failed_answer:
            self.console._emit(
                {"type": "answer", "content": self.failed_answer, "failed": True}
            )
        if self.stop:
            self.console.cancelled.set()
        if self.fail:
            raise RuntimeError(
                "`no-such-model` isn't available on Fireworks AI (HTTP 404)"
            )
        return {"result": self.failed_answer or self.say}


def _no_relay(*_a, **_k):
    raise AssertionError("the turn was relayed to a sidecar, not run in-process")


async def _stream_turn(db, model_id, agent):
    """Run one streamed turn on *agent* in-process; return its SSE events."""
    manager = RunManager()
    session = db.create_session(model=model_id, agent_type="chat")
    request = ChatRequest(session_id=session["id"], message="hello", stream=True)
    events = []

    async def consume():
        async for chunk in helpers._stream_chat_response(db, session, request):
            for line in chunk.splitlines():
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: ") :]))

    with (
        patch("gaia.ui.run_manager.run_manager", manager),
        patch.object(helpers, "_agent_registry", None),
        patch.object(helpers, "_get_cached_agent", lambda *a, **k: agent),
        patch.object(helpers, "_dispatch_sidecar_query", _no_relay),
        patch.object(helpers, "wait_for_warm_up", lambda **k: None),
        patch.object(helpers, "_maybe_load_expected_model"),
        patch.object(helpers, "_maybe_update_session_title", AsyncMock()),
    ):
        await asyncio.wait_for(consume(), timeout=15)
    assert agent.calls == 1, "the in-process agent never ran"
    return events


@pytest.mark.asyncio
async def test_a_streamed_turn_that_fails_before_a_reply_adds_no_spending(db):
    events = await _stream_turn(db, MISSING, _StreamAgent(MISSING))
    [done] = [e for e in events if e.get("type") == "done"]
    assert done.get("failed") is True, done
    assert _month(db)["turns"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True], ids=["stopped", "stopped-then-failed"])
async def test_a_turn_stopped_before_any_reply_adds_no_spending(db, fail):
    await _stream_turn(db, FLASH, _StreamAgent(FLASH, stop=True, fail=fail))
    report = _month(db)
    assert (report["turns"], report["unreported_turns"]) == (0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True], ids=["stopped", "stopped-then-failed"])
async def test_an_answer_stopped_partway_is_counted_apart(db, fail):
    """Part of the reply arrived, so the provider may have billed it."""
    agent = _StreamAgent(FLASH, say="The answer is", stop=True, fail=fail)
    await _stream_turn(db, FLASH, agent)
    report = _month(db)
    assert (report["turns"], report["unreported_turns"]) == (1, 1)


@pytest.mark.asyncio
async def test_a_failure_notice_is_not_a_reply(db):
    """The agent's own "the model failed" answer doesn't make the turn billable."""
    agent = _StreamAgent(
        MISSING, fail=False, failed_answer="The model isn't available (HTTP 404)."
    )
    events = await _stream_turn(db, MISSING, agent)
    [done] = [e for e in events if e.get("type") == "done"]
    assert done.get("failed") is True, done
    assert _month(db)["turns"] == 0


@pytest.mark.asyncio
async def test_a_turn_that_fails_after_part_of_a_reply_still_counts(db):
    agent = _StreamAgent(FLASH, say="The answer is")
    await _stream_turn(db, FLASH, agent)
    assert _month(db)["unreported_turns"] == 1


class _ResultAgent:
    """Cached agent whose non-streamed turn returns *result*, as process_query does."""

    def __init__(self, result):
        self._result = result
        self.indexed_files = set()
        self.conversation_history = []
        self.rag = None
        self.console = None
        self._cancel_event = threading.Event()
        self.calls = 0

    def _register_tools(self):
        pass

    def process_query(self, _message, **_kwargs):
        self.calls += 1
        return self._result


def _non_streamed_turn(db, monkeypatch, result):
    monkeypatch.setattr(helpers, "_agent_registry", None)
    monkeypatch.setattr(helpers, "_maybe_load_expected_model", lambda *a, **k: None)
    monkeypatch.setattr(helpers, "_dispatch_sidecar_query", _no_relay)
    monkeypatch.setattr(helpers, "wait_for_warm_up", lambda **k: None)
    agent = _ResultAgent(result)
    monkeypatch.setattr(helpers, "_get_cached_agent", lambda *a, **k: agent)
    db.set_setting("custom_model", FLASH)
    session = db.create_session(title="t", model=FLASH, agent_type="chat")
    request = ChatRequest(session_id=session["id"], message="2+2?", stream=False)
    answer = asyncio.run(helpers._get_chat_response(db, session, request))
    assert agent.calls == 1, "the in-process agent never ran"
    return answer


@pytest.mark.parametrize(
    "result",
    [
        {"result": "", "model_failed": True, "input_tokens": 0, "output_tokens": 0},
        {"result": "", "model_failed": True},
        {
            "result": "",
            "model_failed": True,
            "input_tokens": None,
            "output_tokens": 7,
        },
    ],
)
def test_a_non_streamed_turn_that_failed_unanswered_adds_no_spending(
    db, monkeypatch, result
):
    _non_streamed_turn(db, monkeypatch, result)
    report = _month(db)
    assert (report["turns"], report["unreported_turns"]) == (0, 0)


def test_a_non_streamed_failed_turn_that_reported_usage_is_priced(db, monkeypatch):
    _non_streamed_turn(
        db,
        monkeypatch,
        {
            "result": "",
            "model_failed": True,
            "input_tokens": 100_000,
            "output_tokens": 0,
        },
    )
    report = _month(db)
    assert report["turns"] == 1
    assert report["total_usd"] > 0


@pytest.mark.parametrize("flag", [False, None, 0, ""])
def test_a_non_streamed_turn_not_marked_failed_still_counts(db, monkeypatch, flag):
    _non_streamed_turn(db, monkeypatch, {"result": "4", "model_failed": flag})
    assert _month(db)["unreported_turns"] == 1
