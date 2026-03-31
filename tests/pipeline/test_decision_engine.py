"""
Tests for GAIA Decision Engine.

Tests cover:
- Decision evaluation logic
- Critical defect detection
- Threshold checking
- Iteration limits
"""

import pytest

from gaia.pipeline.decision_engine import (
    Decision,
    DecisionEngine,
    DecisionType,
)


class TestDecisionType:
    """Tests for DecisionType enum."""

    def test_is_terminal(self):
        """Test terminal decision detection."""
        assert DecisionType.COMPLETE.is_terminal()
        assert DecisionType.FAIL.is_terminal()
        assert not DecisionType.CONTINUE.is_terminal()
        assert not DecisionType.LOOP_BACK.is_terminal()
        assert not DecisionType.PAUSE.is_terminal()

    def test_requires_action(self):
        """Test action-requiring decisions."""
        assert DecisionType.PAUSE.requires_action()
        assert DecisionType.FAIL.requires_action()
        assert not DecisionType.CONTINUE.requires_action()
        assert not DecisionType.LOOP_BACK.requires_action()
        assert not DecisionType.COMPLETE.requires_action()


class TestDecision:
    """Tests for Decision dataclass."""

    def test_continue_decision(self):
        """Test CONTINUE decision creation."""
        decision = Decision.continue_decision(reason="Quality threshold met")
        assert decision.decision_type == DecisionType.CONTINUE
        assert "Quality" in decision.reason

    def test_loop_back_decision(self):
        """Test LOOP_BACK decision creation."""
        defects = [{"description": "Bug found"}]
        decision = Decision.loop_back_decision(
            reason="Quality below threshold",
            target_phase="PLANNING",
            defects=defects,
        )
        assert decision.decision_type == DecisionType.LOOP_BACK
        assert decision.target_phase == "PLANNING"
        assert len(decision.defects) == 1

    def test_pause_decision(self):
        """Test PAUSE decision creation."""
        defects = [{"description": "Critical security issue"}]
        decision = Decision.pause_decision(
            reason="Critical defects found",
            defects=defects,
        )
        assert decision.decision_type == DecisionType.PAUSE
        assert len(decision.defects) == 1

    def test_complete_decision(self):
        """Test COMPLETE decision creation."""
        decision = Decision.complete_decision(
            reason="All phases completed successfully"
        )
        assert decision.decision_type == DecisionType.COMPLETE

    def test_fail_decision(self):
        """Test FAIL decision creation."""
        defects = [{"description": "Unfixable issue"}]
        decision = Decision.fail_decision(
            reason="Max iterations exceeded",
            defects=defects,
        )
        assert decision.decision_type == DecisionType.FAIL
        assert len(decision.defects) == 1

    def test_to_dict(self):
        """Test decision serialization."""
        decision = Decision.continue_decision(
            reason="Test reason",
            metadata={"score": 0.95},
        )
        data = decision.to_dict()
        assert data["decision_type"] == "CONTINUE"
        assert data["reason"] == "Test reason"
        assert data["metadata"]["score"] == 0.95


class TestDecisionEngine:
    """Tests for DecisionEngine class."""

    @pytest.fixture
    def engine(self) -> DecisionEngine:
        """Create test decision engine."""
        return DecisionEngine(config={"critical_patterns": ["security", "data loss"]})

    def test_quality_above_threshold_continues(self, engine: DecisionEngine):
        """Test decision when quality is above threshold."""
        decision = engine.evaluate(
            phase_name="DEVELOPMENT",
            quality_score=0.95,
            quality_threshold=0.90,
            defects=[],
            iteration=1,
            max_iterations=5,
            is_final_phase=False,
        )

        assert decision.decision_type == DecisionType.CONTINUE
        assert "threshold" in decision.reason.lower()

    def test_quality_above_threshold_completes_final(self, engine: DecisionEngine):
        """Test decision when quality is above threshold in final phase."""
        decision = engine.evaluate(
            phase_name="DECISION",
            quality_score=0.95,
            quality_threshold=0.90,
            defects=[],
            iteration=1,
            max_iterations=5,
            is_final_phase=True,
        )

        assert decision.decision_type == DecisionType.COMPLETE

    def test_quality_below_threshold_loops_back(self, engine: DecisionEngine):
        """Test decision when quality is below threshold."""
        defects = [{"description": "Minor issue"}]
        decision = engine.evaluate(
            phase_name="DEVELOPMENT",
            quality_score=0.75,
            quality_threshold=0.90,
            defects=defects,
            iteration=1,
            max_iterations=5,
            is_final_phase=False,
        )

        assert decision.decision_type == DecisionType.LOOP_BACK
        assert decision.target_phase == "PLANNING"
        assert len(decision.defects) == 1

    def test_quality_below_threshold_fails_max_iterations(self, engine: DecisionEngine):
        """Test decision when max iterations exceeded."""
        defects = [{"description": "Issue"}]
        decision = engine.evaluate(
            phase_name="DEVELOPMENT",
            quality_score=0.75,
            quality_threshold=0.90,
            defects=defects,
            iteration=5,
            max_iterations=5,
            is_final_phase=False,
        )

        assert decision.decision_type == DecisionType.FAIL
        assert "max iterations" in decision.reason.lower()

    def test_critical_defect_pauses(self, engine: DecisionEngine):
        """Test decision when critical defect found."""
        defects = [
            {"description": "Security vulnerability detected", "severity": "critical"}
        ]
        decision = engine.evaluate(
            phase_name="DEVELOPMENT",
            quality_score=0.95,  # Above threshold
            quality_threshold=0.90,
            defects=defects,
            iteration=1,
            max_iterations=5,
            is_final_phase=False,
        )

        assert decision.decision_type == DecisionType.PAUSE
        assert "Critical defects" in decision.reason

    def test_critical_pattern_detection(self, engine: DecisionEngine):
        """Test critical pattern detection in defects."""
        defects = [
            {
                "description": "Security vulnerability detected in input validation",
                "severity": "high",
            }
        ]
        decision = engine.evaluate(
            phase_name="DEVELOPMENT",
            quality_score=0.95,
            quality_threshold=0.90,
            defects=defects,
            iteration=1,
            max_iterations=5,
            is_final_phase=False,
        )

        # "security" is a critical pattern
        assert decision.decision_type == DecisionType.PAUSE

    def test_severity_critical_detection(self, engine: DecisionEngine):
        """Test detection based on severity field."""
        defects = [{"description": "Some issue", "severity": "critical"}]
        decision = engine.evaluate(
            phase_name="DEVELOPMENT",
            quality_score=0.95,
            quality_threshold=0.90,
            defects=defects,
            iteration=1,
            max_iterations=5,
            is_final_phase=False,
        )

        assert decision.decision_type == DecisionType.PAUSE

    def test_multiple_defects_tracked(self, engine: DecisionEngine):
        """Test multiple defects are tracked in decision."""
        defects = [
            {"description": "Issue 1", "severity": "low"},
            {"description": "Issue 2", "severity": "medium"},
            {"description": "Issue 3", "severity": "high"},
        ]
        decision = engine.evaluate(
            phase_name="DEVELOPMENT",
            quality_score=0.75,
            quality_threshold=0.90,
            defects=defects,
            iteration=1,
            max_iterations=5,
            is_final_phase=False,
        )

        assert decision.decision_type == DecisionType.LOOP_BACK
        assert len(decision.defects) == 3

    def test_metadata_included(self, engine: DecisionEngine):
        """Test metadata is included in decision."""
        decision = engine.evaluate(
            phase_name="DEVELOPMENT",
            quality_score=0.85,
            quality_threshold=0.90,
            defects=[],
            iteration=2,
            max_iterations=5,
            is_final_phase=False,
        )

        assert "score" in decision.metadata
        assert decision.metadata["score"] == 0.85
        assert "threshold" in decision.metadata
        assert "iteration" in decision.metadata

    def test_evaluate_simple(self, engine: DecisionEngine):
        """Test simple evaluation method."""
        decision_type = engine.evaluate_simple(
            quality_score=0.95,
            quality_threshold=0.90,
            has_critical_defects=False,
        )
        assert decision_type == DecisionType.CONTINUE

        decision_type = engine.evaluate_simple(
            quality_score=0.80,
            quality_threshold=0.90,
            has_critical_defects=False,
        )
        assert decision_type == DecisionType.LOOP_BACK

        decision_type = engine.evaluate_simple(
            quality_score=0.80,
            quality_threshold=0.90,
            has_critical_defects=True,
        )
        assert decision_type == DecisionType.PAUSE

    def test_should_loop_back(self, engine: DecisionEngine):
        """Test should_loop_back method."""
        should_loop, reason = engine.should_loop_back(
            quality_score=0.80,
            quality_threshold=0.90,
            iteration=1,
            max_iterations=5,
        )
        assert should_loop is True
        assert "below threshold" in reason

        should_loop, reason = engine.should_loop_back(
            quality_score=0.95,
            quality_threshold=0.90,
            iteration=1,
            max_iterations=5,
        )
        assert should_loop is False

    def test_get_statistics(self, engine: DecisionEngine):
        """Test getting engine statistics."""
        stats = engine.get_statistics()
        assert "critical_patterns" in stats
        assert len(stats["critical_patterns"]) > 0


class TestDecisionEngineCustomPatterns:
    """Tests for custom critical patterns."""

    def test_custom_critical_patterns(self):
        """Test custom critical patterns work."""
        engine = DecisionEngine(
            config={"critical_patterns": ["custom-pattern", "my-critical"]}
        )

        defects = [{"description": "custom-pattern detected in code"}]
        decision = engine.evaluate(
            phase_name="DEVELOPMENT",
            quality_score=0.95,
            quality_threshold=0.90,
            defects=defects,
            iteration=1,
            max_iterations=5,
            is_final_phase=False,
        )

        assert decision.decision_type == DecisionType.PAUSE

    def test_default_patterns_used(self):
        """Test default patterns are used when not specified."""
        engine = DecisionEngine()  # No config
        stats = engine.get_statistics()
        assert len(stats["critical_patterns"]) > 0
        assert "security" in str(stats["critical_patterns"]).lower()
