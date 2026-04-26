"""
Tests for GAIA Pipeline State Machine.

Tests cover:
- State transitions
- Invalid transitions
- Timestamp tracking
- Chronicle entries
- Thread safety
- Context validation and boundary conditions
- Snapshot serialization round-trip
- FSM helper methods
- Thread safety (RLock concurrent access)
"""

from datetime import datetime, timezone
import threading
import time

import pytest

from gaia.exceptions import InvalidStateTransition
from gaia.pipeline.state import (
    PipelineContext,
    PipelineSnapshot,
    PipelineState,
    PipelineStateMachine,
)


class TestPipelineState:
    """Tests for PipelineState enum."""

    def test_is_terminal(self):
        """Test terminal state detection."""
        assert PipelineState.COMPLETED.is_terminal()
        assert PipelineState.FAILED.is_terminal()
        assert PipelineState.CANCELLED.is_terminal()
        assert not PipelineState.RUNNING.is_terminal()
        assert not PipelineState.READY.is_terminal()

    def test_is_active(self):
        """Test active state detection."""
        assert PipelineState.INITIALIZING.is_active()
        assert PipelineState.READY.is_active()
        assert PipelineState.RUNNING.is_active()
        assert PipelineState.PAUSED.is_active()
        assert not PipelineState.COMPLETED.is_active()
        assert not PipelineState.FAILED.is_active()
        assert not PipelineState.CANCELLED.is_active()


class TestPipelineContext:
    """Tests for PipelineContext dataclass."""

    def test_create_context(self):
        """Test context creation."""
        context = PipelineContext(
            pipeline_id="test-001",
            user_goal="Test goal",
        )
        assert context.pipeline_id == "test-001"
        assert context.user_goal == "Test goal"
        assert context.quality_threshold == 0.90  # Default
        assert context.max_iterations == 10  # Default

    def test_invalid_threshold(self):
        """Test invalid quality threshold raises error."""
        with pytest.raises(ValueError):
            PipelineContext(
                pipeline_id="test-001",
                user_goal="Test",
                quality_threshold=1.5,
            )

    def test_invalid_max_iterations(self):
        """Test invalid max iterations raises error."""
        with pytest.raises(ValueError):
            PipelineContext(
                pipeline_id="test-001",
                user_goal="Test",
                max_iterations=-1,
            )

    def test_with_updates(self):
        """Test context updates create new instance."""
        context = PipelineContext(
            pipeline_id="test-001",
            user_goal="Test",
        )
        updated = context.with_updates(quality_threshold=0.95)
        assert context.quality_threshold == 0.90
        assert updated.quality_threshold == 0.95
        assert updated.pipeline_id == context.pipeline_id


class TestPipelineSnapshot:
    """Tests for PipelineSnapshot dataclass."""

    def test_create_snapshot(self):
        """Test snapshot creation."""
        snapshot = PipelineSnapshot(state=PipelineState.INITIALIZING)
        assert snapshot.state == PipelineState.INITIALIZING
        assert snapshot.current_phase is None
        assert snapshot.iteration_count == 0

    def test_to_dict(self):
        """Test snapshot serialization."""
        snapshot = PipelineSnapshot(
            state=PipelineState.RUNNING,
            current_phase="DEVELOPMENT",
            iteration_count=3,
            quality_score=0.85,
        )
        data = snapshot.to_dict()
        assert data["state"] == "RUNNING"
        assert data["current_phase"] == "DEVELOPMENT"
        assert data["iteration_count"] == 3
        assert data["quality_score"] == 0.85

    def test_elapsed_time(self):
        """Test elapsed time calculation."""
        snapshot = PipelineSnapshot(state=PipelineState.INITIALIZING)
        assert snapshot.elapsed_time() is None  # Not started

        snapshot.started_at = datetime.now(timezone.utc)
        # Small delay to ensure time difference
        time.sleep(0.01)
        elapsed = snapshot.elapsed_time()
        assert elapsed is not None
        assert elapsed >= 0.01


class TestPipelineStateMachine:
    """Tests for PipelineStateMachine class."""

    @pytest.fixture
    def context(self) -> PipelineContext:
        """Create test context."""
        return PipelineContext(
            pipeline_id="test-pipeline-001",
            user_goal="Implement feature X",
        )

    @pytest.fixture
    def state_machine(self, context: PipelineContext) -> PipelineStateMachine:
        """Create test state machine."""
        return PipelineStateMachine(context)

    def test_initial_state(self, state_machine: PipelineStateMachine):
        """Test pipeline starts in INITIALIZING state."""
        assert state_machine.current_state == PipelineState.INITIALIZING

    def test_valid_transition_initializing_to_ready(
        self, state_machine: PipelineStateMachine
    ):
        """Test valid transition from INITIALIZING to READY."""
        result = state_machine.transition(PipelineState.READY, "Config validated")
        assert result is True
        assert state_machine.current_state == PipelineState.READY

    def test_invalid_transition_initializing_to_running(
        self, state_machine: PipelineStateMachine
    ):
        """Test invalid transition from INITIALIZING to RUNNING."""
        with pytest.raises(InvalidStateTransition):
            state_machine.transition(PipelineState.RUNNING, "Skip READY")

    def test_transition_log(self, state_machine: PipelineStateMachine):
        """Test state transitions are logged."""
        state_machine.transition(PipelineState.READY, "Config validated")
        state_machine.transition(PipelineState.RUNNING, "Start execution")

        log = state_machine.transition_log
        assert len(log) == 2
        assert log[0].to_state == PipelineState.READY
        assert log[1].to_state == PipelineState.RUNNING

    def test_terminal_state_completed(self, state_machine: PipelineStateMachine):
        """Test COMPLETED is terminal state."""
        state_machine.transition(PipelineState.READY, "Config validated")
        state_machine.transition(PipelineState.RUNNING, "Start execution")
        state_machine.transition(PipelineState.COMPLETED, "Pipeline finished")

        # No transitions from COMPLETED
        with pytest.raises(InvalidStateTransition):
            state_machine.transition(PipelineState.RUNNING, "Resume")

    def test_terminal_state_failed(self, state_machine: PipelineStateMachine):
        """Test FAILED is terminal state."""
        state_machine.transition(PipelineState.READY, "Config validated")
        state_machine.transition(PipelineState.RUNNING, "Start execution")
        state_machine.transition(PipelineState.FAILED, "Critical error occurred")

        # No transitions from FAILED
        with pytest.raises(InvalidStateTransition):
            state_machine.transition(PipelineState.READY, "Retry")

    def test_transition_to_paused(self, state_machine: PipelineStateMachine):
        """Test transition to PAUSED state."""
        state_machine.transition(PipelineState.READY, "Config validated")
        state_machine.transition(PipelineState.RUNNING, "Start execution")
        state_machine.transition(PipelineState.PAUSED, "Waiting for input")

        assert state_machine.current_state == PipelineState.PAUSED

    def test_resume_from_paused(self, state_machine: PipelineStateMachine):
        """Test resuming from PAUSED state."""
        state_machine.transition(PipelineState.READY, "Config validated")
        state_machine.transition(PipelineState.RUNNING, "Start execution")
        state_machine.transition(PipelineState.PAUSED, "Waiting for input")
        state_machine.transition(PipelineState.RUNNING, "Resume execution")

        assert state_machine.current_state == PipelineState.RUNNING

    def test_cancel_from_ready(self, state_machine: PipelineStateMachine):
        """Test cancellation from READY state."""
        state_machine.transition(PipelineState.READY, "Config validated")
        state_machine.transition(PipelineState.CANCELLED, "User cancelled")

        assert state_machine.current_state == PipelineState.CANCELLED

    def test_cancel_from_paused(self, state_machine: PipelineStateMachine):
        """Test cancellation from PAUSED state."""
        state_machine.transition(PipelineState.READY, "Config validated")
        state_machine.transition(PipelineState.RUNNING, "Start execution")
        state_machine.transition(PipelineState.PAUSED, "Waiting for input")
        state_machine.transition(PipelineState.CANCELLED, "User cancelled")

        assert state_machine.current_state == PipelineState.CANCELLED

    def test_timestamps_updated(self, state_machine: PipelineStateMachine):
        """Test timestamps are updated on transitions."""
        state_machine.transition(PipelineState.READY, "Config validated")
        assert state_machine.snapshot.started_at is None

        state_machine.transition(PipelineState.RUNNING, "Start execution")
        assert state_machine.snapshot.started_at is not None

        state_machine.transition(PipelineState.COMPLETED, "Pipeline finished")
        assert state_machine.snapshot.completed_at is not None

    def test_is_terminal(self, state_machine: PipelineStateMachine):
        """Test is_terminal method."""
        assert not state_machine.is_terminal()

        state_machine.transition(PipelineState.READY, "Config validated")
        assert not state_machine.is_terminal()

        state_machine.transition(PipelineState.RUNNING, "Start execution")
        assert not state_machine.is_terminal()

        state_machine.transition(PipelineState.COMPLETED, "Finished")
        assert state_machine.is_terminal()

    def test_is_active(self, state_machine: PipelineStateMachine):
        """Test is_active method."""
        assert state_machine.is_active()

        state_machine.transition(PipelineState.READY, "Config validated")
        assert state_machine.is_active()

        state_machine.transition(PipelineState.RUNNING, "Start execution")
        assert state_machine.is_active()

        state_machine.transition(PipelineState.COMPLETED, "Finished")
        assert not state_machine.is_active()

    def test_set_phase(self, state_machine: PipelineStateMachine):
        """Test setting current phase."""
        state_machine.set_phase("DEVELOPMENT")
        assert state_machine.snapshot.current_phase == "DEVELOPMENT"

    def test_set_quality_score(self, state_machine: PipelineStateMachine):
        """Test setting quality score."""
        state_machine.set_quality_score(0.85)
        assert state_machine.snapshot.quality_score == 0.85

    def test_add_artifact(self, state_machine: PipelineStateMachine):
        """Test adding artifacts."""
        state_machine.add_artifact("planning", {"plan": "data"})
        assert "planning" in state_machine.snapshot.artifacts
        assert state_machine.snapshot.artifacts["planning"] == {"plan": "data"}

    def test_add_defect(self, state_machine: PipelineStateMachine):
        """Test adding defects."""
        defect = {"description": "Bug found", "severity": "high"}
        state_machine.add_defect(defect)
        assert len(state_machine.snapshot.defects) == 1
        assert state_machine.snapshot.defects[0] == defect

    def test_chronicle_entries(self, state_machine: PipelineStateMachine):
        """Test chronicle entries are created on transitions."""
        state_machine.transition(PipelineState.READY, "Config validated")

        chronicle = state_machine.chronicle
        assert len(chronicle) == 1
        assert chronicle[0]["event"] == "STATE_TRANSITION"
        assert chronicle[0]["to_state"] == "READY"

    def test_get_state_info(self, state_machine: PipelineStateMachine):
        """Test getting comprehensive state info."""
        state_machine.set_phase("DEVELOPMENT")
        state_machine.set_quality_score(0.90)
        state_machine.add_artifact("test", {"key": "value"})

        info = state_machine.get_state_info()
        assert info["state"] == "INITIALIZING"
        assert info["phase"] == "DEVELOPMENT"
        assert info["quality_score"] == 0.90
        assert info["artifacts_count"] == 1

    def test_valid_transition_check(self, state_machine: PipelineStateMachine):
        """Test is_valid_transition method."""
        assert state_machine.is_valid_transition(PipelineState.READY)
        assert not state_machine.is_valid_transition(PipelineState.RUNNING)
        assert not state_machine.is_valid_transition(PipelineState.COMPLETED)


class TestPipelineContextValidation:
    """Boundary and validation edge cases for PipelineContext."""

    def test_context_empty_pipeline_id_rejected(self):
        """Empty string pipeline_id should be rejected."""
        with pytest.raises(ValueError):
            PipelineContext(pipeline_id="", user_goal="Test goal")

    def test_context_empty_user_goal_rejected(self):
        """Empty string user_goal should be rejected."""
        with pytest.raises(ValueError):
            PipelineContext(pipeline_id="test-001", user_goal="")

    def test_context_boundary_quality_threshold_zero(self):
        """quality_threshold=0.0 is valid boundary."""
        ctx = PipelineContext(
            pipeline_id="test", user_goal="Test", quality_threshold=0.0
        )
        assert ctx.quality_threshold == 0.0

    def test_context_boundary_quality_threshold_one(self):
        """quality_threshold=1.0 is valid boundary."""
        ctx = PipelineContext(
            pipeline_id="test", user_goal="Test", quality_threshold=1.0
        )
        assert ctx.quality_threshold == 1.0

    def test_context_boundary_concurrent_loops_zero_rejected(self):
        """concurrent_loops=0 should be rejected (must be >= 1)."""
        with pytest.raises(ValueError):
            PipelineContext(
                pipeline_id="test", user_goal="Test", concurrent_loops=0
            )


class TestPipelineSnapshotRoundTrip:
    """Serialization round-trip and edge cases for PipelineSnapshot."""

    def test_snapshot_round_trip_from_dict(self):
        """Full round-trip: to_dict -> from_dict preserves all fields."""
        original = PipelineSnapshot(
            state=PipelineState.RUNNING,
            current_phase="DEVELOPMENT",
            current_loop=2,
            iteration_count=5,
            quality_score=0.85,
            error_message="Test error",
            artifacts={"key": {"nested": "value"}},
            chronicle=[{"event": "TEST", "timestamp": "2024-01-01T00:00:00+00:00"}],
            started_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            completed_at=datetime(2024, 1, 1, 12, 5, 0, tzinfo=timezone.utc),
            defects=[{"description": "Bug", "severity": "high"}],
            context_injected={"key": "value"},
            provenance={"artifact1": {"source": "agent-1"}},
        )
        data = original.to_dict()
        restored = PipelineSnapshot.from_dict(data)

        assert restored.state == PipelineState.RUNNING
        assert restored.current_phase == "DEVELOPMENT"
        assert restored.current_loop == 2
        assert restored.iteration_count == 5
        assert restored.quality_score == 0.85
        assert restored.started_at == original.started_at
        assert restored.completed_at == original.completed_at
        assert restored.defects == original.defects
        assert restored.provenance == original.provenance
        assert restored.context_injected == original.context_injected
        assert restored.error_message == "Test error"

    def test_snapshot_from_dict_minimal(self):
        """from_dict with minimal required fields uses defaults."""
        data = {"state": "INITIALIZING"}
        snapshot = PipelineSnapshot.from_dict(data)
        assert snapshot.state == PipelineState.INITIALIZING
        assert snapshot.current_phase is None
        assert snapshot.iteration_count == 0
        assert snapshot.artifacts == {}
        assert snapshot.defects == []
        assert snapshot.provenance == {}

    def test_snapshot_elapsed_time_with_completed_at(self):
        """elapsed_time uses completed_at when set, not datetime.now()."""
        start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, 12, 5, 30, tzinfo=timezone.utc)
        snapshot = PipelineSnapshot(
            state=PipelineState.COMPLETED,
            started_at=start,
            completed_at=end,
        )
        assert snapshot.elapsed_time() == 330.0  # 5m 30s


class TestPipelineStateMachineMethods:
    """Tests for FSM methods not covered by transition tests."""

    @pytest.fixture
    def context(self) -> PipelineContext:
        return PipelineContext(
            pipeline_id="test-pipeline-001",
            user_goal="Implement feature X",
        )

    @pytest.fixture
    def state_machine(self, context: PipelineContext) -> PipelineStateMachine:
        return PipelineStateMachine(context)

    def test_fsm_increment_iteration(self, state_machine: PipelineStateMachine):
        """increment_iteration returns sequential counts."""
        assert state_machine.increment_iteration() == 1
        assert state_machine.increment_iteration() == 2
        assert state_machine.increment_iteration() == 3
        assert state_machine.snapshot.iteration_count == 3

    def test_fsm_add_defects_batch(self, state_machine: PipelineStateMachine):
        """add_defects adds multiple defects at once."""
        defects = [
            {"description": "Bug 1", "severity": "high"},
            {"description": "Bug 2", "severity": "medium"},
        ]
        state_machine.add_defects(defects)
        assert len(state_machine.snapshot.defects) == 2
        assert state_machine.snapshot.defects[0]["description"] == "Bug 1"
        assert state_machine.snapshot.defects[1]["description"] == "Bug 2"

    def test_fsm_inject_context(self, state_machine: PipelineStateMachine):
        """inject_context merges dicts across multiple calls."""
        state_machine.inject_context({"key1": "value1"})
        state_machine.inject_context({"key2": "value2"})
        assert state_machine.snapshot.context_injected["key1"] == "value1"
        assert state_machine.snapshot.context_injected["key2"] == "value2"
        assert len(state_machine.snapshot.context_injected) == 2

    def test_fsm_set_error(self, state_machine: PipelineStateMachine):
        """set_error stores error message."""
        state_machine.set_error("Connection timeout after 30s")
        assert state_machine.snapshot.error_message == "Connection timeout after 30s"

    def test_fsm_reset_to_ready(self, state_machine: PipelineStateMachine):
        """reset_to_ready clears state and returns to READY."""
        # Build up some state first
        state_machine.transition(PipelineState.READY, "Config validated")
        state_machine.transition(PipelineState.RUNNING, "Start execution")
        state_machine.set_phase("DEVELOPMENT")
        state_machine.set_quality_score(0.85)
        state_machine.add_artifact("plan", {"data": "value"})
        state_machine.add_defect({"description": "bug"})

        # Reset
        state_machine.reset_to_ready()

        assert state_machine.current_state == PipelineState.READY
        assert state_machine.snapshot.iteration_count == 0
        assert state_machine.snapshot.artifacts == {}
        assert state_machine.snapshot.quality_score is None
        assert len(state_machine.transition_log) == 1
        assert state_machine.transition_log[0].from_state == PipelineState.INITIALIZING
        assert state_machine.transition_log[0].to_state == PipelineState.READY

    def test_fsm_set_loop(self, state_machine: PipelineStateMachine):
        """set_loop stores current loop number."""
        state_machine.set_loop(3)
        assert state_machine.snapshot.current_loop == 3

    def test_fsm_add_chronicle_entry(self, state_machine: PipelineStateMachine):
        """add_chronicle_entry creates a structured log entry."""
        state_machine.set_phase("DEVELOPMENT")
        state_machine.add_chronicle_entry(
            "ARTIFACT_PRODUCED", data={"artifact": "plan", "size_kb": 12}
        )
        entry = state_machine.chronicle[-1]
        assert entry["event"] == "ARTIFACT_PRODUCED"
        assert entry["pipeline_id"] == "test-pipeline-001"
        assert entry["phase"] == "DEVELOPMENT"
        assert entry["data"]["artifact"] == "plan"
        assert "timestamp" in entry

    def test_fsm_add_artifact_with_provenance(self, state_machine: PipelineStateMachine):
        """add_artifact with source tracks provenance metadata."""
        state_machine.add_artifact(
            "design_doc",
            {"sections": ["intro", "implementation"]},
            source="planning-agent",
            source_metadata={"loop_id": 1, "phase": "PLANNING"},
        )
        assert "design_doc" in state_machine.snapshot.artifacts
        assert state_machine.snapshot.provenance["design_doc"]["source"] == "planning-agent"
        assert state_machine.snapshot.provenance["design_doc"]["loop_id"] == 1
        assert "timestamp" in state_machine.snapshot.provenance["design_doc"]

    def test_valid_transitions_exhaustiveness(self):
        """VALID_TRANSITIONS covers all 7 states with valid targets only."""
        vt = PipelineStateMachine.VALID_TRANSITIONS
        # All 7 states must have entries
        assert set(vt.keys()) == set(PipelineState)
        # All transition targets must be valid PipelineState values
        for source, targets in vt.items():
            for target in targets:
                assert isinstance(target, PipelineState), (
                    f"Invalid target {target} for {source}"
                )


class TestPipelineStateMachineThreadSafety:
    """Thread safety tests for PipelineStateMachine RLock."""

    @pytest.fixture
    def running_machine(self) -> PipelineStateMachine:
        """Create a machine in RUNNING state for concurrent testing."""
        ctx = PipelineContext(pipeline_id="thread-test", user_goal="Test")
        machine = PipelineStateMachine(ctx)
        machine.transition(PipelineState.READY, "Ready")
        machine.transition(PipelineState.RUNNING, "Start")
        return machine

    def test_concurrent_increment_iteration(self, running_machine: PipelineStateMachine):
        """RLock protects iteration counter from race conditions.

        10 threads x 100 increments = 1000 expected.
        """
        num_threads = 10
        increments_per_thread = 100

        def worker(machine: PipelineStateMachine, count: int):
            for _ in range(count):
                machine.increment_iteration()
                machine.add_defect({"description": "concurrent-defect"})

        threads = [
            threading.Thread(target=worker, args=(running_machine, increments_per_thread))
            for _ in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert running_machine.snapshot.iteration_count == num_threads * increments_per_thread
        assert len(running_machine.snapshot.defects) == num_threads * increments_per_thread

    def test_concurrent_mixed_operations(self, running_machine: PipelineStateMachine):
        """Mixed operations under concurrent access do not deadlock or corrupt."""
        exceptions: list[Exception] = []

        def quality_worker():
            try:
                for i in range(50):
                    running_machine.set_quality_score(0.5 + i * 0.01)
            except Exception as e:
                exceptions.append(e)

        def artifact_worker():
            try:
                for i in range(50):
                    running_machine.add_artifact(f"artifact_{i}", {"data": i})
            except Exception as e:
                exceptions.append(e)

        def chronicle_worker():
            try:
                for i in range(50):
                    running_machine.add_chronicle_entry("EVENT", data={"iter": i})
            except Exception as e:
                exceptions.append(e)

        threads = [
            threading.Thread(target=quality_worker),
            threading.Thread(target=artifact_worker),
            threading.Thread(target=chronicle_worker),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(exceptions) == 0, f"Exceptions occurred: {exceptions}"
        assert len(running_machine.snapshot.artifacts) == 50
        assert len(running_machine.chronicle) >= 50  # includes transitions + entries
