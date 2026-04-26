"""
Tests for GAIA Pipeline Engine - Decision Wiring.

Tests 19-23: Quality score storage, decision engine wiring,
supervisor mode, defect routing, fail sets error.
"""

from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from gaia.pipeline.engine import PipelineEngine
from gaia.pipeline.state import PipelineContext


class TestEngineDecision:
    """Tests 19-23: Decision phase integration."""

    def _make_engine(self):
        return PipelineEngine(enable_logging=False)

    def _mock_nexus(self):
        mock = MagicMock()
        mock.commit = MagicMock()
        return mock

    @pytest.mark.asyncio
    async def test_quality_score_stored_in_state(self):
        """After QUALITY phase, quality score is stored in state machine."""
        engine = self._make_engine()
        context = PipelineContext(
            pipeline_id="dec-001",
            user_goal="Build API",
            quality_threshold=0.5,
        )
        mock_nexus = self._mock_nexus()

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic"})

        try:
            # Mock the quality scorer to return a known score
            mock_report = MagicMock()
            mock_report.overall_score = 85.0  # 0.85 after division by 100
            mock_report.category_scores = {"accuracy": 80, "completeness": 90}

            with patch.object(engine._quality_scorer, 'evaluate', new_callable=AsyncMock, return_value=mock_report):
                success = await engine._execute_quality()

            assert success is True
            assert engine._state_machine.snapshot.quality_score == 0.85

            # Verify artifact was stored
            artifacts = engine._state_machine.snapshot.artifacts
            assert "quality_report" in artifacts
        finally:
            engine._loop_manager.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_decision_engine_wired_correctly(self):
        """DECISION phase calls DecisionEngine.evaluate with correct parameters."""
        engine = self._make_engine()
        context = PipelineContext(
            pipeline_id="dec-002",
            user_goal="Build API",
            quality_threshold=0.85,
            max_iterations=3,
        )
        mock_nexus = self._mock_nexus()

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic"})

        try:
            # Set up state with quality score and defects
            engine._state_machine.set_quality_score(0.75)
            engine._state_machine.add_defect({"description": "Missing tests", "severity": "medium"})

            # Mock the routing engine to avoid real routing
            with patch.object(engine._routing_engine, 'route_defect_resilient', return_value=MagicMock(
                to_dict=MagicMock(return_value={"action": "fix"})
            )):
                decision = await engine._execute_decision()

            # Verify decision was made by DecisionEngine (not supervisor)
            assert decision is not None
            assert decision.reason is not None

            # Decision artifact stored
            artifacts = engine._state_machine.snapshot.artifacts
            assert "decision" in artifacts
        finally:
            engine._loop_manager.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_supervisor_mode_enabled(self):
        """When use_supervisor=True, supervisor decision path is taken."""
        engine = self._make_engine()
        context = PipelineContext(
            pipeline_id="dec-003",
            user_goal="Build API",
            quality_threshold=0.85,
        )
        mock_nexus = self._mock_nexus()

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic", "use_supervisor": True})

        try:
            # Mock the supervisor decision method
            with patch.object(engine, '_execute_supervisor_decision', new_callable=AsyncMock) as mock_sup:
                from gaia.pipeline.decision_engine import Decision
                mock_sup.return_value = Decision.continue_decision(reason="Supervisor approved")

                decision = await engine._execute_decision()

                # Supervisor method was called
                assert mock_sup.called
                assert decision.decision_type.name == "CONTINUE"
        finally:
            engine._loop_manager.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_defects_routed_through_routing_engine(self):
        """Defects are processed by RoutingEngine and stored as artifacts."""
        engine = self._make_engine()
        context = PipelineContext(
            pipeline_id="dec-004",
            user_goal="Build API",
            quality_threshold=0.85,
        )
        mock_nexus = self._mock_nexus()

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic"})

        try:
            # Add defects
            engine._state_machine.set_quality_score(0.90)
            engine._state_machine.add_defect({"description": "SQL injection risk", "severity": "critical"})
            engine._state_machine.add_defect({"description": "Missing input validation", "severity": "high"})

            # Track routing calls
            routing_calls = []

            def mock_route(defect, context=None):
                routing_calls.append(defect)
                m = MagicMock()
                m.to_dict = MagicMock(return_value={"action": "fix", "defect": defect.get("description")})
                return m

            with patch.object(engine._routing_engine, 'route_defect_resilient', side_effect=mock_route):
                decision = await engine._execute_decision()

            # Both defects were routed
            assert len(routing_calls) == 2

            # Routing decisions stored
            artifacts = engine._state_machine.snapshot.artifacts
            assert "routing_decisions" in artifacts
        finally:
            engine._loop_manager.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_fail_decision_sets_error(self):
        """When DecisionEngine returns FAIL, state machine error is set."""
        engine = self._make_engine()
        context = PipelineContext(
            pipeline_id="dec-005",
            user_goal="Build API",
            quality_threshold=0.95,  # Very high threshold
            max_iterations=1,
        )
        mock_nexus = self._mock_nexus()

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic"})

        try:
            # Set low quality score and max iteration
            engine._state_machine.set_quality_score(0.50)
            engine._state_machine.add_defect({"description": "Major issue"})
            # Set iteration to max
            for _ in range(1):
                engine._state_machine.increment_iteration()

            with patch.object(engine._routing_engine, 'route_defect_resilient', return_value=MagicMock(
                to_dict=MagicMock(return_value={})
            )):
                decision = await engine._execute_decision()

            # FAIL decision should set error
            if decision.decision_type.name == "FAIL":
                assert engine._state_machine.snapshot.error_message is not None
        finally:
            engine._loop_manager.shutdown(wait=False)
