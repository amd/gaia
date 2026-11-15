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
        #: Extra fields the sidecar puts on its ``final``.
        self.final = {}

    def init(self):
        return 200, {}

    def query_stream(self, body, read_timeout=None, on_response=None):
        self.models.append(body.get("model"))
        yield {"type": "token", "delta": "Miso."}
        yield {"type": "final", "answer": "Miso.", "usage": {"tokens": 3}, **self.final}

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


# ── A relayed hybrid turn is billed by its route ─────────────────────────────

CHEAP = "Gemma-4-E4B-it-GGUF"
ROUTE = {"model": GLM, "reason": "find_defects", "classified": True}


@pytest.fixture
def hybrid_config(monkeypatch, tmp_path):
    from gaia.config import GaiaConfig

    monkeypatch.setenv("GAIA_CONFIG_FILE", str(tmp_path / "config.json"))
    GaiaConfig(hybrid_cheap_model=CHEAP, hybrid_strong_model=GLM).save()


async def _hybrid_turn(relayed, final):
    db, proxy = relayed
    proxy.final = final
    session = db.create_session(model="hybrid", agent_type="gaia")
    with patch.object(helpers.spending, "record_turn") as record_turn:
        done = await _turn(db, session["id"])
    (answer,) = _answers(db, session["id"])
    return proxy, done, answer, record_turn


async def test_a_relayed_hybrid_turn_is_billed_to_its_routed_model(
    relayed, hybrid_config
):
    proxy, done, answer, record_turn = await _hybrid_turn(relayed, {"route": ROUTE})

    # The sidecar routes: it is sent ``hybrid``, never a guess at the model.
    assert proxy.models == ["hybrid"]
    assert record_turn.call_args.args[1] == GLM
    assert done["stats"]["model"] == GLM
    assert done["stats"]["route"] == ROUTE
    assert answer["inference_stats"]["model"] == GLM
    assert answer["inference_stats"]["route"]["model"] == GLM
    assert done["stats"]["place"] == "via Fireworks AI"


async def test_a_relayed_cheap_route_is_billed_as_the_local_model(
    relayed, hybrid_config
):
    route = {**ROUTE, "model": CHEAP}
    _proxy, done, _answer, record_turn = await _hybrid_turn(relayed, {"route": route})

    assert record_turn.call_args.args[1] == CHEAP
    assert done["stats"]["model"] == CHEAP
    assert "place" not in done["stats"]


async def test_a_degraded_route_notice_reads_in_agent_ui_terms(relayed, hybrid_config):
    """The agent words its fix for the TUI; the Agent UI names its own screen."""
    from gaia.llm.surface_hints import (
        AGENT_UI_GATEWAY_KEY_HINT,
        TUI_GATEWAY_KEY_HINT,
    )

    notice = f"hybrid: the classifier failed. To fix: {TUI_GATEWAY_KEY_HINT}"
    route = {**ROUTE, "degraded": True, "notice": notice, "fix": notice}
    _proxy, done, answer, _record = await _hybrid_turn(relayed, {"route": route})

    for shown in (done["stats"]["route"], answer["inference_stats"]["route"]):
        assert AGENT_UI_GATEWAY_KEY_HINT in shown["notice"]
        assert AGENT_UI_GATEWAY_KEY_HINT in shown["fix"]
        assert "/gateway" not in shown["notice"]
        assert shown["model"] == GLM


@pytest.mark.parametrize(
    "bad",
    [None, "amd.Claude", {"reason": "no model"}, {"model": 7}, ["model", GLM]],
    ids=["null", "string", "no-model", "model-not-a-string", "list"],
)
async def test_a_malformed_route_is_not_billed_as_a_model(
    relayed, hybrid_config, bad, caplog
):
    _proxy, done, _answer, record_turn = await _hybrid_turn(relayed, {"route": bad})

    assert record_turn.call_args.args[1] == "hybrid"
    assert "route" not in done["stats"]
    assert "without its route" in caplog.text


async def test_an_older_sidecar_without_a_route_is_named_in_the_log(
    relayed, hybrid_config, caplog
):
    _proxy, done, _answer, record_turn = await _hybrid_turn(relayed, {})

    assert record_turn.call_args.args[1] == "hybrid"
    assert "predates contract 2.29" in caplog.text


async def test_a_route_on_a_named_model_turn_is_not_used(relayed):
    """Only a ``hybrid`` session is placed by a route; a named model is what ran."""
    db, proxy = relayed
    proxy.final = {"route": ROUTE}
    session = db.create_session(model=DEEPSEEK, agent_type="gaia")
    with patch.object(helpers.spending, "record_turn") as record_turn:
        done = await _turn(db, session["id"])

    assert record_turn.call_args.args[1] == DEEPSEEK
    assert done["stats"]["model"] == DEEPSEEK
    assert "route" not in done["stats"]
