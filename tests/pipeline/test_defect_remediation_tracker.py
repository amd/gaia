"""
Tests for GAIA DefectRemediationTracker.

Tests cover:
- DefectStatus enum and lifecycle methods
- DefectStatusChange dataclass creation and serialization
- DefectStatusTransition enum
- InvalidStatusTransitionError exception
- DefectRemediationTracker core functionality
- Status transition lifecycle enforcement
- Thread safety for concurrent operations
- Analytics calculations (MTTR, MTTV)
- Integration with PhaseContract
"""

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import pytest

from gaia.pipeline.defect_remediation_tracker import (
    TRANSITION_FROM_STATUS,
    TRANSITION_TO_STATUS,
    DefectRemediationTracker,
    DefectStatus,
    DefectStatusChange,
    DefectStatusTransition,
    InvalidStatusTransitionError,
)
from gaia.pipeline.defect_router import (
    Defect,
    DefectSeverity,
)
from gaia.pipeline.defect_router import DefectStatus as RouterDefectStatus
from gaia.pipeline.defect_router import (
    DefectType,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_defect() -> Defect:
    """Create a sample defect for testing."""
    return Defect(
        id="defect-001",
        type=DefectType.MISSING_TESTS,
        severity=DefectSeverity.HIGH,
        description="No unit tests for module",
        phase_detected="QUALITY",
    )


@pytest.fixture
def tracker() -> DefectRemediationTracker:
    """Create a tracker instance for testing."""
    return DefectRemediationTracker(tracker_id="test-tracker")


@pytest.fixture
def tracker_with_data() -> DefectRemediationTracker:
    """Create a tracker with sample data for analytics testing."""
    tracker = DefectRemediationTracker(tracker_id="analytics-test")

    # Add defects and progress them through lifecycle
    for i in range(10):
        defect = Defect(
            id=f"defect-{i:03d}",
            type=DefectType.MISSING_TESTS,
            severity=DefectSeverity.HIGH if i < 3 else DefectSeverity.MEDIUM,
            description=f"Defect {i}",
            phase_detected="QUALITY",
        )
        tracker.add_defect(defect, phase="QUALITY")

        # Progress some to verified
        if i < 7:
            tracker.start_fix(f"defect-{i:03d}")
            tracker.mark_resolved(f"defect-{i:03d}", f"Fixed {i}")
            if i < 5:
                tracker.mark_verified(f"defect-{i:03d}", f"Verified {i}")

    return tracker


# =============================================================================
# DefectStatus Enum Tests
# =============================================================================


class TestDefectStatus:
    """Tests for DefectStatus enum."""

    def test_status_values(self):
        """Test that all status values exist."""
        assert DefectStatus.OPEN is not None
        assert DefectStatus.IN_PROGRESS is not None
        assert DefectStatus.RESOLVED is not None
        assert DefectStatus.VERIFIED is not None
        assert DefectStatus.DEFERRED is not None
        assert DefectStatus.CANNOT_FIX is not None

    def test_is_terminal(self):
        """Test terminal status detection."""
        assert DefectStatus.VERIFIED.is_terminal() is True
        assert DefectStatus.DEFERRED.is_terminal() is True
        assert DefectStatus.CANNOT_FIX.is_terminal() is True

        assert DefectStatus.OPEN.is_terminal() is False
        assert DefectStatus.IN_PROGRESS.is_terminal() is False
        assert DefectStatus.RESOLVED.is_terminal() is False

    def test_is_active(self):
        """Test active status detection."""
        assert DefectStatus.OPEN.is_active() is True
        assert DefectStatus.IN_PROGRESS.is_active() is True

        assert DefectStatus.RESOLVED.is_active() is False
        assert DefectStatus.VERIFIED.is_active() is False
        assert DefectStatus.DEFERRED.is_active() is False
        assert DefectStatus.CANNOT_FIX.is_active() is False


# =============================================================================
# DefectStatusTransition Enum Tests
# =============================================================================


class TestDefectStatusTransition:
    """Tests for DefectStatusTransition enum."""

    def test_transition_from_status(self):
        """Test transition source status mapping."""
        assert (
            TRANSITION_FROM_STATUS[DefectStatusTransition.OPEN_TO_IN_PROGRESS]
            == DefectStatus.OPEN
        )
        assert (
            TRANSITION_FROM_STATUS[DefectStatusTransition.IN_PROGRESS_TO_RESOLVED]
            == DefectStatus.IN_PROGRESS
        )
        assert (
            TRANSITION_FROM_STATUS[DefectStatusTransition.RESOLVED_TO_VERIFIED]
            == DefectStatus.RESOLVED
        )
        assert (
            TRANSITION_FROM_STATUS[DefectStatusTransition.VERIFIED_TO_IN_PROGRESS]
            == DefectStatus.VERIFIED
        )
        assert (
            TRANSITION_FROM_STATUS[DefectStatusTransition.DEFERRED_TO_OPEN]
            == DefectStatus.DEFERRED
        )
        assert (
            TRANSITION_FROM_STATUS[DefectStatusTransition.CANNOT_FIX_TO_OPEN]
            == DefectStatus.CANNOT_FIX
        )

    def test_transition_to_status(self):
        """Test transition target status mapping."""
        assert (
            TRANSITION_TO_STATUS[DefectStatusTransition.OPEN_TO_IN_PROGRESS]
            == DefectStatus.IN_PROGRESS
        )
        assert (
            TRANSITION_TO_STATUS[DefectStatusTransition.IN_PROGRESS_TO_RESOLVED]
            == DefectStatus.RESOLVED
        )
        assert (
            TRANSITION_TO_STATUS[DefectStatusTransition.RESOLVED_TO_VERIFIED]
            == DefectStatus.VERIFIED
        )
        assert (
            TRANSITION_TO_STATUS[DefectStatusTransition.VERIFIED_TO_IN_PROGRESS]
            == DefectStatus.IN_PROGRESS
        )
        assert (
            TRANSITION_TO_STATUS[DefectStatusTransition.DEFERRED_TO_OPEN]
            == DefectStatus.OPEN
        )
        assert (
            TRANSITION_TO_STATUS[DefectStatusTransition.CANNOT_FIX_TO_OPEN]
            == DefectStatus.OPEN
        )


# =============================================================================
# DefectStatusChange Dataclass Tests
# =============================================================================


class TestDefectStatusChange:
    """Tests for DefectStatusChange dataclass."""

    def test_create_status_change(self):
        """Test basic status change creation."""
        change = DefectStatusChange(
            defect_id="defect-001",
            old_status=DefectStatus.OPEN,
            new_status=DefectStatus.IN_PROGRESS,
            description="Starting fix",
        )
        assert change.defect_id == "defect-001"
        assert change.old_status == DefectStatus.OPEN
        assert change.new_status == DefectStatus.IN_PROGRESS
        assert change.description == "Starting fix"
        assert change.changed_by is None
        assert isinstance(change.changed_at, datetime)

    def test_create_status_change_with_all_fields(self):
        """Test status change creation with all fields."""
        change = DefectStatusChange(
            defect_id="defect-001",
            old_status=DefectStatus.OPEN,
            new_status=DefectStatus.IN_PROGRESS,
            changed_by="developer",
            description="Starting fix",
            metadata={"iteration": 1},
        )
        assert change.changed_by == "developer"
        assert change.metadata["iteration"] == 1

    def test_to_dict(self):
        """Test serialization to dictionary."""
        change = DefectStatusChange(
            defect_id="defect-001",
            old_status=DefectStatus.OPEN,
            new_status=DefectStatus.IN_PROGRESS,
        )
        data = change.to_dict()
        assert data["defect_id"] == "defect-001"
        assert data["old_status"] == "OPEN"
        assert data["new_status"] == "IN_PROGRESS"
        assert "changed_at" in data
        assert data["changed_by"] is None
        assert data["description"] == ""

    def test_to_audit_entry(self):
        """Test conversion to audit entry format."""
        change = DefectStatusChange(
            defect_id="defect-001",
            old_status=DefectStatus.OPEN,
            new_status=DefectStatus.IN_PROGRESS,
            changed_by="developer",
            description="Starting fix",
        )
        audit = change.to_audit_entry()
        assert audit["event_type"] == "DEFECT_STATUS_CHANGE"
        assert audit["defect_id"] == "defect-001"
        assert audit["actor"] == "developer"
        assert audit["action"] == "OPEN -> IN_PROGRESS"
        assert audit["description"] == "Starting fix"

    def test_timestamp_default(self):
        """Test that timestamp defaults to current UTC time."""
        before = datetime.now(timezone.utc)
        change = DefectStatusChange(
            defect_id="defect-001",
            old_status=DefectStatus.OPEN,
            new_status=DefectStatus.IN_PROGRESS,
        )
        after = datetime.now(timezone.utc)
        assert before <= change.changed_at <= after

    def test_no_op_warning(self, caplog):
        """Test warning logged for no-op status change."""
        DefectStatusChange(
            defect_id="defect-001",
            old_status=DefectStatus.OPEN,
            new_status=DefectStatus.OPEN,
        )
        assert "no-op" in caplog.text.lower()


# =============================================================================
# InvalidStatusTransitionError Tests
# =============================================================================


class TestInvalidStatusTransitionError:
    """Tests for InvalidStatusTransitionError exception."""

    def test_create_error(self):
        """Test creating transition error."""
        error = InvalidStatusTransitionError(
            defect_id="defect-001",
            current_status=DefectStatus.OPEN,
            requested_status=DefectStatus.VERIFIED,
            allowed_transitions=[DefectStatus.IN_PROGRESS, DefectStatus.DEFERRED],
        )
        assert error.defect_id == "defect-001"
        assert error.current_status == DefectStatus.OPEN
        assert error.requested_status == DefectStatus.VERIFIED
        assert len(error.allowed_transitions) == 2

    def test_error_message(self):
        """Test error message format."""
        error = InvalidStatusTransitionError(
            defect_id="defect-001",
            current_status=DefectStatus.OPEN,
            requested_status=DefectStatus.VERIFIED,
            allowed_transitions=[DefectStatus.IN_PROGRESS],
        )
        message = str(error)
        assert "defect-001" in message
        assert "OPEN" in message
        assert "VERIFIED" in message
        assert "IN_PROGRESS" in message

    def test_error_to_dict(self):
        """Test error serialization."""
        error = InvalidStatusTransitionError(
            defect_id="defect-001",
            current_status=DefectStatus.OPEN,
            requested_status=DefectStatus.VERIFIED,
            allowed_transitions=[DefectStatus.IN_PROGRESS],
        )
        data = error.to_dict()
        assert data["error"] == "InvalidStatusTransitionError"
        assert data["defect_id"] == "defect-001"
        assert data["current_status"] == "OPEN"
        assert data["requested_status"] == "VERIFIED"


# =============================================================================
# DefectRemediationTracker Basic Tests
# =============================================================================


class TestDefectRemediationTracker:
    """Tests for DefectRemediationTracker core functionality."""

    def test_create_tracker(self, tracker):
        """Test tracker creation."""
        assert tracker.tracker_id == "test-tracker"
        assert len(tracker.get_all_defects()) == 0

    def test_create_tracker_auto_id(self):
        """Test tracker creation with auto-generated ID."""
        tracker = DefectRemediationTracker()
        assert tracker.tracker_id.startswith("tracker-")

    def test_add_defect(self, tracker, sample_defect):
        """Test adding a defect."""
        tracker.add_defect(sample_defect, phase="QUALITY")

        retrieved = tracker.get_defect("defect-001")
        assert retrieved is not None
        assert retrieved.status == DefectStatus.OPEN
        assert retrieved.type == DefectType.MISSING_TESTS

    def test_add_defect_none_raises_error(self, tracker):
        """Test that adding None defect raises ValueError."""
        with pytest.raises(ValueError, match="cannot be None"):
            tracker.add_defect(None, phase="QUALITY")

    def test_add_defect_duplicate_ignored(self, tracker, sample_defect, caplog):
        """Test that duplicate defect IDs are ignored."""
        tracker.add_defect(sample_defect, phase="QUALITY")
        tracker.add_defect(sample_defect, phase="QUALITY")  # Duplicate

        assert len(tracker.get_all_defects()) == 1
        assert "already exists" in caplog.text.lower()

    def test_add_defect_non_open_status_reset(self, tracker):
        """Test that non-OPEN status is reset to OPEN."""
        defect = Defect(
            id="defect-002",
            type=DefectType.CODE_STYLE,
            severity=DefectSeverity.LOW,
            status=DefectStatus.RESOLVED,  # Non-OPEN
            description="Test defect",
        )
        tracker.add_defect(defect, phase="DEVELOPMENT")

        retrieved = tracker.get_defect("defect-002")
        assert retrieved.status == DefectStatus.OPEN

    def test_add_defect_creates_audit_record(self, tracker, sample_defect):
        """Test that adding defect creates audit record."""
        tracker.add_defect(sample_defect, phase="QUALITY")

        history = tracker.get_defect_history("defect-001")
        assert len(history) == 1
        assert history[0].new_status == DefectStatus.OPEN
        assert "QUALITY" in history[0].description

    def test_get_defect_not_found(self, tracker):
        """Test getting non-existent defect."""
        result = tracker.get_defect("nonexistent")
        assert result is None

    def test_get_all_defects(self, tracker, sample_defect):
        """Test getting all defects."""
        defect2 = Defect(
            id="defect-002",
            type=DefectType.CODE_STYLE,
            severity=DefectSeverity.LOW,
            description="Another defect",
        )
        tracker.add_defect(sample_defect, phase="QUALITY")
        tracker.add_defect(defect2, phase="DEVELOPMENT")

        all_defects = tracker.get_all_defects()
        assert len(all_defects) == 2


# =============================================================================
# DefectRemediationTracker Status Transition Tests
# =============================================================================


class TestDefectRemediationTrackerTransitions:
    """Tests for defect status transitions."""

    def test_start_fix(self, tracker, sample_defect):
        """Test starting fix (OPEN -> IN_PROGRESS)."""
        tracker.add_defect(sample_defect, phase="QUALITY")
        change = tracker.start_fix("defect-001", changed_by="developer")

        assert change.old_status == DefectStatus.OPEN
        assert change.new_status == DefectStatus.IN_PROGRESS
        assert tracker.get_defect("defect-001").status == DefectStatus.IN_PROGRESS

    def test_mark_resolved(self, tracker, sample_defect):
        """Test marking resolved (IN_PROGRESS -> RESOLVED)."""
        tracker.add_defect(sample_defect, phase="QUALITY")
        tracker.start_fix("defect-001")

        change = tracker.mark_resolved(
            "defect-001",
            description="Added 15 tests",
            metadata={"tests_added": 15},
        )

        assert change.new_status == DefectStatus.RESOLVED
        assert change.description == "Added 15 tests"
        assert change.metadata["tests_added"] == 15

    def test_mark_verified(self, tracker, sample_defect):
        """Test marking verified (RESOLVED -> VERIFIED)."""
        tracker.add_defect(sample_defect, phase="QUALITY")
        tracker.start_fix("defect-001")
        tracker.mark_resolved("defect-001", "Fix applied")

        change = tracker.mark_verified(
            "defect-001",
            notes="QA passed",
            changed_by="qa-team",
        )

        assert change.new_status == DefectStatus.VERIFIED
        assert change.changed_by == "qa-team"

    def test_mark_deferred(self, tracker, sample_defect):
        """Test deferring defect (OPEN -> DEFERRED)."""
        tracker.add_defect(sample_defect, phase="QUALITY")
        change = tracker.mark_deferred(
            "defect-001",
            reason="Low priority",
            changed_by="product-owner",
        )

        assert change.new_status == DefectStatus.DEFERRED
        assert change.metadata["defer_reason"] == "Low priority"

    def test_mark_cannot_fix(self, tracker, sample_defect):
        """Test marking cannot fix (OPEN -> CANNOT_FIX)."""
        tracker.add_defect(sample_defect, phase="QUALITY")
        change = tracker.mark_cannot_fix(
            "defect-001",
            reason="Platform limitation",
        )

        assert change.new_status == DefectStatus.CANNOT_FIX
        assert change.metadata["cannot_fix_reason"] == "Platform limitation"

    def test_invalid_transition_open_to_verified(self, tracker, sample_defect):
        """Test that OPEN -> VERIFIED is invalid."""
        tracker.add_defect(sample_defect, phase="QUALITY")

        with pytest.raises(InvalidStatusTransitionError) as exc_info:
            tracker.mark_verified("defect-001", "QA passed")

        assert exc_info.value.current_status == DefectStatus.OPEN
        assert exc_info.value.requested_status == DefectStatus.VERIFIED
        assert DefectStatus.VERIFIED not in exc_info.value.allowed_transitions

    def test_invalid_transition_open_to_resolved(self, tracker, sample_defect):
        """Test that OPEN -> RESOLVED is invalid."""
        tracker.add_defect(sample_defect, phase="QUALITY")

        with pytest.raises(InvalidStatusTransitionError):
            tracker.mark_resolved("defect-001", "Fixed")

    def test_deferred_to_open(self, tracker, sample_defect):
        """Test DEFERRED -> OPEN transition."""
        tracker.add_defect(sample_defect, phase="QUALITY")
        tracker.mark_deferred("defect-001", "Blocked")
        tracker.start_fix("defect-001")  # DEFERRED -> IN_PROGRESS is valid

        assert tracker.get_defect("defect-001").status == DefectStatus.IN_PROGRESS

    def test_reopen_from_resolved(self, tracker, sample_defect):
        """Test RESOLVED -> OPEN/IN_PROGRESS transition."""
        tracker.add_defect(sample_defect, phase="QUALITY")
        tracker.start_fix("defect-001")
        tracker.mark_resolved("defect-001", "Fix applied")

        # Can reopen from RESOLVED
        tracker._transition_status(
            "defect-001", DefectStatus.IN_PROGRESS, "Needs more work"
        )
        assert tracker.get_defect("defect-001").status == DefectStatus.IN_PROGRESS

    def test_verified_regression(self, tracker, sample_defect):
        """Test VERIFIED -> IN_PROGRESS for regression."""
        tracker.add_defect(sample_defect, phase="QUALITY")
        tracker.start_fix("defect-001")
        tracker.mark_resolved("defect-001", "Fixed")
        tracker.mark_verified("defect-001", "QA passed")

        # Regression - reopen
        tracker._transition_status(
            "defect-001", DefectStatus.IN_PROGRESS, "Regression found"
        )
        assert tracker.get_defect("defect-001").status == DefectStatus.IN_PROGRESS

    def test_not_found_raises_keyerror(self, tracker):
        """Test that operations on non-existent defect raise KeyError."""
        with pytest.raises(KeyError, match="not found"):
            tracker.start_fix("nonexistent")

        with pytest.raises(KeyError, match="not found"):
            tracker.mark_resolved("nonexistent", "Fixed")

        with pytest.raises(KeyError, match="not found"):
            tracker.mark_verified("nonexistent", "Verified")


# =============================================================================
# DefectRemediationTracker Query Tests
# =============================================================================


class TestDefectRemediationTrackerQueries:
    """Tests for defect query methods."""

    def test_get_pending_defects(self, tracker, sample_defect):
        """Test getting pending defects."""
        defect2 = Defect(
            id="defect-002",
            type=DefectType.CODE_STYLE,
            severity=DefectSeverity.LOW,
            description="Style issue",
        )

        tracker.add_defect(sample_defect, phase="QUALITY")
        tracker.add_defect(defect2, phase="DEVELOPMENT")

        # Resolve one
        tracker.start_fix("defect-001")
        tracker.mark_resolved("defect-001", "Fixed")
        tracker.mark_verified("defect-001", "Verified")

        pending = tracker.get_pending_defects()
        assert len(pending) == 1
        assert pending[0].id == "defect-002"

    def test_get_pending_defects_sorted_by_severity(self, tracker):
        """Test that pending defects are sorted by severity."""
        critical = Defect(
            id="defect-critical",
            type=DefectType.SECURITY_VULNERABILITY,
            severity=DefectSeverity.CRITICAL,
            description="Critical security issue",
        )
        low = Defect(
            id="defect-low",
            type=DefectType.CODE_STYLE,
            severity=DefectSeverity.LOW,
            description="Minor style issue",
        )
        high = Defect(
            id="defect-high",
            type=DefectType.MISSING_TESTS,
            severity=DefectSeverity.HIGH,
            description="Missing tests",
        )

        tracker.add_defect(critical, phase="QUALITY")
        tracker.add_defect(low, phase="DEVELOPMENT")
        tracker.add_defect(high, phase="QUALITY")

        pending = tracker.get_pending_defects()
        assert len(pending) == 3
        # Should be sorted: CRITICAL (1), HIGH (2), LOW (4)
        assert pending[0].severity == DefectSeverity.CRITICAL
        assert pending[1].severity == DefectSeverity.HIGH
        assert pending[2].severity == DefectSeverity.LOW

    def test_get_summary(self, tracker, sample_defect):
        """Test getting summary statistics."""
        tracker.add_defect(sample_defect, phase="QUALITY")

        summary = tracker.get_summary()
        assert summary["total"] == 1
        assert summary["by_status"]["OPEN"] == 1
        assert summary["pending_count"] == 1
        assert summary["resolution_rate"] == 0.0

    def test_get_summary_with_mixed_status(self, tracker):
        """Test summary with mixed defect statuses."""
        for i in range(6):
            defect = Defect(
                id=f"defect-{i:03d}",
                type=DefectType.MISSING_TESTS,
                severity=DefectSeverity.MEDIUM,
                description=f"Defect {i}",
            )
            tracker.add_defect(defect, phase="QUALITY")

        # Progress defects
        for i in range(4):
            tracker.start_fix(f"defect-{i:03d}")
            tracker.mark_resolved(f"defect-{i:03d}", "Fixed")
            if i < 2:
                tracker.mark_verified(f"defect-{i:03d}", "Verified")
        tracker.mark_deferred("defect-004", "Low priority")
        tracker.mark_cannot_fix("defect-005", "Platform limitation")

        summary = tracker.get_summary()
        assert summary["total"] == 6
        assert summary["verified_count"] == 2
        assert summary["deferred_count"] == 1
        assert summary["cannot_fix_count"] == 1
        assert summary["pending_count"] == 2  # 2 resolved but not verified
        assert (
            summary["resolution_rate"] == 4 / 6
        )  # 4 out of 6 have terminal status (verified + deferred + cannot_fix)

    def test_get_defect_history(self, tracker, sample_defect):
        """Test getting defect history."""
        tracker.add_defect(sample_defect, phase="QUALITY")
        tracker.start_fix("defect-001")
        tracker.mark_resolved("defect-001", "Fixed")
        tracker.mark_verified("defect-001", "Verified")

        history = tracker.get_defect_history("defect-001")
        assert len(history) == 4  # Initial + 3 transitions

        # Check chronological order
        for i in range(len(history) - 1):
            assert history[i].changed_at <= history[i + 1].changed_at

    def test_get_defect_history_all(self, tracker, sample_defect):
        """Test getting all history without filter."""
        defect2 = Defect(
            id="defect-002",
            type=DefectType.CODE_STYLE,
            severity=DefectSeverity.LOW,
            description="Another defect",
        )
        tracker.add_defect(sample_defect, phase="QUALITY")
        tracker.add_defect(defect2, phase="DEVELOPMENT")
        tracker.start_fix("defect-001")

        all_history = tracker.get_defect_history()
        assert len(all_history) == 3  # 2 initial + 1 transition

    def test_get_defect_history_with_status_filter(self, tracker, sample_defect):
        """Test getting history filtered by status."""
        tracker.add_defect(sample_defect, phase="QUALITY")
        tracker.start_fix("defect-001")
        tracker.mark_resolved("defect-001", "Fixed")
        tracker.mark_verified("defect-001", "Verified")

        verified_history = tracker.get_defect_history(
            status_filter=DefectStatus.VERIFIED
        )
        assert len(verified_history) == 1
        assert verified_history[0].new_status == DefectStatus.VERIFIED

    def test_get_defects_by_phase(self, tracker, sample_defect):
        """Test getting defects by phase."""
        defect2 = Defect(
            id="defect-002",
            type=DefectType.CODE_STYLE,
            severity=DefectSeverity.LOW,
            description="Dev defect",
            phase_detected="DEVELOPMENT",
        )
        tracker.add_defect(sample_defect, phase="QUALITY")
        tracker.add_defect(defect2, phase="DEVELOPMENT")

        quality_defects = tracker.get_defects_by_phase("QUALITY")
        dev_defects = tracker.get_defects_by_phase("DEVELOPMENT")

        assert len(quality_defects) == 1
        assert len(dev_defects) == 1
        assert quality_defects[0].id == "defect-001"

    def test_get_defects_by_phase_empty(self, tracker):
        """Test getting defects for phase with no defects."""
        defects = tracker.get_defects_by_phase("NONEXISTENT")
        assert len(defects) == 0

    def test_get_defects_by_status(self, tracker):
        """Test getting defects by status."""
        for i in range(3):
            defect = Defect(
                id=f"defect-{i:03d}",
                type=DefectType.MISSING_TESTS,
                severity=DefectSeverity.MEDIUM,
                description=f"Defect {i}",
            )
            tracker.add_defect(defect, phase="QUALITY")

        tracker.start_fix("defect-000")
        tracker.mark_resolved("defect-000", "Fixed")
        tracker.mark_verified("defect-000", "Verified")

        open_defects = tracker.get_defects_by_status(DefectStatus.OPEN)
        in_progress = tracker.get_defects_by_status(DefectStatus.IN_PROGRESS)
        verified = tracker.get_defects_by_status(DefectStatus.VERIFIED)

        assert len(open_defects) == 2
        assert len(in_progress) == 0  # Already resolved
        assert len(verified) == 1

    def test_export_audit_log(self, tracker, sample_defect):
        """Test exporting audit log."""
        tracker.add_defect(sample_defect, phase="QUALITY")
        tracker.start_fix("defect-001", changed_by="developer")

        audit_log = tracker.export_audit_log()
        assert len(audit_log) == 2

        # Check audit entry format
        entry = audit_log[1]
        assert entry["event_type"] == "DEFECT_STATUS_CHANGE"
        assert "OPEN -> IN_PROGRESS" in entry["action"]
        assert entry["actor"] == "developer"

    def test_clear(self, tracker, sample_defect):
        """Test clearing all defects."""
        tracker.add_defect(sample_defect, phase="QUALITY")
        tracker.start_fix("defect-001")

        tracker.clear()

        assert len(tracker.get_all_defects()) == 0
        assert len(tracker.get_defect_history()) == 0
        assert len(tracker.get_defects_by_phase("QUALITY")) == 0


# =============================================================================
# DefectRemediationTracker Analytics Tests
# =============================================================================


class TestDefectRemediationTrackerAnalytics:
    """Tests for defect analytics methods."""

    def test_get_analytics(self, tracker_with_data):
        """Test getting analytics."""
        analytics = tracker_with_data.get_analytics()

        assert "mean_time_to_resolve" in analytics
        assert "mean_time_to_verify" in analytics
        assert "defects_by_severity_priority" in analytics
        assert "phase_distribution" in analytics
        assert "status_trend" in analytics

    def test_analytics_phase_distribution(self, tracker_with_data):
        """Test phase distribution in analytics."""
        analytics = tracker_with_data.get_analytics()
        assert analytics["phase_distribution"]["QUALITY"] == 10

    def test_analytics_severity_distribution(self, tracker_with_data):
        """Test severity distribution in analytics."""
        analytics = tracker_with_data.get_analytics()
        assert analytics["defects_by_severity_priority"]["HIGH"] == 3
        assert analytics["defects_by_severity_priority"]["MEDIUM"] == 7

    def test_analytics_status_trend(self, tracker_with_data):
        """Test status trend in analytics."""
        analytics = tracker_with_data.get_analytics()
        trend = analytics["status_trend"]

        # 5 verified, 2 resolved, 3 open
        assert trend["VERIFIED"] == 5
        assert trend["RESOLVED"] == 2
        assert trend["OPEN"] == 3

    def test_analytics_mttr_calculation(self, tracker):
        """Test MTTR calculation."""
        # Create defect and progress it
        defect = Defect(
            id="defect-001",
            type=DefectType.MISSING_TESTS,
            severity=DefectSeverity.HIGH,
            description="Test defect",
        )
        tracker.add_defect(defect, phase="QUALITY")
        tracker.start_fix("defect-001")
        tracker.mark_resolved("defect-001", "Fixed")

        analytics = tracker.get_analytics()
        assert analytics["mean_time_to_resolve"] is not None
        assert analytics["mean_time_to_resolve"] >= 0

    def test_analytics_mttv_calculation(self, tracker):
        """Test MTTV calculation."""
        defect = Defect(
            id="defect-001",
            type=DefectType.MISSING_TESTS,
            severity=DefectSeverity.HIGH,
            description="Test defect",
        )
        tracker.add_defect(defect, phase="QUALITY")
        tracker.start_fix("defect-001")
        tracker.mark_resolved("defect-001", "Fixed")
        tracker.mark_verified("defect-001", "Verified")

        analytics = tracker.get_analytics()
        assert analytics["mean_time_to_verify"] is not None
        assert analytics["mean_time_to_verify"] >= 0

    def test_analytics_no_resolved_defects(self, tracker):
        """Test analytics when no defects are resolved."""
        defect = Defect(
            id="defect-001",
            type=DefectType.MISSING_TESTS,
            severity=DefectSeverity.HIGH,
            description="Test defect",
        )
        tracker.add_defect(defect, phase="QUALITY")
        # Don't progress the defect

        analytics = tracker.get_analytics()
        assert analytics["mean_time_to_resolve"] is None
        assert analytics["mean_time_to_verify"] is None


# =============================================================================
# DefectRemediationTracker Thread Safety Tests
# =============================================================================


class TestDefectRemediationTrackerThreadSafety:
    """Tests for thread safety of DefectRemediationTracker."""

    def test_concurrent_add_defects(self, tracker):
        """Test concurrent defect addition."""
        errors = []

        def add_defect(i):
            try:
                defect = Defect(
                    id=f"defect-{i:03d}",
                    type=DefectType.MISSING_TESTS,
                    severity=DefectSeverity.MEDIUM,
                    description=f"Defect {i}",
                )
                tracker.add_defect(defect, phase="QUALITY")
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(50):
            t = threading.Thread(target=add_defect, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(tracker.get_all_defects()) == 50

    def test_concurrent_status_transitions(self, tracker):
        """Test concurrent status transitions."""
        # Add initial defects
        for i in range(20):
            defect = Defect(
                id=f"defect-{i:03d}",
                type=DefectType.MISSING_TESTS,
                severity=DefectSeverity.MEDIUM,
                description=f"Defect {i}",
            )
            tracker.add_defect(defect, phase="QUALITY")

        errors = []

        def process_defect(defect_id):
            try:
                tracker.start_fix(defect_id)
                tracker.mark_resolved(defect_id, "Fixed")
                tracker.mark_verified(defect_id, "Verified")
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(20):
            t = threading.Thread(target=process_defect, args=(f"defect-{i:03d}",))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0

        # Verify all defects are verified
        verified = tracker.get_defects_by_status(DefectStatus.VERIFIED)
        assert len(verified) == 20

    def test_concurrent_mixed_operations(self, tracker):
        """Test concurrent mixed operations."""
        errors = []

        def add_and_process(i):
            try:
                defect = Defect(
                    id=f"defect-{i:03d}",
                    type=DefectType.MISSING_TESTS,
                    severity=DefectSeverity.MEDIUM,
                    description=f"Defect {i}",
                )
                tracker.add_defect(defect, phase="QUALITY")
                tracker.start_fix(f"defect-{i:03d}")
                tracker.mark_resolved(f"defect-{i:03d}", "Fixed")
                tracker.mark_verified(f"defect-{i:03d}", "Verified")
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(100):
            t = threading.Thread(target=add_and_process, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(tracker.get_all_defects()) == 100
        assert len(tracker.get_defect_history()) == 400  # 4 transitions per defect

    def test_concurrent_reads_and_writes(self, tracker):
        """Test concurrent reads and writes."""
        # Add initial defects
        for i in range(10):
            defect = Defect(
                id=f"defect-{i:03d}",
                type=DefectType.MISSING_TESTS,
                severity=DefectSeverity.MEDIUM,
                description=f"Defect {i}",
            )
            tracker.add_defect(defect, phase="QUALITY")

        errors = []
        read_count = [0]
        write_count = [0]

        def reader():
            try:
                for _ in range(10):
                    tracker.get_all_defects()
                    tracker.get_summary()
                    tracker.get_pending_defects()
                    read_count[0] += 1
                time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        def writer(i):
            try:
                tracker.start_fix(f"defect-{i:03d}")
                tracker.mark_resolved(f"defect-{i:03d}", "Fixed")
                write_count[0] += 1
            except Exception as e:
                errors.append(e)

        threads = []
        # Start readers
        for _ in range(5):
            t = threading.Thread(target=reader)
            threads.append(t)
            t.start()

        # Start writers
        for i in range(10):
            t = threading.Thread(target=writer, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0
        assert read_count[0] == 50  # 5 readers * 10 iterations
        assert write_count[0] == 10


# =============================================================================
# DefectRemediationTracker Integration Tests
# =============================================================================


class TestDefectRemediationTrackerIntegration:
    """Integration tests with PhaseContract."""

    def test_defects_flow_to_phase_contract(self, tracker, sample_defect):
        """Test that defects can flow to phase contracts."""
        from gaia.pipeline.phase_contract import (
            PhaseContractRegistry,
            create_planning_contract,
        )

        registry = PhaseContractRegistry()
        registry.register(create_planning_contract())

        tracker.add_defect(sample_defect, phase="QUALITY")
        tracker.mark_deferred(
            "defect-001",
            reason="Waiting on requirements",
            changed_by="product-owner",
        )

        # Get defects for PLANNING phase
        defects = tracker.get_defects_by_status(DefectStatus.DEFERRED)
        assert len(defects) == 1

        # PLANNING contract should accept defects as optional input
        planning_contract = registry.get("PLANNING")
        assert "defects" in planning_contract.optional_inputs

    def test_full_lifecycle_workflow(self, tracker, sample_defect):
        """Test complete defect lifecycle workflow."""
        # Add defect
        tracker.add_defect(sample_defect, phase="QUALITY")

        # Verify initial state
        assert tracker.get_defect("defect-001").status == DefectStatus.OPEN

        # Start fix
        tracker.start_fix("defect-001", changed_by="developer")
        assert tracker.get_defect("defect-001").status == DefectStatus.IN_PROGRESS

        # Mark resolved
        tracker.mark_resolved(
            "defect-001",
            description="Added unit tests",
            changed_by="developer",
            metadata={"tests_added": 15},
        )
        assert tracker.get_defect("defect-001").status == DefectStatus.RESOLVED

        # Mark verified
        tracker.mark_verified(
            "defect-001",
            notes="Quality check passed",
            changed_by="qa-reviewer",
        )
        assert tracker.get_defect("defect-001").status == DefectStatus.VERIFIED

        # Verify audit trail (4 transitions: OPEN->IN_PROGRESS->RESOLVED->VERIFIED)
        # Note: Initial add creates OPEN->OPEN record, so we have 4 total entries
        history = tracker.get_defect_history("defect-001")
        assert len(history) == 4

        # Verify summary
        summary = tracker.get_summary()
        assert summary["verified_count"] == 1
        assert summary["resolution_rate"] == 1.0

    def test_multiple_defects_workflow(self, tracker):
        """Test workflow with multiple defects."""
        defects = [
            Defect(
                id=f"defect-{i:03d}",
                type=DefectType.MISSING_TESTS,
                severity=DefectSeverity.HIGH if i < 2 else DefectSeverity.MEDIUM,
                description=f"Defect {i}",
                phase_detected="QUALITY",
            )
            for i in range(5)
        ]

        for defect in defects:
            tracker.add_defect(defect, phase="QUALITY")

        # Progress first 3 to verified
        for i in range(3):
            tracker.start_fix(f"defect-{i:03d}")
            tracker.mark_resolved(f"defect-{i:03d}", f"Fixed {i}")
            tracker.mark_verified(f"defect-{i:03d}", f"Verified {i}")

        # Defer one
        tracker.mark_deferred("defect-003", "Low priority")

        # Leave last one open
        # defect-004 stays OPEN

        # Verify counts
        summary = tracker.get_summary()
        assert summary["total"] == 5
        assert summary["verified_count"] == 3
        assert summary["deferred_count"] == 1
        assert summary["pending_count"] == 1  # defect-004 is OPEN

        # Verify pending defects sorted by severity
        pending = tracker.get_pending_defects()
        assert len(pending) == 1
        assert pending[0].id == "defect-004"
