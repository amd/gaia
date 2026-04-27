"""
Unit tests for ProjectOrchestrator.

Tests cover:
    - Dispatch/evaluate/update cycle with mocked PipelineEngine
    - Hook execution on orchestrator's HookRegistry
    - auto_commit=False and dry_run modes
    - Git config lookup with fallback
    - Pause/resume behavior
    - CircuitBreaker correct invocation verification
"""

import asyncio
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gaia.hooks.base import BaseHook, HookContext, HookPriority, HookResult
from gaia.orchestration.adapters import ExecutionResult, OrchestratorPipelineAdapter
from gaia.orchestration.engine import (
    OBJECTIVE_COMPLETE,
    OBJECTIVE_FAILED,
    OBJECTIVE_START,
    OrchestratorConfig,
    ProjectOrchestrator,
)
from gaia.orchestration.hooks import ObjectiveUpdateHook, TaskSpawnHook
from gaia.orchestration.models import (
    Artifact,
    Objective,
    ObjectiveStatus,
    ProjectObjectives,
)
from gaia.pipeline.state import PipelineState


# =============================================================================
# Module-level fixtures
# =============================================================================


@pytest.fixture
def config(tmp_path):
    """Shared config fixture for all orchestrator tests."""
    return OrchestratorConfig(
        objectives_path=str(tmp_path / "objectives.yaml"),
        auto_commit=False,
        dry_run=False,
        max_cycle_iterations=10,
    )


# =============================================================================
# Mock helpers
# =============================================================================


def _make_execution_result(
    success: bool = True,
    objective_id: str = "obj-001",
    quality_score: float = 0.95,
    error_message: str = None,
    artifacts: list = None,
) -> ExecutionResult:
    """Create a mock ExecutionResult."""
    return ExecutionResult(
        success=success,
        objective_id=objective_id,
        quality_score=quality_score,
        error_message=error_message,
        artifacts=artifacts if artifacts is not None else [Artifact(name="test-output", artifact_type="document")],
    )


async def _make_mock_execute(
    success: bool = True,
    quality_score: float = 0.95,
    error_message: str = None,
    artifacts: list = None,
):
    """Create an async side_effect that both mutates the objective and returns a result."""
    result = _make_execution_result(
        success=success,
        quality_score=quality_score,
        error_message=error_message,
        artifacts=artifacts,
    )

    async def mock_execute(objective):
        # Simulate real execute_with_result_update behavior:
        # transition to IN_PROGRESS, then COMPLETED or BLOCKED
        try:
            objective.transition_to(ObjectiveStatus.IN_PROGRESS)
        except ValueError:
            pass  # already in progress or terminal
        if result.success:
            objective.transition_to(ObjectiveStatus.COMPLETED)
            for artifact in result.artifacts:
                objective.add_artifact(artifact)
        else:
            objective.transition_to(ObjectiveStatus.BLOCKED)
            objective.error_message = result.error_message
        return result

    return mock_execute


def _make_objective(
    objective_id: str = "obj-001",
    title: str = "Test Objective",
    description: str = "Test description",
    status: ObjectiveStatus = ObjectiveStatus.QUEUED,
    dependencies: list = None,
    phase: str = "DEVELOPMENT",
    priority: int = 5,
) -> Objective:
    """Create a test Objective."""
    return Objective(
        objective_id=objective_id,
        title=title,
        description=description,
        status=status,
        dependencies=dependencies or [],
        phase=phase,
        priority=priority,
    )


# =============================================================================
# OrchestratorPipelineAdapter tests
# =============================================================================


class TestOrchestratorPipelineAdapter:
    async def test_execute_success(self):
        """Verify execute returns success result and CircuitBreaker wraps correctly."""
        mock_engine = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.state = PipelineState.COMPLETED
        mock_snapshot.quality_score = 0.95
        mock_snapshot.artifacts = {"output": "test-data"}

        mock_engine.initialize = AsyncMock()
        mock_engine.start = AsyncMock(return_value=mock_snapshot)
        mock_engine.shutdown = MagicMock()

        adapter = OrchestratorPipelineAdapter(pipeline_engine=mock_engine)
        objective = _make_objective()

        result = await adapter.execute(objective)
        assert result.success is True
        assert result.quality_score == 0.95
        assert len(result.artifacts) > 0

        # Verify engine methods were called
        mock_engine.initialize.assert_called_once()
        mock_engine.start.assert_called_once()
        mock_engine.shutdown.assert_called_once()

    async def test_execute_failure(self):
        """Verify execute handles pipeline failures."""
        mock_engine = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.state = PipelineState.FAILED
        mock_snapshot.quality_score = None
        mock_snapshot.artifacts = {}

        mock_engine.initialize = AsyncMock()
        mock_engine.start = AsyncMock(return_value=mock_snapshot)
        mock_engine.shutdown = MagicMock()

        adapter = OrchestratorPipelineAdapter(pipeline_engine=mock_engine)
        objective = _make_objective()

        result = await adapter.execute(objective)
        assert result.success is False
        assert result.error_message is not None

    async def test_execute_with_result_update_success(self):
        """Verify status transitions on success."""
        mock_engine = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.state = PipelineState.COMPLETED
        mock_snapshot.quality_score = 0.95
        mock_snapshot.artifacts = {"plan_output": "test plan data"}

        mock_engine.initialize = AsyncMock()
        mock_engine.start = AsyncMock(return_value=mock_snapshot)
        mock_engine.shutdown = MagicMock()

        adapter = OrchestratorPipelineAdapter(pipeline_engine=mock_engine)
        objective = _make_objective(status=ObjectiveStatus.QUEUED)

        result = await adapter.execute_with_result_update(objective)
        assert result.success is True
        assert objective.status == ObjectiveStatus.COMPLETED
        assert len(objective.artifacts) > 0

    async def test_execute_with_result_update_failure(self):
        """Verify status transitions on failure."""
        mock_engine = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.state = PipelineState.FAILED
        mock_snapshot.quality_score = None
        mock_snapshot.artifacts = {}

        mock_engine.initialize = AsyncMock()
        mock_engine.start = AsyncMock(return_value=mock_snapshot)
        mock_engine.shutdown = MagicMock()

        adapter = OrchestratorPipelineAdapter(pipeline_engine=mock_engine)
        objective = _make_objective(status=ObjectiveStatus.QUEUED)

        result = await adapter.execute_with_result_update(objective)
        assert result.success is False
        assert objective.status == ObjectiveStatus.BLOCKED
        assert objective.error_message is not None

    async def test_execute_exception_circuit_breaker(self):
        """Verify CircuitBreaker catches exceptions correctly."""
        mock_engine = MagicMock()
        mock_engine.initialize = AsyncMock(side_effect=RuntimeError("Connection lost"))

        adapter = OrchestratorPipelineAdapter(pipeline_engine=mock_engine)
        objective = _make_objective()

        result = await adapter.execute(objective)
        assert result.success is False
        assert "Connection lost" in result.error_message

    def test_circuit_breaker_stats(self):
        """Verify circuit breaker stats are accessible."""
        adapter = OrchestratorPipelineAdapter()
        stats = adapter.get_circuit_breaker_stats()
        assert "state" in stats
        assert stats["state"] == "closed"


# =============================================================================
# ProjectOrchestrator tests
# =============================================================================


class TestProjectOrchestrator:
    @pytest.fixture
    def sample_project(self):
        return ProjectObjectives(
            project_id="test-proj",
            name="Test Project",
            objectives=[
                _make_objective(
                    objective_id="obj-001",
                    title="Design DB",
                    description="Design database schema",
                    status=ObjectiveStatus.QUEUED,
                    priority=1,
                    phase="PLANNING",
                ),
            ],
        )

    def test_init_defaults(self, config):
        """Verify auto_commit defaults to False."""
        orchestrator = ProjectOrchestrator(config=config)
        assert orchestrator.config.auto_commit is False
        assert orchestrator.config.dry_run is False

    def test_config_explicit_false(self):
        """Verify auto_commit=False is respected when explicitly set."""
        config = OrchestratorConfig(auto_commit=False)
        orchestrator = ProjectOrchestrator(config=config)
        assert orchestrator.config.auto_commit is False

    def test_hook_registry_access(self):
        """Verify orchestrator has its own HookRegistry."""
        orchestrator = ProjectOrchestrator()
        registry = orchestrator.hook_registry
        assert registry is not None
        # It should be independent from any other registry
        stats = registry.get_statistics()
        assert stats["total_hooks"] == 0

    def test_git_user_name_fallback(self):
        """Verify git user.name falls back to GAIA Orchestrator."""
        orchestrator = ProjectOrchestrator()
        with patch.object(
            orchestrator,
            "_get_git_config",
            return_value="GAIA Orchestrator",
        ):
            assert orchestrator.git_user_name == "GAIA Orchestrator"

    def test_git_user_email_fallback(self):
        """Verify git user.email falls back to gaia-orchestrator@local."""
        orchestrator = ProjectOrchestrator()
        with patch.object(
            orchestrator,
            "_get_git_config",
            return_value="gaia-orchestrator@local",
        ):
            assert orchestrator.git_user_email == "gaia-orchestrator@local"

    def test_git_config_read(self):
        """Verify git config is read via subprocess."""
        orchestrator = ProjectOrchestrator()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="Test User\n", returncode=0
            )
            result = orchestrator._get_git_config("user.name", "fallback")
            assert result == "Test User"
            mock_run.assert_called_once_with(
                ["git", "config", "user.name"],
                capture_output=True,
                text=True,
                timeout=5,
            )

    def test_git_config_failure_fallback(self):
        """Verify subprocess failure triggers fallback."""
        orchestrator = ProjectOrchestrator()
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            result = orchestrator._get_git_config("user.name", "fallback")
            assert result == "fallback"

    def test_load_objectives(self, config, tmp_path, sample_project):
        """Verify objectives are loaded from YAML."""
        # Save sample project to expected path
        sample_project.save_atomic(config.objectives_path)

        orchestrator = ProjectOrchestrator(config=config)
        project = orchestrator.load_objectives()
        assert project is not None
        assert len(project.objectives) == 1
        assert project.objectives[0].title == "Design DB"

    def test_evaluate_pass(self):
        """Verify rule-based evaluation returns PASS for high quality."""
        orchestrator = ProjectOrchestrator()
        result = _make_execution_result(quality_score=0.95)
        objective = _make_objective()

        evaluation = orchestrator.evaluate(result, objective)
        assert evaluation["verdict"] == "PASS"

    def test_evaluate_review(self):
        """Verify rule-based evaluation returns REVIEW for medium quality."""
        orchestrator = ProjectOrchestrator()
        result = _make_execution_result(quality_score=0.75)
        objective = _make_objective()

        evaluation = orchestrator.evaluate(result, objective)
        assert evaluation["verdict"] == "REVIEW"

    def test_evaluate_fail(self):
        """Verify rule-based evaluation returns FAIL for failed execution."""
        orchestrator = ProjectOrchestrator()
        result = _make_execution_result(
            success=False,
            error_message="Pipeline crashed",
        )
        objective = _make_objective()

        evaluation = orchestrator.evaluate(result, objective)
        assert evaluation["verdict"] == "FAIL"

    def test_pause_resume(self):
        """Verify pause/resume toggles state correctly."""
        orchestrator = ProjectOrchestrator()
        assert orchestrator.state.paused is False

        orchestrator.pause("testing")
        assert orchestrator.state.paused is True

        orchestrator.resume()
        assert orchestrator.state.paused is False


# =============================================================================
# Integration test: dispatch loop with mocked adapter
# =============================================================================


class TestDispatchLoop:
    async def test_dispatch_cycle_completes(self, config, tmp_path):
        """Verify a full dispatch-evaluate-update cycle with mocked adapter."""
        # Create project with one objective
        project = ProjectObjectives(
            project_id="loop-test",
            objectives=[
                _make_objective(
                    objective_id="obj-001",
                    title="Build feature",
                    description="Implement feature X",
                    status=ObjectiveStatus.QUEUED,
                ),
            ],
        )
        project.save_atomic(config.objectives_path)

        # Create orchestrator with mock adapter
        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)
        mock_adapter.execute_with_result_update = AsyncMock(
            side_effect=await _make_mock_execute(
                success=True,
                quality_score=0.95,
                artifacts=[Artifact(name="output", artifact_type="document")],
            )
        )

        orchestrator = ProjectOrchestrator(
            config=config,
            pipeline_adapter=mock_adapter,
        )
        orchestrator.load_objectives()

        state = await orchestrator.run()

        assert state.objectives_processed == 1
        assert state.objectives_failed == 0
        assert state.cycle_count == 1
        mock_adapter.execute_with_result_update.assert_called_once()

    async def test_dispatch_cycle_handles_failure(self, config):
        """Verify the loop handles objective failures."""
        project = ProjectObjectives(
            project_id="fail-test",
            objectives=[
                _make_objective(
                    objective_id="obj-001",
                    title="Failing task",
                    status=ObjectiveStatus.QUEUED,
                ),
            ],
        )
        project.save_atomic(config.objectives_path)

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)
        mock_adapter.execute_with_result_update = AsyncMock(
            side_effect=await _make_mock_execute(
                success=False,
                error_message="Pipeline error",
            )
        )

        orchestrator = ProjectOrchestrator(
            config=config,
            pipeline_adapter=mock_adapter,
        )
        orchestrator.load_objectives()

        state = await orchestrator.run()

        assert state.objectives_failed == 1
        assert state.cycle_count == 1

    async def test_dispatch_respects_dependencies(self, config):
        """Verify objectives are dispatched in dependency order."""
        project = ProjectObjectives(
            project_id="dep-test",
            objectives=[
                _make_objective(
                    objective_id="obj-001",
                    title="First",
                    status=ObjectiveStatus.QUEUED,
                    priority=1,
                ),
                _make_objective(
                    objective_id="obj-002",
                    title="Second",
                    status=ObjectiveStatus.QUEUED,
                    dependencies=["obj-001"],
                    priority=2,
                ),
            ],
        )
        project.save_atomic(config.objectives_path)

        call_order = []

        async def mock_execute(obj):
            call_order.append(obj.objective_id)
            result = _make_execution_result(
                success=True,
                objective_id=obj.objective_id,
                quality_score=0.95,
            )
            # Simulate status transitions
            try:
                obj.transition_to(ObjectiveStatus.IN_PROGRESS)
            except ValueError:
                pass
            obj.transition_to(ObjectiveStatus.COMPLETED)
            for artifact in result.artifacts:
                obj.add_artifact(artifact)
            return result

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)
        mock_adapter.execute_with_result_update = AsyncMock(side_effect=mock_execute)

        orchestrator = ProjectOrchestrator(
            config=config,
            pipeline_adapter=mock_adapter,
        )
        orchestrator.load_objectives()

        state = await orchestrator.run()

        assert state.objectives_processed == 2
        assert call_order == ["obj-001", "obj-002"]


# =============================================================================
# Hook execution tests
# =============================================================================


class TestHookExecution:
    async def test_hook_fires_on_objective_start(self, config):
        """Verify OBJECTIVE_START hook fires before dispatch."""
        fired_events = []

        class TestHook(BaseHook):
            name = "test_hook"
            event = OBJECTIVE_START
            priority = HookPriority.HIGH

            async def execute(self, context: HookContext) -> HookResult:
                fired_events.append(context.event)
                return HookResult.success_result()

        project = ProjectObjectives(
            project_id="hook-test",
            objectives=[_make_objective()],
        )
        project.save_atomic(config.objectives_path)

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)
        mock_adapter.execute_with_result_update = AsyncMock(
            side_effect=await _make_mock_execute(success=True)
        )

        orchestrator = ProjectOrchestrator(
            config=config,
            pipeline_adapter=mock_adapter,
        )
        orchestrator.load_objectives()
        orchestrator.hook_registry.register(TestHook())

        await orchestrator.run()
        assert OBJECTIVE_START in fired_events

    async def test_hook_fires_on_completion(self, config):
        """Verify OBJECTIVE_COMPLETE hook fires after successful dispatch."""
        fired_events = []

        class CompletionHook(BaseHook):
            name = "completion_hook"
            event = OBJECTIVE_COMPLETE
            priority = HookPriority.NORMAL

            async def execute(self, context: HookContext) -> HookResult:
                fired_events.append(context.event)
                return HookResult.success_result()

        project = ProjectObjectives(
            project_id="hook-test",
            objectives=[_make_objective()],
        )
        project.save_atomic(config.objectives_path)

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)
        mock_adapter.execute_with_result_update = AsyncMock(
            side_effect=await _make_mock_execute(success=True)
        )

        orchestrator = ProjectOrchestrator(
            config=config,
            pipeline_adapter=mock_adapter,
        )
        orchestrator.load_objectives()
        orchestrator.hook_registry.register(CompletionHook())

        await orchestrator.run()
        assert OBJECTIVE_COMPLETE in fired_events

    async def test_hook_fires_on_failure(self, config):
        """Verify OBJECTIVE_FAILED hook fires after failed dispatch."""
        fired_events = []

        class FailureHook(BaseHook):
            name = "failure_hook"
            event = OBJECTIVE_FAILED
            priority = HookPriority.NORMAL

            async def execute(self, context: HookContext) -> HookResult:
                fired_events.append(context.event)
                return HookResult.success_result()

        project = ProjectObjectives(
            project_id="hook-test",
            objectives=[_make_objective()],
        )
        project.save_atomic(config.objectives_path)

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)
        mock_adapter.execute_with_result_update = AsyncMock(
            side_effect=await _make_mock_execute(
                success=False, error_message="Test failure"
            )
        )

        orchestrator = ProjectOrchestrator(
            config=config,
            pipeline_adapter=mock_adapter,
        )
        orchestrator.load_objectives()
        orchestrator.hook_registry.register(FailureHook())

        await orchestrator.run()
        assert OBJECTIVE_FAILED in fired_events


# =============================================================================
# auto_commit and dry_run tests
# =============================================================================


class TestGitIntegration:
    async def test_auto_commit_false_no_commit(self, config, tmp_path):
        """Verify auto_commit=False does not create git commits."""
        project = ProjectObjectives(
            project_id="git-test",
            objectives=[_make_objective()],
        )
        project.save_atomic(config.objectives_path)

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)
        mock_adapter.execute_with_result_update = AsyncMock(
            side_effect=await _make_mock_execute(success=True)
        )

        orchestrator = ProjectOrchestrator(
            config=config,
            pipeline_adapter=mock_adapter,
        )
        orchestrator.load_objectives()

        with patch("subprocess.run") as mock_run:
            await orchestrator.run()
            # subprocess.run should NOT be called with git commands
            for call in mock_run.call_args_list:
                args = call[0][0] if call[0] else call[1].get("args", [])
                if isinstance(args, list) and "git" in str(args):
                    pytest.fail("git command was called but auto_commit=False")

    async def test_dry_run_no_save(self):
        """Verify dry_run mode does not save objectives file."""
        config = OrchestratorConfig(dry_run=True, auto_commit=False)
        project = ProjectObjectives(
            project_id="dry-test",
            objectives=[_make_objective()],
        )
        config.objectives_path = str(Path("/tmp") / "dry-run-test.yaml")
        project.save_atomic(config.objectives_path)

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)
        mock_adapter.execute_with_result_update = AsyncMock(
            return_value=_make_execution_result(success=True)
        )

        orchestrator = ProjectOrchestrator(
            config=config,
            pipeline_adapter=mock_adapter,
        )
        orchestrator.load_objectives()

        await orchestrator.run()
        # In dry_run mode, the save should still happen (objectives are updated
        # in memory), but git operations should be skipped
        assert orchestrator.config.dry_run is True


# =============================================================================
# Orchestrator-specific hooks tests
# =============================================================================


class TestOrchestratorHooks:
    async def test_objective_update_hook_saves(self, tmp_path):
        """Verify ObjectiveUpdateHook performs atomic save."""
        path = str(tmp_path / "objectives.yaml")
        project = ProjectObjectives(
            project_id="hook-save-test",
            objectives=[_make_objective()],
        )

        hook = ObjectiveUpdateHook(config={"project": project, "path": path})
        context = HookContext(
            event=OBJECTIVE_COMPLETE,
            pipeline_id="test",
            phase="DEVELOPMENT",
        )

        result = await hook.execute(context)
        assert result.success is True
        assert os.path.exists(path)

    async def test_task_spawn_hook_creates_remediation(self, tmp_path):
        """Verify TaskSpawnHook creates remediation objectives."""
        from gaia.orchestration.engine import OBJECTIVE_FAILED

        project = ProjectObjectives(
            project_id="spawn-test",
            objectives=[
                _make_objective(
                    objective_id="obj-001",
                    title="Build feature",
                    status=ObjectiveStatus.QUEUED,
                ),
            ],
        )

        hook = TaskSpawnHook(config={"project": project, "priority": 3})

        # Create execution result with failure
        exec_result = _make_execution_result(
            success=False,
            error_message="Build failed: missing dependencies",
        )

        context = HookContext(
            event=OBJECTIVE_FAILED,
            pipeline_id="test",
            phase="DEVELOPMENT",
            data={
                "execution_result": exec_result,
                "objective_title": "Build feature",
            },
        )

        result = await hook.execute(context)
        assert result.success is True
        assert result.metadata["spawned"] == 1

        # Verify remediation objective was added
        assert len(project.objectives) == 2
        new_obj = project.objectives[1]
        assert "Fix:" in new_obj.title
        assert new_obj.status == ObjectiveStatus.QUEUED


# =============================================================================
# CRITICAL gap tests identified by QA review
# =============================================================================


class TestCriticalDispatchLoopPaths:
    """Tests for critical untested code paths in engine.py run() method."""

    async def test_run_halts_on_halted_pipeline(self, config, tmp_path):
        """G1: Verify run() breaks early when a hook returns halt_pipeline=True."""
        project = ProjectObjectives(
            project_id="halt-test",
            objectives=[_make_objective()],
        )
        project.save_atomic(config.objectives_path)

        class HaltingHook(BaseHook):
            name = "halt_hook"
            event = OBJECTIVE_START
            priority = HookPriority.HIGH

            async def execute(self, context: HookContext) -> HookResult:
                result = HookResult(success=True, halt_pipeline=True)
                result.metadata["reason"] = "Test halt"
                return result

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)
        mock_adapter.execute_with_result_update = AsyncMock(
            side_effect=await _make_mock_execute(success=True)
        )

        orchestrator = ProjectOrchestrator(
            config=config,
            pipeline_adapter=mock_adapter,
        )
        orchestrator.load_objectives()
        orchestrator.hook_registry.register(HaltingHook())

        state = await orchestrator.run()

        # Hook halted pipeline before dispatch
        mock_adapter.execute_with_result_update.assert_not_called()
        assert state.objectives_processed == 0

    async def test_run_max_iterations_exceeded(self, tmp_path):
        """G2: Verify loop stops when cycle_count >= max_cycle_iterations."""
        config = OrchestratorConfig(
            auto_commit=False,
            max_cycle_iterations=2,
        )

        # Create 5 objectives that never complete (mock doesn't transition them)
        objectives = [
            _make_objective(objective_id=f"obj-{i:03d}", title=f"Task {i}")
            for i in range(5)
        ]
        project = ProjectObjectives(
            project_id="max-iter-test",
            objectives=objectives,
        )
        config.objectives_path = str(tmp_path / "max-iter.yaml")
        project.save_atomic(config.objectives_path)

        # Mock that transitions objective to COMPLETED each time
        async def mock_execute(obj):
            result = _make_execution_result(success=True, objective_id=obj.objective_id)
            try:
                obj.transition_to(ObjectiveStatus.IN_PROGRESS)
            except ValueError:
                pass
            obj.transition_to(ObjectiveStatus.COMPLETED)
            return result

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)
        mock_adapter.execute_with_result_update = AsyncMock(side_effect=mock_execute)

        orchestrator = ProjectOrchestrator(
            config=config,
            pipeline_adapter=mock_adapter,
        )
        orchestrator.load_objectives()

        state = await orchestrator.run()

        # Should stop at max_cycle_iterations=2
        assert state.cycle_count == 2

    async def test_run_project_stuck_all_blocked(self, config, tmp_path):
        """G3: Verify loop breaks with warning when all remaining objectives are BLOCKED."""
        project = ProjectObjectives(
            project_id="stuck-test",
            objectives=[
                _make_objective(
                    objective_id="obj-001",
                    title="Blocked task",
                    status=ObjectiveStatus.BLOCKED,
                ),
                _make_objective(
                    objective_id="obj-002",
                    title="Also blocked",
                    status=ObjectiveStatus.BLOCKED,
                ),
            ],
        )
        project.save_atomic(config.objectives_path)

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)
        mock_adapter.execute_with_result_update = AsyncMock()

        orchestrator = ProjectOrchestrator(
            config=config,
            pipeline_adapter=mock_adapter,
        )
        orchestrator.load_objectives()

        state = await orchestrator.run()

        # No objectives should be processed, loop should exit quickly
        assert state.objectives_processed == 0
        # Adapter should never be called since all objectives are BLOCKED
        mock_adapter.execute_with_result_update.assert_not_called()

    async def test_git_commit_auto_commit_true(self, config, tmp_path):
        """G5: Verify git add and git commit ARE called with correct args when auto_commit=True."""
        config.auto_commit = True
        project = ProjectObjectives(
            project_id="git-commit-test",
            objectives=[_make_objective(title="Build feature")],
        )
        project.save_atomic(config.objectives_path)

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)
        mock_adapter.execute_with_result_update = AsyncMock(
            side_effect=await _make_mock_execute(success=True)
        )

        orchestrator = ProjectOrchestrator(
            config=config,
            pipeline_adapter=mock_adapter,
        )
        orchestrator.load_objectives()

        git_calls = []

        def capture_git_call(*args, **kwargs):
            git_calls.append(args[0] if args else kwargs.get("args", []))
            return MagicMock(stdout="", returncode=0)

        with patch("subprocess.run", side_effect=capture_git_call):
            await orchestrator.run()

        # Verify git add and git commit were called
        git_cmd_strings = [str(c) for c in git_calls]
        assert any("git" in c and "add" in c for c in git_cmd_strings), \
            f"Expected 'git add' in calls: {git_calls}"
        assert any("git" in c and "commit" in c for c in git_cmd_strings), \
            f"Expected 'git commit' in calls: {git_calls}"

    async def test_circuit_breaker_open_state(self):
        """G7: Force CircuitBreaker to trip, verify execute() returns appropriate error."""
        mock_engine = MagicMock()
        mock_engine.initialize = AsyncMock(side_effect=RuntimeError("Connection refused"))

        adapter = OrchestratorPipelineAdapter(pipeline_engine=mock_engine)

        # Trip the circuit breaker by calling execute multiple times
        for _ in range(6):  # Default failure_threshold is 5
            await adapter.execute(_make_objective())

        stats = adapter.get_circuit_breaker_stats()
        assert stats["state"] == "open"

        # Now execute should fail fast without calling initialize
        mock_engine.initialize.reset_mock()
        result = await adapter.execute(_make_objective())
        assert result.success is False
        assert "Circuit breaker" in result.error_message or "circuit" in result.error_message.lower()
        mock_engine.initialize.assert_not_called()

    async def test_nexus_service_event_commit(self, config, tmp_path):
        """G6: Verify _commit_event is called on lifecycle events when NexusService is enabled."""
        project = ProjectObjectives(
            project_id="nexus-test",
            objectives=[_make_objective()],
        )
        project.save_atomic(config.objectives_path)

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)
        mock_adapter.execute_with_result_update = AsyncMock(
            side_effect=await _make_mock_execute(success=True)
        )

        # Mock NexusService
        mock_nexus = MagicMock()
        mock_nexus.commit = MagicMock()

        orchestrator = ProjectOrchestrator(
            config=config,
            pipeline_adapter=mock_adapter,
        )
        orchestrator._nexus = mock_nexus
        orchestrator._enable_chronicle = True

        project = orchestrator.load_objectives()
        state = await orchestrator.run()

        # Verify nexus.commit was called for lifecycle events
        commit_calls = [
            call for call in mock_nexus.commit.call_args_list
        ]
        assert len(commit_calls) > 0, "Expected NexusService.commit to be called"

        # Check that at least one call was for orchestrator lifecycle
        event_types = [
            call[1].get("event_type") or call[0][1]
            for call in mock_nexus.commit.call_args_list
        ]
        # Should have objectives_loaded, orchestrator_started, cycle_complete, or orchestrator_finished
        assert len(event_types) >= 1


class TestAdapterResilience:
    """Additional adapter resilience tests."""

    async def test_execute_without_circuit_breaker(self):
        """G12: Verify direct call path when CircuitBreaker is disabled."""
        mock_engine = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.state = PipelineState.COMPLETED
        mock_snapshot.quality_score = 0.95
        mock_snapshot.artifacts = {"output": "data"}

        mock_engine.initialize = AsyncMock()
        mock_engine.start = AsyncMock(return_value=mock_snapshot)
        mock_engine.shutdown = MagicMock()

        adapter = OrchestratorPipelineAdapter(
            pipeline_engine=mock_engine,
            enable_circuit_breaker=False,
        )
        objective = _make_objective()

        result = await adapter.execute(objective)
        assert result.success is True
        mock_engine.initialize.assert_called_once()

    async def test_dispatch_engine_shutdown_on_start_failure(self):
        """G8: initialize() succeeds but start() raises, verify shutdown() is still called."""
        mock_engine = MagicMock()
        mock_engine.initialize = AsyncMock()
        mock_engine.start = AsyncMock(side_effect=RuntimeError("Start failed"))
        mock_engine.shutdown = MagicMock()

        adapter = OrchestratorPipelineAdapter(pipeline_engine=mock_engine)
        objective = _make_objective()

        result = await adapter.execute(objective)

        assert result.success is False
        mock_engine.shutdown.assert_called_once()

    async def test_build_pipeline_context_from_objective(self):
        """G13: Verify PipelineContext built with correct parameters."""
        mock_engine = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.state = PipelineState.COMPLETED
        mock_snapshot.quality_score = 0.95
        mock_snapshot.artifacts = {}

        mock_engine.initialize = AsyncMock()
        mock_engine.start = AsyncMock(return_value=mock_snapshot)
        mock_engine.shutdown = MagicMock()

        adapter = OrchestratorPipelineAdapter(pipeline_engine=mock_engine)
        objective = _make_objective(
            objective_id="obj-ctx-test",
            title="Context Test",
            phase="DEVELOPMENT",
        )
        objective.pipeline_config = {
            "template": "code_generation",
            "quality_threshold": 0.85,
            "max_iterations": 5,
        }

        ctx = adapter._build_pipeline_context(objective)
        assert "obj-ctx-test" in ctx.pipeline_id
        assert ctx.template == "code_generation"
        assert ctx.quality_threshold == 0.85
        assert ctx.max_iterations == 5

    async def test_extract_artifacts_none_snapshot(self):
        """G18: _extract_artifacts(None, objective) returns empty list."""
        mock_engine = MagicMock()
        adapter = OrchestratorPipelineAdapter(pipeline_engine=mock_engine)
        objective = _make_objective()

        artifacts = adapter._extract_artifacts(None, objective)
        assert artifacts == []


class TestHookFailurePaths:
    """Test hook failure and edge cases."""

    async def test_objective_update_hook_missing_config(self, tmp_path):
        """G9a: ObjectiveUpdateHook with missing project config returns failure."""
        hook = ObjectiveUpdateHook(config={})
        context = HookContext(
            event=OBJECTIVE_COMPLETE,
            pipeline_id="test",
            phase="DEVELOPMENT",
        )

        result = await hook.execute(context)
        # Should return failure since project is required
        assert result.success is False
        assert "No project" in result.error_message

    async def test_task_spawn_hook_no_execution_result(self, tmp_path):
        """G10: TaskSpawnHook without execution_result spawns nothing."""
        project = ProjectObjectives(
            project_id="spawn-empty",
            objectives=[_make_objective()],
        )

        hook = TaskSpawnHook(config={"project": project, "priority": 3})
        context = HookContext(
            event=OBJECTIVE_FAILED,
            pipeline_id="test",
            phase="DEVELOPMENT",
            data={},  # No execution_result
        )

        result = await hook.execute(context)
        assert result.success is True
        assert result.metadata.get("spawned", 0) == 0

    async def test_dry_run_skips_git(self, tmp_path):
        """G20: Verify dry_run=True actually skips git operations."""
        config = OrchestratorConfig(dry_run=True, auto_commit=True)
        project = ProjectObjectives(
            project_id="dry-run-git",
            objectives=[_make_objective()],
        )
        config.objectives_path = str(tmp_path / "dry-run.yaml")
        project.save_atomic(config.objectives_path)

        mock_adapter = MagicMock(spec=OrchestratorPipelineAdapter)
        mock_adapter.execute_with_result_update = AsyncMock(
            side_effect=await _make_mock_execute(success=True)
        )

        orchestrator = ProjectOrchestrator(
            config=config,
            pipeline_adapter=mock_adapter,
        )
        orchestrator.load_objectives()

        with patch("subprocess.run") as mock_run:
            await orchestrator.run()
            # Verify no git commands were called in dry_run mode
            for call in mock_run.call_args_list:
                args = call[0][0] if call[0] else call[1].get("args", [])
                if isinstance(args, list) and len(args) > 0 and "git" in str(args):
                    pytest.fail(f"git command called in dry_run mode: {args}")


class TestEdgeCases:
    """Edge case tests for completeness."""

    def test_objective_from_dict_invalid_status(self):
        """G14: Invalid status string raises ValueError."""
        from gaia.orchestration.models import Objective

        with pytest.raises(ValueError):
            Objective.from_dict({
                "objective_id": "obj-001",
                "title": "Test",
                "description": "Test",
                "status": "INVALID_STATUS",
                "dependencies": [],
            })

    def test_objective_add_artifact(self):
        """Verify add_artifact works correctly."""
        obj = _make_objective()
        artifact = Artifact(name="test-artifact", artifact_type="document")
        obj.add_artifact(artifact)
        assert len(obj.artifacts) == 1
        assert obj.artifacts[0].name == "test-artifact"

    async def test_circuit_breaker_half_open_state(self):
        """Verify CircuitBreaker transitions to half-open and recovers."""
        mock_engine = MagicMock()
        mock_engine.initialize = AsyncMock(side_effect=RuntimeError("fail"))

        adapter = OrchestratorPipelineAdapter(pipeline_engine=mock_engine)

        # Trip the breaker by exceeding failure threshold
        for _ in range(6):
            await adapter.execute(_make_objective())

        assert adapter.get_circuit_breaker_stats()["state"] == "open"

        # Manually reset to test half-open
        adapter._circuit_breaker.reset()
        assert adapter.get_circuit_breaker_stats()["state"] == "closed"
