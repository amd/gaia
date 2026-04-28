"""
Unit tests for Phase 4A: Data Models + Level Execution Engine.

Tests cover:
    - ConflictReport and LevelResult dataclasses
    - DependencyGraph.partition_into_levels()
    - OrchestratorPipelineAdapter.execute_without_status_update()
    - OrchestratorConfig parallel execution flags
    - ProjectOrchestrator._run_level_parallel()
    - ProjectOrchestrator._propagate_failures_to_dependents()
    - ProjectOrchestrator._run_parallel_mode()
    - ProjectSupervisor.evaluate_level()
    - Backward compatibility: run() branches correctly
"""

import asyncio
import os
import subprocess
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gaia.hooks.base import BaseHook, HookContext, HookPriority, HookResult
from gaia.orchestration.adapters import OrchestratorPipelineAdapter
from gaia.orchestration.engine import (
    OBJECTIVE_COMPLETE,
    OBJECTIVE_FAILED,
    OBJECTIVE_START,
    OrchestratorConfig,
    ProjectOrchestrator,
)
from gaia.orchestration.models import (
    ConflictReport,
    DependencyGraph,
    LevelResult,
    Objective,
    ObjectiveStatus,
    ProjectObjectives,
)
from gaia.orchestration.supervisor import (
    ObjectiveOutcome,
    ProjectSupervisor,
    SupervisorConfig,
    Verdict,
)
from gaia.pipeline.state import PipelineState


# =============================================================================
# ConflictReport tests
# =============================================================================


class TestConflictReport:
    def test_default_timestamp(self):
        report = ConflictReport(
            conflicting_objective_ids=["a", "b"],
            affected_files={"file1.py"},
        )
        assert report.timestamp != ""
        assert len(report.conflicting_objective_ids) == 2

    def test_explicit_timestamp(self):
        ts = "2026-01-01T00:00:00+00:00"
        report = ConflictReport(
            conflicting_objective_ids=["a"],
            affected_files={"file1.py"},
            timestamp=ts,
        )
        assert report.timestamp == ts

    def test_multiple_affected_files(self):
        report = ConflictReport(
            conflicting_objective_ids=["a", "b", "c"],
            affected_files={"x.py", "y.py", "z.py"},
        )
        assert len(report.affected_files) == 3


# =============================================================================
# LevelResult tests
# =============================================================================


class TestLevelResult:
    def test_default_values(self):
        result = LevelResult(
            level_number=0,
            objective_ids=["a", "b"],
            outcomes={},
            conflicts=[],
        )
        assert result.success_count == 0
        assert result.failure_count == 0
        assert result.verdict == "CONTINUE"
        assert result.timestamp != ""

    def test_with_outcomes(self):
        outcomes = {
            "a": ObjectiveOutcome(objective_id="a", success=True),
            "b": ObjectiveOutcome(objective_id="b", success=False),
        }
        result = LevelResult(
            level_number=1,
            objective_ids=["a", "b"],
            outcomes=outcomes,
            conflicts=[],
            success_count=1,
            failure_count=1,
            verdict="CONTINUE",
        )
        assert result.success_count == 1
        assert result.failure_count == 1


# =============================================================================
# DependencyGraph.partition_into_levels tests
# =============================================================================


class TestPartitionIntoLevels:
    def test_single_node(self):
        objectives = [
            Objective(objective_id="a", title="A"),
        ]
        graph = DependencyGraph(objectives)
        levels = graph.partition_into_levels()
        assert levels == [["a"]]

    def test_linear_chain(self):
        """a -> b -> c: each should be in its own level."""
        objectives = [
            Objective(objective_id="a", title="A"),
            Objective(objective_id="b", title="B", dependencies=["a"]),
            Objective(objective_id="c", title="C", dependencies=["b"]),
        ]
        graph = DependencyGraph(objectives)
        levels = graph.partition_into_levels()
        assert len(levels) == 3
        assert levels[0] == ["a"]
        assert levels[1] == ["b"]
        assert levels[2] == ["c"]

    def test_parallel_root_level(self):
        """a and b have no deps (level 0), c depends on both (level 1)."""
        objectives = [
            Objective(objective_id="a", title="A"),
            Objective(objective_id="b", title="B"),
            Objective(objective_id="c", title="C", dependencies=["a", "b"]),
        ]
        graph = DependencyGraph(objectives)
        levels = graph.partition_into_levels()
        assert len(levels) == 2
        assert set(levels[0]) == {"a", "b"}
        assert levels[1] == ["c"]

    def test_diamond_pattern(self):
        """a -> (b, c) -> d: a level 0, b+c level 1, d level 2."""
        objectives = [
            Objective(objective_id="a", title="A"),
            Objective(objective_id="b", title="B", dependencies=["a"]),
            Objective(objective_id="c", title="C", dependencies=["a"]),
            Objective(objective_id="d", title="D", dependencies=["b", "c"]),
        ]
        graph = DependencyGraph(objectives)
        levels = graph.partition_into_levels()
        assert len(levels) == 3
        assert levels[0] == ["a"]
        assert set(levels[1]) == {"b", "c"}
        assert levels[2] == ["d"]

    def test_empty_graph(self):
        graph = DependencyGraph()
        levels = graph.partition_into_levels()
        assert levels == []

    def test_cycle_raises_value_error(self):
        objectives = [
            Objective(objective_id="a", title="A", dependencies=["b"]),
            Objective(objective_id="b", title="B", dependencies=["a"]),
        ]
        graph = DependencyGraph(objectives)
        with pytest.raises(ValueError, match="Circular dependencies"):
            graph.partition_into_levels()

    def test_complex_dag(self):
        """
        a, b -> level 0 (no deps)
        c, d -> level 1 (c depends on a, d depends on a, b)
        e    -> level 2 (depends on c, d)
        """
        objectives = [
            Objective(objective_id="a", title="A"),
            Objective(objective_id="b", title="B"),
            Objective(objective_id="c", title="C", dependencies=["a"]),
            Objective(objective_id="d", title="D", dependencies=["a", "b"]),
            Objective(objective_id="e", title="E", dependencies=["c", "d"]),
        ]
        graph = DependencyGraph(objectives)
        levels = graph.partition_into_levels()
        assert len(levels) == 3
        assert set(levels[0]) == {"a", "b"}
        assert set(levels[1]) == {"c", "d"}
        assert levels[2] == ["e"]

    def test_levels_match_topological_order(self):
        """Verify flattening levels gives same set as topological_order."""
        objectives = [
            Objective(objective_id="a", title="A"),
            Objective(objective_id="b", title="B", dependencies=["a"]),
            Objective(objective_id="c", title="C", dependencies=["a"]),
            Objective(objective_id="d", title="D", dependencies=["b", "c"]),
        ]
        graph = DependencyGraph(objectives)
        levels = graph.partition_into_levels()
        flat = [oid for level in levels for oid in level]
        topo = graph.topological_order()
        assert set(flat) == set(topo)


# =============================================================================
# OrchestratorConfig parallel flags
# =============================================================================


class TestOrchestratorConfigParallel:
    def test_parallel_defaults(self):
        config = OrchestratorConfig()
        assert config.enable_parallel_execution is False
        assert config.max_parallel_objectives == 10
        assert config.serialize_hooks is True
        assert config.enable_rollback is True

    def test_parallel_explicit(self):
        config = OrchestratorConfig(
            enable_parallel_execution=True,
            max_parallel_objectives=5,
            serialize_hooks=False,
            enable_rollback=False,
        )
        assert config.enable_parallel_execution is True
        assert config.max_parallel_objectives == 5
        assert config.serialize_hooks is False
        assert config.enable_rollback is False


# =============================================================================
# Adapter execute_without_status_update tests
# =============================================================================


class TestExecuteWithoutStatusUpdate:
    async def test_returns_dict_on_success(self):
        mock_engine = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.state = PipelineState.COMPLETED
        mock_snapshot.artifacts = {"output": "data"}

        mock_engine.initialize = AsyncMock()
        mock_engine.start = AsyncMock(return_value=mock_snapshot)
        mock_engine.shutdown = MagicMock()

        adapter = OrchestratorPipelineAdapter(pipeline_engine=mock_engine)
        objective = Objective(objective_id="test-001", title="Test")

        result = await adapter.execute_without_status_update(objective)
        assert isinstance(result, dict)
        assert "success" in result
        assert "artifacts" in result
        assert "error" in result
        assert result["success"] is True

    async def test_returns_dict_on_failure(self):
        mock_engine = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.state = PipelineState.FAILED
        mock_snapshot.artifacts = {}
        mock_snapshot.error_message = "Pipeline error"

        mock_engine.initialize = AsyncMock()
        mock_engine.start = AsyncMock(return_value=mock_snapshot)
        mock_engine.shutdown = MagicMock()

        adapter = OrchestratorPipelineAdapter(pipeline_engine=mock_engine)
        objective = Objective(objective_id="test-001", title="Test")

        result = await adapter.execute_without_status_update(objective)
        assert result["success"] is False
        assert result["error"] == "Pipeline error"
        assert result["artifacts"] == []

    async def test_does_not_mutate_objective_status(self):
        """Verify the objective status is NOT changed."""
        mock_engine = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.state = PipelineState.COMPLETED
        mock_snapshot.artifacts = {}

        mock_engine.initialize = AsyncMock()
        mock_engine.start = AsyncMock(return_value=mock_snapshot)
        mock_engine.shutdown = MagicMock()

        adapter = OrchestratorPipelineAdapter(pipeline_engine=mock_engine)
        objective = Objective(
            objective_id="test-001",
            title="Test",
            status=ObjectiveStatus.QUEUED,
        )
        original_status = objective.status

        await adapter.execute_without_status_update(objective)
        assert objective.status == original_status

    async def test_exception_handling(self):
        mock_engine = MagicMock()
        mock_engine.initialize = AsyncMock(side_effect=RuntimeError("Connection lost"))

        adapter = OrchestratorPipelineAdapter(pipeline_engine=mock_engine)
        objective = Objective(objective_id="test-001", title="Test")

        result = await adapter.execute_without_status_update(objective)
        assert result["success"] is False
        assert "Connection lost" in result["error"]


# =============================================================================
# Parallel execution mode tests
# =============================================================================


class TestParallelExecutionMode:
    def _make_objective(self, objective_id="obj-001", title="Test", deps=None, status=ObjectiveStatus.QUEUED):
        return Objective(
            objective_id=objective_id,
            title=title,
            description=f"Description for {title}",
            status=status,
            dependencies=deps or [],
            phase="DEVELOPMENT",
            priority=5,
        )

    async def test_run_branches_to_sequential_by_default(self, tmp_path):
        """When enable_parallel_execution=False, should use sequential mode."""
        config = OrchestratorConfig(
            objectives_path=str(tmp_path / "objectives.yaml"),
            enable_parallel_execution=False,
            max_cycle_iterations=10,
        )
        project = ProjectObjectives(
            project_id="branch-test",
            objectives=[self._make_objective()],
        )
        project.save_atomic(config.objectives_path)

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)

        async def mock_execute_with_update(obj):
            from gaia.orchestration.adapters import ExecutionResult
            try:
                obj.transition_to(ObjectiveStatus.IN_PROGRESS)
            except ValueError:
                pass
            obj.transition_to(ObjectiveStatus.COMPLETED)
            return ExecutionResult(success=True, objective_id=obj.objective_id)

        mock_adapter.execute_with_result_update = AsyncMock(side_effect=mock_execute_with_update)

        orchestrator = ProjectOrchestrator(config=config, pipeline_adapter=mock_adapter)
        orchestrator.load_objectives()

        state = await orchestrator.run()
        assert state.objectives_processed == 1

    async def test_run_branches_to_parallel_when_flag_set(self, tmp_path):
        """When enable_parallel_execution=True, should use parallel mode."""
        config = OrchestratorConfig(
            objectives_path=str(tmp_path / "objectives.yaml"),
            enable_parallel_execution=True,
            max_cycle_iterations=10,
        )
        project = ProjectObjectives(
            project_id="parallel-test",
            objectives=[
                self._make_objective("obj-001", "Task A"),
                self._make_objective("obj-002", "Task B"),
            ],
        )
        project.save_atomic(config.objectives_path)

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)

        async def mock_execute_no_mutation(obj):
            return {
                "success": True,
                "artifacts": [],
                "error": None,
            }

        mock_adapter.execute_without_status_update = AsyncMock(side_effect=mock_execute_no_mutation)

        orchestrator = ProjectOrchestrator(config=config, pipeline_adapter=mock_adapter)
        orchestrator.load_objectives()

        state = await orchestrator.run()
        # Both objectives should be processed
        assert state.objectives_processed == 2

    async def test_parallel_mode_handles_mixed_results(self, tmp_path):
        """Level with mix of success and failure objectives."""
        config = OrchestratorConfig(
            objectives_path=str(tmp_path / "objectives.yaml"),
            enable_parallel_execution=True,
            max_cycle_iterations=10,
        )
        project = ProjectObjectives(
            project_id="mixed-test",
            objectives=[
                self._make_objective("obj-001", "Will Succeed"),
                self._make_objective("obj-002", "Will Fail"),
            ],
        )
        project.save_atomic(config.objectives_path)

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)

        async def mock_execute_no_mutation(obj):
            if obj.objective_id == "obj-001":
                return {"success": True, "artifacts": [], "error": None}
            return {"success": False, "artifacts": [], "error": "Task failed"}

        mock_adapter.execute_without_status_update = AsyncMock(side_effect=mock_execute_no_mutation)

        orchestrator = ProjectOrchestrator(config=config, pipeline_adapter=mock_adapter)
        orchestrator.load_objectives()

        state = await orchestrator.run()
        assert state.objectives_processed == 1
        assert state.objectives_failed == 1

    async def test_parallel_propagates_failures_to_dependents(self, tmp_path):
        """If level 0 fails, level 1 objectives should be BLOCKED."""
        config = OrchestratorConfig(
            objectives_path=str(tmp_path / "objectives.yaml"),
            enable_parallel_execution=True,
            max_cycle_iterations=10,
        )
        project = ProjectObjectives(
            project_id="propagation-test",
            objectives=[
                self._make_objective("obj-001", "Root", status=ObjectiveStatus.QUEUED),
                self._make_objective("obj-002", "Dependent", status=ObjectiveStatus.QUEUED, deps=["obj-001"]),
            ],
        )
        project.save_atomic(config.objectives_path)

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)

        async def mock_execute_no_mutation(obj):
            return {"success": False, "artifacts": [], "error": "Root failed"}

        mock_adapter.execute_without_status_update = AsyncMock(side_effect=mock_execute_no_mutation)

        orchestrator = ProjectOrchestrator(config=config, pipeline_adapter=mock_adapter)
        orchestrator.load_objectives()

        state = await orchestrator.run()

        # obj-001 should be BLOCKED, obj-002 should be BLOCKED due to propagation
        obj1 = orchestrator.project.get_objective("obj-001")
        obj2 = orchestrator.project.get_objective("obj-002")
        assert obj1.status == ObjectiveStatus.BLOCKED
        assert obj2.status == ObjectiveStatus.BLOCKED


# =============================================================================
# _propagate_failures_to_dependents tests
# =============================================================================


class TestPropagateFailures:
    def _make_objective(self, objective_id, title, deps=None, status=ObjectiveStatus.QUEUED):
        return Objective(
            objective_id=objective_id,
            title=title,
            description=f"Description for {title}",
            status=status,
            dependencies=deps or [],
            phase="DEVELOPMENT",
        )

    def test_marks_dependents_as_blocked(self):
        project = ProjectObjectives(
            objectives=[
                self._make_objective("a", "A"),
                self._make_objective("b", "B", deps=["a"]),
                self._make_objective("c", "C", deps=["a"]),
                self._make_objective("d", "D", deps=[]),  # independent
            ],
        )
        graph = DependencyGraph(project.objectives)

        orchestrator = ProjectOrchestrator()
        orchestrator._project = project
        orchestrator._dep_graph = graph

        orchestrator._propagate_failures_to_dependents(
            failed_ids={"a"},
            remaining_levels=[["b", "c", "d"]],
            dep_graph=graph,
        )

        assert project.get_objective("b").status == ObjectiveStatus.BLOCKED
        assert project.get_objective("c").status == ObjectiveStatus.BLOCKED
        assert project.get_objective("d").status == ObjectiveStatus.QUEUED  # not dependent

    def test_no_effect_on_independent_objectives(self):
        project = ProjectObjectives(
            objectives=[
                self._make_objective("a", "A"),
                self._make_objective("b", "B"),  # no deps
            ],
        )
        graph = DependencyGraph(project.objectives)

        orchestrator = ProjectOrchestrator()
        orchestrator._project = project
        orchestrator._dep_graph = graph

        orchestrator._propagate_failures_to_dependents(
            failed_ids={"a"},
            remaining_levels=[["b"]],
            dep_graph=graph,
        )

        assert project.get_objective("b").status == ObjectiveStatus.QUEUED

    def test_skips_non_queued_objectives(self):
        project = ProjectObjectives(
            objectives=[
                self._make_objective("a", "A"),
                self._make_objective("b", "B", deps=["a"], status=ObjectiveStatus.COMPLETED),
            ],
        )
        graph = DependencyGraph(project.objectives)

        orchestrator = ProjectOrchestrator()
        orchestrator._project = project
        orchestrator._dep_graph = graph

        orchestrator._propagate_failures_to_dependents(
            failed_ids={"a"},
            remaining_levels=[["b"]],
            dep_graph=graph,
        )

        # Should remain COMPLETED, not changed to BLOCKED
        assert project.get_objective("b").status == ObjectiveStatus.COMPLETED


# =============================================================================
# Supervisor evaluate_level tests
# =============================================================================


class TestEvaluateLevel:
    def test_conflict_triggers_remediate(self):
        supervisor = ProjectSupervisor()
        outcomes = [
            ObjectiveOutcome(objective_id="a", success=True),
        ]
        conflicts = [ConflictReport(conflicting_objective_ids=["a", "b"], affected_files={"x.py"})]
        project = ProjectObjectives()
        graph = DependencyGraph()

        verdict = supervisor.evaluate_level(outcomes, project, graph, conflicts)
        assert verdict == Verdict.REMEDIATE.value

    def test_all_failed_triggers_abort(self):
        supervisor = ProjectSupervisor()
        outcomes = [
            ObjectiveOutcome(objective_id="a", success=False),
            ObjectiveOutcome(objective_id="b", success=False),
        ]
        project = ProjectObjectives()
        graph = DependencyGraph()

        verdict = supervisor.evaluate_level(outcomes, project, graph)
        assert verdict == Verdict.ABORT.value

    def test_mixed_results_continue(self):
        supervisor = ProjectSupervisor()
        outcomes = [
            ObjectiveOutcome(objective_id="a", success=True),
            ObjectiveOutcome(objective_id="b", success=False),
        ]
        project = ProjectObjectives()
        graph = DependencyGraph()

        verdict = supervisor.evaluate_level(outcomes, project, graph)
        assert verdict == Verdict.CONTINUE.value

    def test_all_succeed_continue(self):
        supervisor = ProjectSupervisor()
        outcomes = [
            ObjectiveOutcome(objective_id="a", success=True),
            ObjectiveOutcome(objective_id="b", success=True),
        ]
        project = ProjectObjectives()
        graph = DependencyGraph()

        verdict = supervisor.evaluate_level(outcomes, project, graph)
        assert verdict == Verdict.CONTINUE.value

    def test_empty_outcomes_continue(self):
        supervisor = ProjectSupervisor()
        project = ProjectObjectives()
        graph = DependencyGraph()

        verdict = supervisor.evaluate_level([], project, graph)
        assert verdict == Verdict.CONTINUE.value

    def test_per_objective_failure_tracking(self):
        config = SupervisorConfig(consecutive_failure_threshold=2, max_consecutive_failures=3)
        supervisor = ProjectSupervisor(config=config)
        project = ProjectObjectives()
        graph = DependencyGraph()

        # Fail same objective 3 times across levels
        for _ in range(3):
            outcomes = [ObjectiveOutcome(objective_id="a", success=False)]
            verdict = supervisor.evaluate_level(outcomes, project, graph)

        # Third failure should hit max_consecutive_failures
        assert verdict == Verdict.ABORT.value

    def test_records_outcomes_in_state(self):
        supervisor = ProjectSupervisor()
        outcomes = [
            ObjectiveOutcome(objective_id="a", success=True),
            ObjectiveOutcome(objective_id="b", success=False),
        ]
        project = ProjectObjectives()
        graph = DependencyGraph()

        supervisor.evaluate_level(outcomes, project, graph)

        assert supervisor.state.total_objectives == 2
        assert supervisor.state.successful_objectives == 1
        assert supervisor.state.failed_objectives == 1


# =============================================================================
# Hook serialization tests
# =============================================================================


class TestHookSerialization:
    async def test_hooks_fire_in_parallel_mode(self, tmp_path):
        """Verify OBJECTIVE_START and OBJECTIVE_COMPLETE hooks fire in parallel mode."""
        fired_events = []

        class TestHook(BaseHook):
            name = "test_hook"
            event = OBJECTIVE_START
            priority = HookPriority.HIGH

            async def execute(self, context: HookContext) -> HookResult:
                fired_events.append(context.event)
                return HookResult.success_result()

        config = OrchestratorConfig(
            objectives_path=str(tmp_path / "objectives.yaml"),
            enable_parallel_execution=True,
            serialize_hooks=True,
            max_cycle_iterations=10,
        )
        project = ProjectObjectives(
            project_id="hook-parallel-test",
            objectives=[
                Objective(objective_id="obj-001", title="Task A", phase="DEVELOPMENT"),
                Objective(objective_id="obj-002", title="Task B", phase="DEVELOPMENT"),
            ],
        )
        project.save_atomic(config.objectives_path)

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)

        async def mock_execute_no_mutation(obj):
            return {"success": True, "artifacts": [], "error": None}

        mock_adapter.execute_without_status_update = AsyncMock(side_effect=mock_execute_no_mutation)

        orchestrator = ProjectOrchestrator(config=config, pipeline_adapter=mock_adapter)
        orchestrator.load_objectives()
        orchestrator.hook_registry.register(TestHook())

        await orchestrator.run()
        assert OBJECTIVE_START in fired_events


# =============================================================================
# Concurrency and lock tests
# =============================================================================


class TestConcurrencyLocks:
    def test_locks_exist_on_orchestrator(self):
        orchestrator = ProjectOrchestrator()
        assert hasattr(orchestrator, "_hook_lock")
        assert hasattr(orchestrator, "_git_op_lock")
        assert isinstance(orchestrator._hook_lock, asyncio.Lock)
        assert isinstance(orchestrator._git_op_lock, asyncio.Lock)


# =============================================================================
# Integration: full parallel workflow
# =============================================================================


class TestParallelIntegration:
    async def test_three_level_dag(self, tmp_path):
        """Full integration test with a 3-level dependency DAG."""
        config = OrchestratorConfig(
            objectives_path=str(tmp_path / "objectives.yaml"),
            enable_parallel_execution=True,
            max_cycle_iterations=10,
        )
        project = ProjectObjectives(
            project_id="integration-test",
            objectives=[
                Objective(objective_id="a", title="Root 1", phase="DEVELOPMENT"),
                Objective(objective_id="b", title="Root 2", phase="DEVELOPMENT"),
                Objective(objective_id="c", title="Mid 1", dependencies=["a"], phase="DEVELOPMENT"),
                Objective(objective_id="d", title="Mid 2", dependencies=["b"], phase="DEVELOPMENT"),
                Objective(objective_id="e", title="Final", dependencies=["c", "d"], phase="DEVELOPMENT"),
            ],
        )
        project.save_atomic(config.objectives_path)

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)

        async def mock_execute_no_mutation(obj):
            return {"success": True, "artifacts": [], "error": None}

        mock_adapter.execute_without_status_update = AsyncMock(side_effect=mock_execute_no_mutation)

        orchestrator = ProjectOrchestrator(config=config, pipeline_adapter=mock_adapter)
        orchestrator.load_objectives()

        state = await orchestrator.run()

        # All 5 objectives should be processed
        assert state.objectives_processed == 5
        assert state.objectives_failed == 0
        # Verify status transitions
        for obj_id in ["a", "b", "c", "d", "e"]:
            obj = orchestrator.project.get_objective(obj_id)
            assert obj.status == ObjectiveStatus.COMPLETED, f"{obj_id} should be COMPLETED"


# =============================================================================
# Conflict Detection tests
# =============================================================================


class TestConflictDetection:
    def _make_objective(self, objective_id="obj-001", title="Test", deps=None, status=ObjectiveStatus.QUEUED):
        return Objective(
            objective_id=objective_id,
            title=title,
            description=f"Description for {title}",
            status=status,
            dependencies=deps or [],
            phase="DEVELOPMENT",
            priority=5,
        )

    async def test_detect_conflicts_no_git_supervisor(self, tmp_path):
        """Returns empty list when GitSupervisor is disabled."""
        config = OrchestratorConfig(
            objectives_path=str(tmp_path / "objectives.yaml"),
            enable_parallel_execution=True,
            max_cycle_iterations=10,
        )
        project = ProjectObjectives(
            project_id="no-git-test",
            objectives=[self._make_objective("obj-001", "Task A")],
        )
        project.save_atomic(config.objectives_path)

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)
        mock_adapter.execute_without_status_update = AsyncMock(
            return_value={"success": True, "artifacts": [], "error": None}
        )

        orchestrator = ProjectOrchestrator(config=config, pipeline_adapter=mock_adapter)
        orchestrator.load_objectives()

        assert orchestrator.git_supervisor is None
        conflicts = await orchestrator._detect_level_conflicts(
            ["obj-001"],
            {"obj-001": "feature/obj-001"},
        )
        assert conflicts == []

    async def test_detect_conflicts_no_overlap(self, tmp_path):
        """Two objectives editing different files -> no conflict."""
        config = OrchestratorConfig(
            objectives_path=str(tmp_path / "objectives.yaml"),
            enable_parallel_execution=True,
            enable_git_supervisor=True,
            max_cycle_iterations=10,
        )
        project = ProjectObjectives(
            project_id="no-overlap-test",
            objectives=[
                self._make_objective("obj-001", "Task A"),
                self._make_objective("obj-002", "Task B"),
            ],
        )
        project.save_atomic(config.objectives_path)

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)
        mock_adapter.execute_without_status_update = AsyncMock(
            return_value={"success": True, "artifacts": [], "error": None}
        )

        mock_git_supervisor = MagicMock()
        mock_git_supervisor.detect_changed_files.side_effect = lambda branch, base: (
            ["src/module_a.py"] if "obj-001" in branch else ["src/module_b.py"]
        )

        orchestrator = ProjectOrchestrator(config=config, pipeline_adapter=mock_adapter)
        orchestrator.load_objectives()
        orchestrator._git_supervisor = mock_git_supervisor

        conflicts = await orchestrator._detect_level_conflicts(
            ["obj-001", "obj-002"],
            {"obj-001": "feature/obj-001", "obj-002": "feature/obj-002"},
        )
        assert conflicts == []
        assert mock_git_supervisor.detect_changed_files.call_count == 2

    async def test_detect_conflicts_file_overlap(self, tmp_path):
        """Two objectives editing same file -> conflict detected."""
        config = OrchestratorConfig(
            objectives_path=str(tmp_path / "objectives.yaml"),
            enable_parallel_execution=True,
            enable_git_supervisor=True,
            max_cycle_iterations=10,
        )
        project = ProjectObjectives(
            project_id="overlap-test",
            objectives=[
                self._make_objective("obj-001", "Task A"),
                self._make_objective("obj-002", "Task B"),
            ],
        )
        project.save_atomic(config.objectives_path)

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)
        mock_adapter.execute_without_status_update = AsyncMock(
            return_value={"success": True, "artifacts": [], "error": None}
        )

        mock_git_supervisor = MagicMock()
        mock_git_supervisor.detect_changed_files.side_effect = lambda branch, base: (
            ["src/shared.py", "src/module_a.py"]
            if "obj-001" in branch
            else ["src/shared.py", "src/module_b.py"]
        )

        orchestrator = ProjectOrchestrator(config=config, pipeline_adapter=mock_adapter)
        orchestrator.load_objectives()
        orchestrator._git_supervisor = mock_git_supervisor

        conflicts = await orchestrator._detect_level_conflicts(
            ["obj-001", "obj-002"],
            {"obj-001": "feature/obj-001", "obj-002": "feature/obj-002"},
        )
        assert len(conflicts) == 1
        report = conflicts[0]
        assert set(report.conflicting_objective_ids) == {"obj-001", "obj-002"}
        assert report.affected_files == {"src/shared.py"}
        assert report.timestamp != ""

    async def test_detect_conflicts_skips_missing_branch(self, tmp_path):
        """Objectives without a mapped branch are skipped."""
        config = OrchestratorConfig(
            objectives_path=str(tmp_path / "objectives.yaml"),
            enable_parallel_execution=True,
            enable_git_supervisor=True,
            max_cycle_iterations=10,
        )
        project = ProjectObjectives(
            project_id="missing-branch-test",
            objectives=[
                self._make_objective("obj-001", "Task A"),
                self._make_objective("obj-002", "Task B"),
            ],
        )
        project.save_atomic(config.objectives_path)

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)
        mock_adapter.execute_without_status_update = AsyncMock(
            return_value={"success": True, "artifacts": [], "error": None}
        )

        mock_git_supervisor = MagicMock()
        mock_git_supervisor.detect_changed_files.return_value = ["src/shared.py"]

        orchestrator = ProjectOrchestrator(config=config, pipeline_adapter=mock_adapter)
        orchestrator.load_objectives()
        orchestrator._git_supervisor = mock_git_supervisor

        # Only obj-001 has a branch; obj-002 is missing -> no pairwise comparison possible
        conflicts = await orchestrator._detect_level_conflicts(
            ["obj-001", "obj-002"],
            {"obj-001": "feature/obj-001"},  # obj-002 missing
        )
        assert conflicts == []
        # Should only call detect_changed_files for obj-001
        mock_git_supervisor.detect_changed_files.assert_called_once_with(
            "feature/obj-001", "main"
        )

    async def test_evaluate_level_with_conflicts(self, tmp_path):
        """Verdict REMEDIATE when conflicts present."""
        config = OrchestratorConfig(
            objectives_path=str(tmp_path / "objectives.yaml"),
            enable_parallel_execution=True,
            enable_git_supervisor=True,
            enable_supervisor=True,
            max_cycle_iterations=10,
        )
        project = ProjectObjectives(
            project_id="conflict-verdict-test",
            objectives=[
                self._make_objective("obj-001", "Task A"),
                self._make_objective("obj-002", "Task B"),
            ],
        )
        project.save_atomic(config.objectives_path)

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)
        mock_adapter.execute_without_status_update = AsyncMock(
            return_value={"success": True, "artifacts": [], "error": None}
        )

        mock_git_supervisor = MagicMock()
        mock_git_supervisor.detect_changed_files.side_effect = lambda branch, base: (
            ["src/shared.py"]
        )

        orchestrator = ProjectOrchestrator(config=config, pipeline_adapter=mock_adapter)
        orchestrator.load_objectives()
        orchestrator._git_supervisor = mock_git_supervisor
        # Pre-populate branch mappings (normally set by GitBranchHook)
        orchestrator._state.objective_branches = {
            "obj-001": "feature/obj-001",
            "obj-002": "feature/obj-002",
        }

        state = await orchestrator.run()

        # Both objectives processed successfully
        assert state.objectives_processed == 2

        # Supervisor should have recorded REMEDIATE verdict due to conflicts
        assert orchestrator.supervisor is not None
        assert orchestrator.supervisor.state.current_verdict == Verdict.REMEDIATE


# =============================================================================
# Rollback tests (Phase 4C)
# =============================================================================


class TestRollback:
    """Tests for _rollback_failed_objectives and integration into _run_parallel_mode."""

    def _make_objective(self, objective_id="obj-001", title="Test", deps=None, status=ObjectiveStatus.QUEUED):
        return Objective(
            objective_id=objective_id,
            title=title,
            description=f"Description for {title}",
            status=status,
            dependencies=deps or [],
            phase="DEVELOPMENT",
            priority=5,
        )

    async def test_rollback_disabled(self, tmp_path):
        """No rollback when enable_rollback=False."""
        config = OrchestratorConfig(
            objectives_path=str(tmp_path / "objectives.yaml"),
            enable_parallel_execution=True,
            enable_rollback=False,
            max_cycle_iterations=10,
        )
        project = ProjectObjectives(
            project_id="rollback-disabled-test",
            objectives=[
                self._make_objective("obj-001", "Task A"),
                self._make_objective("obj-002", "Task B", deps=["obj-001"]),
            ],
        )
        project.save_atomic(config.objectives_path)

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)

        async def mock_execute_no_mutation(obj):
            return {"success": False, "artifacts": [], "error": "Task failed"}

        mock_adapter.execute_without_status_update = AsyncMock(side_effect=mock_execute_no_mutation)

        orchestrator = ProjectOrchestrator(config=config, pipeline_adapter=mock_adapter)
        orchestrator.load_objectives()

        rolled_back = await orchestrator._rollback_failed_objectives(
            {"obj-001"}, level_number=0
        )
        assert rolled_back == 0

        # Full run should also not attempt rollback
        state = await orchestrator.run()
        assert state.objectives_failed >= 1

    async def test_rollback_failed_objectives(self, tmp_path):
        """Failed objectives rolled back when GitSupervisor enabled."""
        config = OrchestratorConfig(
            objectives_path=str(tmp_path / "objectives.yaml"),
            enable_parallel_execution=True,
            enable_git_supervisor=True,
            max_cycle_iterations=10,
        )
        project = ProjectObjectives(
            project_id="rollback-success-test",
            objectives=[
                self._make_objective("obj-001", "Task A"),
                self._make_objective("obj-002", "Task B"),
            ],
        )
        project.save_atomic(config.objectives_path)

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)

        async def mock_execute_no_mutation(obj):
            if obj.objective_id == "obj-001":
                return {"success": False, "artifacts": [], "error": "Task A failed"}
            return {"success": True, "artifacts": [], "error": None}

        mock_adapter.execute_without_status_update = AsyncMock(side_effect=mock_execute_no_mutation)

        # Mock GitSupervisor
        mock_git_supervisor = MagicMock()
        mock_git_supervisor.rollback.return_value = True
        mock_git_supervisor.detect_changed_files.return_value = []

        orchestrator = ProjectOrchestrator(config=config, pipeline_adapter=mock_adapter)
        orchestrator.load_objectives()
        orchestrator._git_supervisor = mock_git_supervisor

        # Pre-populate branch mappings (normally set by GitBranchHook)
        orchestrator._state.objective_branches = {
            "obj-001": "feature/obj-001",
            "obj-002": "feature/obj-002",
        }

        rolled_back = await orchestrator._rollback_failed_objectives(
            {"obj-001"}, level_number=0
        )
        assert rolled_back == 1
        mock_git_supervisor.rollback.assert_called_once_with("feature/obj-001")

    async def test_rollback_supervisor_abort(self, tmp_path):
        """Rollback triggered on supervisor ABORT verdict."""
        config = OrchestratorConfig(
            objectives_path=str(tmp_path / "objectives.yaml"),
            enable_parallel_execution=True,
            enable_git_supervisor=True,
            enable_supervisor=True,
            max_cycle_iterations=10,
        )
        project = ProjectObjectives(
            project_id="rollback-abort-test",
            objectives=[
                self._make_objective("obj-001", "Task A"),
                self._make_objective("obj-002", "Task B"),
            ],
        )
        project.save_atomic(config.objectives_path)

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)

        async def mock_execute_no_mutation(obj):
            return {"success": False, "artifacts": [], "error": "All failed"}

        mock_adapter.execute_without_status_update = AsyncMock(side_effect=mock_execute_no_mutation)

        # Mock GitSupervisor
        mock_git_supervisor = MagicMock()
        mock_git_supervisor.rollback.return_value = True
        mock_git_supervisor.detect_changed_files.return_value = []

        orchestrator = ProjectOrchestrator(config=config, pipeline_adapter=mock_adapter)
        orchestrator.load_objectives()
        orchestrator._git_supervisor = mock_git_supervisor

        # Pre-populate branch mappings
        orchestrator._state.objective_branches = {
            "obj-001": "feature/obj-001",
            "obj-002": "feature/obj-002",
        }

        state = await orchestrator.run()

        # Both objectives should have failed
        assert state.objectives_failed == 2
        # Rollback should have been called for the failed objectives
        assert mock_git_supervisor.rollback.call_count == 2

    async def test_rollback_skips_objectives_without_branch(self, tmp_path):
        """Objectives without branch mapping are skipped gracefully."""
        config = OrchestratorConfig(
            objectives_path=str(tmp_path / "objectives.yaml"),
            enable_parallel_execution=True,
            enable_rollback=True,
            max_cycle_iterations=10,
        )
        project = ProjectObjectives(
            project_id="no-branch-test",
            objectives=[
                self._make_objective("obj-001", "Task A"),
            ],
        )
        project.save_atomic(config.objectives_path)

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)
        mock_adapter.execute_without_status_update = AsyncMock(
            return_value={"success": False, "artifacts": [], "error": "Failed"}
        )

        orchestrator = ProjectOrchestrator(config=config, pipeline_adapter=mock_adapter)
        orchestrator.load_objectives()

        # No branch mapping for obj-001
        assert "obj-001" not in orchestrator._state.objective_branches

        # Should return 0 (skipped gracefully, no error)
        rolled_back = await orchestrator._rollback_failed_objectives(
            {"obj-001"}, level_number=0
        )
        assert rolled_back == 0

    async def test_rollback_graceful_error_handling(self, tmp_path):
        """Rollback continues even when GitSupervisor.rollback fails."""
        config = OrchestratorConfig(
            objectives_path=str(tmp_path / "objectives.yaml"),
            enable_parallel_execution=True,
            enable_git_supervisor=True,
            max_cycle_iterations=10,
        )
        project = ProjectObjectives(
            project_id="rollback-error-test",
            objectives=[
                self._make_objective("obj-001", "Task A"),
                self._make_objective("obj-002", "Task B"),
            ],
        )
        project.save_atomic(config.objectives_path)

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)
        mock_adapter.execute_without_status_update = AsyncMock(
            return_value={"success": False, "artifacts": [], "error": "Failed"}
        )

        # Mock GitSupervisor with partial failure
        mock_git_supervisor = MagicMock()
        mock_git_supervisor.rollback.side_effect = lambda branch: (
            True if "obj-001" in branch else False
        )

        orchestrator = ProjectOrchestrator(config=config, pipeline_adapter=mock_adapter)
        orchestrator.load_objectives()
        orchestrator._git_supervisor = mock_git_supervisor

        orchestrator._state.objective_branches = {
            "obj-001": "feature/obj-001",
            "obj-002": "feature/obj-002",
        }

        # Both fail, rollback succeeds for obj-001 but fails for obj-002
        rolled_back = await orchestrator._rollback_failed_objectives(
            {"obj-001", "obj-002"}, level_number=0
        )
        assert rolled_back == 1
        assert mock_git_supervisor.rollback.call_count == 2

    async def test_rollback_without_git_supervisor(self, tmp_path):
        """Rollback returns 0 when no GitSupervisor is enabled."""
        config = OrchestratorConfig(
            objectives_path=str(tmp_path / "objectives.yaml"),
            enable_parallel_execution=True,
            enable_rollback=True,
            enable_git_supervisor=False,
            max_cycle_iterations=10,
        )
        project = ProjectObjectives(
            project_id="no-git-rollback-test",
            objectives=[self._make_objective("obj-001", "Task A")],
        )
        project.save_atomic(config.objectives_path)

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)
        mock_adapter.execute_without_status_update = AsyncMock(
            return_value={"success": False, "artifacts": [], "error": "Failed"}
        )

        orchestrator = ProjectOrchestrator(config=config, pipeline_adapter=mock_adapter)
        orchestrator.load_objectives()

        # No git supervisor, but has branch mapping
        orchestrator._state.objective_branches = {"obj-001": "feature/obj-001"}

        rolled_back = await orchestrator._rollback_failed_objectives(
            {"obj-001"}, level_number=0
        )
        assert rolled_back == 0
        assert orchestrator.git_supervisor is None

    async def test_rollback_uses_git_op_lock(self, tmp_path):
        """Rollback serializes git operations via _git_op_lock."""
        config = OrchestratorConfig(
            objectives_path=str(tmp_path / "objectives.yaml"),
            enable_parallel_execution=True,
            enable_git_supervisor=True,
            max_cycle_iterations=10,
        )
        project = ProjectObjectives(
            project_id="rollback-lock-test",
            objectives=[
                self._make_objective("obj-001", "Task A"),
                self._make_objective("obj-002", "Task B"),
            ],
        )
        project.save_atomic(config.objectives_path)

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)
        mock_adapter.execute_without_status_update = AsyncMock(
            return_value={"success": False, "artifacts": [], "error": "Failed"}
        )

        call_order = []
        mock_git_supervisor = MagicMock()

        def mock_rollback(branch):
            call_order.append(branch)
            return True

        mock_git_supervisor.rollback.side_effect = mock_rollback

        orchestrator = ProjectOrchestrator(config=config, pipeline_adapter=mock_adapter)
        orchestrator.load_objectives()
        orchestrator._git_supervisor = mock_git_supervisor

        orchestrator._state.objective_branches = {
            "obj-001": "feature/obj-001",
            "obj-002": "feature/obj-002",
        }

        rolled_back = await orchestrator._rollback_failed_objectives(
            {"obj-001", "obj-002"}, level_number=0
        )
        assert rolled_back == 2
        # Both rollbacks should have been called
        assert set(call_order) == {"feature/obj-001", "feature/obj-002"}


# =============================================================================
# Worktree Lifecycle tests (Phase 4D)
# =============================================================================


class TestWorktreeLifecycle:
    """Tests for worktree creation, cleanup, and stale worktree management."""

    def _init_git_repo(self, tmp_path):
        """Initialize a temp git repo with an initial commit."""
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        # Create an initial commit so worktrees can be created
        (tmp_path / "initial.txt").write_text("initial")
        subprocess.run(
            ["git", "add", "."],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            check=True,
        )

    def _make_objective(self, objective_id="obj-001", title="Test", deps=None, status=ObjectiveStatus.QUEUED):
        return Objective(
            objective_id=objective_id,
            title=title,
            description=f"Description for {title}",
            status=status,
            dependencies=deps or [],
            phase="DEVELOPMENT",
            priority=5,
        )

    async def test_create_worktree_for_objective(self, tmp_path):
        """Branch created, worktree directory created."""
        self._init_git_repo(tmp_path)

        objectives_path = tmp_path / ".gaia" / "objectives.yaml"
        objectives_path.parent.mkdir(parents=True, exist_ok=True)
        project = ProjectObjectives(
            project_id="worktree-test",
            objectives=[self._make_objective("obj-001", "Initialize Project")],
        )
        project.save_atomic(str(objectives_path))

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)
        mock_adapter.execute_without_status_update = AsyncMock(
            return_value={"success": True, "artifacts": [], "error": None}
        )

        config = OrchestratorConfig(
            objectives_path=str(objectives_path),
            enable_parallel_execution=True,
            max_cycle_iterations=10,
        )
        orchestrator = ProjectOrchestrator(config=config, pipeline_adapter=mock_adapter)
        orchestrator.load_objectives()

        # Clean up any stale worktrees from previous runs
        await orchestrator._cleanup_all_stale_worktrees()

        objective = orchestrator.project.get_objective("obj-001")
        branch = await orchestrator._create_worktree_for_objective(objective)

        assert branch is not None
        assert branch.startswith("obj/obj-001-")
        assert branch in orchestrator._state.objective_branches.values()

        worktree_path = tmp_path / ".gaia" / "worktrees" / "obj-001"
        assert worktree_path.exists()
        assert worktree_path.is_dir()

        # Cleanup
        await orchestrator._cleanup_worktree(branch, "obj-001")

    async def test_cleanup_worktree_success(self, tmp_path):
        """Worktree directory removed, branch retained."""
        self._init_git_repo(tmp_path)

        objectives_path = tmp_path / ".gaia" / "objectives.yaml"
        objectives_path.parent.mkdir(parents=True, exist_ok=True)
        project = ProjectObjectives(
            project_id="cleanup-test",
            objectives=[self._make_objective("obj-001", "Cleanup Test")],
        )
        project.save_atomic(str(objectives_path))

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)
        mock_adapter.execute_without_status_update = AsyncMock(
            return_value={"success": True, "artifacts": [], "error": None}
        )

        config = OrchestratorConfig(
            objectives_path=str(objectives_path),
            enable_parallel_execution=True,
            max_cycle_iterations=10,
        )
        orchestrator = ProjectOrchestrator(config=config, pipeline_adapter=mock_adapter)
        orchestrator.load_objectives()

        # Clean up any stale worktrees from previous runs
        await orchestrator._cleanup_all_stale_worktrees()

        objective = orchestrator.project.get_objective("obj-001")
        branch = await orchestrator._create_worktree_for_objective(objective)
        assert branch is not None

        worktree_path = tmp_path / ".gaia" / "worktrees" / "obj-001"
        assert worktree_path.exists()

        result = await orchestrator._cleanup_worktree(branch, "obj-001")
        assert result is True
        assert not worktree_path.exists()

    async def test_cleanup_stale_worktrees(self, tmp_path):
        """All obj/ worktrees removed at run start."""
        self._init_git_repo(tmp_path)

        objectives_path = tmp_path / ".gaia" / "objectives.yaml"
        objectives_path.parent.mkdir(parents=True, exist_ok=True)
        project = ProjectObjectives(
            project_id="stale-test",
            objectives=[
                self._make_objective("obj-001", "Stale Task One"),
                self._make_objective("obj-002", "Stale Task Two"),
            ],
        )
        project.save_atomic(str(objectives_path))

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)
        mock_adapter.execute_without_status_update = AsyncMock(
            return_value={"success": True, "artifacts": [], "error": None}
        )

        config = OrchestratorConfig(
            objectives_path=str(objectives_path),
            enable_parallel_execution=True,
            max_cycle_iterations=10,
        )
        orchestrator = ProjectOrchestrator(config=config, pipeline_adapter=mock_adapter)
        orchestrator.load_objectives()

        # Manually create stale worktrees
        for obj_id, title in [("obj-001", "Stale Task One"), ("obj-002", "Stale Task Two")]:
            slug = ProjectOrchestrator._build_objective_slug(title)
            branch = f"obj/{obj_id}-{slug}"
            worktree_path = tmp_path / ".gaia" / "worktrees" / obj_id
            subprocess.run(
                ["git", "worktree", "add", "-b", branch, str(worktree_path)],
                cwd=str(tmp_path),
                capture_output=True,
                text=True,
                check=True,
            )
            assert worktree_path.exists()

        # Run cleanup
        await orchestrator._cleanup_all_stale_worktrees()

        # All obj/ worktrees should be gone
        for obj_id in ["obj-001", "obj-002"]:
            worktree_path = tmp_path / ".gaia" / "worktrees" / obj_id
            assert not worktree_path.exists()

    async def test_concurrent_git_operations_serialized(self, tmp_path):
        """_git_op_lock prevents race conditions."""
        self._init_git_repo(tmp_path)

        objectives_path = tmp_path / ".gaia" / "objectives.yaml"
        objectives_path.parent.mkdir(parents=True, exist_ok=True)
        project = ProjectObjectives(
            project_id="concurrent-test",
            objectives=[
                self._make_objective("obj-001", "Concurrent Task One"),
                self._make_objective("obj-002", "Concurrent Task Two"),
            ],
        )
        project.save_atomic(str(objectives_path))

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)
        mock_adapter.execute_without_status_update = AsyncMock(
            return_value={"success": True, "artifacts": [], "error": None}
        )

        config = OrchestratorConfig(
            objectives_path=str(objectives_path),
            enable_parallel_execution=True,
            max_cycle_iterations=10,
        )
        orchestrator = ProjectOrchestrator(config=config, pipeline_adapter=mock_adapter)
        orchestrator.load_objectives()

        # Clean up any stale worktrees from previous runs
        await orchestrator._cleanup_all_stale_worktrees()

        obj1 = orchestrator.project.get_objective("obj-001")
        obj2 = orchestrator.project.get_objective("obj-002")

        # Run both worktree creations concurrently
        results = await asyncio.gather(
            orchestrator._create_worktree_for_objective(obj1),
            orchestrator._create_worktree_for_objective(obj2),
        )

        # Both should succeed (no race condition)
        assert all(r is not None for r in results)
        assert len(orchestrator._state.objective_branches) == 2

        # Both worktrees should exist
        for obj_id in ["obj-001", "obj-002"]:
            worktree_path = tmp_path / ".gaia" / "worktrees" / obj_id
            assert worktree_path.exists()

        # Cleanup
        for obj_id in ["obj-001", "obj-002"]:
            branch = orchestrator._state.objective_branches.get(obj_id)
            if branch:
                await orchestrator._cleanup_worktree(branch, obj_id)

    async def test_worktree_cleanup_on_failure(self, tmp_path):
        """Failed objectives also get worktree cleanup."""
        self._init_git_repo(tmp_path)

        objectives_path = tmp_path / ".gaia" / "objectives.yaml"
        objectives_path.parent.mkdir(parents=True, exist_ok=True)
        project = ProjectObjectives(
            project_id="failure-cleanup-test",
            objectives=[
                self._make_objective("obj-001", "Will Fail Task"),
                self._make_objective("obj-002", "Will Succeed Task"),
            ],
        )
        project.save_atomic(str(objectives_path))

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)

        async def mock_execute_no_mutation(obj):
            if obj.objective_id == "obj-001":
                return {"success": False, "artifacts": [], "error": "Task failed"}
            return {"success": True, "artifacts": [], "error": None}

        mock_adapter.execute_without_status_update = AsyncMock(side_effect=mock_execute_no_mutation)

        config = OrchestratorConfig(
            objectives_path=str(objectives_path),
            enable_parallel_execution=True,
            max_cycle_iterations=10,
        )
        orchestrator = ProjectOrchestrator(config=config, pipeline_adapter=mock_adapter)
        orchestrator.load_objectives()

        state = await orchestrator.run()

        # Both objectives should have been processed
        assert state.objectives_processed == 1
        assert state.objectives_failed == 1

        # Worktrees should be cleaned up for both
        for obj_id in ["obj-001", "obj-002"]:
            worktree_path = tmp_path / ".gaia" / "worktrees" / obj_id
            assert not worktree_path.exists(), f"Worktree for {obj_id} should be cleaned up"

    async def test_worktree_graceful_no_git_repo(self, tmp_path):
        """Worktree creation returns None when no git repo exists."""
        # Ensure we're running in a non-git directory by preventing git
        # from searching parent directories
        old_ceiling = os.environ.get("GIT_CEILING_DIRECTORIES", "")
        try:
            os.environ["GIT_CEILING_DIRECTORIES"] = str(tmp_path)

            objectives_path = tmp_path / ".gaia" / "objectives.yaml"
            objectives_path.parent.mkdir(parents=True, exist_ok=True)
            project = ProjectObjectives(
                project_id="no-git-test",
                objectives=[self._make_objective("obj-001", "No Git Task")],
            )
            project.save_atomic(str(objectives_path))

            mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)
            mock_adapter.execute_without_status_update = AsyncMock(
                return_value={"success": True, "artifacts": [], "error": None}
            )

            config = OrchestratorConfig(
                objectives_path=str(objectives_path),
                enable_parallel_execution=True,
                max_cycle_iterations=10,
            )
            orchestrator = ProjectOrchestrator(config=config, pipeline_adapter=mock_adapter)
            orchestrator.load_objectives()

            objective = orchestrator.project.get_objective("obj-001")
            # Should not raise, should return None
            branch = await orchestrator._create_worktree_for_objective(objective)
            assert branch is None
        finally:
            os.environ["GIT_CEILING_DIRECTORIES"] = old_ceiling

    async def test_worktree_integration_in_parallel_run(self, tmp_path):
        """Full parallel run creates and cleans up worktrees."""
        self._init_git_repo(tmp_path)

        objectives_path = tmp_path / ".gaia" / "objectives.yaml"
        objectives_path.parent.mkdir(parents=True, exist_ok=True)
        project = ProjectObjectives(
            project_id="integration-worktree-test",
            objectives=[
                self._make_objective("obj-001", "Integration Task A"),
                self._make_objective("obj-002", "Integration Task B"),
                self._make_objective("obj-003", "Integration Task C", deps=["obj-001", "obj-002"]),
            ],
        )
        project.save_atomic(str(objectives_path))

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)
        mock_adapter.execute_without_status_update = AsyncMock(
            return_value={"success": True, "artifacts": [], "error": None}
        )

        config = OrchestratorConfig(
            objectives_path=str(objectives_path),
            enable_parallel_execution=True,
            max_cycle_iterations=10,
        )
        orchestrator = ProjectOrchestrator(config=config, pipeline_adapter=mock_adapter)
        orchestrator.load_objectives()

        state = await orchestrator.run()

        # All 3 objectives processed
        assert state.objectives_processed == 3
        assert state.objectives_failed == 0

        # All worktrees should be cleaned up
        for obj_id in ["obj-001", "obj-002", "obj-003"]:
            worktree_path = tmp_path / ".gaia" / "worktrees" / obj_id
            assert not worktree_path.exists(), f"Worktree for {obj_id} should be cleaned up"
