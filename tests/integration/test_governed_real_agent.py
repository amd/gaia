# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# pylint: disable=protected-access
"""Integration test: GovernedAgentMixin against the real gaia.Agent class.

The full ``Agent.__init__`` starts Lemonade / MCP, which we don't want
to depend on in a unit-level gate. This test proves the mixin's MRO
binds correctly against the real class by:

1. Building a ``GovernedAgentMixin + gaia.Agent`` subclass.
2. Instantiating via ``__new__`` and setting only the state
   ``_execute_tool`` actually reads (``console`` for confirmation gate,
   the governance state attributes).
3. Registering a real ``@tool`` and calling ``_execute_tool`` through
   the mixin, verifying BLOCK short-circuits and ALLOW reaches the tool.

If this test ever breaks, the mixin's contract with the real Agent has
regressed — long before anyone runs the full interactive demo.
"""

from __future__ import annotations

from gaia import Agent, tool
from gaia.governance import (
    GaiaGovernanceAdapter,
    GovernedAgentMixin,
)
from gaia.governance.checkpoint_bridge import InMemoryCheckpointBridge
from gaia.governance.policy_binding import StaticPolicyBindingService
from gaia.governance.receipt_service import InMemoryReceiptService
from gaia.governance.stubs import RuleBasedPolicyEngine


@tool
def _governed_real_agent_probe(x: int = 1) -> dict:
    """Minimal tool used only by this test."""
    return {"status": "ok", "x": x}


class _StubConsole:
    """Minimal console stand-in to satisfy the confirmation gate path."""

    def confirm_tool_execution(self, _tool_name, _tool_args):
        return True


class _GovernedRealAgent(GovernedAgentMixin, Agent):
    """Real Agent subclass with the governance mixin mixed in."""

    def _register_tools(self) -> None:
        # Abstract on Agent; no-op here because we bypass __init__ and
        # rely on the module-level tool registry populated by @tool.
        return None

    def _get_system_prompt(self) -> str:  # pragma: no cover - unused
        return ""


def _build_agent(adapter: GaiaGovernanceAdapter | None, risk_tags: dict):
    """Build a _GovernedRealAgent bypassing __init__ (no Lemonade/MCP)."""
    agent = _GovernedRealAgent.__new__(_GovernedRealAgent)
    # Governance state that the mixin reads.
    agent.governance_adapter = adapter
    agent._governance_actor_id = "real-agent-test"
    agent._governance_workflow_id = "wf_real"
    agent._governance_risk_tags = risk_tags
    agent._governance_callback = None
    # Minimal Agent state touched by _execute_tool.
    agent.console = _StubConsole()
    agent.error_history = []
    agent._current_query = None
    agent.current_plan = None
    agent.current_step = 0
    agent.total_plan_steps = 0
    return agent


def _adapter() -> GaiaGovernanceAdapter:
    return GaiaGovernanceAdapter(
        policy_engine=RuleBasedPolicyEngine(),
        checkpoint_runtime=InMemoryCheckpointBridge(),
        receipt_service=InMemoryReceiptService(),
        policy_binding=StaticPolicyBindingService(),
    )


def test_mro_places_mixin_before_agent():
    mro = _GovernedRealAgent.__mro__
    names = [c.__name__ for c in mro]
    assert names.index("GovernedAgentMixin") < names.index("Agent")


def test_mixin_passes_through_to_real_agent_when_no_adapter():
    agent = _build_agent(adapter=None, risk_tags={})
    result = agent._execute_tool("_governed_real_agent_probe", {"x": 7})
    assert result == {"status": "ok", "x": 7}


def test_block_decision_short_circuits_real_agent():
    agent = _build_agent(
        adapter=_adapter(),
        risk_tags={"_governed_real_agent_probe": ["blocked"]},
    )
    result = agent._execute_tool("_governed_real_agent_probe", {"x": 9})
    assert result["status"] == "denied"
    assert result["governance_decision"] == "BLOCK"
    assert "blocked by governance" in result["error"]


def test_allow_decision_reaches_real_tool_registry():
    agent = _build_agent(adapter=_adapter(), risk_tags={})  # no tags -> ALLOW
    result = agent._execute_tool("_governed_real_agent_probe", {"x": 42})
    assert result == {"status": "ok", "x": 42}
