# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A failed turn is never re-run behind the user's back.

A turn that already ran tools (a file write, a shell command, a sent email)
and then hits a model error used to be replayed from the start, so every tool
ran twice. The turn now ends with the error and the user decides whether to
send it again.
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from gaia.llm.providers.lemonade import LemonadeModelNotLoadedError
from gaia.ui import _chat_helpers as helpers
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest
from gaia.ui.run_manager import RunManager

pytestmark = pytest.mark.asyncio


class _ToolThenModelError:
    """Runs a tool with a side effect, then the model call fails."""

    def __init__(self):
        self.model_id = "M-GGUF"
        self.conversation_history = []
        self.indexed_files = set()
        self._cancel_event = None
        self.console = None
        self.tool_runs = 0

    def _register_tools(self):
        pass

    def process_query(self, _message):
        self.tool_runs += 1  # e.g. wrote a file, sent an email
        raise LemonadeModelNotLoadedError()


@pytest.fixture
def runtime():
    db = ChatDatabase(":memory:")
    helpers._agent_cache.clear()
    with (
        patch("gaia.ui.run_manager.run_manager", RunManager()),
        patch.object(helpers, "_agent_registry", None),
        patch.object(helpers, "_maybe_load_expected_model"),
        patch.object(helpers, "_maybe_update_session_title", AsyncMock()),
    ):
        yield db
    helpers._agent_cache.clear()
    db.close()


def _seed(db, agent):
    session = db.create_session(model="M-GGUF", agent_type="chat")
    helpers._store_agent(session["id"], "M-GGUF", [], agent, "chat")
    return session


async def test_streamed_turn_is_not_replayed_after_a_model_error(runtime):
    db = runtime
    agent = _ToolThenModelError()
    session = _seed(db, agent)
    request = ChatRequest(session_id=session["id"], message="do it", stream=True)

    events = []
    async for chunk in helpers._stream_chat_response(db, session, request):
        for line in chunk.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))

    assert agent.tool_runs == 1, "the turn's tools ran again on a hidden retry"
    assert not any("retrying" in (e.get("message") or "") for e in events)
    errors = [e for e in events if e.get("type") == "error"]
    assert errors and LemonadeModelNotLoadedError.user_message in errors[0]["content"]


async def test_non_streamed_turn_is_not_replayed_after_a_model_error(runtime):
    db = runtime
    agent = _ToolThenModelError()
    session = _seed(db, agent)
    request = ChatRequest(session_id=session["id"], message="do it", stream=False)

    with patch.object(helpers, "wait_for_warm_up"):
        answer = await asyncio.wait_for(
            helpers._get_chat_response(db, session, request), 10
        )

    assert agent.tool_runs == 1, "the turn's tools ran again on a hidden retry"
    assert answer == LemonadeModelNotLoadedError.user_message


def test_a_too_small_window_says_to_send_it_again():
    """The one overflow a resend fixes must not say to start a fresh task."""
    from gaia.llm.providers.lemonade import LemonadeContextOverflowError

    err = LemonadeContextOverflowError().mark_reloadable()
    assert err.retryable
    assert "send your message again" in err.user_message.lower()
    assert "fresh task" not in err.user_message.lower()
