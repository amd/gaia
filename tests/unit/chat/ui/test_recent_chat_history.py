# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""History reconstruction must read the end of the persisted transcript."""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from gaia.ui import _chat_helpers as helpers
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest


@pytest.fixture
def transcript():
    db = ChatDatabase(":memory:")
    session = db.create_session()
    # Equal timestamps exercise the insertion-order tiebreaker as well.
    with patch.object(db, "_now", return_value="2026-09-11T12:00:00+00:00"):
        for i in range(12):
            db.add_message(session["id"], "user", f"USER_{i}")
            db.add_message(session["id"], "assistant", f"ASST_{i}")
        db.add_message(session["id"], "user", "failed user turn")
        db.add_message(session["id"], "user", "current follow-up")
    yield db, session
    db.close()


def test_recent_messages_preserve_order_and_transcript_pagination(transcript):
    db, session = transcript
    recent = db.get_recent_messages(session["id"], limit=4)
    assert [m["content"] for m in recent] == [
        "USER_11",
        "ASST_11",
        "failed user turn",
        "current follow-up",
    ]
    assert [m["content"] for m in db.get_messages(session["id"], limit=2)] == [
        "USER_0",
        "ASST_0",
    ]
    assert db.get_recent_messages("missing", limit=4) == []


@pytest.mark.asyncio
@pytest.mark.allow_network  # Windows asyncio creates a local socket pair.
@pytest.mark.parametrize("stream", [False, True])
async def test_chat_dispatch_receives_latest_completed_exchanges(transcript, stream):
    db, session = transcript
    captured = []

    class Agent:
        model_id = None
        indexed_files = set()

        def _register_tools(self):
            pass

        def process_query(self, _message):
            captured.extend(self.conversation_history)
            return {"result": "latest history received"}

    request = ChatRequest(
        session_id=session["id"], message="current follow-up", stream=stream
    )
    with (
        patch.object(helpers, "_agent_registry", None),
        patch.object(helpers, "_get_cached_agent", return_value=Agent()),
        patch.object(helpers, "_maybe_load_expected_model"),
        patch.object(helpers, "_maybe_update_session_title", new_callable=AsyncMock),
        patch(
            "gaia.llm.lemonade_manager.LemonadeManager.get_base_url",
            return_value="http://localhost:13305/api/v1",
        ),
        patch("httpx.AsyncClient.get", new_callable=AsyncMock) as stats,
    ):
        stats.return_value.status_code = 503
        if stream:
            events = [
                event
                async for event in helpers._stream_chat_impl(
                    SimpleNamespace(handler=None), db, session, request
                )
            ]
            assert any('"type": "done"' in event for event in events)
        else:
            assert await helpers._get_chat_response(db, session, request) == (
                "latest history received"
            )

    assert [entry["content"] for entry in captured] == [
        content for i in range(7, 12) for content in (f"USER_{i}", f"ASST_{i}")
    ]


@pytest.mark.allow_network  # TestClient uses a local asyncio socket pair on Windows.
@pytest.mark.parametrize("stream", [False, True])
def test_http_follow_up_uses_latest_exchange(transcript, stream):
    from fastapi.testclient import TestClient

    from gaia.ui.server import create_app

    db, session = transcript

    class HistoryEchoAgent:
        model_id = None
        indexed_files = set()

        def _register_tools(self):
            pass

        def process_query(self, _message):
            return {"result": self.conversation_history[-1]["content"]}

    app = create_app(db_path=":memory:")
    app.state.db.close()
    app.state.db = db
    with (
        patch.object(helpers, "_agent_registry", None),
        patch.object(helpers, "_get_cached_agent", return_value=HistoryEchoAgent()),
        patch.object(helpers, "_maybe_load_expected_model"),
        patch.object(helpers, "_maybe_update_session_title", new_callable=AsyncMock),
        patch(
            "gaia.llm.lemonade_manager.LemonadeManager.get_base_url",
            return_value="http://localhost:13305/api/v1",
        ),
        patch("httpx.AsyncClient.get", new_callable=AsyncMock) as stats,
    ):
        stats.return_value.status_code = 503
        response = TestClient(app).post(
            "/api/chat/send",
            json={
                "session_id": session["id"],
                "message": "What was that?",
                "stream": stream,
            },
        )
    assert response.status_code == 200
    assert "ASST_11" in response.text
    assert "ASST_9" not in response.text


@pytest.mark.asyncio
@pytest.mark.allow_network  # Windows asyncio creates a local socket pair.
async def test_background_tick_uses_latest_completed_exchanges(transcript):
    from gaia.ui.agent_loop import AgentLoop

    db, session = transcript
    captured = []

    class Agent:
        def _register_tools(self):
            pass

        def process_query(self, _message):
            captured.extend(self.conversation_history)

    loop = AgentLoop()
    loop._db = db
    module = SimpleNamespace(ChatAgent=Agent, ChatAgentConfig=object)
    with (
        patch.dict(sys.modules, {"gaia_agent_chat.agent": module}),
        patch.object(helpers, "_get_cached_agent", return_value=Agent()),
        patch.object(loop, "_get_actionable_goals", return_value=[]),
    ):
        await loop._execute_tick(session["id"], session, [])
    assert [entry["content"] for entry in captured] == [
        content for i in range(9, 12) for content in (f"USER_{i}", f"ASST_{i}")
    ]
