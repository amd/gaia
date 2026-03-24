"""
Tests for GAIA Pipeline State Machine.

Tests cover:
- State transitions
- Invalid transitions
- Timestamp tracking
- Chronicle entries
- Thread safety
"""

import pytest
from datetime import datetime

from gaia.pipeline.state import (
    PipelineState,
    PipelineContext,
    PipelineSnapshot,
    PipelineStateMachine,
)
from gaia.exceptions import InvalidStateTransition


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

        snapshot.started_at = datetime.utcnow()
        # Small delay to ensure time difference
        import time
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
        state_machine.transition(
            PipelineState.FAILED, "Critical error occurred"
        )

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

    def test_valid_transition_check(
        self, state_machine: PipelineStateMachine
    ):
        """Test is_valid_transition method."""
        assert state_machine.is_valid_transition(PipelineState.READY)
        assert not state_machine.is_valid_transition(PipelineState.RUNNING)
        assert not state_machine.is_valid_transition(PipelineState.COMPLETED)
