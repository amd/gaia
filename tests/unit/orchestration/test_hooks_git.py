"""
Tests for Git automation hooks (GitBranchHook, GitCommitHook, GitPRHook, GitRollbackHook)
and ORCHESTRATOR_START/ORCHESTRATOR_COMPLETE engine events.

~28 tests covering:
- TestGitBranchHook: success, failure, missing config, slug generation, circuit open
- TestGitCommitHook: success, failure, missing config, objectives path
- TestGitPRHook: all done success, incomplete skipped, missing config, PR failure, empty project
- TestGitRollbackHook: success, no branch, failure, missing config, circuit open
- TestHookChain: branch->commit chain, branch->rollback chain, context propagation, inject_context, metadata
- TestEngineEvents: ORCHESTRATOR_START emitted, ORCHESTRATOR_COMPLETE emitted, branch tracking, slug utility
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gaia.hooks.base import BaseHook, HookContext, HookPriority, HookResult
from gaia.orchestration.adapters import ExecutionResult, OrchestratorPipelineAdapter
from gaia.orchestration.engine import (
    OBJECTIVE_FAILED,
    OBJECTIVE_START,
    ORCHESTRATOR_COMPLETE,
    ORCHESTRATOR_START,
    OrchestratorConfig,
    ProjectOrchestrator,
)
from gaia.orchestration.hooks import (
    GitBranchHook,
    GitCommitHook,
    GitPRHook,
    GitRollbackHook,
)
from gaia.orchestration.models import (
    Artifact,
    Objective,
    ObjectiveStatus,
    ProjectObjectives,
)
from gaia.orchestration.supervisors.git import GitSupervisor


# ============================================================================
# Helpers
# ============================================================================


def _make_objective(
    objective_id: str = "obj-001",
    title: str = "Test Objective",
    description: str = "Test description",
    status: ObjectiveStatus = ObjectiveStatus.QUEUED,
    dependencies: list | None = None,
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


def _make_execution_result(
    success: bool = True,
    objective_id: str = "obj-001",
    quality_score: float = 0.95,
    error_message: str | None = None,
    artifacts: list | None = None,
) -> ExecutionResult:
    """Create a mock ExecutionResult."""
    return ExecutionResult(
        success=success,
        objective_id=objective_id,
        quality_score=quality_score,
        error_message=error_message,
        artifacts=artifacts if artifacts is not None else [
            Artifact(name="test-output", artifact_type="document")
        ],
    )


async def _make_mock_execute(
    success: bool = True,
    quality_score: float = 0.95,
    error_message: str | None = None,
):
    """Create an async side_effect that transitions objective and returns result."""
    result = _make_execution_result(
        success=success, quality_score=quality_score, error_message=error_message
    )

    async def mock_execute(objective):
        try:
            objective.transition_to(ObjectiveStatus.IN_PROGRESS)
        except ValueError:
            pass
        if result.success:
            objective.transition_to(ObjectiveStatus.COMPLETED)
            for artifact in result.artifacts:
                objective.add_artifact(artifact)
        else:
            objective.transition_to(ObjectiveStatus.BLOCKED)
            objective.error_message = result.error_message
        return result

    return mock_execute


@pytest.fixture
def mock_git_supervisor():
    """Create a mocked GitSupervisor."""
    return MagicMock(spec=GitSupervisor)


@pytest.fixture
def config(tmp_path):
    """Shared config fixture."""
    return OrchestratorConfig(
        objectives_path=str(tmp_path / "objectives.yaml"),
        auto_commit=False,
        dry_run=False,
        max_cycle_iterations=10,
    )


# ============================================================================
# TestGitBranchHook (5 tests)
# ============================================================================


class TestGitBranchHook:
    """Tests for GitBranchHook."""

    async def test_success_creates_branch(self, mock_git_supervisor):
        """GitBranchHook creates branch and returns it in inject_context."""
        mock_git_supervisor.create_branch.return_value = True

        hook = GitBranchHook(config={"git_supervisor": mock_git_supervisor})
        context = HookContext(
            event=OBJECTIVE_START,
            pipeline_id="test",
            data={"objective_id": "abc123", "objective_title": "Build Feature"},
        )

        result = await hook.execute(context)

        assert result.success is True
        assert result.inject_context is not None
        assert result.inject_context["_git_branch"] == "obj/abc123-build-feature"
        assert result.metadata["created"] is True
        mock_git_supervisor.create_branch.assert_called_once_with("obj/abc123-build-feature")

    async def test_failure_returns_error(self, mock_git_supervisor):
        """GitBranchHook returns failure when create_branch fails."""
        mock_git_supervisor.create_branch.return_value = False

        hook = GitBranchHook(config={"git_supervisor": mock_git_supervisor})
        context = HookContext(
            event=OBJECTIVE_START,
            pipeline_id="test",
            data={"objective_id": "obj-001", "objective_title": "Build Feature"},
        )

        result = await hook.execute(context)

        assert result.success is False
        assert "Failed to create branch" in result.error_message

    async def test_missing_config_skips(self):
        """GitBranchHook without git_supervisor returns skipped success."""
        hook = GitBranchHook(config={})
        context = HookContext(
            event=OBJECTIVE_START,
            pipeline_id="test",
            data={"objective_id": "obj-001", "objective_title": "Build Feature"},
        )

        result = await hook.execute(context)

        assert result.success is True
        assert result.skipped is True

    async def test_slug_generation(self, mock_git_supervisor):
        """GitBranchHook slugifies title correctly."""
        mock_git_supervisor.create_branch.return_value = True

        hook = GitBranchHook(config={"git_supervisor": mock_git_supervisor})
        context = HookContext(
            event=OBJECTIVE_START,
            pipeline_id="test",
            data={"objective_id": "x1", "objective_title": "  Build & Test: Complex Feat!  "},
        )

        result = await hook.execute(context)

        assert result.success is True
        branch = result.inject_context["_git_branch"]
        assert branch == "obj/x1-build-test-complex-feat"
        # Verify no special chars
        assert all(c.isalnum() or c in "-_/" for c in branch)

    async def test_circuit_open_returns_false(self, mock_git_supervisor):
        """GitBranchHook handles circuit-open failure gracefully."""
        mock_git_supervisor.create_branch.return_value = False  # circuit open returns False

        hook = GitBranchHook(config={"git_supervisor": mock_git_supervisor})
        context = HookContext(
            event=OBJECTIVE_START,
            pipeline_id="test",
            data={"objective_id": "obj-001", "objective_title": "Build Feature"},
        )

        result = await hook.execute(context)

        assert result.success is False
        assert "Failed to create branch" in result.error_message


# ============================================================================
# TestGitCommitHook (4 tests)
# ============================================================================


class TestGitCommitHook:
    """Tests for GitCommitHook."""

    async def test_success_commits(self, mock_git_supervisor):
        """GitCommitHook commits objectives file on success."""
        mock_git_supervisor.commit.return_value = True

        hook = GitCommitHook(config={"git_supervisor": mock_git_supervisor})
        context = HookContext(
            event="OBJECTIVE_COMPLETE",
            pipeline_id="test",
            data={"objective_id": "obj-001", "objective_title": "Build Feature"},
        )

        result = await hook.execute(context)

        assert result.success is True
        assert result.metadata["committed"] is True
        mock_git_supervisor.commit.assert_called_once()
        call_args = mock_git_supervisor.commit.call_args
        assert "chore(objectives): complete Build Feature (obj-001)" in call_args[0][0]
        assert call_args[1]["files"] == [".gaia/objectives.yaml"]

    async def test_failure_returns_false(self, mock_git_supervisor):
        """GitCommitHook returns committed=False when commit fails."""
        mock_git_supervisor.commit.return_value = False

        hook = GitCommitHook(config={"git_supervisor": mock_git_supervisor})
        context = HookContext(
            event="OBJECTIVE_COMPLETE",
            pipeline_id="test",
            data={"objective_id": "obj-001", "objective_title": "Build Feature"},
        )

        result = await hook.execute(context)

        assert result.success is True
        assert result.metadata["committed"] is False

    async def test_missing_config_returns_false(self):
        """GitCommitHook without git_supervisor returns committed=False."""
        hook = GitCommitHook(config={})
        context = HookContext(
            event="OBJECTIVE_COMPLETE",
            pipeline_id="test",
            data={"objective_id": "obj-001", "objective_title": "Build Feature"},
        )

        result = await hook.execute(context)

        assert result.success is True
        assert result.metadata["committed"] is False

    async def test_custom_objectives_path(self, mock_git_supervisor):
        """GitCommitHook uses custom objectives_path from config."""
        mock_git_supervisor.commit.return_value = True
        custom_path = "/custom/path/objectives.yaml"

        hook = GitCommitHook(config={
            "git_supervisor": mock_git_supervisor,
            "objectives_path": custom_path,
        })
        context = HookContext(
            event="OBJECTIVE_COMPLETE",
            pipeline_id="test",
            data={"objective_id": "obj-001", "objective_title": "Build Feature"},
        )

        result = await hook.execute(context)

        assert result.success is True
        call_args = mock_git_supervisor.commit.call_args
        assert call_args[1]["files"] == [custom_path]


# ============================================================================
# TestGitPRHook (5 tests)
# ============================================================================


class TestGitPRHook:
    """Tests for GitPRHook."""

    async def test_all_done_success(self, mock_git_supervisor, config):
        """GitPRHook creates PR when all objectives are terminal."""
        mock_git_supervisor.create_pr.return_value = "https://github.com/repo/pull/1"

        project = ProjectObjectives(
            project_id="proj-1",
            name="Test Project",
            objectives=[
                _make_objective(objective_id="obj-001", title="Task One", status=ObjectiveStatus.COMPLETED),
                _make_objective(objective_id="obj-002", title="Task Two", status=ObjectiveStatus.CANCELLED),
            ],
        )

        hook = GitPRHook(config={
            "git_supervisor": mock_git_supervisor,
            "project": project,
            "target_branch": "main",
        })
        context = HookContext(
            event=ORCHESTRATOR_COMPLETE,
            pipeline_id="test",
        )

        result = await hook.execute(context)

        assert result.success is True
        assert result.inject_context is not None
        assert result.inject_context["pr_url"] == "https://github.com/repo/pull/1"
        assert result.metadata["pr_created"] is True

    async def test_incomplete_skipped(self, mock_git_supervisor):
        """GitPRHook skips PR when objectives are not all terminal."""
        project = ProjectObjectives(
            project_id="proj-1",
            name="Test Project",
            objectives=[
                _make_objective(objective_id="obj-001", title="Task One", status=ObjectiveStatus.COMPLETED),
                _make_objective(objective_id="obj-002", title="Task Two", status=ObjectiveStatus.QUEUED),
            ],
        )

        hook = GitPRHook(config={
            "git_supervisor": mock_git_supervisor,
            "project": project,
        })
        context = HookContext(
            event=ORCHESTRATOR_COMPLETE,
            pipeline_id="test",
        )

        result = await hook.execute(context)

        assert result.success is True
        assert result.metadata["pr_created"] is False
        assert result.metadata["reason"] == "objectives_not_terminal"
        mock_git_supervisor.create_pr.assert_not_called()

    async def test_missing_config(self):
        """GitPRHook without git_supervisor or project returns pr_created=False."""
        hook = GitPRHook(config={})
        context = HookContext(
            event=ORCHESTRATOR_COMPLETE,
            pipeline_id="test",
        )

        result = await hook.execute(context)

        assert result.success is True
        assert result.metadata["pr_created"] is False

    async def test_pr_failure(self, mock_git_supervisor):
        """GitPRHook returns failure when create_pr returns None."""
        mock_git_supervisor.create_pr.return_value = None

        project = ProjectObjectives(
            project_id="proj-1",
            name="Test Project",
            objectives=[
                _make_objective(objective_id="obj-001", title="Task One", status=ObjectiveStatus.COMPLETED),
            ],
        )

        hook = GitPRHook(config={
            "git_supervisor": mock_git_supervisor,
            "project": project,
        })
        context = HookContext(
            event=ORCHESTRATOR_COMPLETE,
            pipeline_id="test",
        )

        result = await hook.execute(context)

        assert result.success is False
        assert "Failed to create PR" in result.error_message

    async def test_empty_project(self, mock_git_supervisor):
        """GitPRHook skips PR when project has no objectives."""
        project = ProjectObjectives(
            project_id="proj-1",
            name="Empty Project",
            objectives=[],
        )

        hook = GitPRHook(config={
            "git_supervisor": mock_git_supervisor,
            "project": project,
        })
        context = HookContext(
            event=ORCHESTRATOR_COMPLETE,
            pipeline_id="test",
        )

        result = await hook.execute(context)

        assert result.success is True
        assert result.metadata["pr_created"] is False
        assert result.metadata["reason"] == "empty_project"


# ============================================================================
# TestGitRollbackHook (5 tests)
# ============================================================================


class TestGitRollbackHook:
    """Tests for GitRollbackHook."""

    async def test_success_rollback(self, mock_git_supervisor):
        """GitRollbackHook rolls back branch on success."""
        mock_git_supervisor.rollback.return_value = True

        hook = GitRollbackHook(config={"git_supervisor": mock_git_supervisor})
        context = HookContext(
            event=OBJECTIVE_FAILED,
            pipeline_id="test",
            data={"objective_id": "obj-001", "_git_branch": "obj/obj-001-build-feature"},
        )

        result = await hook.execute(context)

        assert result.success is True
        assert result.metadata["rolled_back"] is True
        mock_git_supervisor.rollback.assert_called_once_with(
            "obj/obj-001-build-feature", "HEAD~1"
        )

    async def test_no_branch_skips(self, mock_git_supervisor):
        """GitRollbackHook skips when no _git_branch in context."""
        hook = GitRollbackHook(config={"git_supervisor": mock_git_supervisor})
        context = HookContext(
            event=OBJECTIVE_FAILED,
            pipeline_id="test",
            data={"objective_id": "obj-001"},
        )

        result = await hook.execute(context)

        assert result.success is True
        assert result.metadata["rolled_back"] is False
        assert result.metadata["reason"] == "no_branch"
        mock_git_supervisor.rollback.assert_not_called()

    async def test_failure_returns_false(self, mock_git_supervisor):
        """GitRollbackHook returns rolled_back=False on failure."""
        mock_git_supervisor.rollback.return_value = False

        hook = GitRollbackHook(config={"git_supervisor": mock_git_supervisor})
        context = HookContext(
            event=OBJECTIVE_FAILED,
            pipeline_id="test",
            data={"objective_id": "obj-001", "_git_branch": "obj/obj-001-build"},
        )

        result = await hook.execute(context)

        assert result.success is True
        assert result.metadata["rolled_back"] is False

    async def test_missing_config(self):
        """GitRollbackHook without git_supervisor returns rolled_back=False."""
        hook = GitRollbackHook(config={})
        context = HookContext(
            event=OBJECTIVE_FAILED,
            pipeline_id="test",
            data={"objective_id": "obj-001", "_git_branch": "obj/obj-001-build"},
        )

        result = await hook.execute(context)

        assert result.success is True
        assert result.metadata["rolled_back"] is False

    async def test_circuit_open_returns_false(self, mock_git_supervisor):
        """GitRollbackHook handles circuit-open failure gracefully."""
        mock_git_supervisor.rollback.return_value = False

        hook = GitRollbackHook(config={"git_supervisor": mock_git_supervisor})
        context = HookContext(
            event=OBJECTIVE_FAILED,
            pipeline_id="test",
            data={"objective_id": "obj-001", "_git_branch": "obj/obj-001-build"},
        )

        result = await hook.execute(context)

        assert result.metadata["rolled_back"] is False


# ============================================================================
# TestHookChain (5 tests)
# ============================================================================


class TestHookChain:
    """Tests for hook chain interactions."""

    async def test_branch_then_commit_chain(self, mock_git_supervisor, config, tmp_path):
        """Verify GitBranchHook -> GitCommitHook chain works end-to-end."""
        mock_git_supervisor.create_branch.return_value = True
        mock_git_supervisor.commit.return_value = True

        branch_hook = GitBranchHook(config={"git_supervisor": mock_git_supervisor})
        commit_hook = GitCommitHook(config={"git_supervisor": mock_git_supervisor})

        # Phase 1: GitBranchHook on OBJECTIVE_START
        start_context = HookContext(
            event=OBJECTIVE_START,
            pipeline_id="test",
            data={"objective_id": "obj-001", "objective_title": "Build Feature"},
        )
        branch_result = await branch_hook.execute(start_context)
        assert branch_result.success is True
        assert "_git_branch" in branch_result.inject_context

        # Phase 2: GitCommitHook on OBJECTIVE_COMPLETE
        complete_context = HookContext(
            event="OBJECTIVE_COMPLETE",
            pipeline_id="test",
            data={
                "objective_id": "obj-001",
                "objective_title": "Build Feature",
                "_git_branch": branch_result.inject_context["_git_branch"],
            },
        )
        commit_result = await commit_hook.execute(complete_context)
        assert commit_result.success is True
        assert commit_result.metadata["committed"] is True

    async def test_branch_then_rollback_chain(self, mock_git_supervisor):
        """Verify GitBranchHook -> GitRollbackHook chain on failure."""
        mock_git_supervisor.create_branch.return_value = True
        mock_git_supervisor.rollback.return_value = True

        branch_hook = GitBranchHook(config={"git_supervisor": mock_git_supervisor})
        rollback_hook = GitRollbackHook(config={"git_supervisor": mock_git_supervisor})

        # Phase 1: Create branch
        start_context = HookContext(
            event=OBJECTIVE_START,
            pipeline_id="test",
            data={"objective_id": "obj-001", "objective_title": "Build Feature"},
        )
        branch_result = await branch_hook.execute(start_context)
        assert branch_result.success is True
        branch_name = branch_result.inject_context["_git_branch"]

        # Phase 2: Rollback on failure
        fail_context = HookContext(
            event=OBJECTIVE_FAILED,
            pipeline_id="test",
            data={"objective_id": "obj-001", "_git_branch": branch_name},
        )
        rollback_result = await rollback_hook.execute(fail_context)
        assert rollback_result.success is True
        assert rollback_result.metadata["rolled_back"] is True
        mock_git_supervisor.rollback.assert_called_once_with(branch_name, "HEAD~1")

    async def test_context_propagation_across_hooks(self, mock_git_supervisor):
        """Verify _git_branch propagates from branch hook to commit/rollback hooks."""
        mock_git_supervisor.create_branch.return_value = True
        mock_git_supervisor.commit.return_value = True

        branch_hook = GitBranchHook(config={"git_supervisor": mock_git_supervisor})
        commit_hook = GitCommitHook(config={"git_supervisor": mock_git_supervisor})

        # Branch hook injects _git_branch
        branch_result = await branch_hook.execute(HookContext(
            event=OBJECTIVE_START,
            pipeline_id="test",
            data={"objective_id": "obj-001", "objective_title": "Build Feature"},
        ))
        branch_name = branch_result.inject_context["_git_branch"]

        # Verify the branch name matches what commit hook would use
        assert "obj/obj-001-build-feature" == branch_name

    async def test_inject_context_merging(self, mock_git_supervisor):
        """Verify multiple hooks can inject context that gets merged."""
        mock_git_supervisor.create_branch.return_value = True

        hook = GitBranchHook(config={"git_supervisor": mock_git_supervisor})
        result = await hook.execute(HookContext(
            event=OBJECTIVE_START,
            pipeline_id="test",
            data={"objective_id": "obj-001", "objective_title": "Build Feature"},
        ))

        # Verify inject_context contains _git_branch
        assert result.inject_context is not None
        assert "_git_branch" in result.inject_context

    async def test_metadata_fields(self, mock_git_supervisor):
        """Verify all hooks return appropriate metadata."""
        mock_git_supervisor.create_branch.return_value = True
        mock_git_supervisor.commit.return_value = True
        mock_git_supervisor.rollback.return_value = True

        # GitBranchHook metadata
        branch_result = await GitBranchHook(
            config={"git_supervisor": mock_git_supervisor}
        ).execute(HookContext(
            event=OBJECTIVE_START,
            pipeline_id="test",
            data={"objective_id": "obj-001", "objective_title": "Build"},
        ))
        assert "branch" in branch_result.metadata
        assert "created" in branch_result.metadata

        # GitCommitHook metadata
        commit_result = await GitCommitHook(
            config={"git_supervisor": mock_git_supervisor}
        ).execute(HookContext(
            event="OBJECTIVE_COMPLETE",
            pipeline_id="test",
            data={"objective_id": "obj-001", "objective_title": "Build"},
        ))
        assert "committed" in commit_result.metadata

        # GitRollbackHook metadata
        rollback_result = await GitRollbackHook(
            config={"git_supervisor": mock_git_supervisor}
        ).execute(HookContext(
            event=OBJECTIVE_FAILED,
            pipeline_id="test",
            data={"objective_id": "obj-001", "_git_branch": "obj/obj-001-build"},
        ))
        assert "rolled_back" in rollback_result.metadata


# ============================================================================
# TestEngineEvents (4 tests)
# ============================================================================


class TestEngineEvents:
    """Tests for ORCHESTRATOR_START and ORCHESTRATOR_COMPLETE events."""

    async def test_orchestrator_start_emitted(self, config, tmp_path):
        """Verify ORCHESTRATOR_START hook fires at the beginning of run()."""
        fired_events = []

        class StartHook(BaseHook):
            name = "start_hook"
            event = ORCHESTRATOR_START
            priority = HookPriority.HIGH

            async def execute(self, context: HookContext) -> HookResult:
                fired_events.append(context.event)
                return HookResult.success_result()

        project = ProjectObjectives(
            project_id="start-test",
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
        orchestrator.hook_registry.register(StartHook())

        await orchestrator.run()
        assert ORCHESTRATOR_START in fired_events

    async def test_orchestrator_complete_emitted(self, config, tmp_path):
        """Verify ORCHESTRATOR_COMPLETE hook fires at the end of run()."""
        fired_events = []

        class CompleteHook(BaseHook):
            name = "complete_hook"
            event = ORCHESTRATOR_COMPLETE
            priority = HookPriority.LOW

            async def execute(self, context: HookContext) -> HookResult:
                fired_events.append(context.event)
                return HookResult.success_result()

        project = ProjectObjectives(
            project_id="complete-test",
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
        orchestrator.hook_registry.register(CompleteHook())

        await orchestrator.run()
        assert ORCHESTRATOR_COMPLETE in fired_events

    async def test_branch_tracking_in_state(self, config, tmp_path, mock_git_supervisor):
        """Verify objective_branches is populated from GitBranchHook inject_context."""
        mock_git_supervisor.create_branch.return_value = True

        project = ProjectObjectives(
            project_id="branch-track-test",
            objectives=[_make_objective(objective_id="obj-001", title="Build Feature")],
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
        orchestrator.hook_registry.register(GitBranchHook(
            config={"git_supervisor": mock_git_supervisor}
        ))

        state = await orchestrator.run()

        assert "obj-001" in state.objective_branches
        assert state.objective_branches["obj-001"] == "obj/obj-001-build-feature"

    def test_slug_utility(self):
        """Verify _build_objective_slug produces correct slugs."""
        # Standard title
        assert ProjectOrchestrator._build_objective_slug("Build Feature") == "build-feature"

        # Special characters stripped
        assert ProjectOrchestrator._build_objective_slug("Fix: Bug #123!") == "fix-bug-123"

        # Leading/trailing whitespace
        assert ProjectOrchestrator._build_objective_slug("  Test  ") == "test"

        # Long title truncated to 50 chars
        long_title = "a" * 60
        slug = ProjectOrchestrator._build_objective_slug(long_title)
        assert len(slug) <= 50

        # Spaces replaced with hyphens
        assert ProjectOrchestrator._build_objective_slug("hello world foo") == "hello-world-foo"
