"""
Tests for GAIA Pipeline Engine - Initialization & Configuration.

Tests 1-5: Engine constructor, initialize(), template resolution,
double-init prevention, and canvas config cloning.
"""

import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from gaia.exceptions import PipelineAlreadyRunningError
from gaia.pipeline.decision_engine import DecisionEngine
from gaia.pipeline.engine import PipelineConfig, PipelineEngine, PipelinePhase
from gaia.pipeline.loop_manager import LoopManager
from gaia.pipeline.routing_engine import RoutingEngine
from gaia.pipeline.state import PipelineContext, PipelineStateMachine


class TestEngineConstructor:
    """Test 1: Engine constructor default values."""

    def test_engine_constructor_default_values(self):
        """Construct PipelineEngine with defaults and verify internal state."""
        engine = PipelineEngine(enable_logging=False)

        assert engine._initialized is False
        assert engine._running is False
        assert engine._state_machine is None
        assert engine._loop_manager is None
        assert engine._decision_engine is None
        assert engine._quality_scorer is None
        assert engine.max_concurrent_loops == 100
        assert isinstance(engine._semaphore, asyncio.Semaphore)
        assert engine._semaphore._value == 100
        assert isinstance(engine._worker_semaphore, asyncio.Semaphore)
        assert engine._worker_semaphore._value == 4
        assert engine._nexus is None
        assert engine._enable_chronicle is True
        assert engine._context is None
        assert engine._config is None
        assert engine._completion_event is None

    def test_engine_constructor_custom_params(self):
        """Constructor with custom parameters."""
        engine = PipelineEngine(
            enable_logging=False,
            max_concurrent_loops=50,
            worker_pool_size=8,
            model_id="test-model",
            skip_lemonade=True,
        )
        assert engine.max_concurrent_loops == 50
        assert engine._semaphore._value == 50
        assert engine._worker_semaphore._value == 8
        assert engine._model_id == "test-model"
        assert engine._skip_lemonade is True


class TestEngineInitialize:
    """Tests 2-4: Engine initialize() wiring, template resolution, double-init."""

    @pytest.fixture
    def context(self) -> PipelineContext:
        return PipelineContext(
            pipeline_id="init-001",
            user_goal="Test goal",
            quality_threshold=0.85,
        )

    def _mock_nexus(self):
        mock_nexus = MagicMock()
        mock_nexus.commit = MagicMock()
        return mock_nexus

    @pytest.mark.asyncio
    async def test_engine_initialize_wires_all_components(self, context):
        """After initialize(), verify all sub-components are instantiated."""
        engine = PipelineEngine(enable_logging=False)
        mock_nexus = self._mock_nexus()

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic", "enable_hooks": True})

        try:
            assert engine._initialized is True
            assert isinstance(engine._state_machine, PipelineStateMachine)
            assert engine._state_machine.current_state.name == "READY"
            assert isinstance(engine._loop_manager, LoopManager)
            assert isinstance(engine._decision_engine, DecisionEngine)
            assert engine._quality_scorer is not None
            assert engine._agent_registry is not None
            assert isinstance(engine._routing_engine, RoutingEngine)
            assert engine._hook_registry is not None
            assert engine._hook_executor is not None
            assert engine._component_loader is not None
            assert engine._nexus is mock_nexus
            assert isinstance(engine._completion_event, asyncio.Event)
            assert engine._context == context
        finally:
            # Cleanup: shutdown loop manager's executor
            engine._loop_manager.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_engine_initialize_template_resolution(self, context):
        """Verify template loading with valid name and invalid name fallback."""
        engine = PipelineEngine(enable_logging=False)
        mock_nexus = self._mock_nexus()

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic"})

        try:
            assert engine._current_template is not None
            assert engine._current_template.name == "generic"
        finally:
            engine._loop_manager.shutdown(wait=False)

        # Test invalid template falls back to generic
        engine2 = PipelineEngine(enable_logging=False)
        context2 = PipelineContext(pipeline_id="init-002", user_goal="Test")
        mock_nexus2 = self._mock_nexus()

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus2
            await engine2.initialize(context2, {"template": "nonexistent"})

        try:
            assert engine2._current_template.name == "generic"
        finally:
            engine2._loop_manager.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_engine_initialize_double_init_raises(self, context):
        """Calling initialize() twice must raise PipelineAlreadyRunningError."""
        engine = PipelineEngine(enable_logging=False)
        mock_nexus = self._mock_nexus()

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, {"template": "generic"})

        try:
            with pytest.raises(PipelineAlreadyRunningError):
                await engine.initialize(context, {"template": "generic"})
        finally:
            engine._loop_manager.shutdown(wait=False)


class TestEngineCanvasConfig:
    """Test 5: Canvas config cloning."""

    @pytest.mark.asyncio
    async def test_engine_initialize_with_canvas_config_clones_template(self):
        """Canvas config should clone template, not mutate shared singleton."""
        from gaia.pipeline.recursive_template import get_recursive_template

        # Get the shared template before
        shared_template = get_recursive_template("generic")
        original_canvas_loops = list(getattr(shared_template, "canvas_loops", []))

        context = PipelineContext(pipeline_id="canvas-001", user_goal="Build API")
        engine = PipelineEngine(enable_logging=False)
        mock_nexus = MagicMock()
        mock_nexus.commit = MagicMock()

        config = {
            "template": "generic",
            "canvas_loops": [
                {
                    "loop_id": "loop-A",
                    "label": "Planning Loop A",
                    "agent_ids": ["senior-developer"],
                    "max_iterations": 3,
                    "quality_threshold": 0.8,
                    "source_stage": "PLANNING",
                    "target_stage": "DEVELOPMENT",
                    "condition": "quality_below_threshold",
                }
            ],
            "canvas_supervisors": [
                {
                    "supervisor_id": "sup-1",
                    "label": "Quality Supervisor",
                    "agent_id": "quality-reviewer",
                    "decision_condition": "quality_below_threshold",
                    "decision_type": "CONTINUE",
                    "monitoring_targets": ["quality_score"],
                }
            ],
        }

        with patch("gaia.state.nexus.NexusService") as mock_nexus_cls:
            mock_nexus_cls.get_instance.return_value = mock_nexus
            await engine.initialize(context, config)

        try:
            # Engine's template should have canvas config
            assert len(engine._current_template.canvas_loops) == 1
            assert engine._current_template.canvas_loops[0].loop_id == "loop-A"
            assert len(engine._current_template.canvas_supervisors) == 1
            assert engine._current_template.canvas_supervisors[0].supervisor_id == "sup-1"

            # Shared singleton should NOT be mutated
            assert getattr(shared_template, "canvas_loops", []) == original_canvas_loops
        finally:
            engine._loop_manager.shutdown(wait=False)
