"""
GAIA Pipeline Engine Integration Tests

Comprehensive integration tests for the pipeline orchestration system.
Tests cover:
- Engine initialization and configuration
- Phase execution flow
- State machine transitions
- Loop-back scenarios
- Hook execution ordering
- Decision engine integration
- Quality scorer integration
- Agent registry integration

Run with:
    python -m pytest tests/integration/test_pipeline_engine.py -v
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gaia.agents.registry import AgentRegistry
from gaia.exceptions import (
    InvalidQualityThresholdError,
    InvalidStateTransition,
    PipelineAlreadyRunningError,
    PipelineNotInitializedError,
)
from gaia.hooks.base import BaseHook, HookContext, HookPriority, HookResult
from gaia.hooks.registry import HookExecutor, HookRegistry
from gaia.pipeline.decision_engine import Decision, DecisionEngine, DecisionType
from gaia.pipeline.engine import PipelineConfig, PipelineEngine, PipelinePhase
from gaia.pipeline.loop_manager import LoopConfig, LoopManager, LoopStatus
from gaia.pipeline.recursive_template import (
    ENTERPRISE_TEMPLATE,
    GENERIC_TEMPLATE,
    RAPID_TEMPLATE,
    get_recursive_template,
)
from gaia.pipeline.state import (
    PipelineContext,
    PipelineSnapshot,
    PipelineState,
    PipelineStateMachine,
)
from gaia.quality.scorer import QualityScorer

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def valid_context():
    """Create a valid pipeline context for testing."""
    return PipelineContext(
        pipeline_id="test-pipeline-001",
        user_goal="Build a REST API with user authentication",
        quality_threshold=0.90,
        max_iterations=10,
        concurrent_loops=5,
        template="generic",
    )


@pytest.fixture
def enterprise_context():
    """Create an enterprise-grade pipeline context."""
    return PipelineContext(
        pipeline_id="enterprise-001",
        user_goal="Implement PCI-DSS compliant payment processing",
        quality_threshold=0.95,
        max_iterations=15,
        concurrent_loops=10,
        template="enterprise",
    )


@pytest.fixture
def rapid_context():
    """Create a rapid prototype pipeline context."""
    return PipelineContext(
        pipeline_id="rapid-001",
        user_goal="Create a quick prototype dashboard",
        quality_threshold=0.75,
        max_iterations=5,
        concurrent_loops=3,
        template="rapid",
    )


@pytest.fixture
def agent_registry():
    """Create an agent registry without loading files."""
    registry = AgentRegistry(agents_dir=None, auto_reload=False)
    return registry


@pytest.fixture
def hook_registry():
    """Create a hook registry."""
    return HookRegistry()


@pytest.fixture
def decision_engine():
    """Create a decision engine."""
    return DecisionEngine()


@pytest.fixture
def quality_scorer():
    """Create a quality scorer."""
    return QualityScorer()


# =============================================================================
# Pipeline Context Tests
# =============================================================================


class TestPipelineContext:
    """Tests for PipelineContext validation and creation."""

    def test_context_creation_valid(self, valid_context):
        """Test valid context creation."""
        assert valid_context.pipeline_id == "test-pipeline-001"
        assert valid_context.user_goal == "Build a REST API with user authentication"
        assert valid_context.quality_threshold == 0.90
        assert valid_context.max_iterations == 10
        assert valid_context.concurrent_loops == 5

    def test_context_quality_threshold_bounds(self):
        """Test quality threshold bounds validation."""
        # Valid thresholds
        ctx = PipelineContext(
            pipeline_id="test-001",
            user_goal="Test",
            quality_threshold=0.0,
        )
        assert ctx.quality_threshold == 0.0

        ctx = PipelineContext(
            pipeline_id="test-002",
            user_goal="Test",
            quality_threshold=1.0,
        )
        assert ctx.quality_threshold == 1.0

    def test_context_invalid_quality_threshold(self):
        """Test invalid quality threshold raises error."""
        with pytest.raises(ValueError, match="quality_threshold must be between"):
            PipelineContext(
                pipeline_id="test-001",
                user_goal="Test",
                quality_threshold=1.5,
            )

        with pytest.raises(ValueError, match="quality_threshold must be between"):
            PipelineContext(
                pipeline_id="test-002",
                user_goal="Test",
                quality_threshold=-0.1,
            )

    def test_context_invalid_max_iterations(self):
        """Test negative max iterations raises error."""
        with pytest.raises(ValueError, match="max_iterations must be non-negative"):
            PipelineContext(
                pipeline_id="test-001",
                user_goal="Test",
                max_iterations=-1,
            )

    def test_context_invalid_concurrent_loops(self):
        """Test invalid concurrent_loops raises error."""
        with pytest.raises(ValueError, match="concurrent_loops must be at least"):
            PipelineContext(
                pipeline_id="test-001",
                user_goal="Test",
                concurrent_loops=0,
            )

    def test_context_missing_pipeline_id(self):
        """Test missing pipeline_id raises error."""
        with pytest.raises(ValueError, match="pipeline_id is required"):
            PipelineContext(
                pipeline_id="",
                user_goal="Test",
            )

    def test_context_missing_user_goal(self):
        """Test missing user_goal raises error."""
        with pytest.raises(ValueError, match="user_goal is required"):
            PipelineContext(
                pipeline_id="test-001",
                user_goal="",
            )

    def test_context_with_updates(self, valid_context):
        """Test context immutable updates."""
        updated = valid_context.with_updates(
            quality_threshold=0.95,
            max_iterations=15,
            metadata={"custom": "value"},
        )

        # Original unchanged
        assert valid_context.quality_threshold == 0.90
        assert valid_context.max_iterations == 10

        # Updated values
        assert updated.quality_threshold == 0.95
        assert updated.max_iterations == 15
        assert updated.metadata.get("custom") == "value"

        # Original pipeline_id preserved
        assert updated.pipeline_id == valid_context.pipeline_id


# =============================================================================
# Pipeline State Machine Tests
# =============================================================================


class TestPipelineStateMachine:
    """Tests for PipelineStateMachine transitions and state management."""

    def test_initial_state(self, valid_context):
        """Test state machine starts in INITIALIZING state."""
        fsm = PipelineStateMachine(valid_context)
        assert fsm.current_state == PipelineState.INITIALIZING

    def test_valid_transitions(self, valid_context):
        """Test valid state transitions."""
        fsm = PipelineStateMachine(valid_context)

        # INITIALIZING -> READY
        result = fsm.transition(PipelineState.READY, "Config validated")
        assert result is True
        assert fsm.current_state == PipelineState.READY

        # READY -> RUNNING
        result = fsm.transition(PipelineState.RUNNING, "Pipeline started")
        assert result is True
        assert fsm.current_state == PipelineState.RUNNING

        # RUNNING -> COMPLETED
        result = fsm.transition(PipelineState.COMPLETED, "Pipeline finished")
        assert result is True
        assert fsm.current_state == PipelineState.COMPLETED

    def test_invalid_transition(self, valid_context):
        """Test invalid state transition raises error."""
        fsm = PipelineStateMachine(valid_context)

        # Cannot go directly from INITIALIZING to RUNNING
        with pytest.raises(InvalidStateTransition):
            fsm.transition(PipelineState.RUNNING, "Invalid transition")

    def test_terminal_states(self, valid_context):
        """Test terminal state behavior."""
        fsm = PipelineStateMachine(valid_context)
        fsm.transition(PipelineState.READY, "Config validated")
        fsm.transition(PipelineState.RUNNING, "Starting execution")
        fsm.transition(PipelineState.COMPLETED, "Done")

        assert fsm.is_terminal() is True

        # Cannot transition from terminal state
        with pytest.raises(InvalidStateTransition):
            fsm.transition(PipelineState.RUNNING, "Try to restart")

    def test_phase_and_loop_setting(self, valid_context):
        """Test setting phase and loop information."""
        fsm = PipelineStateMachine(valid_context)

        fsm.set_phase("PLANNING")
        assert fsm.snapshot.current_phase == "PLANNING"

        fsm.set_loop(1)
        assert fsm.snapshot.current_loop == 1

    def test_quality_score_setting(self, valid_context):
        """Test setting quality score."""
        fsm = PipelineStateMachine(valid_context)

        fsm.set_quality_score(0.85)
        assert fsm.snapshot.quality_score == 0.85

    def test_artifact_management(self, valid_context):
        """Test artifact addition and retrieval."""
        fsm = PipelineStateMachine(valid_context)

        fsm.add_artifact("plan", {"steps": ["step1", "step2"]})
        assert "plan" in fsm.snapshot.artifacts
        assert len(fsm.snapshot.artifacts["plan"]["steps"]) == 2

    def test_defect_management(self, valid_context):
        """Test defect addition."""
        fsm = PipelineStateMachine(valid_context)

        fsm.add_defect({"description": "Missing tests", "severity": "medium"})
        assert len(fsm.snapshot.defects) == 1

        fsm.add_defects(
            [
                {"description": "Security issue", "severity": "critical"},
                {"description": "Performance problem", "severity": "low"},
            ]
        )
        assert len(fsm.snapshot.defects) == 3

    def test_chronicle_entries(self, valid_context):
        """Test chronicle entry addition."""
        fsm = PipelineStateMachine(valid_context)

        fsm.add_chronicle_entry("PHASE_START", {"phase": "PLANNING"})
        assert len(fsm.chronicle) == 1
        assert fsm.chronicle[0]["event"] == "PHASE_START"

    def test_transition_log(self, valid_context):
        """Test transition history is recorded."""
        fsm = PipelineStateMachine(valid_context)

        fsm.transition(PipelineState.READY, "Config validated")
        fsm.transition(PipelineState.RUNNING, "Pipeline started")

        log = fsm.transition_log
        assert len(log) == 2
        assert log[0].to_state == PipelineState.READY
        assert log[1].to_state == PipelineState.RUNNING

    def test_elapsed_time(self, valid_context):
        """Test elapsed time calculation."""
        fsm = PipelineStateMachine(valid_context)
        fsm.transition(PipelineState.READY, "Config validated")
        fsm.transition(PipelineState.RUNNING, "Pipeline started")
        fsm.transition(PipelineState.COMPLETED, "Done")

        elapsed = fsm.snapshot.elapsed_time()
        assert elapsed is not None
        assert elapsed >= 0


# =============================================================================
# Decision Engine Tests
# =============================================================================


class TestDecisionEngine:
    """Tests for DecisionEngine evaluation logic."""

    def test_evaluate_quality_above_threshold(self, decision_engine):
        """Test decision when quality exceeds threshold."""
        decision = decision_engine.evaluate(
            phase_name="DECISION",
            quality_score=0.95,
            quality_threshold=0.90,
            defects=[],
            iteration=1,
            max_iterations=10,
            is_final_phase=True,
        )

        assert decision.decision_type == DecisionType.COMPLETE

    def test_evaluate_quality_below_threshold_loop_back(self, decision_engine):
        """Test decision when quality is below threshold."""
        decision = decision_engine.evaluate(
            phase_name="QUALITY",
            quality_score=0.75,
            quality_threshold=0.90,
            defects=[{"description": "Missing tests"}],
            iteration=1,
            max_iterations=10,
            is_final_phase=False,
        )

        assert decision.decision_type == DecisionType.LOOP_BACK
        assert decision.target_phase == "PLANNING"

    def test_evaluate_critical_defect_pause(self, decision_engine):
        """Test decision when critical defect exists."""
        decision = decision_engine.evaluate(
            phase_name="QUALITY",
            quality_score=0.95,
            quality_threshold=0.90,
            defects=[{"description": "Security vulnerability", "severity": "critical"}],
            iteration=1,
            max_iterations=10,
            is_final_phase=False,
        )

        assert decision.decision_type == DecisionType.PAUSE
        assert decision.metadata.get("critical_count") == 1

    def test_evaluate_max_iterations_fail(self, decision_engine):
        """Test decision when max iterations exceeded."""
        decision = decision_engine.evaluate(
            phase_name="DECISION",
            quality_score=0.75,
            quality_threshold=0.90,
            defects=[{"description": "Persistent issues"}],
            iteration=10,
            max_iterations=10,
            is_final_phase=True,
        )

        assert decision.decision_type == DecisionType.FAIL

    def test_evaluate_simple(self, decision_engine):
        """Test simple evaluation method."""
        # Above threshold
        result = decision_engine.evaluate_simple(
            quality_score=0.95,
            quality_threshold=0.90,
            has_critical_defects=False,
        )
        assert result == DecisionType.CONTINUE

        # Below threshold
        result = decision_engine.evaluate_simple(
            quality_score=0.75,
            quality_threshold=0.90,
            has_critical_defects=False,
        )
        assert result == DecisionType.LOOP_BACK

        # Critical defects
        result = decision_engine.evaluate_simple(
            quality_score=0.95,
            quality_threshold=0.90,
            has_critical_defects=True,
        )
        assert result == DecisionType.PAUSE

    def test_should_loop_back(self, decision_engine):
        """Test should_loop_back helper method."""
        # Should loop back
        should_loop, reason = decision_engine.should_loop_back(
            quality_score=0.75,
            quality_threshold=0.90,
            iteration=1,
            max_iterations=10,
        )
        assert should_loop is True

        # Quality met
        should_loop, reason = decision_engine.should_loop_back(
            quality_score=0.95,
            quality_threshold=0.90,
            iteration=1,
            max_iterations=10,
        )
        assert should_loop is False

        # Max iterations exceeded
        should_loop, reason = decision_engine.should_loop_back(
            quality_score=0.75,
            quality_threshold=0.90,
            iteration=10,
            max_iterations=10,
        )
        assert should_loop is False


# =============================================================================
# Template System Tests
# =============================================================================


class TestRecursivePipelineTemplate:
    """Tests for template loading and configuration."""

    def test_load_generic_template(self):
        """Test loading generic template."""
        template = get_recursive_template("generic")

        assert template.name == "generic"
        assert template.quality_threshold == 0.90
        assert template.max_iterations == 10
        assert len(template.phases) == 4

    def test_load_enterprise_template(self):
        """Test loading enterprise template."""
        template = get_recursive_template("enterprise")

        assert template.name == "enterprise"
        assert template.quality_threshold == 0.95
        assert template.max_iterations == 15

        # Enterprise has additional agents
        assert "security-auditor" in template.agent_categories.get("quality", [])

    def test_load_rapid_template(self):
        """Test loading rapid template."""
        template = get_recursive_template("rapid")

        assert template.name == "rapid"
        assert template.quality_threshold == 0.75
        assert template.max_iterations == 5

    def test_template_not_found(self):
        """Test loading non-existent template raises error."""
        with pytest.raises(KeyError, match="Template.*not found"):
            get_recursive_template("nonexistent")

    def test_template_weight_sum(self):
        """Test that template weights sum to 1.0."""
        for name in ["generic", "rapid", "enterprise"]:
            template = get_recursive_template(name)
            weight_sum = sum(template.quality_weights.values())
            assert abs(weight_sum - 1.0) < 0.01, f"{name} weights don't sum to 1.0"

    def test_get_phase_config(self):
        """Test getting phase configuration."""
        template = get_recursive_template("generic")

        planning_phase = template.get_phase("PLANNING")
        assert planning_phase is not None
        assert planning_phase.category.value == "planning"

        nonexistent = template.get_phase("NONEXISTENT")
        assert nonexistent is None

    def test_routing_rule_evaluation(self):
        """Test routing rule matching."""
        template = get_recursive_template("generic")

        # Security defect should match
        context = {"defect_type": "security"}
        matching_rule = template.evaluate_routing_rules(context)
        assert matching_rule is not None
        assert "security" in matching_rule.route_to

    def test_should_loop_back_method(self):
        """Test template loop-back logic."""
        template = get_recursive_template("generic")

        # Should loop back
        assert (
            template.should_loop_back(
                quality_score=0.75,
                iteration=1,
                has_defects=True,
            )
            is True
        )

        # Quality met
        assert (
            template.should_loop_back(
                quality_score=0.95,
                iteration=1,
                has_defects=True,
            )
            is False
        )

        # Max iterations
        assert (
            template.should_loop_back(
                quality_score=0.75,
                iteration=10,
                has_defects=True,
            )
            is False
        )


# =============================================================================
# Hook System Tests
# =============================================================================


class TestHookSystem:
    """Tests for hook registration and execution."""

    @pytest.mark.asyncio
    async def test_hook_registration(self):
        """Test hook registration and retrieval."""
        registry = HookRegistry()

        class TestHook(BaseHook):
            name = "test_hook"
            event = "TEST_EVENT"
            priority = HookPriority.NORMAL
            blocking = False

            async def execute(self, context: HookContext) -> HookResult:
                return HookResult.success_result()

        hook = TestHook()
        registry.register(hook)

        hooks = registry.get_hooks("TEST_EVENT")
        assert len(hooks) == 1
        assert hooks[0].name == "test_hook"

    @pytest.mark.asyncio
    async def test_global_hook(self):
        """Test global hook registration."""
        registry = HookRegistry()

        class GlobalHook(BaseHook):
            name = "global_hook"
            event = "*"
            priority = HookPriority.LOW
            blocking = False

            async def execute(self, context: HookContext) -> HookResult:
                return HookResult.success_result()

        hook = GlobalHook()
        registry.register(hook)

        # Global hooks should appear for any event
        hooks = registry.get_hooks("ANY_EVENT")
        assert len(hooks) == 1

    @pytest.mark.asyncio
    async def test_hook_execution(self):
        """Test hook execution via executor."""
        registry = HookRegistry()
        executor = HookExecutor(registry)

        executed = []

        class TestHook(BaseHook):
            name = "test_hook"
            event = "TEST_EVENT"
            priority = HookPriority.NORMAL
            blocking = False

            async def execute(self, context: HookContext) -> HookResult:
                executed.append(context.event)
                return HookResult.success_result(metadata={"hook_ran": True})

        registry.register(TestHook())

        context = HookContext(event="TEST_EVENT", pipeline_id="test-001")
        result = await executor.execute_hooks("TEST_EVENT", context)

        assert result.success is True
        assert result.metadata.get("hook_ran") is True
        assert "TEST_EVENT" in executed

    @pytest.mark.asyncio
    async def test_blocking_hook_failure(self):
        """Test that blocking hook failure halts execution."""
        registry = HookRegistry()
        executor = HookExecutor(registry)

        class BlockingHook(BaseHook):
            name = "blocking_hook"
            event = "TEST_EVENT"
            priority = HookPriority.HIGH
            blocking = True

            async def execute(self, context: HookContext) -> HookResult:
                return HookResult.failure_result(
                    "Blocking failure",
                    halt_pipeline=True,
                )

        registry.register(BlockingHook())

        context = HookContext(event="TEST_EVENT", pipeline_id="test-001")
        result = await executor.execute_hooks("TEST_EVENT", context)

        assert result.success is False
        assert result.halt_pipeline is True

    @pytest.mark.asyncio
    async def test_hook_priority_ordering(self):
        """Test that hooks execute in priority order."""
        registry = HookRegistry()
        executor = HookExecutor(registry)

        execution_order = []

        def make_hook(name: str, priority: HookPriority):
            # Use explicit class dict to avoid scope issues
            async def execute(self, context: HookContext) -> HookResult:
                execution_order.append(self.name)
                return HookResult.success_result()

            return type(
                "OrderedHook",
                (BaseHook,),
                {
                    "name": name,
                    "event": "TEST_EVENT",
                    "priority": priority,
                    "blocking": False,
                    "execute": execute,
                },
            )()

        # Register in reverse order
        registry.register(make_hook("low", HookPriority.LOW))
        registry.register(make_hook("normal", HookPriority.NORMAL))
        registry.register(make_hook("high", HookPriority.HIGH))

        context = HookContext(event="TEST_EVENT", pipeline_id="test-001")
        await executor.execute_hooks("TEST_EVENT", context)

        # Should execute: HIGH -> NORMAL -> LOW
        assert execution_order == ["high", "normal", "low"]


# =============================================================================
# Quality Scorer Tests
# =============================================================================


class TestQualityScorer:
    """Tests for QualityScorer evaluation."""

    @pytest.mark.asyncio
    async def test_quality_scorer_initialization(self, quality_scorer):
        """Test quality scorer has all validators."""
        assert len(quality_scorer._validators) == 27  # 27 categories

    @pytest.mark.asyncio
    async def test_quality_evaluation_basic(self, quality_scorer):
        """Test basic quality evaluation."""
        artifact = "def hello(): return 'world'"
        context = {"requirements": ["Create hello function"]}

        report = await quality_scorer.evaluate(artifact, context)

        assert report.overall_score > 0
        assert report.overall_score <= 100
        assert len(report.category_scores) > 0

    @pytest.mark.asyncio
    async def test_quality_evaluation_dimensions(self, quality_scorer):
        """Test that all dimensions are scored."""
        artifact = "test artifact"
        context = {"requirements": ["Test"]}

        report = await quality_scorer.evaluate(artifact, context)

        # Should have scores for all 6 dimensions
        dimension_names = [d.dimension_name for d in report.dimension_scores]
        assert "Code Quality" in dimension_names
        assert "Requirements Coverage" in dimension_names
        assert "Testing" in dimension_names

    @pytest.mark.asyncio
    async def test_quality_defect_counting(self, quality_scorer):
        """Test defect counting in reports."""
        artifact = "test"
        context = {"requirements": ["Test"]}

        report = await quality_scorer.evaluate(artifact, context)

        assert report.total_defects >= 0
        assert report.critical_defects >= 0

    @pytest.mark.asyncio
    async def test_quality_certification_status(self, quality_scorer):
        """Test certification status assignment."""
        artifact = "test"
        context = {"requirements": ["Test"]}

        report = await quality_scorer.evaluate(artifact, context)

        # Should have a valid certification status
        assert report.certification_status is not None
        assert report.certification_status.value in [
            "excellent",
            "good",
            "acceptable",
            "needs_improvement",
            "fail",
        ]


# =============================================================================
# Agent Registry Tests
# =============================================================================


class TestAgentRegistry:
    """Tests for AgentRegistry functionality."""

    def test_registry_creation(self):
        """Test registry creation without agents."""
        registry = AgentRegistry(agents_dir=None, auto_reload=False)
        assert registry is not None

    @pytest.mark.asyncio
    async def test_registry_statistics(self, agent_registry):
        """Test registry statistics."""
        stats = agent_registry.get_statistics()

        assert "total_agents" in stats
        assert "enabled_agents" in stats
        assert "categories" in stats
        assert "capabilities" in stats

    def test_select_agent_no_agents(self, agent_registry):
        """Test select_agent returns None when no agents."""
        selected = agent_registry.select_agent(
            task_description="Build API",
            current_phase="DEVELOPMENT",
            state={"complexity": 0.5},
        )
        assert selected is None

    def test_get_agent_not_found(self, agent_registry):
        """Test getting non-existent agent."""
        agent = agent_registry.get_agent("nonexistent-agent")
        assert agent is None


# =============================================================================
# Pipeline Config Tests
# =============================================================================


class TestPipelineConfig:
    """Tests for PipelineConfig validation."""

    def test_config_defaults(self):
        """Test default configuration values."""
        config = PipelineConfig()

        assert config.template == "generic"
        assert config.quality_threshold == 0.90
        assert config.max_iterations == 10
        assert config.concurrent_loops == 5
        assert config.enable_hooks is True

    def test_config_invalid_quality_threshold(self):
        """Test invalid quality threshold raises error."""
        with pytest.raises(InvalidQualityThresholdError):
            PipelineConfig(quality_threshold=1.5)

    def test_config_invalid_max_iterations(self):
        """Test negative max iterations raises error."""
        with pytest.raises(ValueError, match="max_iterations must be non-negative"):
            PipelineConfig(max_iterations=-1)

    def test_config_invalid_concurrent_loops(self):
        """Test invalid concurrent_loops raises error."""
        with pytest.raises(ValueError, match="concurrent_loops must be at least"):
            PipelineConfig(concurrent_loops=0)


# =============================================================================
# Loop Manager Tests
# =============================================================================


class TestLoopManager:
    """Tests for LoopManager functionality."""

    @pytest.mark.asyncio
    async def test_loop_manager_creation(self):
        """Test loop manager creation."""
        manager = LoopManager(max_concurrent=5)
        assert manager.MAX_CONCURRENT_LOOPS == 5
        assert manager.get_running_count() == 0
        assert manager.get_pending_count() == 0

    @pytest.mark.asyncio
    async def test_loop_creation(self):
        """Test loop creation."""
        manager = LoopManager(max_concurrent=5)

        config = LoopConfig(
            loop_id="test-loop-001",
            phase_name="PLANNING",
            agent_sequence=["agent1"],
            exit_criteria={"quality_threshold": 0.9},
        )

        loop_id = await manager.create_loop(config)
        assert loop_id == "test-loop-001"

        state = manager.get_loop_state("test-loop-001")
        assert state is not None
        assert state.status == LoopStatus.PENDING

    @pytest.mark.asyncio
    async def test_duplicate_loop_id(self):
        """Test that duplicate loop IDs raise error."""
        from gaia.exceptions import LoopCreationError

        manager = LoopManager(max_concurrent=5)

        config = LoopConfig(
            loop_id="duplicate-001",
            phase_name="PLANNING",
            agent_sequence=["agent1"],
            exit_criteria={},
        )

        await manager.create_loop(config)

        with pytest.raises(LoopCreationError):
            await manager.create_loop(config)

    @pytest.mark.asyncio
    async def test_loop_statistics(self):
        """Test loop statistics."""
        manager = LoopManager(max_concurrent=5)

        config = LoopConfig(
            loop_id="stats-loop",
            phase_name="DEVELOPMENT",
            agent_sequence=["agent1"],
            exit_criteria={},
        )

        await manager.create_loop(config)

        stats = manager.get_statistics()
        assert stats["total_loops"] == 1
        assert stats["max_concurrent"] == 5

    @pytest.mark.asyncio
    async def test_loop_cancellation(self):
        """Test loop cancellation."""
        manager = LoopManager(max_concurrent=5)

        config = LoopConfig(
            loop_id="cancel-loop",
            phase_name="PLANNING",
            agent_sequence=["agent1"],
            exit_criteria={},
        )

        await manager.create_loop(config)

        result = await manager.cancel_loop("cancel-loop")
        assert result is True

        state = manager.get_loop_state("cancel-loop")
        assert state.status == LoopStatus.CANCELLED


# =============================================================================
# Integration Tests
# =============================================================================


class TestPipelineIntegration:
    """Integration tests combining multiple components."""

    def test_context_state_machine_integration(self, valid_context):
        """Test context and state machine work together."""
        fsm = PipelineStateMachine(valid_context)

        # Context should be accessible from state machine
        assert fsm.context.pipeline_id == valid_context.pipeline_id
        assert fsm.context.user_goal == valid_context.user_goal

    def test_decision_state_integration(self, valid_context, decision_engine):
        """Test decision engine with state machine."""
        fsm = PipelineStateMachine(valid_context)
        fsm.transition(PipelineState.READY, "Config validated")
        fsm.transition(PipelineState.RUNNING, "Started")
        fsm.set_quality_score(0.75)

        # Decision based on state
        decision = decision_engine.evaluate(
            phase_name="QUALITY",
            quality_score=fsm.snapshot.quality_score,
            quality_threshold=valid_context.quality_threshold,
            defects=fsm.snapshot.defects,
            iteration=fsm.snapshot.iteration_count,
            max_iterations=valid_context.max_iterations,
            is_final_phase=False,
        )

        # Should loop back due to low quality
        assert decision.decision_type == DecisionType.LOOP_BACK

    def test_template_context_integration(self, valid_context):
        """Test template configuration with context."""
        template = get_recursive_template(valid_context.template)

        # Context threshold should match or be configurable from template
        assert template.quality_threshold == valid_context.quality_threshold

    @pytest.mark.asyncio
    async def test_hook_quality_integration(
        self, valid_context, hook_registry, quality_scorer
    ):
        """Test hook execution with quality scoring."""
        executor = HookExecutor(hook_registry)

        # Create a quality result hook
        class QualityResultHook(BaseHook):
            name = "quality_result"
            event = "QUALITY_RESULT"
            priority = HookPriority.NORMAL
            blocking = False

            async def execute(self, context: HookContext) -> HookResult:
                # Simulate quality evaluation
                report = await quality_scorer.evaluate(
                    "test", {"requirements": ["test"]}
                )
                context.data["quality_report"] = report.to_dict()
                return HookResult.success_result()

        hook = QualityResultHook()
        hook_registry.register(hook)

        context = HookContext(
            event="QUALITY_RESULT",
            pipeline_id=valid_context.pipeline_id,
            phase="QUALITY",
        )

        result = await executor.execute_hooks("QUALITY_RESULT", context)

        assert result.success is True
        assert "quality_report" in context.data


# =============================================================================
# Main entry point for running tests directly
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
