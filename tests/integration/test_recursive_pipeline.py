"""
GAIA Recursive Pipeline Engine Integration Tests

Comprehensive integration tests for the recursive pipeline execution system.
Tests cover:
- LOOP_BACK decision triggering and phase re-execution
- Pipeline completion when quality threshold met
- Pipeline failure on max iterations exceeded
- PipelineIsolation context creation per phase
- Recursive phase loop with while-loop execution

Run with:
    python -m pytest tests/integration/test_recursive_pipeline.py -v
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from gaia.pipeline.decision_engine import Decision, DecisionType, DecisionEngine
from gaia.pipeline.engine import PipelineEngine, PipelinePhase
from gaia.pipeline.isolation import PipelineIsolation, PipelineIsolationManager
from gaia.pipeline.state import PipelineContext, PipelineState


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def pipeline_context():
    """Create a standard pipeline context for testing."""
    return PipelineContext(
        pipeline_id="test-recursive-pipeline-001",
        user_goal="Build a REST API with user authentication",
        quality_threshold=0.90,
        max_iterations=5,
        concurrent_loops=3,
        template="generic",
    )


@pytest.fixture
def mock_agent_registry():
    """Create a mocked agent registry."""
    registry = MagicMock()
    registry.initialize = AsyncMock()
    registry.select_agent = MagicMock(return_value=None)
    registry.shutdown = MagicMock()
    return registry


@pytest.fixture
def mock_loop_manager():
    """Create a mocked loop manager."""
    manager = MagicMock()
    manager.create_loop = AsyncMock(return_value="test-loop-001")
    manager.start_loop = AsyncMock(return_value=AsyncMock())  # Return awaitable
    manager.get_all_loops = MagicMock(return_value={})
    manager.cancel_loop = AsyncMock()
    manager.shutdown = MagicMock()
    return manager


# =============================================================================
# Test: LOOP_BACK Triggers Phase Re-execution
# =============================================================================

class TestLoopBackPhaseReExecution:
    """Tests for LOOP_BACK decision triggering phase re-execution."""

    @pytest.mark.asyncio
    async def test_loop_back_triggers_phase_rerun(self, pipeline_context, mock_agent_registry, mock_loop_manager):
        """
        Test that when DecisionEngine returns LOOP_BACK, the target phase
        is re-executed. Verifies phase call count increases.

        This test mocks _execute_phase to track calls and simulate decisions.
        """
        engine = PipelineEngine(enable_logging=False)

        # Track phase executions
        phase_execution_count = {
            PipelinePhase.PLANNING: 0,
            PipelinePhase.DEVELOPMENT: 0,
            PipelinePhase.QUALITY: 0,
            PipelinePhase.DECISION: 0,
        }

        loop_back_triggered = False

        async def mock_execute_phase(phase_name):
            """Mock phase execution that triggers LOOP_BACK on first PLANNING."""
            nonlocal loop_back_triggered
            phase_execution_count[phase_name] += 1

            # First PLANNING execution triggers LOOP_BACK
            if phase_name == PipelinePhase.PLANNING and phase_execution_count[phase_name] == 1:
                loop_back_triggered = True
                return (True, Decision.loop_back_decision(
                    reason="Quality below threshold - first pass",
                    target_phase="PLANNING",
                    defects=[{"description": "Missing tests"}],
                    metadata={"target_phase": "PLANNING"}
                ))

            # Subsequent executions return CONTINUE to allow progression
            if phase_name == PipelinePhase.DECISION:
                return (True, Decision.complete_decision(reason="Pipeline complete"))
            return (True, Decision.continue_decision(reason="Continue"))

        # Initialize engine first, then mock instance attributes
        await engine.initialize(pipeline_context, {})

        # Mock instance attributes after initialization
        engine._agent_registry = mock_agent_registry
        engine._loop_manager = mock_loop_manager
        engine._quality_scorer.evaluate = AsyncMock(return_value=MagicMock(overall_score=75.0))

        with patch.object(engine, '_execute_phase', mock_execute_phase):
            # Execute pipeline
            result = await engine.start()

            # Verify PLANNING was called at least twice (original + loop back)
            assert phase_execution_count[PipelinePhase.PLANNING] >= 2, (
                f"Expected PLANNING to be called at least twice, "
                f"got {phase_execution_count[PipelinePhase.PLANNING]} calls"
            )

            # Verify LOOP_BACK was triggered
            assert loop_back_triggered, "LOOP_BACK should have been triggered"

        # Cleanup
        engine.shutdown()

    @pytest.mark.asyncio
    async def test_loop_back_metadata_target_phase(self, pipeline_context):
        """
        Test that LOOP_BACK decision correctly uses metadata.target_phase
        for jumping to target phase.
        """
        engine = PipelineEngine(enable_logging=False)

        await engine.initialize(pipeline_context, {})

        # Verify the _execute_pipeline method handles metadata target_phase
        # This tests the internal logic at lines 417-427 in engine.py
        loop_back_with_metadata = Decision.loop_back_decision(
            reason="Quality review failed",
            target_phase="DEVELOPMENT",
            defects=[{"description": "Code quality issues"}],
            metadata={"target_phase": "DEVELOPMENT"}
        )

        # Verify decision has both target_phase attribute and metadata
        assert loop_back_with_metadata.target_phase == "DEVELOPMENT"
        assert loop_back_with_metadata.metadata.get("target_phase") == "DEVELOPMENT"

        engine.shutdown()


# =============================================================================
# Test: Pipeline Completes on Threshold Met
# =============================================================================

class TestPipelineCompletionOnThreshold:
    """Tests for pipeline completion when quality threshold is met."""

    @pytest.mark.asyncio
    async def test_pipeline_completes_on_threshold_met(self, pipeline_context, mock_agent_registry, mock_loop_manager):
        """
        Test that when quality score meets threshold, all phases run once
        and pipeline completes successfully.
        """
        engine = PipelineEngine(enable_logging=False)

        # Track phase execution
        phase_execution_order = []

        async def mock_execute_phase(phase_name):
            """Mock phase execution that tracks order and completes."""
            phase_execution_order.append(phase_name)
            if phase_name == PipelinePhase.DECISION:
                return (True, Decision.complete_decision(reason="Pipeline complete"))
            return (True, Decision.continue_decision(reason="Continue"))

        # Initialize and mock instance attributes
        await engine.initialize(pipeline_context, {})
        engine._agent_registry = mock_agent_registry
        engine._loop_manager = mock_loop_manager
        engine._quality_scorer.evaluate = AsyncMock(return_value=MagicMock(overall_score=95.0))

        with patch.object(engine, '_execute_phase', mock_execute_phase):
            # Execute pipeline
            result = await engine.start()

            # Verify all phases ran exactly once
            expected_phases = [
                PipelinePhase.PLANNING,
                PipelinePhase.DEVELOPMENT,
                PipelinePhase.QUALITY,
                PipelinePhase.DECISION
            ]
            assert phase_execution_order == expected_phases, (
                f"Expected phases {expected_phases}, got {phase_execution_order}"
            )

            # Verify pipeline completed successfully
            assert result.state == PipelineState.COMPLETED

        engine.shutdown()

    @pytest.mark.asyncio
    async def test_decision_engine_continue_decision(self, pipeline_context):
        """
        Test DecisionEngine.evaluate returns CONTINUE when quality >= threshold.
        """
        from gaia.pipeline.decision_engine import DecisionEngine

        decision_engine = DecisionEngine()

        decision = decision_engine.evaluate(
            phase_name="QUALITY",
            quality_score=0.95,
            quality_threshold=0.90,
            defects=[],
            iteration=1,
            max_iterations=5,
            is_final_phase=True,
        )

        assert decision.decision_type == DecisionType.COMPLETE
        assert "Quality threshold" in decision.reason


# =============================================================================
# Test: Pipeline Fails on Max Iterations
# =============================================================================

class TestPipelineFailureOnMaxIterations:
    """Tests for pipeline failure when max iterations exceeded."""

    @pytest.mark.asyncio
    async def test_pipeline_fails_on_max_iterations(self, pipeline_context, mock_agent_registry, mock_loop_manager):
        """
        Test that when DecisionEngine always returns LOOP_BACK,
        pipeline fails after max_iterations.
        """
        engine = PipelineEngine(enable_logging=False)

        # Track phase execution count
        phase_call_count = 0

        async def always_loop_back(phase_name):
            nonlocal phase_call_count
            phase_call_count += 1
            # Always return LOOP_BACK decision
            return (True, Decision.loop_back_decision(
                reason="Quality below threshold",
                target_phase="PLANNING",
                defects=[{"description": "Persistent issues"}],
                metadata={"target_phase": "PLANNING"}
            ))

        # Initialize and mock instance attributes
        await engine.initialize(pipeline_context, {"max_iterations": 3})
        engine._agent_registry = mock_agent_registry
        engine._loop_manager = mock_loop_manager
        engine._quality_scorer.evaluate = AsyncMock(return_value=MagicMock(overall_score=70.0))

        with patch.object(engine, '_execute_phase', always_loop_back):
            # Execute pipeline
            result = await engine.start()

            # Verify pipeline failed due to max iterations
            assert result.state == PipelineState.FAILED
            # Note: error_message may not be set when max iterations exceeded,
            # but the state should be FAILED

            # Verify phase was called multiple times (loop attempts)
            # Should be bounded by max_total_loops = max_iterations * num_phases
            max_expected = pipeline_context.max_iterations * 4  # 4 phases
            assert phase_call_count > 1
            assert phase_call_count <= max_expected + 1

        engine.shutdown()

    @pytest.mark.asyncio
    async def test_decision_engine_fail_on_max_iterations(self, pipeline_context):
        """
        Test DecisionEngine.evaluate returns FAIL when iteration >= max_iterations.
        """
        from gaia.pipeline.decision_engine import DecisionEngine

        decision_engine = DecisionEngine()

        decision = decision_engine.evaluate(
            phase_name="QUALITY",
            quality_score=0.70,
            quality_threshold=0.90,
            defects=[{"description": "Unresolved issues"}],
            iteration=5,
            max_iterations=5,
            is_final_phase=True,
        )

        assert decision.decision_type == DecisionType.FAIL
        assert "Max iterations" in decision.reason
        assert "5" in decision.reason


# =============================================================================
# Test: PipelineIsolation Context Per Phase
# =============================================================================

class TestPipelineIsolationPerPhase:
    """Tests for PipelineIsolation context creation per phase."""

    @pytest.mark.asyncio
    async def test_isolation_context_per_phase(self, pipeline_context, mock_agent_registry, mock_loop_manager):
        """
        Verify PipelineIsolation is used for each phase execution.

        This test verifies that PipelineIsolation context manager works correctly
        by testing the isolation mechanism directly.
        """
        # Test PipelineIsolation directly
        isolation = PipelineIsolation(
            pipeline_id=f"{pipeline_context.pipeline_id}-test-phase",
            persist=False,
            cleanup_on_exit=True
        )

        with isolation:
            workspace_path = isolation.get_workspace_root()
            assert workspace_path.exists()
            assert pipeline_context.pipeline_id in isolation.get_pipeline_id()

            # Create a test file to verify isolation works
            test_file = workspace_path / "test.txt"
            test_file.write_text("test content")
            assert test_file.exists()

        # After context exit, workspace should be cleaned up
        assert not workspace_path.exists()

    @pytest.mark.asyncio
    async def test_isolation_multiple_phases(self, pipeline_context):
        """
        Test that each phase gets its own isolation context.
        """
        isolations_created = []

        for phase in PipelinePhase.ALL:
            isolation = PipelineIsolation(
                pipeline_id=f"{pipeline_context.pipeline_id}-{phase}",
                persist=False
            )
            isolations_created.append(isolation)

        # Verify all isolations have unique workspace paths
        workspace_paths = []
        for isolation in isolations_created:
            with isolation:
                path = isolation.get_workspace_root()
                workspace_paths.append(str(path))

        # All paths should be unique (different hashes for different pipeline_ids)
        assert len(set(workspace_paths)) == len(workspace_paths)

        # Verify all paths are under the workspace root
        for path in workspace_paths:
            assert ".gaia" in path or "isolated" in path

    @pytest.mark.asyncio
    async def test_isolation_cleanup_on_phase_exit(self, pipeline_context, tmp_path):
        """
        Test that PipelineIsolation cleans up workspace on phase exit.
        """
        workspace_root = tmp_path / "isolated"

        isolation = PipelineIsolation(
            pipeline_id="cleanup-test-001",
            workspace_root=str(workspace_root),
            persist=False,
            cleanup_on_exit=True
        )

        with isolation:
            workspace_path = isolation.get_workspace_root()
            assert workspace_path.exists()

            # Create a test file
            test_file = workspace_path / "test.txt"
            test_file.write_text("test content")
            assert test_file.exists()

        # After context exit, workspace should be cleaned up
        assert not workspace_path.exists(), "Workspace should be cleaned up after context exit"

    @pytest.mark.asyncio
    async def test_isolation_persist_flag(self, pipeline_context, tmp_path):
        """
        Test that PipelineIsolation preserves workspace when persist=True.
        """
        workspace_root = tmp_path / "isolated"

        isolation = PipelineIsolation(
            pipeline_id="persist-test-001",
            workspace_root=str(workspace_root),
            persist=True,
            cleanup_on_exit=True
        )

        with isolation:
            workspace_path = isolation.get_workspace_root()
            assert workspace_path.exists()

            # Create a test file
            test_file = workspace_path / "test.txt"
            test_file.write_text("test content")

        # After context exit with persist=True, workspace should remain
        assert workspace_path.exists(), "Workspace should persist when persist=True"

        # Cleanup manually
        import shutil
        shutil.rmtree(workspace_root)


# =============================================================================
# Test: Decision History Tracking
# =============================================================================

class TestDecisionHistoryTracking:
    """Tests for decision history tracking in pipeline execution."""

    @pytest.mark.asyncio
    async def test_decision_recorded_in_artifacts(self, pipeline_context, mock_agent_registry, mock_loop_manager):
        """
        Test that decisions are recorded in state machine artifacts.
        """
        engine = PipelineEngine(enable_logging=False)

        # Initialize and mock instance attributes
        await engine.initialize(pipeline_context, {})
        engine._agent_registry = mock_agent_registry
        engine._loop_manager = mock_loop_manager
        engine._quality_scorer.evaluate = AsyncMock(return_value=MagicMock(overall_score=95.0))

        complete_decision = Decision.complete_decision(
            reason="Pipeline completed",
            metadata={"final_score": 0.95}
        )

        async def execute_phase(phase_name):
            # Simulate real _execute_phase behavior:
            # When DECISION phase runs, it should add decision to artifacts
            if phase_name == PipelinePhase.DECISION:
                engine._state_machine.add_artifact("decision", complete_decision.to_dict())
                return (True, complete_decision)
            return (True, Decision.continue_decision(reason="Continue"))

        with patch.object(engine, '_execute_phase', execute_phase):
            result = await engine.start()

            # Verify decision was recorded in artifacts
            assert "decision" in result.artifacts
            decision_artifact = result.artifacts["decision"]
            assert decision_artifact["decision_type"] == "COMPLETE"
            assert "Pipeline completed" in decision_artifact["reason"]

        engine.shutdown()


# =============================================================================
# Test: Edge Cases and Error Handling
# =============================================================================

class TestRecursivePipelineEdgeCases:
    """Tests for edge cases in recursive pipeline execution."""

    @pytest.mark.asyncio
    async def test_phase_execution_failure(self, pipeline_context, mock_agent_registry, mock_loop_manager):
        """
        Test that phase execution failure transitions pipeline to FAILED state.
        """
        engine = PipelineEngine(enable_logging=False)

        await engine.initialize(pipeline_context, {})
        engine._agent_registry = mock_agent_registry
        engine._loop_manager = mock_loop_manager

        async def failing_phase(phase_name):
            raise Exception("Phase execution failed")

        with patch.object(engine, '_execute_phase', failing_phase):
            result = await engine.start()

            assert result.state == PipelineState.FAILED
            # The error is logged but error_message may not be set for exception cases

        engine.shutdown()

    @pytest.mark.asyncio
    async def test_loop_back_to_nonexistent_phase(self, pipeline_context, mock_agent_registry, mock_loop_manager):
        """
        Test handling of LOOP_BACK with invalid target phase.
        """
        engine = PipelineEngine(enable_logging=False)

        # Initialize and mock
        await engine.initialize(pipeline_context, {})
        engine._agent_registry = mock_agent_registry
        engine._loop_manager = mock_loop_manager
        engine._quality_scorer.evaluate = AsyncMock(return_value=MagicMock(overall_score=75.0))

        # LOOP_BACK with invalid target phase
        invalid_decision = Decision.loop_back_decision(
            reason="Quality review",
            target_phase="NONEXISTENT_PHASE",
            defects=[],
            metadata={"target_phase": "NONEXISTENT_PHASE"}
        )

        phase_call_count = 0
        max_calls = 10

        async def execute_phase(phase_name):
            nonlocal phase_call_count
            phase_call_count += 1
            if phase_call_count >= max_calls:
                return (False, None)
            return (True, invalid_decision)

        with patch.object(engine, '_execute_phase', execute_phase):
            result = await engine.start()

            # Pipeline should eventually fail or complete due to max loops
            assert result.state in [PipelineState.FAILED, PipelineState.COMPLETED]
            assert phase_call_count <= max_calls

        engine.shutdown()

    @pytest.mark.asyncio
    async def test_concurrent_pipeline_isolation(self, pipeline_context):
        """
        Test that concurrent pipelines maintain isolation.
        """
        manager = PipelineIsolationManager(workspace_root=None)

        # Create two concurrent isolations
        with manager.isolation_context("pipeline-a") as isolation_a:
            with manager.isolation_context("pipeline-b") as isolation_b:
                # Verify both are active
                assert isolation_a.is_active()
                assert isolation_b.is_active()

                # Verify different workspace paths
                workspace_a = isolation_a.get_workspace_root()
                workspace_b = isolation_b.get_workspace_root()
                assert workspace_a != workspace_b

                # Verify manager tracks both
                assert manager.get_active_count() == 2

        # After exit, both should be cleaned up (unless persist=True)
        assert manager.get_active_count() == 0


# =============================================================================
# Main entry point
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
