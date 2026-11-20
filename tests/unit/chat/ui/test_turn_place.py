# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""An answer's footer says where the turn ran: "on your GPU", "via Fireworks AI"."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from gaia.ui import _chat_helpers as helpers
from gaia.ui._chat_helpers import _cloud_place, _local_place, _turn_location
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest
from gaia.ui.run_manager import RunManager

BASE = "http://localhost:8000/api/v1"
CLOUD_MODEL = "fireworks.accounts/fireworks/models/glm-5"


def _client(loaded=None, status=200):
    resp = MagicMock(status_code=status)
    resp.json.return_value = {"all_models_loaded": loaded or []}
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    return client


@pytest.fixture(autouse=True)
def _no_eval_provider(monkeypatch):
    monkeypatch.delenv("GAIA_EVAL_AGENT_PROVIDER", raising=False)
    monkeypatch.delenv("GAIA_EVAL_CLAUDE_MODEL", raising=False)


@pytest.mark.parametrize(
    "device,expected",
    [
        ("gpu", "on your GPU"),
        ("amd_npu", "on your NPU"),
        ("cpu", "on your CPU"),
        ("gpu npu", "on your NPU and GPU"),
        ("none", "on this PC"),
    ],
)
async def test_local_turn_names_the_device_lemonade_reports(device, expected):
    client = _client(
        [
            {"model_name": "embed-model", "device": "cpu"},
            {"model_name": "Gemma-4-E4B-it-GGUF", "device": device},
        ]
    )
    place = await _local_place("Gemma-4-E4B-it-GGUF", client, BASE, {})
    assert place == expected
    assert client.get.await_args.args[0] == f"{BASE}/health"


async def test_user_prefixed_model_matches_lemonades_bare_name():
    client = _client([{"model_name": "My-Model", "device": "gpu"}])
    assert await _local_place("user.My-Model", client, BASE, {}) == "on your GPU"


async def test_local_model_not_loaded_says_this_pc():
    assert await _local_place("Gemma-4-E4B-it-GGUF", _client(), BASE, {}) == (
        "on this PC"
    )


def test_cloud_place_needs_no_lemonade():
    assert _cloud_place(_turn_location(CLOUD_MODEL)) == "via Fireworks AI"
    assert _cloud_place(_turn_location("Gemma-4-E4B-it-GGUF")) is None


class _AnswerAgent:
    def __init__(self, model_id):
        self.model_id = model_id
        self.conversation_history = []
        self.indexed_files = set()
        self._cancel_event = None
        self.console = None

    def _register_tools(self):
        pass

    def process_query(self, _message):
        self.console.print_final_answer("Miso.", total_tokens=4, input_tokens=9)
        return {"result": "Miso."}


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


class _RoutedAgent(_AnswerAgent):
    """A ``hybrid`` agent: built on the cheap model, the turn moves to the routed one."""

    ROUTE = {
        "model": CLOUD_MODEL,
        "reason": "find_defects",
        "degraded": True,
        "notice": "hybrid: the classifier has failed 3 turns in a row",
    }

    def process_query(self, _message):
        self.model_id = self.ROUTE["model"]
        self.console.print_final_answer("Miso.", total_tokens=4, input_tokens=9)
        return {"result": "Miso.", "route": dict(self.ROUTE)}


async def _done_stats(model_id, agent_cls=_AnswerAgent, agent_model=None):
    db = ChatDatabase(":memory:")
    helpers._agent_cache.clear()
    try:
        with (
            patch("gaia.ui.run_manager.run_manager", RunManager()),
            patch.object(helpers, "_agent_registry", None),
            patch.object(helpers, "_maybe_load_expected_model"),
            patch.object(helpers, "_maybe_update_session_title", AsyncMock()),
            patch("httpx.AsyncClient", _NoLemonade),
            patch(
                "gaia.llm.lemonade_manager.LemonadeManager.get_base_url",
                return_value="http://127.0.0.1:1",
            ),
        ):
            session = db.create_session(model=model_id, agent_type="chat")
            agent = agent_cls(agent_model or model_id)
            helpers._store_agent(session["id"], model_id, [], agent, "chat")
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
            saved = db.get_messages(session["id"])[-1]
            return done[0].get("stats") or {}, (
                saved.get("stats") or saved.get("inference_stats") or {}
            )
    finally:
        helpers._agent_cache.clear()
        db.close()


async def test_cloud_turn_names_its_provider_with_lemonade_stopped():
    done, saved = await _done_stats(CLOUD_MODEL)
    assert done["place"] == "via Fireworks AI"
    assert saved["place"] == "via Fireworks AI"


async def test_local_turn_with_lemonade_stopped_claims_no_place():
    done, _saved = await _done_stats("Gemma-4-E4B-it-GGUF")
    assert "place" not in done


async def test_hybrid_turn_is_placed_and_billed_by_its_routed_model():
    from gaia.config import GaiaConfig

    GaiaConfig(
        hybrid_cheap_model="Gemma-4-E4B-it-GGUF", hybrid_strong_model=CLOUD_MODEL
    ).save()
    with patch.object(helpers.spending, "record_turn") as record_turn:
        done, saved = await _done_stats(
            "hybrid", _RoutedAgent, agent_model="Gemma-4-E4B-it-GGUF"
        )
    assert done["place"] == "via Fireworks AI"
    assert done["model"] == CLOUD_MODEL
    assert done["route"] == _RoutedAgent.ROUTE
    assert saved["route"]["model"] == CLOUD_MODEL
    assert saved["route"]["notice"] == _RoutedAgent.ROUTE["notice"]
    assert record_turn.call_args.args[1] == CLOUD_MODEL


async def test_a_turn_with_no_route_records_none():
    done, saved = await _done_stats(CLOUD_MODEL)
    assert "route" not in done
    assert saved.get("route") is None
