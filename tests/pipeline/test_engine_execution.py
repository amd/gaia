"""
Tests for GAIA Pipeline Engine - Execution Flow.

Tests 6-14: start() transitions, phase order, loop-back, invalid target,
max iterations, hook enter/exit, halt on enter failure, phase exception
isolation, halt on exit failure.
"""

import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from gaia.exceptions import PipelineNotInitializedError, PipelineAlreadyRunningError
from gaia.pipeline.engine import PipelineEngine, PipelinePhase
from gaia.pipeline.state import PipelineContext, PipelineState


class TestEngineStart:
    """Test 6: start() transitions and guards."""

    def _make_engine(self):
        return PipelineEngine(enable_logging=False)

    def _mock_nexus(self):
        mock = MagicMock()
        mock.commit = MagicMock()
        return mock

    @pytest.mark.asyncio
    async def test_start_not_initialized_raises(self):
        """start() without initialize() raises PipelineNotInitializedError."""
        engine = self._make_engine()
        with pytest.raises(PipelineNotInitializedError):
            await engine.start()

    @pytest.mark.asyncio
    async def test_start_transitions_to_running(self):
        """After start(), state machine transitions INITIALIZED -> RUNNING."""
        engine = self._make_engine()
        context = PipelineContext(pipeline_id="exec-001", user_goal="Test")
        mock_nexus = self._mock_nexus()

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic"})

        try:
            # Mock the phase execution to return immediately
            with patch.object(engine, '_execute_pipeline', new_callable=AsyncMock):
                snapshot = await engine.start()

            assert engine._running is True
            assert engine._state_machine.current_state.name == "RUNNING"
        finally:
            engine._loop_manager.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_start_already_running_raises(self):
        """Calling start() twice raises PipelineAlreadyRunningError."""
        engine = self._make_engine()
        context = PipelineContext(pipeline_id="exec-002", user_goal="Test")
        mock_nexus = self._mock_nexus()

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic"})

        try:
            with patch.object(engine, '_execute_pipeline', new_callable=AsyncMock):
                await engine.start()

            with pytest.raises(PipelineAlreadyRunningError):
                await engine.start()
        finally:
            engine._loop_manager.shutdown(wait=False)


class TestEnginePhaseExecution:
    """Tests 7-9: Phase order, loop-back, invalid target."""

    def _make_engine(self):
        return PipelineEngine(enable_logging=False)

    def _mock_nexus(self):
        mock = MagicMock()
        mock.commit = MagicMock()
        return mock

    @pytest.mark.asyncio
    async def test_phase_execution_order(self):
        """Verify phases execute in order: PLANNING -> DEVELOPMENT -> QUALITY -> DECISION."""
        engine = self._make_engine()
        context = PipelineContext(pipeline_id="exec-003", user_goal="Test", quality_threshold=0.5)
        mock_nexus = self._mock_nexus()

        phase_order = []

        async def mock_execute_phase(phase_name):
            phase_order.append(phase_name)
            if phase_name == PipelinePhase.DECISION:
                # Return a COMPLETE decision
                from gaia.pipeline.decision_engine import Decision
                return (True, Decision.complete_decision(reason="Test complete"))
            return (True, None)

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic"})

        try:
            # Mock _execute_phase to track order and return quickly
            with patch.object(engine, '_execute_phase', side_effect=mock_execute_phase):
                snapshot = await engine.start()

            assert phase_order == [
                PipelinePhase.PLANNING,
                PipelinePhase.DEVELOPMENT,
                PipelinePhase.QUALITY,
                PipelinePhase.DECISION,
            ]
            assert engine._state_machine.current_state.name == "COMPLETED"
        finally:
            engine._loop_manager.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_loop_back_jumps_to_target_phase(self):
        """LOOP_BACK decision jumps execution to target phase."""
        engine = self._make_engine()
        context = PipelineContext(pipeline_id="exec-004", user_goal="Test", quality_threshold=0.5)
        mock_nexus = self._mock_nexus()

        phase_order = []
        loop_count = [0]

        async def mock_execute_phase(phase_name):
            from gaia.pipeline.decision_engine import Decision
            phase_order.append(phase_name)

            if phase_name == PipelinePhase.PLANNING:
                if loop_count[0] == 0:
                    loop_count[0] += 1
                    # LOOP_BACK from PLANNING to PLANNING (re-do)
                    return (True, Decision.loop_back_decision(
                        reason="Need more planning",
                        target_phase=PipelinePhase.PLANNING,
                        defects=[],
                    ))
                # Second time through, continue
                return (True, None)

            if phase_name == PipelinePhase.DECISION:
                return (True, Decision.complete_decision(reason="Done"))

            return (True, None)

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic"})

        try:
            with patch.object(engine, '_execute_phase', side_effect=mock_execute_phase):
                snapshot = await engine.start()

            # PLANNING appears twice (initial + loop back), then DEVELOPMENT, QUALITY, DECISION
            assert PipelinePhase.PLANNING in phase_order
            assert phase_order.count(PipelinePhase.PLANNING) >= 2
            assert engine._state_machine.current_state.name == "COMPLETED"
        finally:
            engine._loop_manager.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_loop_back_invalid_target_fails(self):
        """LOOP_BACK with invalid target_phase causes pipeline failure."""
        engine = self._make_engine()
        context = PipelineContext(pipeline_id="exec-005", user_goal="Test", quality_threshold=0.5)
        mock_nexus = self._mock_nexus()

        async def mock_execute_phase(phase_name):
            from gaia.pipeline.decision_engine import Decision
            if phase_name == PipelinePhase.PLANNING:
                # Invalid target phase
                return (True, Decision.loop_back_decision(
                    reason="Bad target",
                    target_phase="NONEXISTENT",
                    defects=[],
                ))
            return (True, None)

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic"})

        try:
            with patch.object(engine, '_execute_phase', side_effect=mock_execute_phase):
                snapshot = await engine.start()

            assert engine._state_machine.current_state.name == "FAILED"
        finally:
            engine._loop_manager.shutdown(wait=False)


class TestEngineMaxIterations:
    """Test 10: Max iterations enforcement."""

    def _make_engine(self):
        return PipelineEngine(enable_logging=False)

    def _mock_nexus(self):
        mock = MagicMock()
        mock.commit = MagicMock()
        return mock

    @pytest.mark.asyncio
    async def test_max_iterations_exceeded(self):
        """Pipeline fails when max_total_loops exceeded."""
        engine = self._make_engine()
        # Very low max_iterations to trigger quickly
        context = PipelineContext(
            pipeline_id="exec-006",
            user_goal="Test",
            max_iterations=1,  # Very low
            quality_threshold=0.5,
        )
        mock_nexus = self._mock_nexus()

        async def mock_execute_phase(phase_name):
            from gaia.pipeline.decision_engine import Decision
            # Always loop back to PLANNING
            if phase_name == PipelinePhase.PLANNING:
                return (True, Decision.loop_back_decision(
                    reason="Loop again",
                    target_phase=PipelinePhase.PLANNING,
                    defects=[],
                ))
            return (True, None)

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic"})

        try:
            with patch.object(engine, '_execute_phase', side_effect=mock_execute_phase):
                snapshot = await engine.start()

            assert engine._state_machine.current_state.name == "FAILED"
        finally:
            engine._loop_manager.shutdown(wait=False)


class TestEngineHookIntegration:
    """Tests 11-14: Hook enter/exit, halt on enter failure, exit failure, phase exception."""

    def _make_engine(self):
        return PipelineEngine(enable_logging=False)

    def _mock_nexus(self):
        mock = MagicMock()
        mock.commit = MagicMock()
        return mock

    @pytest.mark.asyncio
    async def test_hook_enter_executed(self):
        """PHASE_ENTER hooks are called for each phase."""
        engine = self._make_engine()
        context = PipelineContext(pipeline_id="exec-007", user_goal="Test", quality_threshold=0.5)
        mock_nexus = self._mock_nexus()

        # Mock the hook executor to track calls
        mock_hook_result = MagicMock()
        mock_hook_result.halt_pipeline = False

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic", "enable_hooks": True})

        try:
            captured_events = []
            original_execute = engine._hook_executor.execute_hooks

            async def mock_execute_hooks(event, ctx):
                captured_events.append(event)
                return mock_hook_result

            engine._hook_executor.execute_hooks = mock_execute_hooks

            # Mock internal phase methods (not _execute_phase, so hooks still run)
            with patch.object(engine, '_execute_planning', new_callable=AsyncMock, return_value=True):
                with patch.object(engine, '_execute_development', new_callable=AsyncMock, return_value=True):
                    with patch.object(engine, '_execute_quality', new_callable=AsyncMock, return_value=True):
                        with patch.object(engine, '_execute_decision', new_callable=AsyncMock) as mock_dec:
                            from gaia.pipeline.decision_engine import Decision
                            mock_dec.return_value = Decision.complete_decision(reason="Done")
                            await engine.start()

            # PHASE_ENTER called 4 times (once per phase)
            assert captured_events.count("PHASE_ENTER") == 4
            # PHASE_EXIT called 4 times (once per phase)
            assert captured_events.count("PHASE_EXIT") == 4
        finally:
            engine._loop_manager.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_hook_halt_on_enter(self):
        """Pipeline halts when PHASE_ENTER hook returns halt_pipeline=True."""
        engine = self._make_engine()
        context = PipelineContext(pipeline_id="exec-008", user_goal="Test", quality_threshold=0.5)
        mock_nexus = self._mock_nexus()

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic", "enable_hooks": True})

        try:
            mock_halt_result = MagicMock()
            mock_halt_result.halt_pipeline = True
            first_enter = [True]

            async def mock_execute_hooks(event, ctx):
                if event == "PHASE_ENTER" and first_enter[0]:
                    first_enter[0] = False
                    return mock_halt_result
                mock_ok = MagicMock()
                mock_ok.halt_pipeline = False
                return mock_ok

            engine._hook_executor.execute_hooks = mock_execute_hooks

            with patch.object(engine, '_execute_planning', new_callable=AsyncMock, return_value=True):
                with patch.object(engine, '_execute_development', new_callable=AsyncMock, return_value=True):
                    with patch.object(engine, '_execute_quality', new_callable=AsyncMock, return_value=True):
                        with patch.object(engine, '_execute_decision', new_callable=AsyncMock):
                            await engine.start()

            # Pipeline halted after first phase enter
            assert engine._running is False
        finally:
            engine._loop_manager.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_phase_exception_isolation(self):
        """Phase exception returns (False, None) and pipeline fails gracefully."""
        engine = self._make_engine()
        context = PipelineContext(pipeline_id="exec-009", user_goal="Test", quality_threshold=0.5)
        mock_nexus = self._mock_nexus()

        async def mock_execute_phase(phase_name):
            if phase_name == PipelinePhase.DEVELOPMENT:
                raise RuntimeError("Development failure")
            from gaia.pipeline.decision_engine import Decision
            return (True, None)

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic"})

        try:
            with patch.object(engine, '_execute_phase', side_effect=mock_execute_phase):
                snapshot = await engine.start()

            assert engine._state_machine.current_state.name == "FAILED"
            assert engine._running is False
        finally:
            engine._loop_manager.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_hook_halt_on_exit(self):
        """Pipeline halts when PHASE_EXIT hook returns halt_pipeline=True."""
        engine = self._make_engine()
        context = PipelineContext(pipeline_id="exec-010", user_goal="Test", quality_threshold=0.5)
        mock_nexus = self._mock_nexus()

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic", "enable_hooks": True})

        try:
            mock_halt_result = MagicMock()
            mock_halt_result.halt_pipeline = True
            mock_ok_result = MagicMock()
            mock_ok_result.halt_pipeline = False

            exit_count = [0]

            async def mock_execute_hooks(event, ctx):
                if event == "PHASE_EXIT":
                    exit_count[0] += 1
                    if exit_count[0] == 1:
                        return mock_halt_result
                return mock_ok_result

            engine._hook_executor.execute_hooks = mock_execute_hooks

            with patch.object(engine, '_execute_planning', new_callable=AsyncMock, return_value=True):
                with patch.object(engine, '_execute_development', new_callable=AsyncMock, return_value=True):
                    with patch.object(engine, '_execute_quality', new_callable=AsyncMock, return_value=True):
                        with patch.object(engine, '_execute_decision', new_callable=AsyncMock):
                            await engine.start()

            # Pipeline halted after first phase exit
            assert engine._running is False
            assert exit_count[0] == 1
        finally:
            engine._loop_manager.shutdown(wait=False)
