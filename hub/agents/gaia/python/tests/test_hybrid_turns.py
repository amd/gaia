# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A ``hybrid`` session routes each turn once, before it runs, and never
falls back to the other model when the chosen one can't be reached."""

import json
from types import SimpleNamespace

import pytest
from gaia_agent import stdio
from gaia_agent.agent import GaiaAgent
from gaia_agent.chat_agent import ChatAgent

from gaia.llm.hybrid import HybridRouter, HybridSettings, RouteDecision

CHEAP = "amd.Cheap"
STRONG = "amd.Claude-Strong"


class _Client:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        assert "tools" not in kwargs
        return next(self.replies)


def _label(kind, difficulty, stakes):
    return json.dumps({"kind": kind, "difficulty": difficulty, "stakes": stakes})


def _agent(monkeypatch, replies, switch=None):
    monkeypatch.setattr("gaia.llm.local_only.cloud_blocked", lambda: False)
    client = _Client(replies)
    router = HybridRouter(
        HybridSettings(cheap_model=CHEAP, strong_model=STRONG),
        client_factory=lambda model, base_url: client,
    )
    agent = GaiaAgent.__new__(GaiaAgent)
    agent.hybrid_router = router
    agent.last_route = None
    agent.model_id = CHEAP
    agent.chat = SimpleNamespace(config=SimpleNamespace(base_url="http://l/api/v1"))
    steps = []

    def fake_turn(self, user_input, *args, **kwargs):
        steps.append(self.model_id)
        return {"status": "success", "result": "done"}

    switches = []

    def fake_switch(agent_, target, on_status=None):
        if switch is not None:
            switch(target)
        switches.append(target)
        agent_.model_id = target
        return target

    monkeypatch.setattr(ChatAgent, "process_query", fake_turn)
    monkeypatch.setattr(stdio, "switch_concrete_model", fake_switch)
    return agent, client, steps, switches


def test_each_turn_is_routed_once_and_recorded(monkeypatch):
    agent, client, steps, switches = _agent(
        monkeypatch,
        [_label("answer_question", 1, "low"), _label("find_defects", 4, "high")],
    )
    first = agent.process_query("What is 2+2?")
    assert first["route"]["model"] == CHEAP and switches == []
    second = agent.process_query("Audit the session code for races.")
    assert second["route"]["model"] == STRONG and switches == [STRONG]
    assert steps == [CHEAP, STRONG] and client.calls == 2
    assert agent.last_route.model == STRONG


def test_session_stays_strong_after_a_strong_turn(monkeypatch):
    agent, client, steps, switches = _agent(
        monkeypatch, [_label("find_defects", 4, "high")]
    )
    agent.process_query("Audit it.")
    third = agent.process_query("Thanks, summarise that in a line.")
    assert third["route"]["sticky"] is True
    assert steps == [STRONG, STRONG] and switches == [STRONG]
    assert client.calls == 1


def test_unreachable_chosen_model_fails_the_turn_instead_of_switching_back(
    monkeypatch,
):
    def refuse(target):
        raise RuntimeError(f"Unknown Lemonade model '{target}'.")

    agent, _, steps, _ = _agent(
        monkeypatch, [_label("plan_or_design", 4, "high")], switch=refuse
    )
    with pytest.raises(RuntimeError, match="amd.Claude-Strong"):
        agent.process_query("Design the migration.")
    assert steps == [] and agent.model_id == CHEAP
    # It never ran strong, so the session isn't stuck there.
    assert not agent.hybrid_router.escalated


def test_a_session_without_hybrid_is_not_routed(monkeypatch):
    agent, client, steps, _ = _agent(monkeypatch, [])
    agent.hybrid_router = None
    assert "route" not in agent.process_query("hi")
    assert client.calls == 0 and steps == [CHEAP]


def test_two_sessions_keep_their_own_sticky_state(monkeypatch):
    strong_agent, _, _, _ = _agent(monkeypatch, [_label("find_defects", 5, "high")])
    cheap_agent, _, _, _ = _agent(
        monkeypatch, [_label("write_prose", 1, "low"), _label("write_prose", 1, "low")]
    )
    strong_agent.process_query("Audit.")
    cheap_agent.process_query("Write a note.")
    assert cheap_agent.process_query("Another note.")["route"]["model"] == CHEAP


def test_picking_a_model_by_name_ends_routing(monkeypatch):
    agent = GaiaAgent.__new__(GaiaAgent)
    agent.hybrid_router = object()
    monkeypatch.setattr(stdio, "_apply_local_switch", lambda a, t, s=None: t)
    monkeypatch.setattr("gaia.llm.local_only.cloud_blocked", lambda: False)
    assert stdio.switch_model(agent, "amd.Other") == "amd.Other"
    assert agent.hybrid_router is None


def test_a_failed_named_switch_keeps_routing(monkeypatch):
    agent = GaiaAgent.__new__(GaiaAgent)
    router = object()
    agent.hybrid_router = router

    def refuse(a, t, s=None):
        raise RuntimeError("Unknown Lemonade model")

    monkeypatch.setattr(stdio, "_apply_local_switch", refuse)
    monkeypatch.setattr("gaia.llm.local_only.cloud_blocked", lambda: False)
    with pytest.raises(RuntimeError):
        stdio.switch_model(agent, "amd.Missing")
    assert agent.hybrid_router is router


def test_agent_built_on_hybrid_starts_on_the_cheap_model(monkeypatch):
    seen = {}

    def fake_init(self, config=None):
        seen["model_id"] = config.model_id
        self.config = config

    monkeypatch.setattr(ChatAgent, "__init__", fake_init)
    monkeypatch.setattr(
        "gaia_agent.agent.HybridRouter",
        lambda: SimpleNamespace(
            settings=SimpleNamespace(cheap_model=CHEAP, strong_model=STRONG)
        ),
    )
    from gaia_agent.agent import GaiaAgentConfig

    agent = GaiaAgent(GaiaAgentConfig(model_id="hybrid"))
    assert seen["model_id"] == CHEAP and agent.hybrid_router is not None
