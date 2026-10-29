# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Each saved answer names the model that wrote it, on the relayed path too.

The bug: answers were saved with no ``stats.model``, so the UI named every
earlier answer after the chat's current model. Switching a chat from DeepSeek
to GLM relabelled (and re-priced) the DeepSeek answers as GLM's.
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from gaia.ui import _chat_helpers as helpers
from gaia.ui.database import ChatDatabase
from gaia.ui.email_sidecar import daemon_client
from gaia.ui.models import ChatRequest
from gaia.ui.run_manager import RunManager

pytestmark = pytest.mark.asyncio

DEEPSEEK = "fireworks.deepseek-v4p1-flash"
GLM = "fireworks.glm-5p3-flash"


class _Proxy:
    """The flagship sidecar: answers every query, records the model it was sent."""

    agent_id = "gaia"

    def __init__(self):
        self.models = []

    def init(self):
        return 200, {}

    def query_stream(self, body, read_timeout=None, on_response=None):
        self.models.append(body.get("model"))
        yield {"type": "token", "delta": "Miso."}
        yield {"type": "final", "answer": "Miso.", "usage": {"tokens": 3}}

    def cancel_query(self, _rid):
        pass


class _Handle:
    api_version = "2.12"

    def __init__(self, proxy):
        self._proxy = proxy

    def proxy(self):
        return self._proxy


class _NoLemonade:
    """httpx.AsyncClient with Lemonade stopped: every request fails to connect."""

    def __init__(self, *_a, **_k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, url, **_k):
        raise httpx.ConnectError(f"connection refused: {url}")


@pytest.fixture
def relayed(monkeypatch):
    monkeypatch.delenv("GAIA_EVAL_AGENT_PROVIDER", raising=False)
    monkeypatch.delenv("GAIA_EVAL_CLAUDE_MODEL", raising=False)
    database = ChatDatabase(":memory:")
    proxy = _Proxy()
    helpers._agent_cache.clear()
    with (
        patch("gaia.ui.run_manager.run_manager", RunManager()),
        patch.object(helpers, "_agent_registry", None),
        patch.object(helpers, "_should_relay_to_sidecar", return_value=True),
        patch.object(helpers, "_replay_cloud_key_for_relay"),
        patch.object(helpers, "_maybe_update_session_title", AsyncMock()),
        patch.object(daemon_client, "acquire_handle", return_value=_Handle(proxy)),
        patch("httpx.AsyncClient", _NoLemonade),
        patch(
            "gaia.llm.lemonade_manager.LemonadeManager.get_base_url",
            return_value="http://127.0.0.1:1",
        ),
    ):
        yield database, proxy
    helpers._agent_cache.clear()
    database.close()


async def _turn(db, session_id):
    session = db.get_session(session_id)
    request = ChatRequest(session_id=session_id, message="Soup?", stream=True)
    events = []

    async def consume():
        async for frame in helpers._stream_chat_response(db, session, request):
            for line in frame.splitlines():
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: ") :]))

    await asyncio.wait_for(consume(), timeout=15)
    (done,) = [e for e in events if e.get("type") == "done"]
    return done


def _answers(db, session_id):
    return [m for m in db.get_messages(session_id) if m["role"] == "assistant"]


async def test_a_relayed_answer_records_the_model_it_ran_on(relayed):
    db, proxy = relayed
    session = db.create_session(model=DEEPSEEK, agent_type="gaia")

    done = await _turn(db, session["id"])

    assert proxy.models == [DEEPSEEK]
    assert done["stats"]["model"] == DEEPSEEK
    (answer,) = _answers(db, session["id"])
    assert answer["inference_stats"]["model"] == DEEPSEEK


async def test_switching_model_keeps_each_earlier_answers_model(relayed):
    db, proxy = relayed
    session = db.create_session(model=DEEPSEEK, agent_type="gaia")
    await _turn(db, session["id"])

    db.update_session(session["id"], model=GLM)
    await _turn(db, session["id"])

    assert proxy.models == [DEEPSEEK, GLM]
    first, second = _answers(db, session["id"])
    assert first["inference_stats"]["model"] == DEEPSEEK
    assert second["inference_stats"]["model"] == GLM
