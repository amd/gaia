"""
Tests for GAIA AuditLogger.

Tests cover:
- AuditEventType enum and categories
- AuditEvent dataclass creation and hash computation
- IntegrityVerificationError exception
- AuditLogger core functionality (log, verify, query)
- Hash chain integrity verification
- Tampering detection
- Thread safety for concurrent operations
- Export functionality (JSON, CSV)
- Query and filter operations
- Integration with PipelineState and LoopManager
"""

import pytest
from datetime import datetime, timezone, timedelta
from typing import Dict, Any
import threading
import time
import json
import csv
import io

from gaia.pipeline.audit_logger import (
    AuditLogger,
    AuditEvent,
    AuditEventType,
    IntegrityVerificationError,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def logger() -> AuditLogger:
    """Create a logger instance for testing."""
    return AuditLogger(logger_id="test-logger")


@pytest.fixture
def logger_with_events(logger: AuditLogger) -> AuditLogger:
    """Create a logger with sample events for testing."""
    logger.log(AuditEventType.PIPELINE_START, pipeline_id="pipe-001", user_goal="Test goal")
    logger.log(AuditEventType.PHASE_ENTER, phase="PLANNING", inputs_available=["user_goal"])
    logger.log(AuditEventType.AGENT_SELECTED, agent_id="senior-developer", capabilities=["coding"])
    logger.log(AuditEventType.AGENT_EXECUTED, agent_id="senior-developer", execution_time_ms=1500)
    logger.log(AuditEventType.PHASE_EXIT, phase="PLANNING", outputs_produced=["plan"])
    return logger


# =============================================================================
# AuditEventType Enum Tests
# =============================================================================


class TestAuditEventType:
    """Tests for AuditEventType enum."""

    def test_event_type_values(self):
        """Test that all event type values exist."""
        assert AuditEventType.PIPELINE_START is not None
        assert AuditEventType.PIPELINE_COMPLETE is not None
        assert AuditEventType.PHASE_ENTER is not None
        assert AuditEventType.PHASE_EXIT is not None
        assert AuditEventType.AGENT_SELECTED is not None
        assert AuditEventType.AGENT_EXECUTED is not None
        assert AuditEventType.QUALITY_EVALUATED is not None
        assert AuditEventType.DECISION_MADE is not None
        assert AuditEventType.DEFECT_DISCOVERED is not None
        assert AuditEventType.DEFECT_REMEDIATED is not None
        assert AuditEventType.LOOP_BACK is not None
        assert AuditEventType.TOOL_EXECUTED is not None

    def test_category_lifecycle(self):
        """Test lifecycle category detection."""
        assert AuditEventType.PIPELINE_START.category() == "lifecycle"
        assert AuditEventType.PIPELINE_COMPLETE.category() == "lifecycle"

    def test_category_phase_transition(self):
        """Test phase transition category detection."""
        assert AuditEventType.PHASE_ENTER.category() == "phase_transition"
        assert AuditEventType.PHASE_EXIT.category() == "phase_transition"

    def test_category_agent_operation(self):
        """Test agent operation category detection."""
        assert AuditEventType.AGENT_SELECTED.category() == "agent_operation"
        assert AuditEventType.AGENT_EXECUTED.category() == "agent_operation"

    def test_category_quality(self):
        """Test quality category detection."""
        assert AuditEventType.QUALITY_EVALUATED.category() == "quality"

    def test_category_decision(self):
        """Test decision category detection."""
        assert AuditEventType.DECISION_MADE.category() == "decision"

    def test_category_defect(self):
        """Test defect category detection."""
        assert AuditEventType.DEFECT_DISCOVERED.category() == "defect"
        assert AuditEventType.DEFECT_REMEDIATED.category() == "defect"

    def test_category_loop(self):
        """Test loop category detection."""
        assert AuditEventType.LOOP_BACK.category() == "loop"

    def test_category_tool(self):
        """Test tool category detection."""
        assert AuditEventType.TOOL_EXECUTED.category() == "tool"


# =============================================================================
# AuditEvent Dataclass Tests
# =============================================================================


class TestAuditEvent:
    """Tests for AuditEvent dataclass."""

    def test_create_event(self):
        """Test basic event creation."""
        event = AuditEvent(
            event_id="evt-001",
            event_type=AuditEventType.PHASE_ENTER,
            timestamp=datetime.now(timezone.utc),
            previous_hash="0" * 64,
            sequence_number=1,
            phase="PLANNING",
        )
        assert event.event_id == "evt-001"
        assert event.event_type == AuditEventType.PHASE_ENTER
        assert event.phase == "PLANNING"
        assert event.sequence_number == 1

    def test_create_event_with_all_fields(self):
        """Test event creation with all optional fields."""
        event = AuditEvent(
            event_id="evt-002",
            event_type=AuditEventType.AGENT_EXECUTED,
            timestamp=datetime.now(timezone.utc),
            previous_hash="abc123",
            sequence_number=2,
            loop_id="loop-001",
            phase="DEVELOPMENT",
            agent_id="senior-developer",
            payload={"execution_time_ms": 1500},
            metadata={"iteration": 1},
        )
        assert event.loop_id == "loop-001"
        assert event.agent_id == "senior-developer"
        assert event.payload["execution_time_ms"] == 1500
        assert event.metadata["iteration"] == 1

    def test_compute_hash(self):
        """Test hash computation is deterministic."""
        event = AuditEvent(
            event_id="evt-001",
            event_type=AuditEventType.PHASE_ENTER,
            timestamp=datetime.now(timezone.utc),
            previous_hash="0" * 64,
            sequence_number=1,
        )
        hash1 = event.compute_hash()
        hash2 = event.compute_hash()
        assert hash1 == hash2  # Deterministic
        assert len(hash1) == 64  # SHA-256 produces 64 hex chars

    def test_verify_hash(self):
        """Test hash verification."""
        event = AuditEvent(
            event_id="evt-001",
            event_type=AuditEventType.PHASE_ENTER,
            timestamp=datetime.now(timezone.utc),
            previous_hash="0" * 64,
            sequence_number=1,
        )
        assert event.verify_hash() is True

    def test_hash_changes_with_data(self):
        """Test that hash changes when data changes."""
        event1 = AuditEvent(
            event_id="evt-001",
            event_type=AuditEventType.PHASE_ENTER,
            timestamp=datetime.now(timezone.utc),
            previous_hash="0" * 64,
            sequence_number=1,
        )
        event2 = AuditEvent(
            event_id="evt-001",
            event_type=AuditEventType.PHASE_ENTER,
            timestamp=datetime.now(timezone.utc),
            previous_hash="0" * 64,
            sequence_number=2,  # Different sequence
        )
        assert event1.current_hash != event2.current_hash

    def test_to_dict(self):
        """Test serialization to dictionary."""
        event = AuditEvent(
            event_id="evt-001",
            event_type=AuditEventType.PHASE_ENTER,
            timestamp=datetime.now(timezone.utc),
            previous_hash="0" * 64,
            sequence_number=1,
            phase="PLANNING",
        )
        data = event.to_dict()
        assert data["event_id"] == "evt-001"
        assert data["event_type"] == "PHASE_ENTER"
        assert data["phase"] == "PLANNING"
        assert "current_hash" in data
        assert "previous_hash" in data

    def test_to_json(self):
        """Test JSON serialization."""
        event = AuditEvent(
            event_id="evt-001",
            event_type=AuditEventType.PHASE_ENTER,
            timestamp=datetime.now(timezone.utc),
            previous_hash="0" * 64,
            sequence_number=1,
        )
        json_str = event.to_json()
        assert isinstance(json_str, str)
        data = json.loads(json_str)
        assert data["event_id"] == "evt-001"

    def test_to_json_compact(self):
        """Test compact JSON serialization."""
        event = AuditEvent(
            event_id="evt-001",
            event_type=AuditEventType.PHASE_ENTER,
            timestamp=datetime.now(timezone.utc),
            previous_hash="0" * 64,
            sequence_number=1,
        )
        json_str = event.to_json(indent=None)
        assert "\n" not in json_str  # No newlines in compact format

    def test_frozen_dataclass(self):
        """Test that event is immutable."""
        event = AuditEvent(
            event_id="evt-001",
            event_type=AuditEventType.PHASE_ENTER,
            timestamp=datetime.now(timezone.utc),
            previous_hash="0" * 64,
            sequence_number=1,
        )
        with pytest.raises(Exception):  # frozen dataclass raises attr-related error
            event.event_id = "evt-002"


# =============================================================================
# IntegrityVerificationError Tests
# =============================================================================


class TestIntegrityVerificationError:
    """Tests for IntegrityVerificationError exception."""

    def test_create_hash_mismatch_error(self):
        """Test creating hash mismatch error."""
        error = IntegrityVerificationError(
            failed_event_id="evt-001",
            failure_type="HASH_MISMATCH",
            expected_hash="abc123",
            actual_hash="def456",
        )
        assert error.failed_event_id == "evt-001"
        assert error.failure_type == "HASH_MISMATCH"
        assert error.expected_hash == "abc123"
        assert error.actual_hash == "def456"

    def test_create_broken_chain_error(self):
        """Test creating broken chain error."""
        error = IntegrityVerificationError(
            failed_event_id="evt-002",
            failure_type="BROKEN_CHAIN",
            expected_hash="prev_hash",
            actual_hash="event_prev_hash",
        )
        assert error.failure_type == "BROKEN_CHAIN"

    def test_error_message_hash_mismatch(self):
        """Test error message for hash mismatch."""
        error = IntegrityVerificationError(
            failed_event_id="evt-001",
            failure_type="HASH_MISMATCH",
            expected_hash="abc",
            actual_hash="def",
        )
        message = str(error)
        assert "HASH_MISMATCH" not in message  # Message is human-readable
        assert "evt-001" in message

    def test_error_message_broken_chain(self):
        """Test error message for broken chain."""
        error = IntegrityVerificationError(
            failed_event_id="evt-002",
            failure_type="BROKEN_CHAIN",
        )
        message = str(error)
        assert "Broken hash chain" in message
        assert "evt-002" in message

    def test_error_to_dict(self):
        """Test error serialization."""
        error = IntegrityVerificationError(
            failed_event_id="evt-001",
            failure_type="HASH_MISMATCH",
            expected_hash="abc",
            actual_hash="def",
        )
        data = error.to_dict()
        assert data["error"] == "IntegrityVerificationError"
        assert data["failed_event_id"] == "evt-001"
        assert data["failure_type"] == "HASH_MISMATCH"


# =============================================================================
# AuditLogger Basic Tests
# =============================================================================


class TestAuditLogger:
    """Tests for AuditLogger core functionality."""

    def test_create_logger(self, logger):
        """Test logger creation."""
        assert logger.logger_id == "test-logger"
        assert len(logger.get_events()) == 0

    def test_create_logger_auto_id(self):
        """Test logger creation with auto-generated ID."""
        logger = AuditLogger()
        assert logger.logger_id.startswith("audit-")

    def test_create_logger_custom_genesis(self):
        """Test logger creation with custom genesis hash."""
        custom_hash = "a" * 64
        logger = AuditLogger(genesis_hash=custom_hash)
        assert logger._genesis_hash == custom_hash

    def test_log_event(self, logger):
        """Test logging a single event."""
        event = logger.log(
            event_type=AuditEventType.PIPELINE_START,
            pipeline_id="pipe-001",
        )
        assert event.event_type == AuditEventType.PIPELINE_START
        assert event.sequence_number == 1
        assert event.previous_hash == "0" * 64  # Genesis hash

    def test_log_event_with_context(self, logger):
        """Test logging event with full context."""
        event = logger.log(
            event_type=AuditEventType.AGENT_EXECUTED,
            loop_id="loop-001",
            phase="DEVELOPMENT",
            agent_id="senior-developer",
            execution_time_ms=1500,
            artifacts_produced=["code.py"],
        )
        assert event.loop_id == "loop-001"
        assert event.phase == "DEVELOPMENT"
        assert event.agent_id == "senior-developer"
        assert event.payload["execution_time_ms"] == 1500

    def test_log_multiple_events(self, logger):
        """Test logging multiple events."""
        logger.log(AuditEventType.PIPELINE_START)
        logger.log(AuditEventType.PHASE_ENTER, phase="PLANNING")
        logger.log(AuditEventType.AGENT_SELECTED, agent_id="dev")

        events = logger.get_events()
        assert len(events) == 3
        assert events[0].sequence_number == 1
        assert events[1].sequence_number == 2
        assert events[2].sequence_number == 3


# =============================================================================
# AuditLogger Hash Chain Tests
# =============================================================================


class TestAuditLoggerHashChain:
    """Tests for hash chain integrity."""

    def test_hash_chain_linkage(self, logger):
        """Test that events are properly linked."""
        event1 = logger.log(AuditEventType.PIPELINE_START)
        event2 = logger.log(AuditEventType.PHASE_ENTER, phase="PLANNING")
        event3 = logger.log(AuditEventType.AGENT_SELECTED)

        assert event2.previous_hash == event1.current_hash
        assert event3.previous_hash == event2.current_hash

    def test_verify_integrity_empty(self, logger):
        """Test integrity verification on empty logger."""
        assert logger.verify_integrity() is True

    def test_verify_integrity_single_event(self, logger):
        """Test integrity with single event."""
        logger.log(AuditEventType.PIPELINE_START)
        assert logger.verify_integrity() is True

    def test_verify_integrity_multiple_events(self, logger_with_events):
        """Test integrity with multiple events."""
        assert logger_with_events.verify_integrity() is True

    def test_genesis_hash(self, logger):
        """Test genesis hash is used for first event."""
        event = logger.log(AuditEventType.PIPELINE_START)
        assert event.previous_hash == "0" * 64


# =============================================================================
# AuditLogger Tampering Detection Tests
# =============================================================================


class TestAuditLoggerTamperingDetection:
    """Tests for tampering detection."""

    def test_tampering_hash_mismatch(self, logger):
        """Test detection of hash tampering."""
        # Create a new logger and manually corrupt an event
        logger2 = AuditLogger()
        logger2.log(AuditEventType.PIPELINE_START)
        event2 = logger2.log(AuditEventType.PHASE_ENTER, phase="PLANNING")
        logger2.log(AuditEventType.AGENT_SELECTED)

        # Create corrupted event with wrong hash (compute_hash will use different payload)
        # Since current_hash is init=False, we need to use object.__setattr__ after creation
        corrupted_event = AuditEvent(
            event_id=event2.event_id,
            event_type=event2.event_type,
            timestamp=event2.timestamp,
            previous_hash=event2.previous_hash,
            sequence_number=event2.sequence_number,
            payload={"tampered": True},  # Different payload
        )
        # The hash was computed with tampered payload, but we'll swap in the old hash
        # to simulate someone trying to hide tampering
        object.__setattr__(corrupted_event, 'current_hash', event2.current_hash)

        logger2._events[1] = corrupted_event
        del logger2._event_index[event2.event_id]
        logger2._event_index[corrupted_event.event_id] = corrupted_event

        with pytest.raises(IntegrityVerificationError) as exc_info:
            logger2.verify_integrity()

        assert exc_info.value.failure_type == "HASH_MISMATCH"

    def test_tampering_broken_chain(self, logger):
        """Test detection of broken chain."""
        logger.log(AuditEventType.PIPELINE_START)
        event2 = logger.log(AuditEventType.PHASE_ENTER, phase="PLANNING")
        logger.log(AuditEventType.AGENT_SELECTED)

        # Create event with wrong previous hash
        broken_event = AuditEvent(
            event_id="evt-broken",
            event_type=AuditEventType.PHASE_EXIT,
            timestamp=datetime.now(timezone.utc),
            previous_hash="wrong_hash",  # Doesn't match previous event
            sequence_number=4,
        )
        logger._events.append(broken_event)
        logger._event_index[broken_event.event_id] = broken_event

        with pytest.raises(IntegrityVerificationError) as exc_info:
            logger.verify_integrity()

        assert exc_info.value.failure_type == "BROKEN_CHAIN"

    def test_tampering_detection_reports_correct_event(self, logger):
        """Test that tampering reports correct event ID."""
        logger.log(AuditEventType.PIPELINE_START)
        logger.log(AuditEventType.PHASE_ENTER, phase="PLANNING")
        event3 = logger.log(AuditEventType.AGENT_SELECTED)

        # Create corrupted event
        corrupted = AuditEvent(
            event_id=event3.event_id,
            event_type=event3.event_type,
            timestamp=event3.timestamp,
            previous_hash=event3.previous_hash,
            sequence_number=event3.sequence_number,
            payload={"corrupted": True},
        )
        # Set the old hash to simulate tampering
        object.__setattr__(corrupted, 'current_hash', event3.current_hash)

        logger._events[2] = corrupted

        with pytest.raises(IntegrityVerificationError) as exc_info:
            logger.verify_integrity()

        assert exc_info.value.failed_event_id == event3.event_id


# =============================================================================
# AuditLogger Thread Safety Tests
# =============================================================================


class TestAuditLoggerThreadSafety:
    """Tests for thread safety of AuditLogger."""

    def test_concurrent_logging(self, logger):
        """Test concurrent event logging."""
        errors = []

        def log_events(prefix):
            try:
                for i in range(50):
                    logger.log(
                        AuditEventType.TOOL_EXECUTED,
                        tool_name=f"{prefix}_tool_{i}",
                    )
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(10):
            t = threading.Thread(target=log_events, args=(f"thread_{i}",))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(logger.get_events()) == 500

    def test_concurrent_logging_integrity(self, logger):
        """Test integrity after concurrent logging."""
        def log_events():
            for i in range(20):
                logger.log(AuditEventType.TOOL_EXECUTED, tool_name=f"tool_{i}")

        threads = [threading.Thread(target=log_events) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(logger.get_events()) == 100
        assert logger.verify_integrity() is True

    def test_concurrent_mixed_operations(self, logger):
        """Test concurrent reads and writes."""
        # Pre-populate some events
        for i in range(10):
            logger.log(AuditEventType.TOOL_EXECUTED, tool_name=f"tool_{i}")

        errors = []
        read_count = [0]

        def reader():
            try:
                for _ in range(20):
                    logger.get_events()
                    logger.get_chain_summary()
                    logger.verify_integrity()
                    read_count[0] += 1
            except Exception as e:
                errors.append(e)

        def writer():
            try:
                for i in range(10):
                    logger.log(AuditEventType.TOOL_EXECUTED, tool_name=f"writer_tool_{i}")
            except Exception as e:
                errors.append(e)

        threads = []
        # Start readers
        for _ in range(5):
            t = threading.Thread(target=reader)
            threads.append(t)
            t.start()

        # Start writers
        for _ in range(3):
            t = threading.Thread(target=writer)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0
        assert read_count[0] == 100  # 5 readers * 20 iterations

    def test_reentrant_lock(self, logger):
        """Test that RLock allows reentrant access."""
        logger.log(AuditEventType.PIPELINE_START)

        def nested_operation():
            with logger._lock:
                # Should not deadlock - RLock allows reentrant
                logger.log(AuditEventType.PHASE_ENTER, phase="PLANNING")
                events = logger.get_events()
                return len(events)

        with logger._lock:
            count = nested_operation()

        assert count == 2


# =============================================================================
# AuditLogger Export Tests
# =============================================================================


class TestAuditLoggerExport:
    """Tests for export functionality."""

    def test_export_json(self, logger_with_events):
        """Test JSON export."""
        json_str = logger_with_events.export_log(format="json")
        data = json.loads(json_str)

        assert "exported_at" in data
        assert "logger_id" in data
        assert "genesis_hash" in data
        assert "total_events" in data
        assert "events" in data
        assert data["total_events"] == 5
        assert data["integrity_verified"] is True

    def test_export_json_compact(self, logger_with_events):
        """Test compact JSON export."""
        json_str = logger_with_events.export_log(format="json", indent=None)
        assert "\n" not in json_str  # No newlines

    def test_export_csv(self, logger_with_events):
        """Test CSV export."""
        csv_str = logger_with_events.export_log(format="csv")
        lines = csv_str.strip().split("\n")

        assert len(lines) == 6  # Header + 5 events
        assert "sequence_number" in lines[0]
        assert "event_id" in lines[0]
        assert "event_type" in lines[0]

    def test_export_csv_parseable(self, logger_with_events):
        """Test CSV is properly parseable."""
        csv_str = logger_with_events.export_log(format="csv")
        reader = csv.DictReader(io.StringIO(csv_str))
        rows = list(reader)

        assert len(rows) == 5
        assert rows[0]["event_type"] == "PIPELINE_START"

    def test_export_invalid_format(self, logger):
        """Test export with invalid format."""
        with pytest.raises(ValueError, match="Unsupported export format"):
            logger.export_log(format="xml")

    def test_export_with_tampering_warning(self, logger):
        """Test export includes tampering warning."""
        logger.log(AuditEventType.PIPELINE_START)
        event2 = logger.log(AuditEventType.PHASE_ENTER, phase="PLANNING")

        # Create corrupted event
        corrupted = AuditEvent(
            event_id=event2.event_id,
            event_type=event2.event_type,
            timestamp=event2.timestamp,
            previous_hash=event2.previous_hash,
            sequence_number=event2.sequence_number,
            payload={"tampered": True},
        )
        object.__setattr__(corrupted, 'current_hash', event2.current_hash)

        logger._events[1] = corrupted

        json_str = logger.export_log(format="json")
        data = json.loads(json_str)

        assert data["integrity_verified"] is False
        assert "integrity_warning" in data


# =============================================================================
# AuditLogger Query Tests
# =============================================================================


class TestAuditLoggerQueries:
    """Tests for query and filter operations."""

    def test_get_events_empty(self, logger):
        """Test getting events from empty logger."""
        events = logger.get_events()
        assert len(events) == 0

    def test_get_events_all(self, logger_with_events):
        """Test getting all events."""
        events = logger_with_events.get_events()
        assert len(events) == 5

    def test_get_events_by_type(self, logger_with_events):
        """Test getting events by type."""
        events = logger_with_events.get_events_by_type(AuditEventType.PHASE_ENTER)
        assert len(events) == 1
        assert events[0].event_type == AuditEventType.PHASE_ENTER

    def test_get_events_by_loop(self, logger):
        """Test getting events by loop ID."""
        logger.log(AuditEventType.TOOL_EXECUTED, loop_id="loop-001", tool_name="tool1")
        logger.log(AuditEventType.TOOL_EXECUTED, loop_id="loop-002", tool_name="tool2")
        logger.log(AuditEventType.TOOL_EXECUTED, loop_id="loop-001", tool_name="tool3")

        loop1_events = logger.get_events_by_loop("loop-001")
        assert len(loop1_events) == 2

    def test_get_events_by_loop_empty(self, logger):
        """Test getting events for non-existent loop."""
        events = logger.get_events_by_loop("nonexistent")
        assert len(events) == 0

    def test_get_events_by_phase(self, logger_with_events):
        """Test getting events by phase."""
        events = logger_with_events.get_events_by_phase("PLANNING")
        assert len(events) == 2  # PHASE_ENTER and PHASE_EXIT

    def test_get_events_by_phase_empty(self, logger):
        """Test getting events for non-existent phase."""
        events = logger.get_events_by_phase("NONEXISTENT")
        assert len(events) == 0

    def test_get_events_in_range(self, logger):
        """Test getting events in time range."""
        before = datetime.now(timezone.utc) - timedelta(hours=1)

        logger.log(AuditEventType.TOOL_EXECUTED, tool_name="tool1")
        time.sleep(0.01)

        middle = datetime.now(timezone.utc)

        time.sleep(0.01)
        logger.log(AuditEventType.TOOL_EXECUTED, tool_name="tool2")

        after = datetime.now(timezone.utc) + timedelta(hours=1)

        # Get all events
        all_events = logger.get_events_in_range(before, after)
        assert len(all_events) == 2

        # Get only second event
        recent = logger.get_events_in_range(middle, after)
        assert len(recent) == 1
        assert recent[0].payload["tool_name"] == "tool2"

    def test_get_events_with_filters(self, logger):
        """Test getting events with multiple filters."""
        logger.log(
            AuditEventType.AGENT_EXECUTED,
            loop_id="loop-001",
            phase="DEVELOPMENT",
            agent_id="senior-developer",
        )
        logger.log(
            AuditEventType.AGENT_EXECUTED,
            loop_id="loop-002",
            phase="QUALITY",
            agent_id="quality-reviewer",
        )

        # Filter by phase
        dev_events = logger.get_events(filters={"phase": "DEVELOPMENT"})
        assert len(dev_events) == 1
        assert dev_events[0].agent_id == "senior-developer"

        # Filter by loop
        loop1_events = logger.get_events(filters={"loop_id": "loop-001"})
        assert len(loop1_events) == 1

    def test_get_events_filter_by_category(self, logger):
        """Test filtering by event category."""
        logger.log(AuditEventType.PIPELINE_START)
        logger.log(AuditEventType.PHASE_ENTER, phase="PLANNING")
        logger.log(AuditEventType.AGENT_SELECTED)
        logger.log(AuditEventType.DEFECT_DISCOVERED, defect_id="d1")

        lifecycle = logger.get_events(filters={"category": "lifecycle"})
        assert len(lifecycle) == 1

        phase_transitions = logger.get_events(filters={"category": "phase_transition"})
        assert len(phase_transitions) == 1

    def test_get_events_filter_by_payload(self, logger):
        """Test filtering by payload content."""
        logger.log(AuditEventType.TOOL_EXECUTED, tool_name="pytest", exit_code=0)
        logger.log(AuditEventType.TOOL_EXECUTED, tool_name="pytest", exit_code=1)
        logger.log(AuditEventType.TOOL_EXECUTED, tool_name="mypy", exit_code=0)

        # Filter by payload contains
        pytest_events = logger.get_events(
            filters={"payload_contains": ("tool_name", "pytest")}
        )
        assert len(pytest_events) == 2

    def test_get_events_limit(self, logger):
        """Test limit parameter."""
        for i in range(10):
            logger.log(AuditEventType.TOOL_EXECUTED, tool_name=f"tool_{i}")

        events = logger.get_events(limit=5)
        assert len(events) == 5

    def test_get_events_offset(self, logger):
        """Test offset parameter."""
        for i in range(10):
            logger.log(AuditEventType.TOOL_EXECUTED, tool_name=f"tool_{i}")

        events = logger.get_events(offset=5)
        assert len(events) == 5
        assert events[0].payload["tool_name"] == "tool_5"

    def test_get_event_by_id(self, logger):
        """Test getting single event by ID."""
        event = logger.log(AuditEventType.PIPELINE_START)

        retrieved = logger.get_event(event.event_id)
        assert retrieved is not None
        assert retrieved.event_id == event.event_id

    def test_get_event_not_found(self, logger):
        """Test getting non-existent event."""
        event = logger.get_event("nonexistent")
        assert event is None


# =============================================================================
# AuditLogger Summary and Report Tests
# =============================================================================


class TestAuditLoggerSummary:
    """Tests for summary and report methods."""

    def test_get_chain_summary(self, logger_with_events):
        """Test chain summary."""
        summary = logger_with_events.get_chain_summary()

        assert summary["logger_id"] == "test-logger"
        assert summary["total_events"] == 5
        assert "by_type" in summary
        assert "by_category" in summary
        assert summary["first_event"] is not None
        assert summary["last_event"] is not None
        assert summary["genesis_hash"] == "0" * 64

    def test_get_chain_summary_empty(self, logger):
        """Test summary for empty logger."""
        summary = logger.get_chain_summary()

        assert summary["total_events"] == 0
        assert summary["first_event"] is None
        assert summary["last_event"] is None

    def test_get_chain_summary_loop_count(self, logger):
        """Test loop count in summary."""
        logger.log(AuditEventType.TOOL_EXECUTED, loop_id="loop-001")
        logger.log(AuditEventType.TOOL_EXECUTED, loop_id="loop-001")
        logger.log(AuditEventType.TOOL_EXECUTED, loop_id="loop-002")

        summary = logger.get_chain_summary()
        assert summary["loop_count"] == 2

    def test_get_integrity_report_valid(self, logger_with_events):
        """Test integrity report for valid chain."""
        report = logger_with_events.get_integrity_report()

        assert report["is_valid"] is True
        assert report["total_events"] == 5
        assert report["failure_details"] is None
        assert "verified_at" in report

    def test_get_integrity_report_invalid(self, logger):
        """Test integrity report for tampered chain."""
        logger.log(AuditEventType.PIPELINE_START)
        event2 = logger.log(AuditEventType.PHASE_ENTER, phase="PLANNING")

        # Create corrupted event
        corrupted = AuditEvent(
            event_id=event2.event_id,
            event_type=event2.event_type,
            timestamp=event2.timestamp,
            previous_hash=event2.previous_hash,
            sequence_number=event2.sequence_number,
            payload={"tampered": True},
        )
        object.__setattr__(corrupted, 'current_hash', event2.current_hash)

        logger._events[1] = corrupted

        report = logger.get_integrity_report()

        assert report["is_valid"] is False
        assert report["failure_details"] is not None
        assert report["failure_details"]["failure_type"] == "HASH_MISMATCH"

    def test_get_events_by_type_method(self, logger_with_events):
        """Test get_events_by_type method."""
        events = logger_with_events.get_events_by_type(AuditEventType.AGENT_SELECTED)
        assert len(events) == 1
        assert events[0].event_type == AuditEventType.AGENT_SELECTED


# =============================================================================
# AuditLogger Clear Tests
# =============================================================================


class TestAuditLoggerClear:
    """Tests for clear functionality."""

    def test_clear(self, logger_with_events):
        """Test clearing logger."""
        logger_with_events.clear()

        assert len(logger_with_events.get_events()) == 0
        assert len(logger_with_events._loop_buckets) == 0
        assert logger_with_events._sequence_counter == 0

    def test_clear_then_log(self, logger_with_events):
        """Test logging after clear."""
        logger_with_events.clear()
        event = logger_with_events.log(AuditEventType.PIPELINE_START)

        assert event.sequence_number == 1  # Reset counter


# =============================================================================
# AuditLogger Integration Tests
# =============================================================================


class TestAuditLoggerIntegration:
    """Integration tests with other GAIA components."""

    def test_integration_with_pipeline_state_context(self):
        """Test audit logger captures pipeline context."""
        logger = AuditLogger(logger_id="integration-test")

        # Simulate pipeline execution with context
        logger.log(
            AuditEventType.PIPELINE_START,
            pipeline_id="pipe-001",
            user_goal="Build REST API",
            config={"quality_threshold": 0.90},
        )

        logger.log(
            AuditEventType.PHASE_ENTER,
            phase="PLANNING",
            inputs_available=["user_goal", "context"],
        )

        logger.log(
            AuditEventType.AGENT_SELECTED,
            agent_id="senior-developer",
            capabilities=["python", "testing"],
            selection_reason="Best match for task",
        )

        logger.log(
            AuditEventType.QUALITY_EVALUATED,
            phase="QUALITY",
            quality_score=0.92,
            validators_run=["pytest", "mypy", "black"],
            defects_found=0,
        )

        logger.log(
            AuditEventType.PIPELINE_COMPLETE,
            final_state="COMPLETED",
            quality_score=0.92,
            total_iterations=1,
        )

        # Verify integrity
        assert logger.verify_integrity() is True

        # Query by category
        quality_events = logger.get_events(filters={"category": "quality"})
        assert len(quality_events) == 1
        assert quality_events[0].payload["quality_score"] == 0.92

        # Export
        export_data = json.loads(logger.export_log(format="json"))
        assert export_data["total_events"] == 5
        assert export_data["integrity_verified"] is True

    def test_integration_concurrent_loops(self):
        """Test concurrent loop isolation."""
        logger = AuditLogger(logger_id="concurrent-loop-test")

        # Simulate concurrent loops
        logger.log(
            AuditEventType.LOOP_BACK,
            loop_id="loop-001",
            target_phase="DEVELOPMENT",
            defects_count=3,
        )
        logger.log(
            AuditEventType.LOOP_BACK,
            loop_id="loop-002",
            target_phase="QUALITY",
            defects_count=1,
        )

        # Add events for each loop
        logger.log(
            AuditEventType.AGENT_EXECUTED,
            loop_id="loop-001",
            phase="DEVELOPMENT",
            agent_id="senior-developer",
        )
        logger.log(
            AuditEventType.AGENT_EXECUTED,
            loop_id="loop-002",
            phase="QUALITY",
            agent_id="quality-reviewer",
        )

        # Verify loop isolation
        loop1_events = logger.get_events_by_loop("loop-001")
        loop2_events = logger.get_events_by_loop("loop-002")

        assert len(loop1_events) == 2
        assert len(loop2_events) == 2

        # All loop1 events should have loop_id="loop-001"
        for event in loop1_events:
            assert event.loop_id == "loop-001"

    def test_full_pipeline_simulation(self):
        """Test complete pipeline simulation with audit trail."""
        logger = AuditLogger(logger_id="pipeline-sim")

        # Pipeline start
        logger.log(
            AuditEventType.PIPELINE_START,
            pipeline_id="sim-001",
            user_goal="Create data processor",
        )

        # Phase: PLANNING
        logger.log(AuditEventType.PHASE_ENTER, phase="PLANNING")
        logger.log(AuditEventType.AGENT_SELECTED, agent_id="senior-developer", phase="PLANNING")
        logger.log(AuditEventType.AGENT_EXECUTED, agent_id="senior-developer", phase="PLANNING")
        logger.log(AuditEventType.PHASE_EXIT, phase="PLANNING", outputs_produced=["plan"])

        # Phase: DEVELOPMENT
        logger.log(AuditEventType.PHASE_ENTER, phase="DEVELOPMENT")
        logger.log(AuditEventType.AGENT_SELECTED, agent_id="senior-developer", phase="DEVELOPMENT")
        logger.log(AuditEventType.AGENT_EXECUTED, agent_id="senior-developer", phase="DEVELOPMENT")
        logger.log(AuditEventType.PHASE_EXIT, phase="DEVELOPMENT", outputs_produced=["code.py"])

        # Phase: QUALITY
        logger.log(AuditEventType.PHASE_ENTER, phase="QUALITY")
        logger.log(
            AuditEventType.QUALITY_EVALUATED,
            phase="QUALITY",
            quality_score=0.85,
            defects_found=2,
        )
        logger.log(
            AuditEventType.DEFECT_DISCOVERED,
            phase="QUALITY",
            defect_id="d1",
            defect_type="MISSING_TESTS",
        )
        logger.log(
            AuditEventType.DEFECT_DISCOVERED,
            phase="QUALITY",
            defect_id="d2",
            defect_type="CODE_STYLE",
        )
        logger.log(AuditEventType.PHASE_EXIT, phase="QUALITY")

        # Loop back
        logger.log(
            AuditEventType.LOOP_BACK,
            loop_id="loop-001",
            target_phase="DEVELOPMENT",
            defects_count=2,
        )

        # Re-execute DEVELOPMENT
        logger.log(AuditEventType.PHASE_ENTER, phase="DEVELOPMENT", loop_id="loop-001")
        logger.log(
            AuditEventType.AGENT_EXECUTED,
            agent_id="senior-developer",
            phase="DEVELOPMENT",
            loop_id="loop-001",
        )
        logger.log(
            AuditEventType.DEFECT_REMEDIATED,
            phase="DEVELOPMENT",
            loop_id="loop-001",
            defect_id="d1",
        )
        logger.log(AuditEventType.PHASE_EXIT, phase="DEVELOPMENT", loop_id="loop-001")

        # Re-QUALITY
        logger.log(AuditEventType.PHASE_ENTER, phase="QUALITY", loop_id="loop-001")
        logger.log(
            AuditEventType.QUALITY_EVALUATED,
            phase="QUALITY",
            quality_score=0.95,
            loop_id="loop-001",
        )
        logger.log(AuditEventType.PHASE_EXIT, phase="QUALITY", loop_id="loop-001")

        # Pipeline complete
        logger.log(
            AuditEventType.PIPELINE_COMPLETE,
            final_state="COMPLETED",
            quality_score=0.95,
            total_iterations=2,
        )

        # Verify integrity
        assert logger.verify_integrity() is True

        # Query tests
        all_events = logger.get_events()
        assert len(all_events) == 23  # 23 events total

        planning_events = logger.get_events_by_phase("PLANNING")
        assert len(planning_events) == 4

        development_events = logger.get_events_by_phase("DEVELOPMENT")
        assert len(development_events) == 8  # 4 initial + 4 from loop-001

        quality_events = logger.get_events_by_phase("QUALITY")
        assert len(quality_events) == 8  # 4 initial + 4 from loop-001

        loop_events = logger.get_events_by_loop("loop-001")
        assert len(loop_events) == 8  # All events in loop-001

        defect_events = logger.get_events_by_type(AuditEventType.DEFECT_DISCOVERED)
        assert len(defect_events) == 2

        # Export
        export_data = json.loads(logger.export_log(format="json"))
        assert export_data["total_events"] == 23
        assert export_data["integrity_verified"] is True

        # CSV export
        csv_str = logger.export_log(format="csv")
        lines = csv_str.strip().split("\n")
        assert len(lines) == 24  # Header + 23 events

    def test_decision_workflow(self):
        """Test decision workflow audit trail."""
        logger = AuditLogger()

        logger.log(
            AuditEventType.DECISION_MADE,
            decision_type="PROCEED",
            target_phase="DEVELOPMENT",
            reasoning="Quality score meets threshold",
        )
        logger.log(
            AuditEventType.DECISION_MADE,
            decision_type="LOOP_BACK",
            target_phase="DEVELOPMENT",
            reasoning="Defects found requiring fixes",
        )

        decisions = logger.get_events_by_type(AuditEventType.DECISION_MADE)
        assert len(decisions) == 2
        assert decisions[0].payload["decision_type"] == "PROCEED"
        assert decisions[1].payload["decision_type"] == "LOOP_BACK"

    def test_tool_execution_workflow(self):
        """Test tool execution audit trail."""
        logger = AuditLogger()

        logger.log(
            AuditEventType.TOOL_EXECUTED,
            tool_name="pytest",
            command="pytest tests/ -v",
            exit_code=0,
            duration_ms=5000,
        )
        logger.log(
            AuditEventType.TOOL_EXECUTED,
            tool_name="mypy",
            command="mypy src/",
            exit_code=0,
            duration_ms=3000,
        )

        tools = logger.get_events_by_type(AuditEventType.TOOL_EXECUTED)
        assert len(tools) == 2

        # Filter by tool name
        pytest_events = logger.get_events(
            filters={"payload_contains": ("tool_name", "pytest")}
        )
        assert len(pytest_events) == 1
        assert pytest_events[0].payload["exit_code"] == 0
