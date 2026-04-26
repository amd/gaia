"""
Tests for GAIA Pipeline Engine - Lifecycle.

Tests 24-27: pause, resume, cancel, wait_for_completion.
"""

import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from gaia.exceptions import PipelineNotInitializedError
from gaia.pipeline.engine import PipelineEngine
from gaia.pipeline.state import PipelineContext, PipelineState


class TestEngineLifecycle:
    """Tests 24-27: Lifecycle management."""

    def _make_engine(self):
        return PipelineEngine(enable_logging=False)

    def _mock_nexus(self):
        mock = MagicMock()
        mock.commit = MagicMock()
        return mock

    @pytest.mark.asyncio
    async def test_pause_transitions_to_paused(self):
        """pause() transitions pipeline from RUNNING to PAUSED state."""
        engine = self._make_engine()
        context = PipelineContext(pipeline_id="life-001", user_goal="Test")
        mock_nexus = self._mock_nexus()

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic"})

        try:
            # Transition to RUNNING first (only valid path to PAUSED)
            engine._state_machine.transition(PipelineState.RUNNING, "Started")
            engine._running = True

            snapshot = await engine.pause("User requested pause")

            assert engine._state_machine.current_state.name == "PAUSED"
        finally:
            engine._loop_manager.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_pause_not_initialized_raises(self):
        """pause() without initialize() raises PipelineNotInitializedError."""
        engine = self._make_engine()
        with pytest.raises(PipelineNotInitializedError):
            await engine.pause("Test")

    @pytest.mark.asyncio
    async def test_resume_from_paused(self):
        """resume() transitions from PAUSED back to RUNNING."""
        engine = self._make_engine()
        context = PipelineContext(pipeline_id="life-002", user_goal="Test")
        mock_nexus = self._mock_nexus()

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic"})

        try:
            # Transition to RUNNING, then pause
            engine._state_machine.transition(PipelineState.RUNNING, "Started")
            engine._running = True
            await engine.pause("Testing pause/resume")
            assert engine._state_machine.current_state.name == "PAUSED"

            # Then resume
            snapshot = await engine.resume()

            assert engine._state_machine.current_state.name == "RUNNING"
            assert engine._running is True
        finally:
            engine._loop_manager.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_resume_when_not_paused_raises(self):
        """resume() on a non-paused pipeline raises PipelineNotInitializedError."""
        engine = self._make_engine()
        context = PipelineContext(pipeline_id="life-003", user_goal="Test")
        mock_nexus = self._mock_nexus()

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic"})

        try:
            # Don't pause - try to resume directly
            with pytest.raises(PipelineNotInitializedError):
                await engine.resume()
        finally:
            engine._loop_manager.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_cancel_transitions_to_cancelled(self):
        """cancel() transitions pipeline to CANCELLED state."""
        engine = self._make_engine()
        context = PipelineContext(pipeline_id="life-004", user_goal="Test")
        mock_nexus = self._mock_nexus()

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic"})

        try:
            snapshot = await engine.cancel()

            assert engine._state_machine.current_state.name == "CANCELLED"
            assert engine._running is False
        finally:
            engine._loop_manager.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_cancel_not_initialized_raises(self):
        """cancel() without initialize() raises PipelineNotInitializedError."""
        engine = self._make_engine()
        with pytest.raises(PipelineNotInitializedError):
            await engine.cancel()

    @pytest.mark.asyncio
    async def test_wait_for_completion_returns_true(self):
        """wait_for_completion() returns True when pipeline completes."""
        engine = self._make_engine()
        context = PipelineContext(pipeline_id="life-005", user_goal="Test")
        mock_nexus = self._mock_nexus()

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic"})

        try:
            # Start pipeline in background
            async def mock_execute_pipeline():
                await asyncio.sleep(0.05)  # Small delay
                engine._state_machine.transition(PipelineState.COMPLETED, "Done")
                engine._running = False
                engine._completion_event.set()

            with patch.object(engine, '_execute_pipeline', side_effect=mock_execute_pipeline):
                # Start in background
                start_task = asyncio.create_task(engine.start())

                # Wait for completion
                result = await engine.wait_for_completion(timeout=5.0)
                assert result is True

                # Clean up the start task
                try:
                    await asyncio.wait_for(start_task, timeout=1.0)
                except Exception:
                    pass
        finally:
            engine._loop_manager.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_wait_for_completion_timeout(self):
        """wait_for_completion() returns False on timeout."""
        engine = self._make_engine()
        context = PipelineContext(pipeline_id="life-006", user_goal="Test")
        mock_nexus = self._mock_nexus()

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic"})

        try:
            # Never complete - event never set
            result = await engine.wait_for_completion(timeout=0.1)
            assert result is False
        finally:
            engine._loop_manager.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_wait_for_completion_no_event(self):
        """wait_for_completion() returns False when no completion event exists."""
        engine = self._make_engine()
        # Don't initialize - completion event is None
        result = await engine.wait_for_completion(timeout=0.1)
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_cancels_all_loops(self):
        """cancel() cancels all active loops in loop manager."""
        engine = self._make_engine()
        context = PipelineContext(pipeline_id="life-007", user_goal="Test")
        mock_nexus = self._mock_nexus()

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic"})

        try:
            # Track cancel calls
            cancelled_loops = []
            original_cancel = engine._loop_manager.cancel_loop

            async def mock_cancel(loop_id):
                cancelled_loops.append(loop_id)
                # Don't call original to avoid complexity

            engine._loop_manager.cancel_loop = mock_cancel

            # Add a fake loop to the manager
            from gaia.pipeline.loop_manager import LoopConfig, LoopState
            config = LoopConfig(
                loop_id="fake-loop",
                phase_name="PLANNING",
                agent_sequence=[],
                exit_criteria={"goal": "test"},
            )
            await engine._loop_manager.create_loop(config)

            await engine.cancel()

            # The fake loop should have been cancelled
            assert "fake-loop" in cancelled_loops
        finally:
            engine._loop_manager.shutdown(wait=False)
