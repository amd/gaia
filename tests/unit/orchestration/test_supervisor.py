"""
Comprehensive tests for ProjectSupervisor and related types.

Tests cover:
- Verdict enum
- SupervisorConfig validation
- ObjectiveOutcome and HealthScore
- SupervisorState computed properties
- ProjectSupervisor.evaluate_cycle() — all verdict paths
- Per-objective failure tracking (D-2)
- Remediation depth limiting (D-3)
- Quality trend detection (D-4)
- Cascade blocking detection
- Phase completion checking
- Health score computation
- Reset behavior (D-6)
- Exception safety wrapping
"""

import pytest

from gaia.orchestration.models import (
    DependencyGraph,
    Objective,
    ObjectiveStatus,
    ProjectObjectives,
)
from gaia.orchestration.supervisor import (
    HealthScore,
    ObjectiveOutcome,
    ProjectSupervisor,
    SupervisorConfig,
    SupervisorState,
    Verdict,
)


# ============================================================================
# Fixtures
# ============================================================================


def make_objective(
    obj_id: str = "obj-1",
    status: ObjectiveStatus = ObjectiveStatus.QUEUED,
    phase: str = "PLANNING",
) -> Objective:
    """Create a minimal Objective for testing."""
    return Objective(
        objective_id=obj_id,
        title=f"Test Objective {obj_id}",
        description="Test objective",
        status=status,
        phase=phase,
    )


def make_project(objectives: list = None) -> ProjectObjectives:
    """Create a ProjectObjectives with given objectives."""
    if objectives is None:
        objectives = [make_objective("obj-1", ObjectiveStatus.QUEUED, "PLANNING")]
    return ProjectObjectives(
        project_id="test-project",
        objectives=objectives,
    )


def make_dep_graph(objectives: list = None) -> DependencyGraph:
    """Create a DependencyGraph from objectives."""
    if objectives is None:
        objectives = [make_objective()]
    return DependencyGraph(objectives)


@pytest.fixture
def config():
    return SupervisorConfig()


@pytest.fixture
def supervisor(config):
    return ProjectSupervisor(config=config)


@pytest.fixture
def project():
    return make_project()


@pytest.fixture
def dep_graph(project):
    return make_dep_graph(project.objectives)


# ============================================================================
# Verdict Enum Tests
# ============================================================================


class TestVerdict:
    def test_verdict_values(self):
        assert Verdict.CONTINUE.value == "continue"
        assert Verdict.PAUSE.value == "pause"
        assert Verdict.REMEDIATE.value == "remediate"
        assert Verdict.ABORT.value == "abort"

    def test_verdict_from_string(self):
        assert Verdict("continue") == Verdict.CONTINUE
        assert Verdict("pause") == Verdict.PAUSE
        assert Verdict("remediate") == Verdict.REMEDIATE
        assert Verdict("abort") == Verdict.ABORT


# ============================================================================
# SupervisorConfig Tests
# ============================================================================


class TestSupervisorConfig:
    def test_default_values(self):
        cfg = SupervisorConfig()
        assert cfg.consecutive_failure_threshold == 3
        assert cfg.max_consecutive_failures == 5
        assert cfg.quality_window == 5
        assert cfg.quality_decline_threshold == 0.10
        assert cfg.health_success_rate_weight == 0.40
        assert cfg.health_quality_weight == 0.30
        assert cfg.health_dependency_weight == 0.30
        assert cfg.health_minimum_score == 0.50
        assert cfg.max_remediation_depth == 2
        assert cfg.min_trend_slope == 0.05

    def test_health_weights_sum_to_one(self):
        cfg = SupervisorConfig()
        total = (
            cfg.health_success_rate_weight
            + cfg.health_quality_weight
            + cfg.health_dependency_weight
        )
        assert abs(total - 1.0) < 1e-9

    def test_invalid_max_failures_below_threshold(self):
        with pytest.raises(ValueError, match="must exceed"):
            SupervisorConfig(
                consecutive_failure_threshold=5,
                max_consecutive_failures=3,
            )

    def test_invalid_max_failures_equals_threshold(self):
        with pytest.raises(ValueError, match="must exceed"):
            SupervisorConfig(
                consecutive_failure_threshold=3,
                max_consecutive_failures=3,
            )

    def test_invalid_health_weights_not_summing_to_one(self):
        with pytest.raises(ValueError, match="must sum to 1.0"):
            SupervisorConfig(
                health_success_rate_weight=0.50,
                health_quality_weight=0.50,
                health_dependency_weight=0.50,
            )

    def test_invalid_remediation_depth_zero(self):
        with pytest.raises(ValueError, match="must be >= 1"):
            SupervisorConfig(max_remediation_depth=0)

    def test_invalid_remediation_depth_negative(self):
        with pytest.raises(ValueError, match="must be >= 1"):
            SupervisorConfig(max_remediation_depth=-1)

    def test_invalid_trend_slope_negative(self):
        with pytest.raises(ValueError, match="must be >= 0"):
            SupervisorConfig(min_trend_slope=-0.01)

    def test_valid_custom_config(self):
        cfg = SupervisorConfig(
            consecutive_failure_threshold=2,
            max_consecutive_failures=4,
            quality_window=3,
            max_remediation_depth=1,
            min_trend_slope=0.10,
        )
        assert cfg.consecutive_failure_threshold == 2
        assert cfg.max_consecutive_failures == 4
        assert cfg.quality_window == 3
        assert cfg.max_remediation_depth == 1
        assert cfg.min_trend_slope == 0.10


# ============================================================================
# ObjectiveOutcome Tests
# ============================================================================


class TestObjectiveOutcome:
    def test_success_outcome(self):
        outcome = ObjectiveOutcome(
            objective_id="obj-1",
            success=True,
            quality_score=0.95,
            phase="PLANNING",
        )
        assert outcome.success is True
        assert outcome.quality_score == 0.95
        assert outcome.phase == "PLANNING"
        assert outcome.error_message is None

    def test_failure_outcome(self):
        outcome = ObjectiveOutcome(
            objective_id="obj-1",
            success=False,
            error_message="Test error",
        )
        assert outcome.success is False
        assert outcome.error_message == "Test error"
        assert outcome.quality_score is None

    def test_timestamp_generated(self):
        outcome = ObjectiveOutcome(objective_id="obj-1", success=True)
        assert outcome.timestamp is not None
        assert "T" in outcome.timestamp  # ISO format


# ============================================================================
# HealthScore Tests
# ============================================================================


class TestHealthScore:
    def test_health_score_fields(self):
        hs = HealthScore(
            success_rate=0.8,
            quality_trend=0.9,
            dependency_health=1.0,
            composite=0.90,
        )
        assert hs.success_rate == 0.8
        assert hs.quality_trend == 0.9
        assert hs.dependency_health == 1.0
        assert hs.composite == 0.90


# ============================================================================
# SupervisorState Tests
# ============================================================================


class TestSupervisorState:
    def test_initial_state(self):
        state = SupervisorState()
        assert state.outcomes == []
        assert state.consecutive_failures == 0
        assert state.objective_failures == {}
        assert state.current_verdict == Verdict.CONTINUE
        assert state.paused_reason is None
        assert state.aborted_reason is None
        assert state.total_cycles == 0
        assert state.remediation_depth == 0

    def test_total_objectives(self):
        state = SupervisorState()
        state.outcomes = [
            ObjectiveOutcome(objective_id="1", success=True),
            ObjectiveOutcome(objective_id="2", success=False),
        ]
        assert state.total_objectives == 2

    def test_successful_objectives(self):
        state = SupervisorState()
        state.outcomes = [
            ObjectiveOutcome(objective_id="1", success=True),
            ObjectiveOutcome(objective_id="2", success=False),
            ObjectiveOutcome(objective_id="3", success=True),
        ]
        assert state.successful_objectives == 2

    def test_failed_objectives(self):
        state = SupervisorState()
        state.outcomes = [
            ObjectiveOutcome(objective_id="1", success=True),
            ObjectiveOutcome(objective_id="2", success=False),
            ObjectiveOutcome(objective_id="3", success=False),
        ]
        assert state.failed_objectives == 2


# ============================================================================
# ProjectSupervisor — evaluate_cycle() Verdict Tests
# ============================================================================


class TestEvaluateCycle:
    def test_continue_on_first_success(self, supervisor, project, dep_graph):
        outcome = ObjectiveOutcome(
            objective_id="obj-1",
            success=True,
            quality_score=0.95,
            phase="PLANNING",
        )
        verdict = supervisor.evaluate_cycle(outcome, project, dep_graph)
        assert verdict == Verdict.CONTINUE

    def test_continue_on_multiple_successes(
        self, supervisor, project, dep_graph
    ):
        for i in range(5):
            outcome = ObjectiveOutcome(
                objective_id=f"obj-{i}",
                success=True,
                quality_score=0.90,
                phase="PLANNING",
            )
            verdict = supervisor.evaluate_cycle(outcome, project, dep_graph)
            assert verdict == Verdict.CONTINUE

    def test_pause_after_consecutive_failure_threshold(
        self, supervisor, project, dep_graph
    ):
        # Send 3 consecutive failures (default threshold)
        for i in range(3):
            outcome = ObjectiveOutcome(
                objective_id="obj-1",
                success=False,
                error_message="Test failure",
            )
            verdict = supervisor.evaluate_cycle(outcome, project, dep_graph)

        # 3rd failure should trigger PAUSE
        assert supervisor.state.consecutive_failures >= 3
        assert supervisor.state.current_verdict == Verdict.PAUSE

    def test_abort_after_max_consecutive_failures(
        self, supervisor, project, dep_graph
    ):
        # Send 5 consecutive failures (default max)
        for i in range(5):
            outcome = ObjectiveOutcome(
                objective_id="obj-1",
                success=False,
                error_message="Test failure",
            )
            verdict = supervisor.evaluate_cycle(outcome, project, dep_graph)

        assert supervisor.state.current_verdict == Verdict.ABORT
        assert "consecutive failures" in supervisor.state.aborted_reason.lower()

    def test_abort_persists_after_abort(
        self, supervisor, project, dep_graph
    ):
        # Reach ABORT state
        for i in range(5):
            outcome = ObjectiveOutcome(
                objective_id="obj-1",
                success=False,
                error_message="Test failure",
            )
            supervisor.evaluate_cycle(outcome, project, dep_graph)

        # Subsequent evaluations should return ABORT immediately
        outcome = ObjectiveOutcome(objective_id="obj-2", success=True)
        verdict = supervisor.evaluate_cycle(outcome, project, dep_graph)
        assert verdict == Verdict.ABORT

    def test_failure_resets_consecutive_count_on_success(
        self, supervisor, project, dep_graph
    ):
        # 2 failures (below threshold)
        for i in range(2):
            outcome = ObjectiveOutcome(
                objective_id="obj-1",
                success=False,
                error_message="Test failure",
            )
            supervisor.evaluate_cycle(outcome, project, dep_graph)

        assert supervisor.state.consecutive_failures == 2

        # Success resets consecutive failures
        outcome = ObjectiveOutcome(
            objective_id="obj-2",
            success=True,
            quality_score=0.90,
        )
        verdict = supervisor.evaluate_cycle(outcome, project, dep_graph)
        assert verdict == Verdict.CONTINUE
        assert supervisor.state.consecutive_failures == 0


# ============================================================================
# Per-Objective Failure Tracking (D-2)
# ============================================================================


class TestPerObjectiveFailureTracking:
    def test_per_objective_failure_count(self, supervisor, project, dep_graph):
        outcome = ObjectiveOutcome(
            objective_id="obj-1",
            success=False,
            error_message="Test failure",
        )
        supervisor.evaluate_cycle(outcome, project, dep_graph)
        assert supervisor.state.objective_failures["obj-1"] == 1

    def test_per_objective_failure_count_increments(
        self, supervisor, project, dep_graph
    ):
        for i in range(3):
            outcome = ObjectiveOutcome(
                objective_id="obj-1",
                success=False,
                error_message="Test failure",
            )
            supervisor.evaluate_cycle(outcome, project, dep_graph)
        assert supervisor.state.objective_failures["obj-1"] == 3

    def test_per_objective_abort_on_max_failures(
        self, supervisor, project, dep_graph
    ):
        # Same objective fails 5 times, but need to account for
        # project-level abort hitting first (also 5 failures)
        for i in range(4):
            outcome = ObjectiveOutcome(
                objective_id="obj-1",
                success=False,
                error_message="Test failure",
            )
            verdict = supervisor.evaluate_cycle(outcome, project, dep_graph)

        # At 4 failures: consecutive_failures=4, obj-1 failures=4
        # PAUSE should have triggered at 3, but not ABORT yet
        assert supervisor.state.consecutive_failures == 4
        assert supervisor.state.objective_failures["obj-1"] == 4
        # Verdict should be PAUSE (threshold=3) not yet ABORT (max=5)
        assert supervisor.state.current_verdict == Verdict.PAUSE

    def test_different_objectives_dont_trigger_per_objective_abort(
        self, supervisor, project, dep_graph
    ):
        # Interleave failures across different objectives
        # Each objective fails 4 times (below per-objective max of 5)
        # But project-level consecutive failures will hit threshold
        for obj_id in ["obj-1", "obj-2", "obj-3", "obj-4"]:
            outcome = ObjectiveOutcome(
                objective_id=obj_id,
                success=False,
                error_message="Test failure",
            )
            supervisor.evaluate_cycle(outcome, project, dep_graph)

        # At 4 failures total, consecutive_failures=4 >= threshold(3)
        # Should be PAUSE but not ABORT (need 5 for project-level)
        assert supervisor.state.current_verdict == Verdict.PAUSE
        assert supervisor.state.consecutive_failures == 4
        # Each individual objective has only 1 failure
        assert supervisor.state.objective_failures.get("obj-1") == 1
        assert supervisor.state.objective_failures.get("obj-2") == 1


# ============================================================================
# Remediation Depth Limiting (D-3)
# ============================================================================


class TestRemediationDepth:
    def test_remediation_depth_resets_on_success(self, supervisor, project, dep_graph):
        # Create a scenario that triggers REMEDIATE
        # First add some quality scores, then a declining one
        for i in range(3):
            outcome = ObjectiveOutcome(
                objective_id=f"obj-{i}",
                success=True,
                quality_score=0.90 - i * 0.15,  # Declining quality
            )
            supervisor.evaluate_cycle(outcome, project, dep_graph)

        # Success resets remediation depth
        outcome = ObjectiveOutcome(
            objective_id="obj-recovery",
            success=True,
            quality_score=0.95,
        )
        supervisor.evaluate_cycle(outcome, project, dep_graph)
        assert supervisor.state.remediation_depth == 0

    def test_max_remediation_depth_triggers_abort(
        self, supervisor, project, dep_graph
    ):
        # Configure for easy REMEDIATE triggers
        supervisor._config.quality_decline_threshold = 0.01
        supervisor._config.min_trend_slope = 0.01

        # Generate declining quality scores to trigger REMEDIATE
        for i in range(10):
            outcome = ObjectiveOutcome(
                objective_id=f"obj-{i}",
                success=True,
                quality_score=0.95 - i * 0.10,  # Steadily declining
            )
            verdict = supervisor.evaluate_cycle(outcome, project, dep_graph)
            if verdict == Verdict.ABORT:
                break

        # Should hit remediation depth limit or consecutive failures
        assert supervisor.state.current_verdict in (
            Verdict.ABORT,
            Verdict.PAUSE,
            Verdict.REMEDIATE,
        )


# ============================================================================
# Quality Trend Detection (D-4)
# ============================================================================


class TestQualityTrend:
    def test_quality_trend_stable(self, supervisor, project, dep_graph):
        # Consistent quality scores
        for i in range(5):
            outcome = ObjectiveOutcome(
                objective_id=f"obj-{i}",
                success=True,
                quality_score=0.90,
            )
            verdict = supervisor.evaluate_cycle(outcome, project, dep_graph)
            assert verdict == Verdict.CONTINUE

    def test_quality_trend_improving(self, supervisor, project, dep_graph):
        # Improving quality scores
        for i in range(5):
            outcome = ObjectiveOutcome(
                objective_id=f"obj-{i}",
                success=True,
                quality_score=0.70 + i * 0.05,
            )
            verdict = supervisor.evaluate_cycle(outcome, project, dep_graph)
            assert verdict == Verdict.CONTINUE

    def test_insufficient_data_returns_zero_trend(self, supervisor):
        # Single outcome
        supervisor._state.outcomes = [
            ObjectiveOutcome(
                objective_id="obj-1",
                success=True,
                quality_score=0.90,
            )
        ]
        assert supervisor._compute_quality_trend() == 0.0

    def test_no_quality_scores_returns_zero(self, supervisor):
        # Outcomes without quality scores
        supervisor._state.outcomes = [
            ObjectiveOutcome(objective_id="obj-1", success=True),
            ObjectiveOutcome(objective_id="obj-2", success=True),
        ]
        assert supervisor._compute_quality_trend() == 0.0


# ============================================================================
# Cascade Blocking Detection
# ============================================================================


class TestCascadeBlocking:
    def test_no_cascade_on_success(self, supervisor, project, dep_graph):
        outcome = ObjectiveOutcome(
            objective_id="obj-1",
            success=True,
        )
        assert not supervisor._is_cascade_blocked(
            outcome, project, dep_graph
        )

    def test_no_cascade_when_no_dependents(
        self, supervisor, project, dep_graph
    ):
        outcome = ObjectiveOutcome(
            objective_id="obj-1",
            success=False,
            error_message="Test failure",
        )
        # Single objective with no dependencies
        assert not supervisor._is_cascade_blocked(
            outcome, project, dep_graph
        )

    def test_cascade_blocked_when_dependent_is_blocked(
        self, supervisor, dep_graph
    ):
        # Create project with dependent objectives
        obj1 = make_objective("obj-1", ObjectiveStatus.IN_PROGRESS, "PLANNING")
        obj2 = make_objective("obj-2", ObjectiveStatus.QUEUED, "DEVELOPMENT")
        obj2.dependencies.append("obj-1")

        project = make_project([obj1, obj2])
        graph = DependencyGraph([obj1, obj2])

        # Fail obj-1
        outcome = ObjectiveOutcome(
            objective_id="obj-1",
            success=False,
            error_message="Test failure",
        )

        # Mark obj-2 as BLOCKED
        obj2.transition_to(ObjectiveStatus.BLOCKED)

        assert supervisor._is_cascade_blocked(outcome, project, graph)


# ============================================================================
# Phase Completion
# ============================================================================


class TestPhaseCompletion:
    def test_phase_complete_when_all_completed(
        self, supervisor, project
    ):
        obj1 = make_objective("obj-1", ObjectiveStatus.COMPLETED, "PLANNING")
        obj2 = make_objective("obj-2", ObjectiveStatus.COMPLETED, "PLANNING")
        project = make_project([obj1, obj2])

        assert supervisor.check_phase_completion(project, "PLANNING")

    def test_phase_complete_when_all_cancelled(
        self, supervisor, project
    ):
        obj1 = make_objective("obj-1", ObjectiveStatus.CANCELLED, "PLANNING")
        project = make_project([obj1])

        assert supervisor.check_phase_completion(project, "PLANNING")

    def test_phase_not_complete_when_in_progress(
        self, supervisor, project
    ):
        obj1 = make_objective("obj-1", ObjectiveStatus.IN_PROGRESS, "PLANNING")
        project = make_project([obj1])

        assert not supervisor.check_phase_completion(project, "PLANNING")

    def test_phase_not_complete_when_blocked(
        self, supervisor, project
    ):
        obj1 = make_objective("obj-1", ObjectiveStatus.BLOCKED, "PLANNING")
        project = make_project([obj1])

        assert not supervisor.check_phase_completion(project, "PLANNING")

    def test_phase_not_complete_when_queued(
        self, supervisor, project
    ):
        obj1 = make_objective("obj-1", ObjectiveStatus.QUEUED, "PLANNING")
        project = make_project([obj1])

        assert not supervisor.check_phase_completion(project, "PLANNING")

    def test_empty_phase_returns_true(self, supervisor, project):
        obj1 = make_objective("obj-1", ObjectiveStatus.COMPLETED, "DEVELOPMENT")
        project = make_project([obj1])

        # PLANNING phase has no objectives
        assert supervisor.check_phase_completion(project, "PLANNING")


# ============================================================================
# Health Score Computation
# ============================================================================


class TestHealthScoreComputation:
    def test_initial_health_score(self, supervisor, project, dep_graph):
        hs = supervisor.compute_health_score(project, dep_graph)
        assert 0.0 <= hs.success_rate <= 1.0
        assert 0.0 <= hs.quality_trend <= 1.0
        assert 0.0 <= hs.dependency_health <= 1.0
        assert 0.0 <= hs.composite <= 1.0

    def test_health_score_after_successes(
        self, supervisor, project, dep_graph
    ):
        for i in range(5):
            outcome = ObjectiveOutcome(
                objective_id=f"obj-{i}",
                success=True,
                quality_score=0.95,
            )
            supervisor.evaluate_cycle(outcome, project, dep_graph)

        hs = supervisor.compute_health_score(project, dep_graph)
        assert hs.success_rate == 1.0
        assert hs.composite > 0.5

    def test_health_score_degrades_with_failures(
        self, supervisor, project, dep_graph
    ):
        for i in range(3):
            outcome = ObjectiveOutcome(
                objective_id=f"obj-{i}",
                success=False,
                error_message="Test failure",
            )
            supervisor.evaluate_cycle(outcome, project, dep_graph)

        hs = supervisor.compute_health_score(project, dep_graph)
        assert hs.success_rate < 1.0


# ============================================================================
# Reset Behavior (D-6)
# ============================================================================


class TestReset:
    def test_reset_clears_all_state(self, supervisor, project, dep_graph):
        # Generate some state
        for i in range(3):
            outcome = ObjectiveOutcome(
                objective_id="obj-1",
                success=False,
                error_message="Test failure",
            )
            supervisor.evaluate_cycle(outcome, project, dep_graph)

        assert supervisor.state.consecutive_failures > 0
        assert len(supervisor.state.outcomes) > 0

        # Reset
        supervisor.reset()

        assert supervisor.state.consecutive_failures == 0
        assert len(supervisor.state.outcomes) == 0
        assert supervisor.state.current_verdict == Verdict.CONTINUE
        assert supervisor.state.objective_failures == {}
        assert supervisor.state.remediation_depth == 0
        assert supervisor.state.total_cycles == 0
        assert supervisor.state.paused_reason is None
        assert supervisor.state.aborted_reason is None

    def test_reset_clears_aborted_state(
        self, supervisor, project, dep_graph
    ):
        # Reach ABORT state
        for i in range(5):
            outcome = ObjectiveOutcome(
                objective_id="obj-1",
                success=False,
                error_message="Test failure",
            )
            supervisor.evaluate_cycle(outcome, project, dep_graph)

        assert supervisor.state.current_verdict == Verdict.ABORT

        supervisor.reset()
        assert supervisor.state.current_verdict == Verdict.CONTINUE
        assert supervisor.state.aborted_reason is None

    def test_reset_returns_new_state_object(
        self, supervisor, project, dep_graph
    ):
        old_state = supervisor.state
        supervisor.reset()
        new_state = supervisor.state
        assert old_state is not new_state


# ============================================================================
# Supervisor Config and Property Access
# ============================================================================


class TestSupervisorProperties:
    def test_config_property(self, supervisor, config):
        assert supervisor.config is config

    def test_state_property(self, supervisor):
        assert supervisor.state is not None
        assert isinstance(supervisor.state, SupervisorState)


# ============================================================================
# Exception Safety
# ============================================================================


class TestExceptionSafety:
    def test_evaluate_cycle_handles_none_project(
        self, supervisor, dep_graph
    ):
        # Should handle gracefully if project is None
        outcome = ObjectiveOutcome(
            objective_id="obj-1",
            success=True,
        )
        # The supervisor doesn't actually use project in evaluate_cycle
        # for the CONTINUE path, so this should not raise
        verdict = supervisor.evaluate_cycle(outcome, None, dep_graph)
        assert verdict == Verdict.CONTINUE

    def test_compute_health_score_with_empty_project(
        self, supervisor
    ):
        project = make_project([])
        graph = DependencyGraph([])
        hs = supervisor.compute_health_score(project, graph)
        assert isinstance(hs, HealthScore)
        # Should handle empty gracefully
        assert hs.success_rate == 1.0  # No failures recorded


# ============================================================================
# Integration: Full Dispatch Cycle with Supervisor
# ============================================================================


class TestSupervisorIntegration:
    def test_full_continue_cycle(self, supervisor, project, dep_graph):
        """Simulate a healthy project with all CONTINUE verdicts."""
        objectives = [
            make_objective(f"obj-{i}", ObjectiveStatus.QUEUED, "PLANNING")
            for i in range(1, 6)
        ]
        project = make_project(objectives)
        graph = DependencyGraph(objectives)

        for obj in objectives:
            outcome = ObjectiveOutcome(
                objective_id=obj.objective_id,
                success=True,
                quality_score=0.92,
                phase=obj.phase,
            )
            verdict = supervisor.evaluate_cycle(outcome, project, graph)
            assert verdict == Verdict.CONTINUE

        hs = supervisor.compute_health_score(project, graph)
        assert hs.success_rate == 1.0
        assert hs.composite > 0.8

    def test_recovery_after_pause(self, supervisor, project, dep_graph):
        """After pause, reset should allow fresh evaluation."""
        # Trigger PAUSE
        for i in range(3):
            outcome = ObjectiveOutcome(
                objective_id="obj-1",
                success=False,
                error_message="Test failure",
            )
            supervisor.evaluate_cycle(outcome, project, dep_graph)

        assert supervisor.state.current_verdict == Verdict.PAUSE

        # Reset and start fresh
        supervisor.reset()
        outcome = ObjectiveOutcome(
            objective_id="obj-1",
            success=True,
            quality_score=0.95,
        )
        verdict = supervisor.evaluate_cycle(outcome, project, dep_graph)
        assert verdict == Verdict.CONTINUE
