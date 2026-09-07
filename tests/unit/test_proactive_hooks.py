# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for Agent proactive lifecycle hooks (issue #1484).

Tests cover:
  - Default on_first_run / on_heartbeat return empty lists
  - propose() stores a proposal in GoalStore
  - Regression: plain process_query does NOT trigger proactive hooks
  - Every proposal awaits explicit user approval, regardless of risk tier

All tests use temp-file GoalStore — no real ~/.gaia directory touched.
"""

from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.console import SilentConsole
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
                    Proposal(
                        action="check_files", rationale="heartbeat", risk="medium"
                    ),
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

        with patch("gaia.agents.base.goal_store.GoalStore", return_value=mock_store):
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

        with patch("gaia.agents.base.goal_store.GoalStore", return_value=mock_store):
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
        with patch.object(agent, "_process_query_impl", return_value={"result": "ok"}):
            result = agent.process_query("hello", max_steps=1)

        # process_query should NOT trigger proactive hooks
        assert (
            calls["first_run"] == 0
        ), "on_first_run must not be called by process_query"
        assert (
            calls["heartbeat"] == 0
        ), "on_heartbeat must not be called by process_query"
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
    """``_agent_identity_context`` marks an agent turn, and binds an identity
    to it when there is one.

    It used to return ``None`` for an agent with no namespaced id, and
    ``process_query`` then ran that turn outside any context at all. That is
    precisely the state the connectors layer reads as "no agent — take the
    ungated CLI path", so an unregistered agent got credentials without any
    grant check (#915). The block is now entered either way: no identity
    inside an agent turn means REFUSED, not unrestricted.
    """

    def test_no_ns_id_still_enters_an_agent_turn(self):
        from gaia.connectors.context import agent_runtime_active, current_agent_id

        agent = _make_test_agent()
        with agent._agent_identity_context(None):
            # An agent turn is in progress...
            assert agent_runtime_active() is True
            # ...but it carries no identity to check a grant against.
            assert current_agent_id() is None
        assert agent_runtime_active() is False

    def test_credential_access_inside_that_turn_is_refused(self, tmp_path, monkeypatch):
        """The reason the block is entered at all. Before, this call returned a
        token; the grant check never ran because there was no context to read.
        """
        import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
        from gaia.connectors.api import _authorize_access
        from gaia.connectors.errors import AuthRequiredError

        monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
        agent = _make_test_agent()

        with agent._agent_identity_context(None):
            with pytest.raises(AuthRequiredError) as exc:
                _authorize_access(
                    provider="google",
                    scopes=["https://www.googleapis.com/auth/gmail.readonly"],
                    agent_id=None,
                    account_email="default",
                )
        assert exc.value.reason is AuthRequiredError.Reason.AGENT_NOT_GRANTED

    def test_agent_identity_context_returns_cm_for_ns_id(self):
        agent = _make_test_agent()
        result = agent._agent_identity_context("agent:test-id")
        assert result is not None
        # Should be a context manager (has __enter__ and __exit__)
        assert hasattr(result, "__enter__")
        assert hasattr(result, "__exit__")

    def test_ns_id_is_readable_inside_the_block(self):
        from gaia.connectors.context import agent_runtime_active, current_agent_id

        agent = _make_test_agent()
        with agent._agent_identity_context("agent:test-id"):
            assert current_agent_id() == "agent:test-id"
            assert agent_runtime_active() is True
        assert current_agent_id() is None


# ===========================================================================
# 6. End-to-end: the documented example agent against a real GoalStore
# ===========================================================================


class _WorkspaceAgent(Agent):
    """Mirror of the WorkspaceAgent in docs/sdk/core/agent-system.mdx."""

    def _get_system_prompt(self):
        return "You help keep a project workspace tidy."

    def _create_console(self):
        return SilentConsole()

    def _register_tools(self):
        pass

    def on_first_run(self, context):
        return [
            Proposal(
                action="index_workspace",
                rationale="No index found; build one for fast retrieval.",
                action_class="file_read",
                risk="low",
            )
        ]

    def on_heartbeat(self, context):
        return [
            Proposal(
                action="archive_stale_logs",
                rationale="12 log files older than 30 days.",
                action_class="file_write",
                risk="high",
            )
        ]


class TestExampleAgentEndToEnd:
    """Exercises the full override -> propose() -> real GoalStore path (no mocks),
    keeping the documented example honest."""

    def test_workspace_agent_proposes_end_to_end(self, tmp_path):
        from gaia.agents.base.goal_store import GoalStore

        store = GoalStore(db_path=tmp_path / "goals.db")
        try:
            agent = _WorkspaceAgent(silent_mode=True, skip_lemonade=True)

            # Agent.propose() constructs its own GoalStore(); point it at ours.
            with patch("gaia.agents.base.goal_store.GoalStore", return_value=store):
                first = [agent.propose(p) for p in agent.on_first_run(None)]
                beat = [agent.propose(p) for p in agent.on_heartbeat(None)]

            # Low-risk first-run proposal still waits for the user (spec §6.3).
            assert len(first) == 1
            assert first[0].status == "pending_approval"
            assert first[0].approved_for_auto is False

            # High-risk heartbeat proposal waits for the user.
            assert len(beat) == 1
            assert beat[0].status == "pending_approval"
            assert beat[0].approved_for_auto is False

            # Both are persisted and both are pending approval.
            pending = store.get_pending_approval()
            assert {g.id for g in pending} == {first[0].id, beat[0].id}
        finally:
            store.close()
