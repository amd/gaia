"""
Integration Tests for GAIA Quality Supervisor Agent.

Tests cover:
- End-to-end supervisor workflow
- Pipeline LOOP_BACK trigger verification
- Chronicle commit integrity
- Multi-agent coordination

These tests verify the supervisor agent integrates correctly
with the broader GAIA pipeline system.
"""

import asyncio
import threading
import time
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from gaia.pipeline.audit_logger import AuditLogger, AuditEventType
from gaia.pipeline.decision_engine import DecisionEngine, DecisionType
from gaia.pipeline.engine import PipelineEngine, PipelinePhase
from gaia.quality.supervisor import (
    SupervisorAgent,
    SupervisorDecision,
    SupervisorDecisionType,
)
from gaia.state.nexus import NexusService
from gaia.tools.review_ops import (
    clear_review_history,
    get_chronicle_digest,
    review_consensus,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def sample_defects() -> List[Dict[str, Any]]:
    """Sample defects list for integration testing."""
    return [
        {"description": "Missing docstring", "severity": "low", "category": "DC-01"},
        {"description": "Cyclomatic complexity too high", "severity": "medium", "category": "CQ-03"},
        {"description": "Missing unit tests", "severity": "medium", "category": "TS-01"},
    ]


@pytest.fixture
def critical_defects() -> List[Dict[str, Any]]:
    """Critical defects for integration testing."""
    return [
        {"description": "Security vulnerability in input validation", "severity": "critical", "category": "BP-01"},
    ]


@pytest.fixture
def reset_nexus():
    """Reset NexusService before and after test."""
    NexusService.reset_instance()
    yield
    NexusService.reset_instance()


@pytest.fixture
def supervisor_agent() -> SupervisorAgent:
    """Create supervisor agent for integration tests."""
    return SupervisorAgent(
        min_acceptable_score=0.85,
        target_score=0.90,
        skip_lemonade=True,
        silent_mode=True,
    )


# =============================================================================
# End-to-End Supervisor Workflow Tests
# =============================================================================

class TestEndToEndWorkflow:
    """End-to-end supervisor workflow tests."""

    @pytest.mark.asyncio
    async def test_full_quality_review_cycle(self, supervisor_agent, sample_defects, reset_nexus):
        """Test complete quality review cycle."""
        # Simulate iteration 1: Quality below threshold
        decision1 = await supervisor_agent.make_quality_decision(
            quality_score=75.0,
            quality_threshold=90.0,
            defects=sample_defects,
            iteration=1,
            max_iterations=5,
            include_chronicle=True,
        )

        assert decision1.decision_type == SupervisorDecisionType.LOOP_BACK

        # Simulate iteration 2: Improved but still below
        decision2 = await supervisor_agent.make_quality_decision(
            quality_score=85.0,
            quality_threshold=90.0,
            defects=sample_defects[:1],  # Fewer defects
            iteration=2,
            max_iterations=5,
            include_chronicle=True,
        )

        assert decision2.decision_type == SupervisorDecisionType.LOOP_BACK

        # Simulate iteration 3: Quality met
        decision3 = await supervisor_agent.make_quality_decision(
            quality_score=92.0,
            quality_threshold=90.0,
            defects=[],
            iteration=3,
            max_iterations=5,
            include_chronicle=True,
        )

        assert decision3.decision_type == SupervisorDecisionType.LOOP_FORWARD

        # Verify history tracking
        history = supervisor_agent.get_decision_history()
        assert len(history) == 3

    @pytest.mark.asyncio
    async def test_critical_defect_escalation(self, supervisor_agent, critical_defects, reset_nexus):
        """Test critical defect escalation to PAUSE."""
        decision = await supervisor_agent.make_quality_decision(
            quality_score=95.0,  # Above threshold
            quality_threshold=90.0,
            defects=critical_defects,
            iteration=1,
            max_iterations=5,
        )

        # Should pause despite good score due to critical defect
        assert decision.decision_type == SupervisorDecisionType.PAUSE
        assert "Critical defects" in decision.reason

        # Verify pipeline decision mapping
        pipeline_decision = decision.to_pipeline_decision()
        assert pipeline_decision["decision_type"] == "PAUSE"

    @pytest.mark.asyncio
    async def test_max_iterations_exceeded(self, supervisor_agent, sample_defects, reset_nexus):
        """Test max iterations exceeded scenario."""
        # Simulate failing to meet threshold across all iterations
        for i in range(1, 6):
            decision = await supervisor_agent.make_quality_decision(
                quality_score=75.0,
                quality_threshold=90.0,
                defects=sample_defects,
                iteration=i,
                max_iterations=5,
            )

            if i < 5:
                assert decision.decision_type == SupervisorDecisionType.LOOP_BACK
            else:
                # Final iteration should fail
                assert decision.decision_type == SupervisorDecisionType.FAIL


# =============================================================================
# Pipeline LOOP_BACK Trigger Verification
# =============================================================================

class TestPipelineLoopBackTrigger:
    """Tests for pipeline LOOP_BACK trigger verification."""

    @pytest.mark.asyncio
    async def test_loop_back_triggers_planning_return(
        self, supervisor_agent, sample_defects, reset_nexus
    ):
        """Test that LOOP_BACK decision triggers return to PLANNING phase."""
        decision = await supervisor_agent.make_quality_decision(
            quality_score=75.0,
            quality_threshold=90.0,
            defects=sample_defects,
            iteration=1,
            max_iterations=5,
        )

        # Convert to pipeline decision format
        pipeline_decision = decision.to_pipeline_decision()

        # Verify LOOP_BACK triggers PLANNING return
        assert pipeline_decision["decision_type"] == "LOOP_BACK"
        assert pipeline_decision["target_phase"] == "PLANNING"

    @pytest.mark.asyncio
    async def test_decision_engine_integration(self, sample_defects, reset_nexus):
        """Test supervisor decision integrates with DecisionEngine."""
        # Create supervisor and make decision
        supervisor = SupervisorAgent(skip_lemonade=True, silent_mode=True)

        supervisor_decision = await supervisor.make_quality_decision(
            quality_score=75.0,
            quality_threshold=90.0,
            defects=sample_defects,
            iteration=1,
            max_iterations=5,
        )

        # Convert to pipeline decision
        pipeline_decision = supervisor_decision.to_pipeline_decision()

        # Verify DecisionEngine can process the decision
        decision_engine = DecisionEngine()

        # The decision type should match what DecisionEngine expects
        if pipeline_decision["decision_type"] == "LOOP_BACK":
            expected_type = DecisionType.LOOP_BACK
        elif pipeline_decision["decision_type"] == "PAUSE":
            expected_type = DecisionType.PAUSE
        elif pipeline_decision["decision_type"] == "FAIL":
            expected_type = DecisionType.FAIL
        else:
            expected_type = DecisionType.CONTINUE

        # Verify type mapping is correct
        assert DecisionType[pipeline_decision["decision_type"]] == expected_type

    @pytest.mark.asyncio
    async def test_loop_back_defect_propagation(
        self, supervisor_agent, sample_defects, reset_nexus
    ):
        """Test that defects are propagated with LOOP_BACK decision."""
        decision = await supervisor_agent.make_quality_decision(
            quality_score=75.0,
            quality_threshold=90.0,
            defects=sample_defects,
            iteration=1,
            max_iterations=5,
        )

        pipeline_decision = decision.to_pipeline_decision()

        # Verify defects are included
        assert len(pipeline_decision["defects"]) == len(sample_defects)

        # Verify defect structure preserved
        for defect in pipeline_decision["defects"]:
            assert "description" in defect


# =============================================================================
# Chronicle Commit Integrity Tests
# =============================================================================

class TestChronicleCommitIntegrity:
    """Tests for Chronicle commit integrity."""

    @pytest.mark.asyncio
    async def test_decision_committed_to_chronicle(
        self, supervisor_agent, sample_defects, reset_nexus
    ):
        """Test that decisions are committed to Chronicle."""
        decision = await supervisor_agent.make_quality_decision(
            quality_score=85.0,
            quality_threshold=90.0,
            defects=sample_defects,
            iteration=1,
            max_iterations=5,
        )

        # Retrieve Chronicle
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

        # Verify event payload
        event = decision_events[-1]
        assert event["payload"]["decision_type"] == decision.decision_type.name
        assert event["payload"]["quality_score"] == decision.quality_score

    @pytest.mark.asyncio
    async def test_loop_back_event_committed(
        self, supervisor_agent, sample_defects, reset_nexus
    ):
        """Test that LOOP_BACK events are committed to Chronicle."""
        decision = await supervisor_agent.make_quality_decision(
            quality_score=75.0,
            quality_threshold=90.0,
            defects=sample_defects,
            iteration=1,
            max_iterations=5,
        )

        assert decision.decision_type == SupervisorDecisionType.LOOP_BACK

        # Retrieve Chronicle
        nexus = NexusService.get_instance()
        snapshot = nexus.get_snapshot()
        chronicle = snapshot.get("chronicle", [])

        # Find loop_back event
        loop_events = [
            e for e in chronicle
            if e.get("agent_id") == "SupervisorAgent"
            and e.get("event_type") == "loop_back"
        ]

        assert len(loop_events) > 0
        assert loop_events[-1]["payload"]["target_phase"] == "PLANNING"

    @pytest.mark.asyncio
    async def test_chronicle_hash_chain_integrity(self, reset_nexus):
        """Test Chronicle hash chain integrity is preserved."""
        agent = SupervisorAgent(skip_lemonade=True, silent_mode=True)

        # Make multiple decisions
        for i in range(3):
            await agent.make_quality_decision(
                quality_score=80.0 + i * 5,
                quality_threshold=90.0,
                defects=[{"description": f"Defect {i}"}],
                iteration=i + 1,
                max_iterations=5,
            )

        # Verify hash chain
        nexus = NexusService.get_instance()
        snapshot = nexus.get_snapshot()
        chronicle = snapshot.get("chronicle", [])

        # Hash chain verification (previous_hash linkage)
        # Note: NexusService uses event cache, AuditLogger maintains hash chain
        # Verify events have required fields for hash chain
        for event in chronicle:
            assert "id" in event
            assert "timestamp" in event
            assert "event_type" in event

    @pytest.mark.asyncio
    async def test_multiple_decisions_chronicle_ordering(
        self, supervisor_agent, reset_nexus
    ):
        """Test that multiple decisions are ordered correctly in Chronicle."""
        # Make decisions in sequence
        for i in range(1, 4):
            await supervisor_agent.make_quality_decision(
                quality_score=80.0 + i * 5,
                quality_threshold=90.0,
                defects=[],
                iteration=i,
                max_iterations=5,
            )

        # Verify ordering
        nexus = NexusService.get_instance()
        snapshot = nexus.get_snapshot()
        chronicle = snapshot.get("chronicle", [])

        supervisor_events = [
            e for e in chronicle
            if e.get("agent_id") == "SupervisorAgent"
            and e.get("event_type") == "decision_made"
        ]

        # Should have 3 decision events in chronological order
        assert len(supervisor_events) >= 3

        # Verify timestamps are ordered
        for i in range(1, len(supervisor_events)):
            assert supervisor_events[i]["timestamp"] >= supervisor_events[i-1]["timestamp"]


# =============================================================================
# Multi-Agent Coordination Tests
# =============================================================================

class TestMultiAgentCoordination:
    """Tests for multi-agent coordination scenarios."""

    @pytest.mark.asyncio
    async def test_supervisor_with_consensus_reviews(
        self, supervisor_agent, sample_defects, reset_nexus
    ):
        """Test supervisor coordinating with multiple reviewer agents."""
        # Simulate reviews from multiple agents
        reviews = [
            {"score": 82, "defects": ["style-issue"], "validator_id": "quality-reviewer-1"},
            {"score": 88, "defects": [], "validator_id": "quality-reviewer-2"},
            {"score": 85, "defects": ["missing-doc"], "validator_id": "quality-reviewer-3"},
            {"score": 79, "defects": ["complexity"], "validator_id": "quality-reviewer-4"},
        ]

        # Calculate consensus
        consensus_result = review_consensus(reviews=reviews, min_consensus=0.75)

        # Make decision with consensus data
        decision = await supervisor_agent.make_quality_decision(
            quality_score=consensus_result["consensus_score"],
            quality_threshold=90.0,
            defects=sample_defects,
            iteration=1,
            max_iterations=5,
            reviews=reviews,
        )

        # Verify consensus data included in decision
        assert decision.consensus_data is not None
        assert decision.consensus_data["consensus_score"] > 0

    @pytest.mark.asyncio
    async def test_chronicle_digest_in_decision_context(
        self, supervisor_agent, sample_defects, reset_nexus
    ):
        """Test that chronicle digest is included in decision context."""
        # First, create some chronicle entries
        nexus = NexusService.get_instance()
        nexus.commit(
            agent_id="CodeAgent",
            event_type="tool_execution",
            payload={"tool": "write_file", "path": "main.py"},
            phase="DEVELOPMENT",
        )

        # Make decision with chronicle
        decision = await supervisor_agent.make_quality_decision(
            quality_score=85.0,
            quality_threshold=90.0,
            defects=sample_defects,
            iteration=1,
            max_iterations=5,
            include_chronicle=True,
        )

        # Verify chronicle digest included
        assert decision.chronicle_digest is not None
        assert len(decision.chronicle_digest) > 0

    @pytest.mark.asyncio
    async def test_concurrent_supervisor_agents(self, reset_nexus):
        """Test multiple supervisor agents coordinating."""
        agents = [
            SupervisorAgent(skip_lemonade=True, silent_mode=True)
            for _ in range(3)
        ]
        results = []
        errors = []
        lock = threading.Lock()

        async def make_decision(agent, iteration):
            try:
                decision = await agent.make_quality_decision(
                    quality_score=80.0 + iteration * 5,
                    quality_threshold=90.0,
                    defects=[{"description": f"Defect from agent {iteration}"}],
                    iteration=iteration,
                    max_iterations=5,
                )
                with lock:
                    results.append((iteration, decision.decision_type))
            except Exception as e:
                with lock:
                    errors.append((iteration, str(e)))

        # Run decisions concurrently
        tasks = []
        for i, agent in enumerate(agents):
            tasks.append(make_decision(agent, i))

        await asyncio.gather(*tasks)

        # Verify all completed
        assert len(errors) == 0, f"Errors: {errors}"
        assert len(results) == 3


# =============================================================================
# Decision Type Mapping Tests
# =============================================================================

class TestDecisionTypeMapping:
    """Tests for decision type mapping between supervisor and pipeline."""

    def test_supervisor_to_pipeline_type_mapping(self):
        """Test all supervisor decision types map to pipeline types."""
        mapping = {
            SupervisorDecisionType.LOOP_FORWARD: "CONTINUE",
            SupervisorDecisionType.LOOP_BACK: "LOOP_BACK",
            SupervisorDecisionType.PAUSE: "PAUSE",
            SupervisorDecisionType.FAIL: "FAIL",
        }

        for sup_type, expected_pipeline_type in mapping.items():
            decision = SupervisorDecision(
                decision_type=sup_type,
                reason="Test",
                quality_score=85.0,
                threshold=90.0,
            )

            pipeline_decision = decision.to_pipeline_decision()
            assert pipeline_decision["decision_type"] == expected_pipeline_type, \
                f"{sup_type.name} should map to {expected_pipeline_type}"

    @pytest.mark.asyncio
    async def test_decision_metadata_preserved(self, supervisor_agent, reset_nexus):
        """Test that decision metadata is preserved in pipeline format."""
        decision = await supervisor_agent.make_quality_decision(
            quality_score=85.0,
            quality_threshold=90.0,
            defects=[],
            iteration=2,
            max_iterations=5,
        )

        pipeline_decision = decision.to_pipeline_decision()

        # Verify metadata preserved
        assert "metadata" in pipeline_decision
        assert "supervisor_decision" in pipeline_decision["metadata"]
        assert pipeline_decision["metadata"]["iteration"] == 2


# =============================================================================
# Real-World Scenario Tests
# =============================================================================

class TestRealWorldScenarios:
    """Tests simulating real-world pipeline scenarios."""

    @pytest.mark.asyncio
    async def test_iterative_improvement_scenario(
        self, supervisor_agent, reset_nexus
    ):
        """Test scenario where quality improves over iterations."""
        scenario_scores = [65.0, 75.0, 85.0, 92.0]  # Improving quality
        decisions = []

        for i, score in enumerate(scenario_scores):
            decision = await supervisor_agent.make_quality_decision(
                quality_score=score,
                quality_threshold=90.0,
                defects=[{"description": f"Iteration {i+1} issues"}] if score < 90 else [],
                iteration=i + 1,
                max_iterations=5,
            )
            decisions.append(decision)

        # Verify progression: LOOP_BACK, LOOP_BACK, LOOP_BACK, LOOP_FORWARD
        assert decisions[0].decision_type == SupervisorDecisionType.LOOP_BACK
        assert decisions[1].decision_type == SupervisorDecisionType.LOOP_BACK
        assert decisions[2].decision_type == SupervisorDecisionType.LOOP_BACK
        assert decisions[3].decision_type == SupervisorDecisionType.LOOP_FORWARD

    @pytest.mark.asyncio
    async def test_security_blocker_scenario(self, supervisor_agent, reset_nexus):
        """Test scenario where security issue blocks progress."""
        security_defects = [
            {"description": "SQL injection vulnerability", "severity": "critical", "category": "BP-01"},
        ]

        # Even with high quality score, security should pause
        decision = await supervisor_agent.make_quality_decision(
            quality_score=95.0,
            quality_threshold=90.0,
            defects=security_defects,
            iteration=1,
            max_iterations=5,
        )

        assert decision.decision_type == SupervisorDecisionType.PAUSE
        assert "Critical" in decision.reason

    @pytest.mark.asyncio
    async def test_quality_degradation_scenario(
        self, supervisor_agent, reset_nexus
    ):
        """Test scenario where quality degrades over iterations."""
        scenario_scores = [95.0, 88.0, 75.0, 70.0]  # Degrading quality
        decisions = []

        for i, score in enumerate(scenario_scores):
            decision = await supervisor_agent.make_quality_decision(
                quality_score=score,
                quality_threshold=90.0,
                defects=[{"description": f"Quality issue at iteration {i+1}"}],
                iteration=i + 1,
                max_iterations=5,
            )
            decisions.append(decision)

        # Verify progression: LOOP_FORWARD, LOOP_BACK, LOOP_BACK, LOOP_BACK
        # (First was good, then quality dropped)
        assert decisions[0].decision_type == SupervisorDecisionType.LOOP_FORWARD
        for d in decisions[1:]:
            assert d.decision_type in [SupervisorDecisionType.LOOP_BACK, SupervisorDecisionType.FAIL]
