import logging
from unittest.mock import MagicMock

import pytest

from gaia.agents.base.agent import Agent
from gaia.governance.mixin import GovernedAgentMixin
from gaia.governance.schemas import GovernanceDecision, CheckpointResolution


class _SimpleAgent(GovernedAgentMixin, Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        pass

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def agent(monkeypatch):
    # Patch heavy AgentSDK used in Agent.__init__
    from unittest.mock import MagicMock

    monkeypatch.setattr("gaia.agents.base.agent.AgentSDK", MagicMock())
    a = _SimpleAgent(silent_mode=True, skip_lemonade=True)
    return a


def test_resolve_canonical_tool_name_uses_resolver(agent):
    agent._resolve_tool_name = lambda name: f"canon_{name}"
    assert agent._resolve_canonical_tool_name("foo") == "canon_foo"


def test_resolve_canonical_tool_name_lookuperror(agent):
    def resolver(_):
        raise LookupError()

    agent._resolve_tool_name = resolver
    assert agent._resolve_canonical_tool_name("x") == "x"


def test_resolve_canonical_tool_name_exception_logs_and_returns_raw(agent, caplog):
    def resolver(_):
        raise RuntimeError("boom")

    agent._resolve_tool_name = resolver
    caplog.set_level(logging.WARNING)
    assert agent._resolve_canonical_tool_name("y") == "y"
    assert any("governance: _resolve_tool_name raised unexpectedly" in r.message for r in caplog.records)


def test_handle_review_checkpoint_approved(monkeypatch, agent):
    # Adapter resolves checkpoint to RESUMED
    adapter = MagicMock()
    from gaia.governance.schemas import TransitionOutcome

    transition = MagicMock()
    decision = GovernanceDecision(decision="REVIEW", reason="r", policy_version="v1", rule_ids=[])

    # Adapter.resolve_checkpoint returns RESUMED
    adapter.resolve_checkpoint.return_value = TransitionOutcome(status="RESUMED", reason="ok", checkpoint_id="chk_1", metadata={})

    # Patch Agent._execute_tool to observe call
    monkeypatch.setattr(Agent, "_execute_tool", lambda self, n, a: "EXECUTED")

    res = agent._handle_review_checkpoint(adapter, "tool", {"a": 1}, decision, transition, "chk_1")
    assert res == "EXECUTED"


def test_handle_review_checkpoint_rejected(monkeypatch, agent):
    adapter = MagicMock()
    from gaia.governance.schemas import TransitionOutcome

    transition = MagicMock()
    decision = GovernanceDecision(decision="REVIEW", reason="r", policy_version="v1", rule_ids=["r1"]) 

    adapter.resolve_checkpoint.return_value = TransitionOutcome(status="TERMINATED", reason="rejected", checkpoint_id="chk_2", metadata={"receipt_id": "rcp1"})

    res = agent._handle_review_checkpoint(adapter, "tool", {"a": 1}, decision, transition, "chk_2")
    assert isinstance(res, dict)
    assert res["status"] == "denied"
    assert res.get("receipt_id") == "rcp1"


def test_emit_policy_alert_calls_console(monkeypatch, agent):
    class C:
        def __init__(self):
            self.called = False

        def print_policy_alert(self, *args, **kwargs):
            self.called = True

    console = C()
    agent.console = console
    # Should do nothing for non-BLOCK decisions
    agent._emit_policy_alert("t", "ALLOW", "r", [], "v", None)
    assert not console.called

    # BLOCK should call
    agent._emit_policy_alert("t", "BLOCK", "r", ["r1"], "v", "rcp")
    assert console.called


def test_emit_policy_alert_handles_exceptions(monkeypatch, agent, caplog):
    class C:
        def print_policy_alert(self, *a, **k):
            raise RuntimeError("boom")

    agent.console = C()
    caplog.set_level(logging.WARNING)
    agent._emit_policy_alert("t", "BLOCK", "r", [], "v", None)
    assert any("governance: failed to emit policy alert" in r.message for r in caplog.records)
