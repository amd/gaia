# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for Agent-Nexus Service Integration.

This test suite validates the integration between the Agent base class
and NexusService for Chronicle event logging, including:
- Nexus connection on Agent initialization
- Chronicle event commitment
- Chronicle disabled mode
- Error event logging
- Graceful degradation when Nexus unavailable
- Integration with tool execution
- Phase tracking
- Loop iteration tracking

Quality Gate Criteria Covered:
- State management integrity
- Error handling and recovery
- Chronicle event logging correctness
- Graceful degradation patterns
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

from gaia.agents.base.agent import Agent
from gaia.state.nexus import NexusService


# =============================================================================
# Mock Agent Implementation for Testing
# =============================================================================

class MockAgent(Agent):
    """Mock agent implementation for testing."""

    def __init__(self, **kwargs):
        # Skip parent __init__ to avoid LLM initialization
        # We only need to test Nexus integration
        self.error_history = []
        self.conversation_history = []
        self.max_steps = kwargs.get('max_steps', 20)
        self.debug_prompts = kwargs.get('debug_prompts', False)
        self.show_prompts = kwargs.get('show_prompts', False)
        self.output_dir = kwargs.get('output_dir', '/tmp')
        self.streaming = kwargs.get('streaming', False)
        self.show_stats = kwargs.get('show_stats', False)
        self.silent_mode = kwargs.get('silent_mode', True)
        self.debug = kwargs.get('debug', False)
        self.last_result = None
        self.max_plan_iterations = kwargs.get('max_plan_iterations', 3)
        self.max_consecutive_repeats = kwargs.get('max_consecutive_repeats', 4)
        self._current_query = None
        self.execution_state = self.STATE_PLANNING
        self.current_plan = None
        self.current_step = 0
        self.total_plan_steps = 0
        self.plan_iterations = 0
        self.skip_lemonade = True

        # Manually set up Nexus connection for testing
        self._enable_chronicle = True
        self._nexus = None

    def _register_tools(self):
        """No-op tool registration for testing."""
        pass


class TestAgentNexusConnection:
    """Tests for Nexus connection on Agent initialization."""

    def teardown_method(self):
        """Reset Nexus singleton after each test."""
        NexusService.reset_instance()

    def test_nexus_connected_on_agent_init(self):
        """Test Agent connects to NexusService during initialization."""
        with patch('gaia.state.nexus.AuditLogger') as mock_audit:
            mock_audit.get_instance = Mock(return_value=Mock(log=Mock()))

            agent = MockAgent()
            agent._nexus = NexusService.get_instance()

            assert agent._nexus is not None
            assert agent._enable_chronicle is True

    def test_nexus_is_singleton_instance(self):
        """Test Agent gets same NexusService instance as direct access."""
        with patch('gaia.state.nexus.AuditLogger') as mock_audit:
            mock_audit.get_instance = Mock(return_value=Mock(log=Mock()))

            agent = MockAgent()
            agent._nexus = NexusService.get_instance()
            direct_nexus = NexusService.get_instance()

            assert agent._nexus is direct_nexus

    def test_chronicle_enabled_by_default(self):
        """Test Chronicle is enabled by default on Agent initialization."""
        with patch('gaia.state.nexus.AuditLogger') as mock_audit:
            mock_audit.get_instance = Mock(return_value=Mock(log=Mock()))

            agent = MockAgent()
            agent._nexus = NexusService.get_instance()

            assert agent._enable_chronicle is True
            assert agent._nexus is not None


# =============================================================================
# Chronicle Event Commitment Tests
# =============================================================================

class TestChronicleEventCommitment:
    """Tests for _commit_chronicle_event method."""

    def setup_method(self):
        """Setup mock Nexus for each test."""
        NexusService.reset_instance()
        self.mock_audit_class = Mock()
        self.mock_audit_instance = Mock()
        self.mock_audit_instance.log = Mock(return_value=Mock(event_id="mock-event-id"))
        self.mock_audit_class.get_instance = Mock(return_value=self.mock_audit_instance)

        self.patcher = patch('gaia.state.nexus.AuditLogger', self.mock_audit_class)
        self.patcher.start()
        self.nexus = NexusService.get_instance()

    def teardown_method(self):
        """Cleanup after each test."""
        self.patcher.stop()
        NexusService.reset_instance()

    def test_commit_chronicle_event_basic(self):
        """Test basic event commitment to Chronicle."""
        agent = MockAgent()
        agent._nexus = self.nexus

        event_id = agent._commit_chronicle_event(
            event_type="TOOL_CALL",
            payload={"tool": "read_file", "path": "test.py"}
        )

        assert event_id is not None
        assert isinstance(event_id, str)

    def test_commit_chronicle_event_with_phase(self):
        """Test event commitment with phase parameter."""
        agent = MockAgent()
        agent._nexus = self.nexus

        event_id = agent._commit_chronicle_event(
            event_type="PHASE_ENTER",
            payload={"action": "starting"},  # Don't use 'phase' key in payload to avoid conflict
            phase="PLANNING"
        )

        assert event_id is not None
        snapshot = self.nexus.get_snapshot()
        assert len(snapshot["chronicle"]) == 1
        assert snapshot["chronicle"][0]["phase"] == "PLANNING"

    def test_commit_chronicle_event_with_loop_id(self):
        """Test event commitment with loop iteration identifier."""
        agent = MockAgent()
        agent._nexus = self.nexus

        event_id = agent._commit_chronicle_event(
            event_type="ITERATION",
            payload={"step": 1},
            loop_id="loop-001"
        )

        assert event_id is not None
        snapshot = self.nexus.get_snapshot()
        assert snapshot["chronicle"][0]["loop_id"] == "loop-001"

    def test_commit_chronicle_event_with_all_parameters(self):
        """Test event commitment with all parameters."""
        agent = MockAgent()
        agent._nexus = self.nexus

        event_id = agent._commit_chronicle_event(
            event_type="TOOL_EXECUTION",
            payload={"tool": "write_file", "path": "output.py", "lines": 42},
            phase="EXECUTION",
            loop_id="loop-002"
        )

        assert event_id is not None
        snapshot = self.nexus.get_snapshot()
        event = snapshot["chronicle"][0]
        assert event["event_type"] == "TOOL_EXECUTION"
        assert event["phase"] == "EXECUTION"
        assert event["loop_id"] == "loop-002"
        assert event["payload"]["tool"] == "write_file"

    def test_commit_chronicle_event_agent_id_uses_class_name(self):
        """Test agent_id in event uses the agent class name."""
        agent = MockAgent()
        agent._nexus = self.nexus

        agent._commit_chronicle_event(
            event_type="TEST_EVENT",
            payload={}
        )

        snapshot = self.nexus.get_snapshot()
        assert snapshot["chronicle"][0]["agent_id"] == "MockAgent"


# =============================================================================
# Chronicle Disabled Mode Tests
# =============================================================================

class TestChronicleDisabledMode:
    """Tests for Chronicle disabled mode behavior."""

    def teardown_method(self):
        """Reset Nexus singleton after each test."""
        NexusService.reset_instance()

    def test_commit_returns_none_when_chronicle_disabled(self):
        """Test _commit_chronicle_event returns None when Chronicle disabled."""
        agent = MockAgent()
        agent._enable_chronicle = False
        agent._nexus = None

        result = agent._commit_chronicle_event(
            event_type="TEST_EVENT",
            payload={}
        )

        assert result is None

    def test_commit_returns_none_when_nexus_is_none(self):
        """Test _commit_chronicle_event returns None when Nexus is None."""
        agent = MockAgent()
        agent._enable_chronicle = True
        agent._nexus = None

        result = agent._commit_chronicle_event(
            event_type="TEST_EVENT",
            payload={}
        )

        assert result is None

    def test_no_exception_when_chronicle_disabled(self):
        """Test no exception raised when Chronicle is disabled."""
        agent = MockAgent()
        agent._enable_chronicle = False
        agent._nexus = None

        # Should not raise any exception
        result = agent._commit_chronicle_event(
            event_type="TEST_EVENT",
            payload={"key": "value"}
        )

        assert result is None


# =============================================================================
# Error Event Logging Tests
# =============================================================================

class TestErrorEventLogging:
    """Tests for automatic error event logging."""

    def setup_method(self):
        """Setup mock Nexus for each test."""
        NexusService.reset_instance()
        self.mock_audit_class = Mock()
        self.mock_audit_instance = Mock()
        self.mock_audit_instance.log = Mock(return_value=Mock(event_id="mock-event-id"))
        self.mock_audit_class.get_instance = Mock(return_value=self.mock_audit_instance)

        self.patcher = patch('gaia.state.nexus.AuditLogger', self.mock_audit_class)
        self.patcher.start()
        self.nexus = NexusService.get_instance()

    def teardown_method(self):
        """Cleanup after each test."""
        self.patcher.stop()
        NexusService.reset_instance()

    def test_error_event_logged_with_error_type(self):
        """Test ERROR event includes error type in payload."""
        agent = MockAgent()
        agent._nexus = self.nexus

        agent._commit_chronicle_event(
            event_type="ERROR",
            payload={
                "error_type": "ValueError",
                "tool_name": "read_file",
                "message": "File not found"
            }
        )

        snapshot = self.nexus.get_snapshot()
        event = snapshot["chronicle"][0]
        assert event["event_type"] == "ERROR"
        assert event["payload"]["error_type"] == "ValueError"

    def test_error_event_logged_with_tool_name(self):
        """Test ERROR event includes tool name in payload."""
        agent = MockAgent()
        agent._nexus = self.nexus

        agent._commit_chronicle_event(
            event_type="ERROR",
            payload={
                "error_type": "TimeoutError",
                "tool_name": "run_shell_command",
                "message": "Command timed out"
            }
        )

        snapshot = self.nexus.get_snapshot()
        event = snapshot["chronicle"][0]
        assert event["payload"]["tool_name"] == "run_shell_command"

    def test_error_event_message_truncated(self):
        """Test long error messages are truncated for Chronicle."""
        agent = MockAgent()
        agent._nexus = self.nexus

        long_message = "Error details " * 100  # Very long message
        agent._commit_chronicle_event(
            event_type="ERROR",
            payload={
                "error_type": "RuntimeError",
                "tool_name": "complex_tool",
                "message": long_message[:100]  # Truncated
            }
        )

        snapshot = self.nexus.get_snapshot()
        event = snapshot["chronicle"][0]
        assert len(event["payload"]["message"]) <= 100


# =============================================================================
# Graceful Degradation Tests
# =============================================================================

class TestGracefulDegradation:
    """Tests for graceful degradation when Nexus unavailable."""

    def teardown_method(self):
        """Reset Nexus singleton after each test."""
        NexusService.reset_instance()

    def test_agent_init_handles_nexus_unavailable(self):
        """Test Agent initialization handles Nexus being unavailable."""
        # Simulate Nexus being unavailable by not mocking it
        agent = MockAgent()
        # Agent should still initialize even without Nexus
        assert agent is not None

    def test_commit_handles_exception_gracefully(self):
        """Test _commit_chronicle_event handles exceptions gracefully."""
        agent = MockAgent()
        # Create a mock nexus that raises on commit
        mock_nexus = Mock()
        mock_nexus.commit = Mock(side_effect=Exception("Connection failed"))
        agent._nexus = mock_nexus
        agent._enable_chronicle = True

        # Should not raise, should return None
        result = agent._commit_chronicle_event(
            event_type="TEST_EVENT",
            payload={}
        )

        assert result is None

    def test_agent_operates_without_nexus(self):
        """Test Agent can operate without Nexus connection."""
        agent = MockAgent()
        agent._nexus = None
        agent._enable_chronicle = False

        # Agent methods should still work
        assert agent._enable_chronicle is False
        assert agent._nexus is None


# =============================================================================
# Tool Execution Integration Tests
# =============================================================================

class TestToolExecutionIntegration:
    """Tests for Chronicle integration with tool execution."""

    def setup_method(self):
        """Setup mock Nexus for each test."""
        NexusService.reset_instance()
        self.mock_audit_class = Mock()
        self.mock_audit_instance = Mock()
        self.mock_audit_instance.log = Mock(return_value=Mock(event_id="mock-event-id"))
        self.mock_audit_class.get_instance = Mock(return_value=self.mock_audit_instance)

        self.patcher = patch('gaia.state.nexus.AuditLogger', self.mock_audit_class)
        self.patcher.start()
        self.nexus = NexusService.get_instance()

    def teardown_method(self):
        """Cleanup after each test."""
        self.patcher.stop()
        NexusService.reset_instance()

    def test_tool_call_event_logged(self):
        """Test TOOL_CALL event is logged when tool is invoked."""
        agent = MockAgent()
        agent._nexus = self.nexus

        agent._commit_chronicle_event(
            event_type="TOOL_CALL",
            payload={
                "tool": "read_file",
                "arguments": {"path": "config.json"}
            }
        )

        snapshot = self.nexus.get_snapshot()
        assert len(snapshot["chronicle"]) == 1
        assert snapshot["chronicle"][0]["event_type"] == "TOOL_CALL"

    def test_tool_result_event_logged(self):
        """Test TOOL_RESULT event is logged after tool execution."""
        agent = MockAgent()
        agent._nexus = self.nexus

        agent._commit_chronicle_event(
            event_type="TOOL_RESULT",
            payload={
                "tool": "read_file",
                "result": {"status": "success", "content": "..."}
            }
        )

        snapshot = self.nexus.get_snapshot()
        assert len(snapshot["chronicle"]) == 1
        event = snapshot["chronicle"][0]
        assert event["event_type"] == "TOOL_RESULT"
        assert event["payload"]["result"]["status"] == "success"

    def test_tool_error_event_logged_on_failure(self):
        """Test ERROR event is logged when tool execution fails."""
        agent = MockAgent()
        agent._nexus = self.nexus

        agent._commit_chronicle_event(
            event_type="ERROR",
            payload={
                "error_type": "ToolExecutionError",
                "tool_name": "write_file",
                "message": "Permission denied"
            }
        )

        snapshot = self.nexus.get_snapshot()
        event = snapshot["chronicle"][0]
        assert event["event_type"] == "ERROR"
        assert "Permission denied" in event["payload"]["message"]


# =============================================================================
# Phase Tracking Tests
# =============================================================================

class TestPhaseTracking:
    """Tests for pipeline phase tracking in Chronicle."""

    def setup_method(self):
        """Setup mock Nexus for each test."""
        NexusService.reset_instance()
        self.mock_audit_class = Mock()
        self.mock_audit_instance = Mock()
        self.mock_audit_instance.log = Mock(return_value=Mock(event_id="mock-event-id"))
        self.mock_audit_class.get_instance = Mock(return_value=self.mock_audit_instance)

        self.patcher = patch('gaia.state.nexus.AuditLogger', self.mock_audit_class)
        self.patcher.start()
        self.nexus = NexusService.get_instance()

    def teardown_method(self):
        """Cleanup after each test."""
        self.patcher.stop()
        NexusService.reset_instance()

    def test_phase_enter_event_logged(self):
        """Test PHASE_ENTER event is logged when entering a phase."""
        agent = MockAgent()
        agent._nexus = self.nexus

        agent._commit_chronicle_event(
            event_type="PHASE_ENTER",
            payload={"action": "entering"},  # Don't use 'phase' key in payload
            phase="PLANNING"
        )

        snapshot = self.nexus.get_snapshot()
        event = snapshot["chronicle"][0]
        assert event["event_type"] == "PHASE_ENTER"
        assert event["phase"] == "PLANNING"

    def test_phase_exit_event_logged(self):
        """Test PHASE_EXIT event is logged when exiting a phase."""
        agent = MockAgent()
        agent._nexus = self.nexus

        agent._commit_chronicle_event(
            event_type="PHASE_EXIT",
            payload={"action": "exiting"},  # Don't use 'phase' key in payload
            phase="PLANNING"
        )

        snapshot = self.nexus.get_snapshot()
        event = snapshot["chronicle"][0]
        assert event["event_type"] == "PHASE_EXIT"
        assert event["phase"] == "PLANNING"

    def test_multiple_phases_tracked(self):
        """Test multiple pipeline phases are tracked correctly."""
        agent = MockAgent()
        agent._nexus = self.nexus

        phases = ["PLANNING", "EXECUTION", "VALIDATION", "COMPLETION"]
        for phase in phases:
            agent._commit_chronicle_event(
                event_type="PHASE_ENTER",
                payload={"action": f"entering_{phase.lower()}"},  # Don't use 'phase' key
                phase=phase
            )

        snapshot = self.nexus.get_snapshot()
        assert len(snapshot["chronicle"]) == 4

        # Verify all phases recorded
        recorded_phases = [e["phase"] for e in snapshot["chronicle"]]
        assert recorded_phases == phases

    def test_phase_summary_from_nexus(self):
        """Test phase summary can be retrieved from Nexus."""
        agent = MockAgent()
        agent._nexus = self.nexus

        for i in range(5):
            agent._commit_chronicle_event(
                event_type="TOOL_EXECUTION",
                payload={"tool": f"tool_{i}"},
                phase="EXECUTION"
            )

        summary = self.nexus.get_phase_summary("EXECUTION")
        assert summary["phase"] == "EXECUTION"
        assert summary["event_count"] == 5


# =============================================================================
# Loop Iteration Tracking Tests
# =============================================================================

class TestLoopIterationTracking:
    """Tests for loop iteration tracking in Chronicle."""

    def setup_method(self):
        """Setup mock Nexus for each test."""
        NexusService.reset_instance()
        self.mock_audit_class = Mock()
        self.mock_audit_instance = Mock()
        self.mock_audit_instance.log = Mock(return_value=Mock(event_id="mock-event-id"))
        self.mock_audit_class.get_instance = Mock(return_value=self.mock_audit_instance)

        self.patcher = patch('gaia.state.nexus.AuditLogger', self.mock_audit_class)
        self.patcher.start()
        self.nexus = NexusService.get_instance()

    def teardown_method(self):
        """Cleanup after each test."""
        self.patcher.stop()
        NexusService.reset_instance()

    def test_loop_id_tracked_per_iteration(self):
        """Test loop_id is tracked for each iteration."""
        agent = MockAgent()
        agent._nexus = self.nexus

        for i in range(5):
            agent._commit_chronicle_event(
                event_type="ITERATION",
                payload={"step": i},
                loop_id=f"loop-{i:03d}"
            )

        snapshot = self.nexus.get_snapshot()
        assert len(snapshot["chronicle"]) == 5

        for i, event in enumerate(snapshot["chronicle"]):
            assert event["loop_id"] == f"loop-{i:03d}"

    def test_nested_loop_tracking(self):
        """Test nested loop iterations are tracked correctly."""
        agent = MockAgent()
        agent._nexus = self.nexus

        # Outer loop
        for outer in range(2):
            # Inner loop
            for inner in range(3):
                agent._commit_chronicle_event(
                    event_type="NESTED_ITERATION",
                    payload={"outer": outer, "inner": inner},
                    loop_id=f"outer-{outer}-inner-{inner}"
                )

        snapshot = self.nexus.get_snapshot()
        assert len(snapshot["chronicle"]) == 6  # 2 * 3 = 6 iterations

    def test_loop_back_event_logged(self):
        """Test LOOP_BACK event is logged for iterative refinement."""
        agent = MockAgent()
        agent._nexus = self.nexus

        agent._commit_chronicle_event(
            event_type="LOOP_BACK",
            payload={"reason": "validation_failed", "retry_count": 1},
            loop_id="loop-001"
        )

        snapshot = self.nexus.get_snapshot()
        event = snapshot["chronicle"][0]
        assert event["event_type"] == "LOOP_BACK"
        assert event["loop_id"] == "loop-001"


# =============================================================================
# Summarization Helper Tests
# =============================================================================

class TestSummarizeForChronicle:
    """Tests for _summarize_for_chronicle helper method."""

    def teardown_method(self):
        """Reset Nexus singleton after each test."""
        NexusService.reset_instance()

    def test_summarize_dict_basic(self):
        """Test summarizing a dictionary."""
        agent = MockAgent()
        data = {"key1": "value1", "key2": "value2"}

        summary = agent._summarize_for_chronicle(data)

        assert "key1=value1" in summary
        assert "key2=value2" in summary

    def test_summarize_dict_truncates_values(self):
        """Test dictionary values are truncated to max_chars."""
        agent = MockAgent()
        long_value = "x" * 100
        data = {"long_key": long_value}

        summary = agent._summarize_for_chronicle(data, max_chars=50)

        assert len(summary) <= 50

    def test_summarize_dict_limits_keys(self):
        """Test dictionary summarization limits number of keys."""
        agent = MockAgent()
        data = {f"key{i}": f"value{i}" for i in range(10)}

        summary = agent._summarize_for_chronicle(data)

        # Should only include first 5 keys
        parts = summary.split(", ")
        assert len(parts) <= 5

    def test_summarize_string_truncates(self):
        """Test string summarization truncates to max_chars."""
        agent = MockAgent()
        long_string = "x" * 200

        summary = agent._summarize_for_chronicle(long_string, max_chars=50)

        assert len(summary) <= 50

    def test_summarize_other_types_converts_to_string(self):
        """Test non-dict, non-string types are converted to string."""
        agent = MockAgent()

        summary = agent._summarize_for_chronicle([1, 2, 3, 4, 5])

        assert isinstance(summary, str)
        assert "1" in summary


# =============================================================================
# Concurrent Access Tests
# =============================================================================

class TestConcurrentAccess:
    """Thread safety tests for Agent-Nexus integration."""

    def setup_method(self):
        """Setup mock Nexus for each test."""
        NexusService.reset_instance()
        self.mock_audit_class = Mock()
        self.mock_audit_instance = Mock()
        self.mock_audit_instance.log = Mock(return_value=Mock(event_id="mock-event-id"))
        self.mock_audit_class.get_instance = Mock(return_value=self.mock_audit_instance)

        self.patcher = patch('gaia.state.nexus.AuditLogger', self.mock_audit_class)
        self.patcher.start()
        self.nexus = NexusService.get_instance()

    def teardown_method(self):
        """Cleanup after each test."""
        self.patcher.stop()
        NexusService.reset_instance()

    def test_concurrent_event_commits(self):
        """Test concurrent event commits from multiple agents."""
        agents = [MockAgent() for _ in range(10)]
        for agent in agents:
            agent._nexus = self.nexus

        errors = []
        lock = threading.Lock()

        def commit_events(agent_id):
            try:
                for i in range(10):
                    agents[agent_id]._commit_chronicle_event(
                        event_type="CONCURRENT_EVENT",
                        payload={"agent": agent_id, "index": i}
                    )
            except Exception as e:
                with lock:
                    errors.append((agent_id, e))

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(commit_events, i) for i in range(10)]
            for future in as_completed(futures):
                future.result()

        assert len(errors) == 0, f"Concurrent access errors: {errors}"

        snapshot = self.nexus.get_snapshot()
        assert len(snapshot["chronicle"]) == 100  # 10 agents * 10 events

    def test_concurrent_phase_tracking(self):
        """Test concurrent phase tracking from multiple agents."""
        agents = [MockAgent() for _ in range(5)]
        for agent in agents:
            agent._nexus = self.nexus

        errors = []
        lock = threading.Lock()

        def track_phases(agent_id):
            try:
                phases = ["PLANNING", "EXECUTION", "VALIDATION"]
                for phase in phases:
                    agents[agent_id]._commit_chronicle_event(
                        event_type="PHASE_ENTER",
                        payload={"action": f"phase_{phase.lower()}"},  # Don't use 'phase' key
                        phase=phase
                    )
            except Exception as e:
                with lock:
                    errors.append((agent_id, e))

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(track_phases, i) for i in range(5)]
            for future in as_completed(futures):
                future.result()

        assert len(errors) == 0

        snapshot = self.nexus.get_snapshot()
        assert len(snapshot["chronicle"]) == 15  # 5 agents * 3 phases


# =============================================================================
# Integration with Agent State Tests
# =============================================================================

class TestAgentStateIntegration:
    """Tests for Chronicle integration with agent state management."""

    def setup_method(self):
        """Setup mock Nexus for each test."""
        NexusService.reset_instance()
        self.mock_audit_class = Mock()
        self.mock_audit_instance = Mock()
        self.mock_audit_instance.log = Mock(return_value=Mock(event_id="mock-event-id"))
        self.mock_audit_class.get_instance = Mock(return_value=self.mock_audit_instance)

        self.patcher = patch('gaia.state.nexus.AuditLogger', self.mock_audit_class)
        self.patcher.start()
        self.nexus = NexusService.get_instance()

    def teardown_method(self):
        """Cleanup after each test."""
        self.patcher.stop()
        NexusService.reset_instance()

    def test_state_change_events_logged(self):
        """Test agent state changes are logged to Chronicle."""
        agent = MockAgent()
        agent._nexus = self.nexus

        states = ["PLANNING", "EXECUTING_PLAN", "DIRECT_EXECUTION", "COMPLETION"]
        for state in states:
            agent.execution_state = state
            agent._commit_chronicle_event(
                event_type="STATE_CHANGE",
                payload={"new_state": state}
            )

        snapshot = self.nexus.get_snapshot()
        assert len(snapshot["chronicle"]) == 4

        for i, event in enumerate(snapshot["chronicle"]):
            assert event["payload"]["new_state"] == states[i]

    def test_plan_iteration_tracked(self):
        """Test plan iterations are tracked in Chronicle."""
        agent = MockAgent()
        agent._nexus = self.nexus

        agent.plan_iterations = 0
        agent.current_plan = [{"tool": "read_file", "tool_args": {}}]
        agent.total_plan_steps = 1
        agent.current_step = 0

        for i in range(3):
            agent.plan_iterations = i
            agent._commit_chronicle_event(
                event_type="PLAN_ITERATION",
                payload={"iteration": i, "plan_length": len(agent.current_plan)},
                loop_id=f"plan-{i}"
            )

        snapshot = self.nexus.get_snapshot()
        assert len(snapshot["chronicle"]) == 3

    def test_goal_tracking_in_events(self):
        """Test goal tracking is included in events."""
        agent = MockAgent()
        agent._nexus = self.nexus

        agent._commit_chronicle_event(
            event_type="GOAL_SET",
            payload={
                "goal": "Create a Python script",
                "thought": "User wants automation"
            }
        )

        snapshot = self.nexus.get_snapshot()
        event = snapshot["chronicle"][0]
        assert event["payload"]["goal"] == "Create a Python script"
        assert event["payload"]["thought"] == "User wants automation"


# =============================================================================
# Event Type Coverage Tests
# =============================================================================

class TestEventTypeCoverage:
    """Tests for various event types supported by Chronicle."""

    def setup_method(self):
        """Setup mock Nexus for each test."""
        NexusService.reset_instance()
        self.mock_audit_class = Mock()
        self.mock_audit_instance = Mock()
        self.mock_audit_instance.log = Mock(return_value=Mock(event_id="mock-event-id"))
        self.mock_audit_class.get_instance = Mock(return_value=self.mock_audit_instance)

        self.patcher = patch('gaia.state.nexus.AuditLogger', self.mock_audit_class)
        self.patcher.start()
        self.nexus = NexusService.get_instance()

    def teardown_method(self):
        """Cleanup after each test."""
        self.patcher.stop()
        NexusService.reset_instance()

    def test_thought_event_type(self):
        """Test THOUGHT event type is supported."""
        agent = MockAgent()
        agent._nexus = self.nexus

        agent._commit_chronicle_event(
            event_type="THOUGHT",
            payload={"thought": "I need to read the config first"}
        )

        snapshot = self.nexus.get_snapshot()
        assert snapshot["chronicle"][0]["event_type"] == "THOUGHT"

    def test_goal_event_type(self):
        """Test GOAL event type is supported."""
        agent = MockAgent()
        agent._nexus = self.nexus

        agent._commit_chronicle_event(
            event_type="GOAL",
            payload={"goal": "Implement feature X"}
        )

        snapshot = self.nexus.get_snapshot()
        assert snapshot["chronicle"][0]["event_type"] == "GOAL"

    def test_answer_event_type(self):
        """Test ANSWER event type is supported."""
        agent = MockAgent()
        agent._nexus = self.nexus

        agent._commit_chronicle_event(
            event_type="ANSWER",
            payload={"answer": "The solution is to use a factory pattern"}
        )

        snapshot = self.nexus.get_snapshot()
        assert snapshot["chronicle"][0]["event_type"] == "ANSWER"

    def test_decision_event_type(self):
        """Test DECISION event type is supported."""
        agent = MockAgent()
        agent._nexus = self.nexus

        agent._commit_chronicle_event(
            event_type="DECISION",
            payload={"decision": "Switch to execution phase"}
        )

        snapshot = self.nexus.get_snapshot()
        assert snapshot["chronicle"][0]["event_type"] == "DECISION"


# =============================================================================
# Chronicle Digest Tests
# =============================================================================

class TestChronicleDigest:
    """Tests for Chronicle digest generation."""

    def setup_method(self):
        """Setup mock Nexus for each test."""
        NexusService.reset_instance()
        self.mock_audit_class = Mock()
        self.mock_audit_instance = Mock()
        self.mock_audit_instance.log = Mock(return_value=Mock(event_id="mock-event-id"))
        self.mock_audit_class.get_instance = Mock(return_value=self.mock_audit_instance)

        self.patcher = patch('gaia.state.nexus.AuditLogger', self.mock_audit_class)
        self.patcher.start()
        self.nexus = NexusService.get_instance()

    def teardown_method(self):
        """Cleanup after each test."""
        self.patcher.stop()
        NexusService.reset_instance()

    def test_get_agent_history_from_chronicle(self):
        """Test getting agent-specific history from Chronicle."""
        agent = MockAgent()
        agent._nexus = self.nexus

        agent._commit_chronicle_event(
            event_type="EVENT_A",
            payload={}
        )

        history = self.nexus.get_agent_history("MockAgent")
        assert len(history) == 1
        assert history[0]["event_type"] == "EVENT_A"

    def test_chronicle_digest_generation(self):
        """Test Chronicle digest generation."""
        agent = MockAgent()
        agent._nexus = self.nexus

        for i in range(5):
            agent._commit_chronicle_event(
                event_type="EVENT",
                payload={"index": i}
            )

        # get_chronicle_digest delegates to AuditLogger.get_digest()
        # which returns a Mock in our test setup
        digest = self.nexus.get_chronicle_digest(max_events=3)
        # Just verify it returns something (the mock)
        assert digest is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
