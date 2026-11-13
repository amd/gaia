# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A ``hybrid`` session routes each turn once, before it runs, and never
falls back to the other model when the chosen one can't be reached."""

import io
import json
from types import SimpleNamespace

import pytest
from gaia_agent import stdio
from gaia_agent.agent import GaiaAgent
from gaia_agent.chat_agent import ChatAgent

from gaia.llm import hybrid
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


@pytest.fixture(autouse=True)
def _fresh_classifier_health():
    hybrid.reset_classifier_health()
    yield
    hybrid.reset_classifier_health()


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
    agent.warnings = []
    agent.console = SimpleNamespace(print_warning=agent.warnings.append)
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


def test_clear_starts_the_next_turn_unescalated(monkeypatch):
    agent, client, steps, switches = _agent(
        monkeypatch,
        [_label("find_defects", 4, "high"), _label("answer_question", 1, "low")],
    )
    agent.conversation_history = []
    agent.process_query("Audit it.")
    wire = io.StringIO()
    stdio.dispatch_query(agent, stdio.CLEAR_CONVERSATION_QUERY, wire)
    assert agent.last_route is None and not agent.hybrid_router.escalated
    after = agent.process_query("What is 2+2?")
    assert after["route"]["model"] == CHEAP and not after["route"]["sticky"]
    assert switches == [STRONG, CHEAP] and client.calls == 2


def test_host_clear_history_resets_routing_too(monkeypatch):
    agent, _, _, _ = _agent(monkeypatch, [_label("find_defects", 4, "high")])
    agent.process_query("Audit it.")
    stdio._reset_hybrid_route(agent)
    assert not agent.hybrid_router.escalated


def test_clear_on_an_agent_without_routing_is_harmless():
    stdio._reset_hybrid_route(SimpleNamespace())


def test_routed_model_refusal_names_its_setting(monkeypatch):
    from gaia.llm.lemonade_client import CloudModelUnavailableError

    agent, _, _, _ = _agent(monkeypatch, [_label("answer_question", 1, "low")])
    agent.process_query("What is 2+2?")
    message = agent._cloud_account_refusal(
        CloudModelUnavailableError(
            "The selected cloud model is unavailable (HTTP 404).", model_id=CHEAP
        )
    )
    assert message.startswith(
        f"hybrid routed this turn to {CHEAP} (hybrid_cheap_model)"
    )
    assert "gaia config set hybrid_cheap_model" in message


def test_refusal_without_routing_is_unchanged(monkeypatch):
    from gaia.llm.lemonade_client import CloudModelUnavailableError

    agent, _, _, _ = _agent(monkeypatch, [])
    agent.hybrid_router = None
    error = CloudModelUnavailableError(
        "The selected cloud model is unavailable (HTTP 404).", model_id=CHEAP
    )
    assert "hybrid" not in (agent._cloud_account_refusal(error) or "")


def test_a_refused_switch_names_the_setting(monkeypatch):
    from gaia.llm.hybrid import HybridModelError
    from gaia.llm.lemonade_client import CloudModelUnavailableError

    def refuse(target):
        raise CloudModelUnavailableError(
            "The selected cloud model is unavailable (HTTP 404).", model_id=target
        )

    agent, _, _, _ = _agent(
        monkeypatch, [_label("plan_or_design", 4, "high")], switch=refuse
    )
    with pytest.raises(HybridModelError, match=r"\(hybrid_strong_model\)"):
        agent.process_query("Design the migration.")
    assert not agent.hybrid_router.escalated


def test_a_degraded_classifier_warns_in_the_turn(monkeypatch):
    for _ in range(hybrid.DEGRADED_AFTER):
        agent, _, _, _ = _agent(monkeypatch, ["not json"])
        result = agent.process_query("Write a note.")
    assert result["route"]["degraded"] is True
    (warning,) = agent.warnings
    assert warning == result["route"]["notice"] and "hybrid_classifier_model" in warning


def test_a_single_classifier_failure_does_not_warn_in_the_turn(monkeypatch):
    agent, _, _, _ = _agent(monkeypatch, ["not json"])
    result = agent.process_query("Write a note.")
    assert result["route"]["model"] == STRONG and agent.warnings == []


def _named_agent(monkeypatch, switched):
    agent = GaiaAgent.__new__(GaiaAgent)
    agent.hybrid_router = None
    agent.last_route = RouteDecision(model=STRONG)

    def fake_switch(a, target, on_status=None):
        switched.append(target)
        return target

    monkeypatch.setattr(stdio, "switch_concrete_model", fake_switch)
    monkeypatch.setattr(
        hybrid,
        "HybridSettings",
        SimpleNamespace(
            load=lambda config=None: HybridSettings(
                cheap_model=CHEAP, strong_model=STRONG
            )
        ),
    )
    return agent


def test_switching_to_hybrid_starts_routing_on_the_cheap_model(monkeypatch):
    monkeypatch.setattr("gaia.llm.local_only.cloud_blocked", lambda: False)
    switched = []
    agent = _named_agent(monkeypatch, switched)
    assert stdio.switch_model(agent, "Hybrid") == "hybrid"
    assert switched == [CHEAP]
    assert agent.hybrid_router.settings.strong_model == STRONG
    assert agent.last_route is None and not agent.hybrid_router.escalated


def test_switching_to_hybrid_under_local_only_is_refused(monkeypatch):
    monkeypatch.setattr("gaia.llm.local_only.cloud_blocked", lambda: True)
    switched = []
    agent = _named_agent(monkeypatch, switched)
    with pytest.raises(RuntimeError, match="Local only"):
        stdio.switch_model(agent, "hybrid")
    assert switched == [] and agent.hybrid_router is None


def test_a_failed_switch_to_hybrid_leaves_the_session_unrouted(monkeypatch):
    monkeypatch.setattr("gaia.llm.local_only.cloud_blocked", lambda: False)
    agent = _named_agent(monkeypatch, [])

    def refuse(a, target, on_status=None):
        raise RuntimeError("unavailable")

    monkeypatch.setattr(stdio, "switch_concrete_model", refuse)
    with pytest.raises(RuntimeError, match="unavailable"):
        stdio.switch_model(agent, "hybrid")
    assert agent.hybrid_router is None


def test_only_the_gaia_agent_can_switch_to_hybrid(monkeypatch):
    with pytest.raises(RuntimeError, match="only in the GAIA agent"):
        stdio.switch_model(SimpleNamespace(), "hybrid")


def test_model_command_to_hybrid_says_what_it_routes_between(monkeypatch):
    agent = SimpleNamespace(
        hybrid_router=HybridRouter(
            HybridSettings(cheap_model=CHEAP, strong_model=STRONG)
        )
    )
    monkeypatch.setattr(stdio, "_switch_model", lambda a, t, on_status=None: "hybrid")
    monkeypatch.setattr(stdio, "_model_state_event", lambda a: {"type": "status"})
    out = io.StringIO()
    stdio.run_model_command(agent, "/model hybrid", out)
    final = json.loads(out.getvalue().strip().splitlines()[-1])
    assert final["type"] == "final"
    assert "hybrid" in final["answer"] and CHEAP in final["answer"]
    assert STRONG in final["answer"]
