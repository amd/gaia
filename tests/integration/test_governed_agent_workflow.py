# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# pylint: disable=protected-access
"""Integration test for GovernedAgentMixin + GaiaGovernanceAdapter.

Uses a minimal fake base agent so the test does not depend on Lemonade
or MCP. The goal is to prove that:

1. Tool execution flows through the mixin unchanged when no adapter is set.
2. An adapter with a BLOCK rule short-circuits tool execution.
3. An ALLOW decision passes through to the underlying tool.
4. The governance callback receives the decision.
"""

from __future__ import annotations

import logging
from typing import Any

from gaia.governance import (
    GaiaGovernanceAdapter,
    GovernedAgentMixin,
)
from gaia.governance.checkpoint_bridge import InMemoryCheckpointBridge
from gaia.governance.policy_binding import StaticPolicyBindingService
from gaia.governance.receipt_service import InMemoryReceiptService
from gaia.governance.stubs import RuleBasedPolicyEngine


class _FakeAgent:
    """Stand-in for gaia.Agent that records tool invocations.

    The mixin's contract is purely that ``super()._execute_tool`` exists
    and returns whatever the tool returns. This fake honors that contract
    without pulling the full Agent runtime into the test.
    """

    def __init__(self, **_: Any) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _execute_tool(self, tool_name: str, tool_args: dict[str, Any]) -> Any:
        self.calls.append((tool_name, dict(tool_args)))
        return {"status": "ok", "tool": tool_name, "args": tool_args}


class _GovernedFakeAgent(GovernedAgentMixin, _FakeAgent):
    pass


def _adapter() -> GaiaGovernanceAdapter:
    return GaiaGovernanceAdapter(
        policy_engine=RuleBasedPolicyEngine(),
        checkpoint_runtime=InMemoryCheckpointBridge(),
        receipt_service=InMemoryReceiptService(),
        policy_binding=StaticPolicyBindingService(),
    )


def test_no_adapter_is_pure_pass_through():
    agent = _GovernedFakeAgent()
    result = agent._execute_tool("get_weather", {"city": "Austin"})
    assert result["status"] == "ok"
    assert agent.calls == [("get_weather", {"city": "Austin"})]


def test_adapter_with_allow_decision_executes_tool():
    seen: list[str] = []
    agent = _GovernedFakeAgent(
        governance_adapter=_adapter(),
        governance_actor_id="tester",
        governance_risk_tags={},  # nothing tagged -> ALLOW
        governance_callback=lambda tn, *_: seen.append(tn),
    )
    result = agent._execute_tool("get_weather", {"city": "Austin"})
    assert result["status"] == "ok"
    assert agent.calls == [("get_weather", {"city": "Austin"})]
    assert seen == ["get_weather"]


def test_adapter_with_block_decision_short_circuits():
    decisions: list[str] = []

    def cb(_tn, _args, _action, decision):
        decisions.append(decision.decision)

    agent = _GovernedFakeAgent(
        governance_adapter=_adapter(),
        governance_risk_tags={"drop_table": ["blocked"]},
        governance_callback=cb,
    )
    result = agent._execute_tool("drop_table", {"name": "users"})
    assert result["status"] == "denied"
    assert result["governance_decision"] == "BLOCK"
    assert "blocked by governance" in result["error"]
    # tool was NOT invoked on the underlying agent
    assert agent.calls == []
    assert decisions == ["BLOCK"]


def test_review_decision_without_reviewer_fails_closed():
    decisions: list[str] = []

    def cb(_tn, _args, _action, decision):
        decisions.append(decision.decision)

    agent = _GovernedFakeAgent(
        governance_adapter=_adapter(),
        governance_risk_tags={"publish_post": ["review"]},
        governance_callback=cb,
    )
    result = agent._execute_tool("publish_post", {"body": "hi"})
    # No reviewer + no console -> REVIEW fails closed.
    assert result["status"] == "denied"
    assert result["governance_decision"] == "REVIEW_REJECTED"
    assert agent.calls == []
    # Callback still sees the original REVIEW decision.
    assert decisions == ["REVIEW"]


def test_callback_exception_does_not_break_execution(caplog):
    def boom(*_a, **_kw):
        raise RuntimeError("callback exploded")

    agent = _GovernedFakeAgent(
        governance_adapter=_adapter(),
        governance_callback=boom,
    )
    caplog.set_level(logging.WARNING, "gaia.governance.mixin")
    result = agent._execute_tool("get_weather", {"city": "Austin"})
    assert result["status"] == "ok"
    # The exception was swallowed but a warning was logged so an operator
    # can detect a misbehaving callback. Don't assert the message string —
    # just that something was warned about the mixin.
    assert any(
        record.levelname == "WARNING" and record.name == "gaia.governance.mixin"
        for record in caplog.records
    )


def test_unknown_transition_outcome_fails_closed():
    """Defensive: if a custom CheckpointRuntime returns an outcome status
    the mixin doesn't recognize, deny the call rather than letting it
    silently pass through.
    """

    class _BogusOutcomeAdapter(GaiaGovernanceAdapter):
        def handle_transition(self, transition, decision):
            from gaia.governance.schemas import TransitionOutcome

            return TransitionOutcome(status="WAT", reason="from outer space")

    adapter = _BogusOutcomeAdapter(
        policy_engine=RuleBasedPolicyEngine(),
        checkpoint_runtime=InMemoryCheckpointBridge(),
        receipt_service=InMemoryReceiptService(),
        policy_binding=StaticPolicyBindingService(),
    )
    agent = _GovernedFakeAgent(governance_adapter=adapter)
    result = agent._execute_tool("get_weather", {"city": "Austin"})
    assert result["status"] == "denied"
    assert result["governance_decision"] == "ERROR"
    assert "unknown transition outcome" in result["error"]
    assert agent.calls == []
