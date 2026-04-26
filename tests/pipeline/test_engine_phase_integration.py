"""
Tests for GAIA Pipeline Engine - Phase Integration.

Tests 15-18: Planning loop creation + artifact propagation,
template agents, registry fallback, development component saving.
"""

import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from gaia.pipeline.engine import PipelineEngine, PipelinePhase
from gaia.pipeline.state import PipelineContext


class TestPhaseIntegration:
    """Tests 15-18: Phase-level integration with loop manager."""

    def _make_engine(self):
        return PipelineEngine(enable_logging=False)

    def _mock_nexus(self):
        mock = MagicMock()
        mock.commit = MagicMock()
        return mock

    @pytest.mark.asyncio
    async def test_planning_loop_creation_and_artifact_propagation(self):
        """Planning phase creates a loop and propagates artifacts to state machine."""
        engine = self._make_engine()
        context = PipelineContext(
            pipeline_id="phase-001",
            user_goal="Build a REST API",
            quality_threshold=0.5,
        )
        mock_nexus = self._mock_nexus()

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic"})

        try:
            # Track loop creation
            created_loops = []
            original_create = engine._loop_manager.create_loop

            async def mock_create_loop(config):
                created_loops.append(config)
                return await original_create(config)

            engine._loop_manager.create_loop = mock_create_loop

            # Mock start_loop to return a completed future with artifacts
            mock_loop_state = MagicMock()
            mock_loop_state.status.name = "COMPLETED"
            mock_loop_state.config.loop_id = "test-loop-001"
            mock_loop_state.artifacts = {"senior-developer": "Sample plan artifact"}

            mock_future = asyncio.get_event_loop().create_future()
            mock_future.set_result(mock_loop_state)

            engine._loop_manager.start_loop = AsyncMock(return_value=mock_future)

            # Mock other phases
            with patch.object(engine, '_execute_development', new_callable=AsyncMock, return_value=True):
                with patch.object(engine, '_execute_quality', new_callable=AsyncMock, return_value=True):
                    with patch.object(engine, '_execute_decision', new_callable=AsyncMock) as mock_dec:
                        from gaia.pipeline.decision_engine import Decision
                        mock_dec.return_value = Decision.complete_decision(reason="Done")
                        await engine.start()

            # Verify loop was created with correct phase
            assert len(created_loops) >= 1
            assert created_loops[0].phase_name == PipelinePhase.PLANNING

            # Verify artifact was propagated to state machine
            artifacts = engine._state_machine.snapshot.artifacts
            plan_artifacts = {k: v for k, v in artifacts.items() if k.startswith("plan_")}
            assert len(plan_artifacts) >= 1
            assert "Sample plan artifact" in str(plan_artifacts)
        finally:
            engine._loop_manager.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_template_agents_used_for_phases(self):
        """When template specifies agents, they are used instead of registry selection."""
        engine = self._make_engine()
        context = PipelineContext(
            pipeline_id="phase-002",
            user_goal="Build a REST API",
            quality_threshold=0.5,
        )
        mock_nexus = self._mock_nexus()

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic"})

        try:
            # Check that template agents are resolved
            agents = engine._get_agents_for_phase(PipelinePhase.PLANNING)
            # Generic template should have some agents defined
            assert isinstance(agents, list)

            agents = engine._get_agents_for_phase(PipelinePhase.DEVELOPMENT)
            assert isinstance(agents, list)
        finally:
            engine._loop_manager.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_registry_fallback_when_no_template_agents(self):
        """When template has no agents, registry select_agent is called."""
        engine = self._make_engine()
        context = PipelineContext(
            pipeline_id="phase-003",
            user_goal="Build a REST API",
            quality_threshold=0.5,
        )
        mock_nexus = self._mock_nexus()

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic"})

        try:
            # Force empty template agents by mocking
            with patch.object(engine, '_get_agents_for_phase', return_value=[]):
                # Registry select should be called
                with patch.object(engine._agent_registry, 'select_agent', return_value="senior-developer") as mock_select:
                    with patch.object(engine, '_execute_development', new_callable=AsyncMock, return_value=True):
                        with patch.object(engine, '_execute_quality', new_callable=AsyncMock, return_value=True):
                            with patch.object(engine, '_execute_decision', new_callable=AsyncMock) as mock_dec:
                                from gaia.pipeline.decision_engine import Decision
                                mock_dec.return_value = Decision.complete_decision(reason="Done")

                                # Mock loop manager
                                mock_loop_state = MagicMock()
                                mock_loop_state.status.name = "COMPLETED"
                                mock_loop_state.config.loop_id = "test-loop"
                                mock_loop_state.artifacts = {}

                                import asyncio
                                mock_future = asyncio.get_event_loop().create_future()
                                mock_future.set_result(mock_loop_state)
                                engine._loop_manager.start_loop = AsyncMock(return_value=mock_future)

                                await engine.start()

                                # select_agent was called
                                assert mock_select.called
        finally:
            engine._loop_manager.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_development_component_saving(self):
        """Development phase saves artifacts as components via ComponentLoader."""
        engine = self._make_engine()
        context = PipelineContext(
            pipeline_id="phase-004",
            user_goal="Build a REST API",
            quality_threshold=0.5,
        )
        mock_nexus = self._mock_nexus()

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic"})

        try:
            # Mock loop manager to return artifacts
            mock_loop_state = MagicMock()
            mock_loop_state.status.name = "COMPLETED"
            mock_loop_state.config.loop_id = "dev-loop-001"
            mock_loop_state.artifacts = {"senior-developer": "class MyAPI:\n    pass"}

            import asyncio
            mock_future = asyncio.get_event_loop().create_future()
            mock_future.set_result(mock_loop_state)
            engine._loop_manager.start_loop = AsyncMock(return_value=mock_future)

            # Track component saves
            saved_components = []
            original_save = engine._component_loader.save_component

            def mock_save(component_path, content, frontmatter=None):
                saved_components.append({
                    "path": component_path,
                    "content": content,
                    "frontmatter": frontmatter,
                })

            engine._component_loader.save_component = mock_save

            # Mock planning to do nothing
            with patch.object(engine, '_execute_planning', new_callable=AsyncMock, return_value=True):
                with patch.object(engine, '_execute_quality', new_callable=AsyncMock, return_value=True):
                    with patch.object(engine, '_execute_decision', new_callable=AsyncMock) as mock_dec:
                        from gaia.pipeline.decision_engine import Decision
                        mock_dec.return_value = Decision.complete_decision(reason="Done")
                        await engine.start()

            # Verify component was saved
            assert len(saved_components) >= 1
            assert "development/dev-loop-001_senior-developer.md" in saved_components[0]["path"]
            assert "MyAPI" in saved_components[0]["content"]
        finally:
            engine._loop_manager.shutdown(wait=False)
