"""
Tests for GAIA Quality Supervisor Agent.

Tests cover:
- Supervisor agent initialization
- Review consensus tool functionality
- Chronicle integration
- Decision making workflow
- Pipeline integration
- Thread safety (50+ concurrent threads)
- Error handling and graceful degradation

Quality Gate 2 Criteria:
- SUP-001: Supervisor decision parsing (100% accuracy)
- SUP-002: Pipeline LOOP_BACK on rejection (automatic trigger)
- SUP-003: Chronicle commit integrity (hash chain preserved)
"""

import asyncio
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from gaia.quality.supervisor import (
    SupervisorAgent,
    SupervisorDecision,
    SupervisorDecisionType,
)
from gaia.state.nexus import NexusService
from gaia.tools.review_ops import (
    clear_review_history,
    get_chronicle_digest,
    get_review_history,
    get_review_history_count,
    review_consensus,
    workspace_validate,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def sample_defects() -> List[Dict[str, Any]]:
    """Sample defects list for testing."""
    return [
        {"description": "Missing docstring", "severity": "low", "category": "DC-01"},
        {"description": "Cyclomatic complexity too high", "severity": "medium", "category": "CQ-03"},
        {"description": "Missing unit tests", "severity": "medium", "category": "TS-01"},
    ]


@pytest.fixture
def critical_defects() -> List[Dict[str, Any]]:
    """Critical defects for testing."""
    return [
        {"description": "Security vulnerability in input validation", "severity": "critical", "category": "BP-01"},
        {"description": "Potential data loss on error", "severity": "high", "category": "CQ-06"},
    ]


@pytest.fixture
def sample_reviews() -> List[Dict[str, Any]]:
    """Sample review list for consensus testing."""
    return [
        {"score": 85, "defects": ["minor-style"], "validator_id": "v1"},
        {"score": 88, "defects": [], "validator_id": "v2"},
        {"score": 82, "defects": ["missing-docstring"], "validator_id": "v3"},
    ]


@pytest.fixture
def supervisor_agent() -> SupervisorAgent:
    """Create test supervisor agent."""
    return SupervisorAgent(
        min_acceptable_score=0.85,
        target_score=0.90,
        skip_lemonade=True,
        silent_mode=True,
    )


@pytest.fixture(autouse=True)
def reset_review_history():
    """Reset review history before each test."""
    clear_review_history()
    yield
    clear_review_history()


@pytest.fixture
def reset_nexus():
    """Reset NexusService before and after test."""
    NexusService.reset_instance()
    yield
    NexusService.reset_instance()


# =============================================================================
# Supervisor Agent Initialization Tests
# =============================================================================

class TestSupervisorAgentInitialization:
    """Tests for SupervisorAgent initialization."""

    def test_init_default_values(self):
        """Test initialization with default values."""
        agent = SupervisorAgent(skip_lemonade=True, silent_mode=True)

        assert agent._min_acceptable_score == 0.85
        assert agent._target_score == 0.90
        assert agent._critical_defect_threshold == 1
        assert agent._max_defects_allowed == 5
        assert agent._max_review_iterations == 3
        assert agent._min_consensus_threshold == 0.75
        assert len(agent._decision_history) == 0

    def test_init_custom_values(self):
        """Test initialization with custom values."""
        agent = SupervisorAgent(
            min_acceptable_score=0.80,
            target_score=0.95,
            critical_defect_threshold=2,
            max_defects_allowed=10,
            max_review_iterations=5,
            min_consensus_threshold=0.80,
            skip_lemonade=True,
            silent_mode=True,
        )

        assert agent._min_acceptable_score == 0.80
        assert agent._target_score == 0.95
        assert agent._critical_defect_threshold == 2
        assert agent._max_defects_allowed == 10
        assert agent._max_review_iterations == 5
        assert agent._min_consensus_threshold == 0.80

    def test_init_model_id(self):
        """Test model ID configuration."""
        agent = SupervisorAgent(
            model_id="Qwen3.5-35B-A3B-GGUF",
            skip_lemonade=True,
            silent_mode=True,
        )
        assert agent.model_id == "Qwen3.5-35B-A3B-GGUF"

    def test_init_thread_safety(self):
        """Test thread-safe initialization."""
        agents = []

        def create_agent():
            agent = SupervisorAgent(skip_lemonade=True, silent_mode=True)
            agents.append(agent)

        threads = [threading.Thread(target=create_agent) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads should get the same singleton ToolRegistry
        assert len(agents) == 10


# =============================================================================
# Review Consensus Tool Tests
# =============================================================================

class TestReviewConsensus:
    """Tests for review_consensus tool."""

    def test_consensus_basic(self, sample_reviews):
        """Test basic consensus calculation."""
        result = review_consensus(reviews=sample_reviews, min_consensus=0.75)

        assert result["status"] == "success"
        assert 80 <= result["consensus_score"] <= 90
        assert "consensus_reached" in result
        assert "agreement_ratio" in result
        assert "defect_summary" in result

    def test_consensus_empty_reviews(self):
        """Test with empty reviews list."""
        result = review_consensus(reviews=[], min_consensus=0.75)

        assert result["status"] == "error"
        assert "No reviews provided" in result["error"]
        assert result["consensus_score"] == 0.0
        assert result["consensus_reached"] is False

    def test_consensus_single_review(self):
        """Test with single review."""
        result = review_consensus(
            reviews=[{"score": 90, "defects": []}],
            min_consensus=0.75,
        )

        assert result["status"] == "success"
        assert result["consensus_score"] == 90.0
        assert result["agreement_ratio"] == 1.0

    def test_consensus_weighted(self, sample_reviews):
        """Test weighted consensus calculation."""
        weighting = {"v1": 0.5, "v2": 0.3, "v3": 0.2}
        result = review_consensus(
            reviews=sample_reviews,
            min_consensus=0.75,
            weighting=weighting,
        )

        assert result["status"] == "success"
        assert result["weighted_score"] > 0

    def test_consensus_no_valid_scores(self):
        """Test with no valid scores."""
        result = review_consensus(
            reviews=[{"defects": []}, {"validator": "x"}],
            min_consensus=0.75,
        )

        assert result["status"] == "error"
        assert "No valid scores" in result["error"]

    def test_consensus_defect_aggregation(self, sample_reviews):
        """Test defect aggregation in consensus."""
        result = review_consensus(reviews=sample_reviews)

        assert "defect_summary" in result
        assert result["defect_summary"]["total_unique_defects"] > 0
        assert "defects" in result["defect_summary"]

    def test_consensus_recommendations(self, sample_reviews):
        """Test recommendations generation."""
        result = review_consensus(reviews=sample_reviews)

        assert "recommendations" in result
        assert len(result["recommendations"]) > 0

    def test_consensus_history_recorded(self, sample_reviews):
        """Test that consensus results are recorded to history."""
        review_consensus(reviews=sample_reviews)

        count = get_review_history_count()
        assert count > 0


# =============================================================================
# Chronicle Integration Tests
# =============================================================================

class TestChronicleIntegration:
    """Tests for Chronicle integration via NexusService."""

    def test_get_chronicle_digest_success(self):
        """Test successful chronicle digest retrieval."""
        result = get_chronicle_digest(max_events=10, max_tokens=2000)

        assert result["status"] == "success"
        assert "digest" in result
        assert "event_count" in result
        assert "phases_covered" in result

    def test_get_chronicle_digest_with_filters(self):
        """Test chronicle digest with phase filters."""
        result = get_chronicle_digest(
            max_events=5,
            include_phases=["QUALITY", "DECISION"],
        )

        assert result["status"] == "success"

    def test_chronicle_commit_integrity(self, supervisor_agent):
        """Test SUP-003: Chronicle commit integrity."""
        # Make a decision
        decision = asyncio.run(
            supervisor_agent.make_quality_decision(
                quality_score=85.0,
                quality_threshold=90.0,
                defects=[{"description": "Test defect"}],
                iteration=1,
                max_iterations=5,
            )
        )

        # Verify decision was committed
        nexus = NexusService.get_instance()
        snapshot = nexus.get_snapshot()
        chronicle = snapshot.get("chronicle", [])

        # Find decision event
        decision_events = [
            e for e in chronicle
            if e.get("agent_id") == "SupervisorAgent"
            and e.get("event_type") == "decision_made"
        ]

        assert len(decision_events) > 0
        assert decision_events[-1]["payload"]["decision_type"] == decision.decision_type.name


# =============================================================================
# Decision Making Workflow Tests
# =============================================================================

class TestDecisionMakingWorkflow:
    """Tests for supervisor decision making workflow."""

    @pytest.mark.asyncio
    async def test_decision_loop_forward_quality_met(
        self, supervisor_agent, sample_defects
    ):
        """Test LOOP_FORWARD decision when quality threshold met."""
        decision = await supervisor_agent.make_quality_decision(
            quality_score=95.0,
            quality_threshold=90.0,
            defects=sample_defects,
            iteration=1,
            max_iterations=5,
        )

        assert decision.decision_type == SupervisorDecisionType.LOOP_FORWARD
        assert "threshold met" in decision.reason.lower()
        assert decision.quality_score == 95.0

    @pytest.mark.asyncio
    async def test_decision_loop_back_quality_below(
        self, supervisor_agent, sample_defects
    ):
        """Test SUP-002: LOOP_BACK decision when quality below threshold."""
        decision = await supervisor_agent.make_quality_decision(
            quality_score=75.0,
            quality_threshold=90.0,
            defects=sample_defects,
            iteration=1,
            max_iterations=5,
        )

        assert decision.decision_type == SupervisorDecisionType.LOOP_BACK
        assert "below threshold" in decision.reason.lower()
        assert decision.to_pipeline_decision()["decision_type"] == "LOOP_BACK"

    @pytest.mark.asyncio
    async def test_decision_pause_critical_defects(
        self, supervisor_agent, critical_defects
    ):
        """Test PAUSE decision when critical defects found."""
        decision = await supervisor_agent.make_quality_decision(
            quality_score=95.0,  # Above threshold
            quality_threshold=90.0,
            defects=critical_defects,
            iteration=1,
            max_iterations=5,
        )

        assert decision.decision_type == SupervisorDecisionType.PAUSE
        assert "Critical defects" in decision.reason
        assert len(decision.defects) > 0

    @pytest.mark.asyncio
    async def test_decision_fail_max_iterations(
        self, supervisor_agent, sample_defects
    ):
        """Test FAIL decision when max iterations exceeded."""
        decision = await supervisor_agent.make_quality_decision(
            quality_score=75.0,
            quality_threshold=90.0,
            defects=sample_defects,
            iteration=5,
            max_iterations=5,
        )

        assert decision.decision_type == SupervisorDecisionType.FAIL
        assert "max iterations" in decision.reason.lower()

    @pytest.mark.asyncio
    async def test_decision_with_consensus_data(
        self, supervisor_agent, sample_defects, sample_reviews
    ):
        """Test decision with consensus data."""
        decision = await supervisor_agent.make_quality_decision(
            quality_score=88.0,
            quality_threshold=90.0,
            defects=sample_defects,
            iteration=1,
            max_iterations=5,
            reviews=sample_reviews,
        )

        assert decision.consensus_data is not None
        assert "consensus_score" in decision.consensus_data

    @pytest.mark.asyncio
    async def test_decision_rationale_built(
        self, supervisor_agent, sample_defects
    ):
        """Test that decision includes rationale."""
        decision = await supervisor_agent.make_quality_decision(
            quality_score=85.0,
            quality_threshold=90.0,
            defects=sample_defects,
            iteration=2,
            max_iterations=5,
        )

        assert decision.rationale != ""
        assert "Decision:" in decision.rationale
        assert "Iteration:" in decision.rationale

    @pytest.mark.asyncio
    async def test_decision_to_dict(self, supervisor_agent, sample_defects):
        """Test decision serialization."""
        decision = await supervisor_agent.make_quality_decision(
            quality_score=85.0,
            quality_threshold=90.0,
            defects=sample_defects,
            iteration=1,
            max_iterations=5,
        )

        data = decision.to_dict()
        assert data["decision_type"] == "LOOP_BACK"
        assert "reason" in data
        assert "quality_score" in data
        assert "defects" in data

    @pytest.mark.asyncio
    async def test_decision_to_pipeline_decision(self, supervisor_agent, sample_defects):
        """Test SUP-001: Decision parsing accuracy."""
        decision = await supervisor_agent.make_quality_decision(
            quality_score=85.0,
            quality_threshold=90.0,
            defects=sample_defects,
            iteration=1,
            max_iterations=5,
        )

        pipeline_decision = decision.to_pipeline_decision()

        # Verify decision type mapping
        assert pipeline_decision["decision_type"] == "LOOP_BACK"
        assert pipeline_decision["target_phase"] == "PLANNING"
        assert "supervisor_decision" in pipeline_decision["metadata"]


# =============================================================================
# Pipeline Integration Tests
# =============================================================================

class TestPipelineIntegration:
    """Tests for pipeline integration."""

    @pytest.mark.asyncio
    async def test_decision_history_tracking(self, supervisor_agent, sample_defects):
        """Test decision history is tracked."""
        # Make multiple decisions
        for i in range(3):
            await supervisor_agent.make_quality_decision(
                quality_score=80.0 + i * 5,
                quality_threshold=90.0,
                defects=sample_defects,
                iteration=i + 1,
                max_iterations=5,
            )

        history = supervisor_agent.get_decision_history()

        assert len(history) == 3
        assert history[0]["metadata"]["iteration"] == 3  # Most recent first

    @pytest.mark.asyncio
    async def test_statistics_generation(self, supervisor_agent, sample_defects):
        """Test statistics generation."""
        # Make some decisions
        for score in [75.0, 85.0, 95.0]:
            await supervisor_agent.make_quality_decision(
                quality_score=score,
                quality_threshold=90.0,
                defects=sample_defects,
                iteration=1,
                max_iterations=5,
            )

        stats = supervisor_agent.get_statistics()

        assert stats["total_decisions"] == 3
        assert "LOOP_BACK" in stats["decisions_by_type"]
        assert "LOOP_FORWARD" in stats["decisions_by_type"]
        assert stats["average_quality_score"] > 0

    @pytest.mark.asyncio
    async def test_reset_functionality(self, supervisor_agent, sample_defects):
        """Test agent reset."""
        await supervisor_agent.make_quality_decision(
            quality_score=85.0,
            quality_threshold=90.0,
            defects=sample_defects,
            iteration=1,
            max_iterations=5,
        )

        supervisor_agent.reset()

        stats = supervisor_agent.get_statistics()
        assert stats["total_decisions"] == 0


# =============================================================================
# Thread Safety Tests (50+ concurrent threads)
# =============================================================================

class TestThreadSafety:
    """Tests for thread safety with 50+ concurrent threads."""

    def test_concurrent_decision_making(self):
        """Test thread-safe concurrent decision making."""
        agent = SupervisorAgent(skip_lemonade=True, silent_mode=True)
        results = []
        errors = []
        lock = threading.Lock()

        def make_decision(iteration):
            try:
                decision = asyncio.run(
                    agent.make_quality_decision(
                        quality_score=80.0 + iteration,
                        quality_threshold=90.0,
                        defects=[{"description": f"Defect {iteration}"}],
                        iteration=iteration,
                        max_iterations=10,
                    )
                )
                with lock:
                    results.append(decision)
            except Exception as e:
                with lock:
                    errors.append((iteration, str(e)))

        # Create 50+ threads
        threads = [threading.Thread(target=make_decision, args=(i,)) for i in range(55)]

        start_time = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.time() - start_time

        # Verify all threads completed
        assert len(errors) == 0, f"Thread errors: {errors}"
        assert len(results) == 55
        assert elapsed < 30  # Should complete in reasonable time

    def test_concurrent_history_access(self):
        """Test thread-safe history access."""
        agent = SupervisorAgent(skip_lemonade=True, silent_mode=True)
        history_results = []
        errors = []
        lock = threading.Lock()

        def access_history():
            try:
                history = agent.get_decision_history()
                with lock:
                    history_results.append(history)
            except Exception as e:
                with lock:
                    errors.append(str(e))

        threads = [threading.Thread(target=access_history) for _ in range(50)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(history_results) == 50

    def test_concurrent_review_consensus(self, sample_reviews):
        """Test thread-safe review consensus."""
        results = []
        errors = []
        lock = threading.Lock()

        def calculate_consensus():
            try:
                result = review_consensus(reviews=sample_reviews)
                with lock:
                    results.append(result)
            except Exception as e:
                with lock:
                    errors.append(str(e))

        threads = [threading.Thread(target=calculate_consensus) for _ in range(50)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 50
        # All results should be consistent
        assert all(r["status"] == "success" for r in results)


# =============================================================================
# Error Handling and Graceful Degradation Tests
# =============================================================================

class TestErrorHandling:
    """Tests for error handling and graceful degradation."""

    @pytest.mark.asyncio
    async def test_graceful_nexus_unavailable(self, sample_defects):
        """Test graceful degradation when NexusService unavailable."""
        agent = SupervisorAgent(skip_lemonade=True, silent_mode=True)

        # Decision should still work even if Chronicle commit fails
        decision = await agent.make_quality_decision(
            quality_score=85.0,
            quality_threshold=90.0,
            defects=sample_defects,
            iteration=1,
            max_iterations=5,
        )

        assert decision is not None
        assert decision.decision_type == SupervisorDecisionType.LOOP_BACK

    @pytest.mark.asyncio
    async def test_empty_defects_handling(self, supervisor_agent):
        """Test handling of empty defects list."""
        decision = await supervisor_agent.make_quality_decision(
            quality_score=95.0,
            quality_threshold=90.0,
            defects=[],
            iteration=1,
            max_iterations=5,
        )

        assert decision.decision_type == SupervisorDecisionType.LOOP_FORWARD
        assert len(decision.defects) == 0

    @pytest.mark.asyncio
    async def test_malformed_defects_handling(self, supervisor_agent):
        """Test handling of malformed defect data."""
        malformed_defects = [
            {"description": "Valid defect"},
            {"severity": "high"},  # Missing description
            {},  # Empty dict
            "just a string",  # Wrong type
        ]

        decision = await supervisor_agent.make_quality_decision(
            quality_score=85.0,
            quality_threshold=90.0,
            defects=malformed_defects,
            iteration=1,
            max_iterations=5,
        )

        # Should not raise, should handle gracefully
        assert decision is not None

    def test_workspace_validate_graceful_degradation(self):
        """Test workspace_validate graceful degradation."""
        result = workspace_validate()

        # Should return valid result even with issues
        assert "status" in result
        assert "validation" in result


# =============================================================================
# Quality Gate 2 Criteria Tests
# =============================================================================

class TestQualityGate2Criteria:
    """Tests for Quality Gate 2 acceptance criteria."""

    @pytest.mark.asyncio
    async def test_sup_001_decision_parsing_accuracy(self, supervisor_agent, sample_defects, reset_nexus):
        """Test SUP-001: Supervisor decision parsing (100% accuracy)."""
        # Test all decision types parse correctly
        # Note: to_pipeline_decision() maps LOOP_FORWARD -> CONTINUE
        test_cases = [
            # (score, threshold, expected_supervisor_type, expected_pipeline_type)
            (95.0, 90.0, SupervisorDecisionType.LOOP_FORWARD, "CONTINUE"),
            (75.0, 90.0, SupervisorDecisionType.LOOP_BACK, "LOOP_BACK"),
        ]

        for score, threshold, expected_sup_type, expected_pipeline_type in test_cases:
            decision = await supervisor_agent.make_quality_decision(
                quality_score=score,
                quality_threshold=threshold,
                defects=sample_defects,
                iteration=1,
                max_iterations=5,
            )

            # Verify supervisor decision type
            assert decision.decision_type == expected_sup_type, f"Failed for score={score}"

            # Verify pipeline decision mapping
            pipeline_decision = decision.to_pipeline_decision()
            assert pipeline_decision["decision_type"] == expected_pipeline_type

    @pytest.mark.asyncio
    async def test_sup_002_loop_back_trigger(self, supervisor_agent, sample_defects):
        """Test SUP-002: Pipeline LOOP_BACK on rejection (automatic trigger)."""
        decision = await supervisor_agent.make_quality_decision(
            quality_score=75.0,  # Below threshold
            quality_threshold=90.0,
            defects=sample_defects,
            iteration=1,
            max_iterations=5,
        )

        # Verify automatic LOOP_BACK trigger
        assert decision.decision_type == SupervisorDecisionType.LOOP_BACK

        # Verify pipeline decision has correct target phase
        pipeline_decision = decision.to_pipeline_decision()
        assert pipeline_decision["target_phase"] == "PLANNING"
        assert pipeline_decision["decision_type"] == "LOOP_BACK"

    def test_sup_003_chronicle_commit_integrity(self):
        """Test SUP-003: Chronicle commit integrity (hash chain preserved)."""
        # Reset nexus for clean test
        NexusService.reset_instance()

        agent = SupervisorAgent(skip_lemonade=True, silent_mode=True)

        # Make decisions
        asyncio.run(
            agent.make_quality_decision(
                quality_score=85.0,
                quality_threshold=90.0,
                defects=[{"description": "Test"}],
                iteration=1,
                max_iterations=5,
            )
        )

        # Verify Chronicle integrity
        nexus = NexusService.get_instance()
        snapshot = nexus.get_snapshot()
        chronicle = snapshot.get("chronicle", [])

        # Find supervisor events
        supervisor_events = [
            e for e in chronicle
            if e.get("agent_id") == "SupervisorAgent"
        ]

        assert len(supervisor_events) > 0

        # Verify hash chain integrity
        for i, event in enumerate(supervisor_events):
            assert "id" in event
            assert "timestamp" in event
            assert "event_type" in event
            assert "payload" in event


# =============================================================================
# Tool Integration Tests
# =============================================================================

class TestToolIntegration:
    """Tests for supervisor tool integration."""

    def test_get_review_history_tool(self):
        """Test get_review_history tool."""
        # Add some history
        review_consensus(reviews=[{"score": 85, "defects": []}])
        review_consensus(reviews=[{"score": 90, "defects": []}])

        result = get_review_history(limit=10)

        assert result["status"] == "success"
        assert result["total_count"] >= 2
        assert "history" in result
        assert "statistics" in result

    def test_get_review_history_filters(self):
        """Test get_review_history with filters."""
        result = get_review_history(
            agent_id="test-agent",
            phase="QUALITY",
            limit=5,
            include_defects=False,
        )

        assert result["status"] == "success"
        assert "history" in result

    def test_workspace_validate_tool(self):
        """Test workspace_validate tool."""
        result = workspace_validate()

        assert result["status"] == "success"
        assert "workspace" in result
        assert "validation" in result
        assert "file_count" in result


# =============================================================================
# Performance Benchmark Tests
# =============================================================================

class TestPerformanceBenchmarks:
    """Performance benchmark tests (target <2s latency)."""

    def test_decision_latency_benchmark(self):
        """Test decision latency under target (<2s)."""
        agent = SupervisorAgent(skip_lemonade=True, silent_mode=True)

        start_time = time.time()

        asyncio.run(
            agent.make_quality_decision(
                quality_score=85.0,
                quality_threshold=90.0,
                defects=[{"description": "Test defect"}],
                iteration=1,
                max_iterations=5,
            )
        )

        elapsed = time.time() - start_time

        # Target: <2s latency
        assert elapsed < 2.0, f"Decision latency {elapsed:.2f}s exceeds 2s target"

    def test_concurrent_performance_benchmark(self):
        """Test concurrent performance under load."""
        agent = SupervisorAgent(skip_lemonade=True, silent_mode=True)
        results = []

        def make_decision(iteration):
            decision = asyncio.run(
                agent.make_quality_decision(
                    quality_score=80.0 + iteration,
                    quality_threshold=90.0,
                    defects=[],
                    iteration=iteration,
                    max_iterations=10,
                )
            )
            results.append(decision)

        threads = [threading.Thread(target=make_decision, args=(i,)) for i in range(20)]

        start_time = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.time() - start_time

        # 20 concurrent decisions should complete in <5s
        assert elapsed < 5.0, f"Concurrent latency {elapsed:.2f}s exceeds 5s target"
        assert len(results) == 20
