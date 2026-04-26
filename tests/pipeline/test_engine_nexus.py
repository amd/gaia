"""
Tests for GAIA Pipeline Engine - Nexus Integration.

Tests 28-30: pipeline_init event, phase events, full artifact flow.
"""

from unittest.mock import MagicMock, AsyncMock, patch, call

import pytest

from gaia.pipeline.engine import PipelineEngine
from gaia.pipeline.state import PipelineContext


class TestEngineNexusIntegration:
    """Tests 28-30: NexusService/Chronicle event tracking."""

    def _make_engine(self):
        return PipelineEngine(enable_logging=False)

    def _mock_nexus(self):
        mock = MagicMock()
        mock.commit = MagicMock()
        return mock

    @pytest.mark.asyncio
    async def test_pipeline_init_event_committed(self):
        """On initialize(), a pipeline_init event is committed to Chronicle."""
        engine = self._make_engine()
        context = PipelineContext(
            pipeline_id="nexus-001",
            user_goal="Build API",
        )
        mock_nexus = self._mock_nexus()

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic", "enable_hooks": False})

        try:
            # Verify pipeline_init event was committed
            commit_calls = mock_nexus.commit.call_args_list
            init_calls = [c for c in commit_calls if c.kwargs.get("event_type") == "pipeline_init"]
            assert len(init_calls) == 1
            payload = init_calls[0].kwargs["payload"]
            assert payload["pipeline_id"] == "nexus-001"
            assert payload["user_goal"] == "Build API"
            assert payload["template"] == "generic"
        finally:
            engine._loop_manager.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_phase_enter_and_exit_events(self):
        """Phase execution commits phase_enter and phase_exit events."""
        engine = self._make_engine()
        context = PipelineContext(
            pipeline_id="nexus-002",
            user_goal="Build API",
            quality_threshold=0.5,
        )
        mock_nexus = self._mock_nexus()

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic", "enable_hooks": False})

        try:
            # Mock internal phase methods to avoid loop manager complexity
            with patch.object(engine, '_execute_planning', new_callable=AsyncMock, return_value=True):
                with patch.object(engine, '_execute_development', new_callable=AsyncMock, return_value=True):
                    with patch.object(engine, '_execute_quality', new_callable=AsyncMock, return_value=True):
                        with patch.object(engine, '_execute_decision', new_callable=AsyncMock) as mock_dec:
                            from gaia.pipeline.decision_engine import Decision
                            mock_dec.return_value = Decision.complete_decision(reason="Done")
                            await engine.start()

            # Collect all commit calls
            commit_calls = mock_nexus.commit.call_args_list
            event_types = [c.kwargs.get("event_type") for c in commit_calls]

            # At least 4 phase_enter and 4 phase_exit events
            assert event_types.count("phase_enter") >= 4
            assert event_types.count("phase_exit") >= 4
        finally:
            engine._loop_manager.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_full_artifact_flow_events(self):
        """Full pipeline flow commits agent_selected, agent_executed, and decision_made events."""
        engine = self._make_engine()
        context = PipelineContext(
            pipeline_id="nexus-003",
            user_goal="Build API",
            quality_threshold=0.5,
        )
        mock_nexus = self._mock_nexus()

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic", "enable_hooks": False})

        try:
            # Mock loop manager to avoid real loop execution
            mock_loop_state = MagicMock()
            mock_loop_state.status.name = "COMPLETED"
            mock_loop_state.config.loop_id = "test-loop"
            mock_loop_state.artifacts = {}

            import asyncio
            mock_future = asyncio.get_event_loop().create_future()
            mock_future.set_result(mock_loop_state)
            engine._loop_manager.start_loop = AsyncMock(return_value=mock_future)

            # Mock quality scorer to return a good score
            mock_report = MagicMock()
            mock_report.overall_score = 90.0
            mock_report.category_scores = {"accuracy": 90}
            with patch.object(engine._quality_scorer, 'evaluate', new_callable=AsyncMock, return_value=mock_report):
                await engine.start()

            commit_calls = mock_nexus.commit.call_args_list
            event_types = [c.kwargs.get("event_type") for c in commit_calls]

            # Verify key events in the flow
            assert "pipeline_init" in event_types
            assert "decision_made" in event_types

            # Verify decision_made payload
            decision_calls = [c for c in commit_calls if c.kwargs.get("event_type") == "decision_made"]
            assert len(decision_calls) == 1
            decision_payload = decision_calls[0].kwargs["payload"]
            assert decision_payload["decision_type"] == "COMPLETE"
            assert "quality_score" in decision_payload
        finally:
            engine._loop_manager.shutdown(wait=False)
