# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""An answer's token footer and step count describe the whole turn.

Lemonade's ``/stats`` only knows the most recent model call. A 19-step turn
that read 382k tokens used to show the last call's 33,872 → 572. These tests
run a real streaming turn and check the ``done`` event and the saved message,
which also carry the agent's own step count for the "N steps" summary.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gaia.ui import _chat_helpers as helpers
from gaia.ui._chat_helpers import _turn_inference_stats
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest
from gaia.ui.run_manager import RunManager

LAST_CALL = {
    "tokens_per_second": 255.2,
    "time_to_first_token": 1.253,
    "input_tokens": 33872,
    "output_tokens": 572,
}


class _TotalsAgent:
    """Answers once, reporting the turn's summed usage like the base agent."""

    def __init__(self, totals, steps=0):
        self.model_id = "M-GGUF"
        self.conversation_history = []
        self.indexed_files = set()
        self._cancel_event = None
        self.console = None
        self.totals = totals
        self.steps = steps

    def _register_tools(self):
        pass

    def process_query(self, _message):
        for n in range(1, self.steps + 1):
            self.console.print_step_header(n, 150)
        self.console.print_final_answer("Three invoices saved.", **self.totals)
        return {"result": "Three invoices saved."}


class _FakeStatsClient:
    def __init__(self, *_a, **_k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, *_a, **_k):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = dict(LAST_CALL)
        return resp


@pytest.fixture
def runtime():
    db = ChatDatabase(":memory:")
    helpers._agent_cache.clear()
    with (
        patch("gaia.ui.run_manager.run_manager", RunManager()),
        patch.object(helpers, "_agent_registry", None),
        patch.object(helpers, "_maybe_load_expected_model"),
        patch.object(helpers, "_maybe_update_session_title", AsyncMock()),
        patch("httpx.AsyncClient", _FakeStatsClient),
        patch(
            "gaia.llm.lemonade_manager.LemonadeManager.get_base_url",
            return_value="http://127.0.0.1:1",
        ),
    ):
        yield db
    helpers._agent_cache.clear()
    db.close()


async def _turn(db, agent):
    session = db.create_session(model="M-GGUF", agent_type="chat")
    helpers._store_agent(session["id"], "M-GGUF", [], agent, "chat")
    request = ChatRequest(session_id=session["id"], message="hi", stream=True)
    events = []

    async def consume():
        async for chunk in helpers._stream_chat_response(db, session, request):
            for line in chunk.splitlines():
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: ") :]))

    await asyncio.wait_for(consume(), timeout=15)
    done = [e for e in events if e.get("type") == "done"]
    assert len(done) == 1, events
    return session, done[0]


@pytest.mark.asyncio
async def test_done_and_saved_message_carry_turn_totals(runtime):
    db = runtime
    agent = _TotalsAgent({"total_tokens": 8212, "input_tokens": 382104}, steps=19)

    session, done = await _turn(db, agent)

    assert done["stats"]["input_tokens"] == 382104
    assert done["stats"]["output_tokens"] == 8212
    assert done["stats"]["steps"] == 19
    # Rate and TTFT still come from the backend's own measurement.
    assert done["stats"]["tokens_per_second"] == 255.2
    saved = db.get_messages(session["id"])[-1]
    stats = saved.get("stats") or saved.get("inference_stats")
    assert stats["input_tokens"] == 382104
    assert stats["output_tokens"] == 8212
    assert stats["steps"] == 19


@pytest.mark.asyncio
async def test_turn_without_counted_usage_shows_no_last_call_counts(runtime):
    db = runtime
    agent = _TotalsAgent({})

    _session, done = await _turn(db, agent)

    assert "input_tokens" not in done["stats"]
    assert "output_tokens" not in done["stats"]
    assert "steps" not in done["stats"]


class TestTurnInferenceStats:
    def test_last_call_counts_never_survive(self):
        merged = _turn_inference_stats(dict(LAST_CALL), {"type": "answer"})
        assert merged == {"tokens_per_second": 255.2, "time_to_first_token": 1.253}

    def test_input_without_output_is_not_reported(self):
        merged = _turn_inference_stats(None, {"input_tokens": 500})
        assert merged is None

    def test_zero_or_non_int_counts_are_ignored(self):
        assert _turn_inference_stats(None, {"tokens": 0, "input_tokens": 9}) is None
        assert _turn_inference_stats(None, {"tokens": "12"}) is None

    def test_totals_without_backend_stats(self):
        merged = _turn_inference_stats(None, {"tokens": 10, "input_tokens": 90})
        assert merged == {"output_tokens": 10, "input_tokens": 90}

    def test_no_answer_event_and_no_backend_is_none(self):
        assert _turn_inference_stats(None, None) is None

    def test_agent_step_count_rides_along(self):
        merged = _turn_inference_stats(None, {"steps": 10, "tools_used": 6})
        assert merged == {"steps": 10}
        assert _turn_inference_stats(None, {"steps": 0}) is None
