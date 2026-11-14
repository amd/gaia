# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A background goal run on ``hybrid`` is billed to the model it ran on.

Chat turns were billed by their route, but goal runs still recorded spend
under ``hybrid``, which no price list knows, so the cost of each run was lost.
"""

import asyncio
import logging
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import gaia.config
import gaia.ui._chat_helpers as helpers
from gaia.agents.base.goal_store import GoalStore
from gaia.ui import spending
from gaia.ui.agent_loop import AgentLoop, AgentTrigger
from gaia.ui.database import ChatDatabase

CHEAP = "amd.DeepSeek-V4.1-Flash"
STRONG = "amd.Claude-Opus-5-5"
USAGE = {"input_tokens": 12_000, "output_tokens": 400}


@pytest.fixture
def gaia_home(tmp_path, monkeypatch):
    home = tmp_path / ".gaia"
    (home / "chat").mkdir(parents=True)
    (home / "chat" / "initialized").touch()
    # Default hybrid settings: cheap and strong are the shipped gateway models.
    monkeypatch.setattr(gaia.config, "GAIA_CONFIG_FILE", tmp_path / "missing.json")
    with patch.dict(os.environ, {"GAIA_HOME": str(home)}):
        yield home


@pytest.fixture
def loop(gaia_home):
    db = ChatDatabase(":memory:")
    db.set_setting("custom_model", "hybrid")
    loop = AgentLoop()
    loop._db = db
    loop._app_state = SimpleNamespace(tunnel=None, session_locks={})
    yield loop
    db.close()


def _tick(loop):
    GoalStore().create_goal("Tidy notes", "Sort my notes", source="user")

    async def run():
        loop._running_lock = asyncio.Lock()
        await loop._process_trigger(AgentTrigger("idle_tick", None))

    asyncio.run(run())


def _billed(loop):
    report = spending.month_report(loop._db)
    return [row["model"] for row in report["by_model"]]


# --- the agent runs as its own binary (the desktop apps) --------------------


def _sidecar(monkeypatch, route=None, fail=False):
    seen = []

    def relay(handler, prompt, **kwargs):
        seen.append(kwargs["model_id"])
        kwargs["stats"].update(USAGE)
        if route is not None:
            handler.relayed_route = route
        if fail:
            raise RuntimeError("the strong model failed mid-turn (HTTP 500)")
        handler.loop_state_directive = {"directive": "idle", "reason": "done"}
        return "Sorted."

    monkeypatch.setattr(
        helpers,
        "_agent_registry",
        SimpleNamespace(get=lambda _id: SimpleNamespace(is_sidecar=True)),
    )
    monkeypatch.setattr(helpers, "relay_one_prompt", relay)
    return seen


@pytest.mark.parametrize("model", [CHEAP, STRONG])
def test_a_relayed_hybrid_tick_is_billed_to_its_route(loop, monkeypatch, model):
    seen = _sidecar(monkeypatch, route={"model": model, "classified": True})

    _tick(loop)

    # The agent routes the turn itself, so it is sent ``hybrid``.
    assert seen == ["hybrid"]
    assert _billed(loop) == [model]


def test_a_relayed_hybrid_tick_that_failed_after_routing_bills_its_route(
    loop, monkeypatch
):
    _sidecar(monkeypatch, route={"model": STRONG}, fail=True)

    _tick(loop)

    assert _billed(loop) == [STRONG]


def test_a_relayed_hybrid_tick_without_a_route_says_so(loop, monkeypatch, caplog):
    """An agent older than contract 2.29 sends no route: billed as hybrid, loudly."""
    _sidecar(monkeypatch, route=None)

    with caplog.at_level(logging.WARNING, logger="gaia.ui.agent_loop"):
        _tick(loop)

    assert _billed(loop) == ["hybrid"]
    assert any("without its route" in r.getMessage() for r in caplog.records)


def test_a_relayed_tick_on_a_named_model_ignores_a_stray_route(loop, monkeypatch):
    loop._db.set_setting("custom_model", CHEAP)
    _sidecar(monkeypatch, route={"model": STRONG})

    _tick(loop)

    assert _billed(loop) == [CHEAP]


# --- the agent runs in this process ----------------------------------------


class _RoutedAgent:
    """Switches to *ran_on* the way GaiaAgent's router does, then answers."""

    conversation_history = []
    device = "gpu"
    system_prompt = "test"

    def __init__(self, ran_on, route=True):
        self.model_id = CHEAP
        self.ran_on = ran_on
        self.route = route

    def _register_tools(self):
        pass

    def process_query(self, _query):
        self.model_id = self.ran_on
        result = {"result": "Sorted.", **USAGE}
        if self.route:
            result["route"] = {"model": self.ran_on, "classified": True}
        return result


def _in_process(monkeypatch, agent):
    built = []

    def cached(_session_id, model_id, *_args):
        built.append(model_id)
        return agent

    monkeypatch.setattr(helpers, "_agent_registry", None)
    monkeypatch.setattr(helpers, "_get_cached_agent", cached)
    return built


@pytest.mark.parametrize("model", [CHEAP, STRONG])
def test_an_in_process_hybrid_tick_is_billed_to_its_route(loop, monkeypatch, model):
    built = _in_process(monkeypatch, _RoutedAgent(model))

    _tick(loop)

    assert built == ["hybrid"]
    assert _billed(loop) == [model]


def test_an_in_process_tick_without_a_route_bills_the_model_it_ran_on(
    loop, monkeypatch
):
    _in_process(monkeypatch, _RoutedAgent(STRONG, route=False))

    _tick(loop)

    assert _billed(loop) == [STRONG]
