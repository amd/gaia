# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A cloud turn that failed before any reply is not counted as spending.

Settings says "Turns that fail or are stopped before a reply aren't counted";
a model id that doesn't exist (HTTP 404 on every message) used to add a turn
to the month for each attempt.
"""

import asyncio
import json
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


def test_a_stopped_turn_with_no_usage_is_still_counted_apart(db):
    spending.record_turn(db, FLASH, {})
    assert _month(db)["unreported_turns"] == 1


class _FailingAgent:
    """Cached agent whose model call fails before any reply, as a 404 does."""

    def __init__(self, model_id):
        self.model_id = model_id
        self.conversation_history = []
        self.indexed_files = set()
        self._cancel_event = None
        self.console = None

    def _register_tools(self):
        pass

    def process_query(self, message):
        raise RuntimeError("`no-such-model` isn't available on Fireworks AI (HTTP 404)")


@pytest.mark.asyncio
async def test_a_streamed_turn_that_fails_before_a_reply_adds_no_spending(db):
    manager = RunManager()
    helpers._agent_cache.clear()
    session = db.create_session(model=MISSING, agent_type="chat")
    helpers._store_agent(session["id"], MISSING, [], _FailingAgent(MISSING), "chat")
    request = ChatRequest(session_id=session["id"], message="hello", stream=True)
    events = []

    async def consume():
        async for chunk in helpers._stream_chat_response(db, session, request):
            for line in chunk.splitlines():
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: ") :]))

    try:
        with (
            patch("gaia.ui.run_manager.run_manager", manager),
            patch.object(helpers, "_agent_registry", None),
            patch.object(helpers, "_maybe_load_expected_model"),
            patch.object(helpers, "_maybe_update_session_title", AsyncMock()),
        ):
            await asyncio.wait_for(consume(), timeout=15)
    finally:
        helpers._agent_cache.clear()

    [done] = [e for e in events if e.get("type") == "done"]
    assert done.get("failed") is True, done
    assert _month(db)["turns"] == 0
