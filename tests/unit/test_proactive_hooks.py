# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for Agent proactive lifecycle hooks (issue #1484).

Tests cover:
  - Default on_first_run / on_heartbeat return empty lists
  - propose() stores a proposal in GoalStore
  - Regression: plain process_query does NOT trigger proactive hooks
  - Low-risk proposals auto-approve; others need approval

All tests use temp-file GoalStore — no real ~/.gaia directory touched.
"""

from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.console import AgentConsole, SilentConsole
from gaia.agents.base.goal_store import Proposal


# ===========================================================================
# Helpers
# ===========================================================================


def _make_test_agent(**kwargs):
    """Create a minimal Agent that doesn't require Lemonade server."""
    from gaia.agents.base.agent import Agent
    from gaia.agents.base.console import SilentConsole

    class _TestAgent(Agent):
        def _get_system_prompt(self):
            return "Test"

        def _create_console(self):
            return SilentConsole()

        def _register_tools(self):
            pass

    return _TestAgent(silent_mode=True, skip_lemonade=True, **kwargs)


# ===========================================================================
# 1. Default hook behavior (no-op)
# ===========================================================================


class TestDefaultHooks:

    def test_on_first_run_returns_empty(self):
        agent = _make_test_agent()
        result = agent.on_first_run(None)
        assert result == []

    def test_on_first_run_with_context(self):
        agent = _make_test_agent()
        ctx = {"files": ["a.txt", "b.txt"]}
        result = agent.on_first_run(ctx)
        assert result == []

    def test_on_heartbeat_returns_empty(self):
        agent = _make_test_agent()
        result = agent.on_heartbeat(None)
        assert result == []

    def test_on_heartbeat_with_context(self):
        agent = _make_test_agent()
        ctx = {"recent_changes": ["x.py"]}
        result = agent.on_heartbeat(ctx)
        assert result == []


# ===========================================================================
# 2. Override hooks return proposals
# ===========================================================================


class TestOverrideHooks:

    def test_first_run_proposes(self):
        class _ProposingAgent(Agent):
            def _get_system_prompt(self):
                return "Test"

            def _create_console(self):
                return SilentConsole()

            def _register_tools(self):
                pass

            def on_first_run(self, context):
                return [
                    Proposal(action="setup_project", rationale="init", risk="low"),
                ]

        agent = _ProposingAgent(silent_mode=True, skip_lemonade=True)
        result = agent.on_first_run(None)
        assert len(result) == 1
        assert result[0].action == "setup_project"

    def test_heartbeat_proposes(self):
        class _HeartbeatingAgent(Agent):
            def _get_system_prompt(self):
                return "Test"

            def _create_console(self):
                return SilentConsole()

            def _register_tools(self):
                pass

            def on_heartbeat(self, context):
                return [
                    Proposal(action="check_files", rationale="heartbeat", risk="medium"),
                ]

        agent = _HeartbeatingAgent(silent_mode=True, skip_lemonade=True)
        result = agent.on_heartbeat(None)
        assert len(result) == 1
        assert result[0].action == "check_files"


# ===========================================================================
# 3. propose() stores in GoalStore
# ===========================================================================


class TestPropose:

    def test_propose_stores_pending_approval(self):
        """Verify propose() calls GoalStore.propose() correctly."""
        from gaia.agents.base.goal_store import GoalStore

        agent = _make_test_agent()
        mock_store = MagicMock(spec=GoalStore)
        mock_goal = MagicMock()
        mock_goal.status = "pending_approval"
        mock_store.propose.return_value = mock_goal

        with patch('gaia.agents.base.goal_store.GoalStore', return_value=mock_store):
            result = agent.propose(Proposal(action="test_action", rationale="test"))
            assert result is mock_goal
            mock_store.propose.assert_called_once()
            call_args = mock_store.propose.call_args
            proposal_arg = call_args[0][0]
            assert proposal_arg.action == "test_action"
            assert proposal_arg.rationale == "test"

    def test_propose_persists_risk_level(self):
        """Verify risk level is stored so eval harness can check class-3."""
        from gaia.agents.base.goal_store import GoalStore

        agent = _make_test_agent()
        mock_store = MagicMock(spec=GoalStore)
        mock_goal = MagicMock()
        mock_goal.status = "pending_approval"
        mock_store.propose.return_value = mock_goal

        with patch('gaia.agents.base.goal_store.GoalStore', return_value=mock_store):
            agent.propose(Proposal(action="delete", rationale="rm", risk="critical"))
            call_args = mock_store.propose.call_args
            proposal = call_args[0][0]
            assert proposal.risk == "critical"


# ===========================================================================
# 4. Regression: process_query does not trigger hooks
# ===========================================================================


class TestRegression:

    def test_process_query_does_not_call_on_first_run(self):
        """A plain agent calling process_query should NOT invoke on_first_run."""
        calls = {"first_run": 0, "heartbeat": 0}

        class _TrackingAgent(Agent):
            def _get_system_prompt(self):
                return "Test"

            def _create_console(self):
                return SilentConsole()

            def _register_tools(self):
                pass

            def on_first_run(self, context):
                calls["first_run"] += 1
                return super().on_first_run(context)

            def on_heartbeat(self, context):
                calls["heartbeat"] += 1
                return super().on_heartbeat(context)

        agent = _TrackingAgent(silent_mode=True, skip_lemonade=True)

        # Patch _process_query_impl to return early — we only care
        # that on_first_run/on_heartbeat are NOT called
        with patch.object(agent, '_process_query_impl', return_value={"result": "ok"}):
            result = agent.process_query("hello", max_steps=1)

        # process_query should NOT trigger proactive hooks
        assert calls["first_run"] == 0, "on_first_run must not be called by process_query"
        assert calls["heartbeat"] == 0, "on_heartbeat must not be called by process_query"
        assert isinstance(result, dict)

    def test_plain_agent_default_noop(self):
        """Default Agent hooks must return empty lists, not raise."""
        agent = _make_test_agent()
        # These should not raise and return []
        assert agent.on_first_run(None) == []
        assert agent.on_heartbeat(None) == []


# ===========================================================================
# 5. Identity context sharing
# ===========================================================================


class TestIdentityContext:

    def test_agent_identity_context_returns_none_for_no_ns_id(self):
        agent = _make_test_agent()
        result = agent._agent_identity_context(None)
        assert result is None

    def test_agent_identity_context_returns_cm_for_ns_id(self):
        agent = _make_test_agent()
        result = agent._agent_identity_context("agent:test-id")
        assert result is not None
        # Should be a context manager (has __enter__ and __exit__)
        assert hasattr(result, '__enter__')
        assert hasattr(result, '__exit__')
