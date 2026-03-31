"""
GAIA AuditLogger

Provides tamper-proof audit trail of pipeline execution with hash chain integrity.

The AuditLogger component provides a cryptographic hash chain mechanism that detects
any attempt to modify or tamper with the audit log, ensuring the integrity and
immutability of the pipeline's execution history.

Features:
    - Hash chain integrity verification
    - Thread-safe concurrent access
    - Loop-based event isolation
    - Multiple export formats (JSON, CSV)
    - Flexible querying and filtering

Example:
    >>> from gaia.pipeline.audit_logger import AuditLogger, AuditEventType
    >>> logger = AuditLogger(logger_id="pipeline-001")
    >>> event = logger.log(
    ...     event_type=AuditEventType.PIPELINE_START,
    ...     pipeline_id="pipe-001",
    ...     user_goal="Build a REST API"
    ... )
    >>> logger.verify_integrity()
    True
"""

import csv
import hashlib
import io
import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Dict, List, Optional

from gaia.pipeline.state import PipelineState
from gaia.utils.logging import get_logger

logger = get_logger(__name__)


class AuditEventType(Enum):
    """
    Enumeration of all auditable pipeline events.

    Categories:
        - Pipeline lifecycle (START, COMPLETE)
        - Phase transitions (ENTER, EXIT)
        - Agent operations (SELECTED, EXECUTED)
        - Quality operations (EVALUATED)
        - Decision operations (MADE)
        - Defect operations (DISCOVERED, REMEDIATED)
        - Loop operations (LOOP_BACK)
        - Tool operations (EXECUTED)

    Example:
        >>> event_type = AuditEventType.PIPELINE_START
        >>> print(event_type.category())  # "lifecycle"
    """

    # Pipeline Lifecycle
    PIPELINE_START = auto()
    PIPELINE_COMPLETE = auto()

    # Phase Transitions
    PHASE_ENTER = auto()
    PHASE_EXIT = auto()

    # Agent Operations
    AGENT_SELECTED = auto()
    AGENT_EXECUTED = auto()

    # Quality Operations
    QUALITY_EVALUATED = auto()

    # Decision Operations
    DECISION_MADE = auto()

    # Defect Operations
    DEFECT_DISCOVERED = auto()
    DEFECT_REMEDIATED = auto()

    # Loop Operations
    LOOP_BACK = auto()

    # Tool Operations
    TOOL_EXECUTED = auto()

    def category(self) -> str:
        """
        Get category of this event type.

        Returns:
            Category string name

        Example:
            >>> AuditEventType.PIPELINE_START.category()
            'lifecycle'
            >>> AuditEventType.PHASE_ENTER.category()
            'phase_transition'
        """
        name = self.name
        if "PIPELINE" in name:
            return "lifecycle"
        elif "PHASE" in name:
            return "phase_transition"
        elif "AGENT" in name:
            return "agent_operation"
        elif "QUALITY" in name:
            return "quality"
        elif "DECISION" in name:
            return "decision"
        elif "DEFECT" in name:
            return "defect"
        elif "LOOP" in name:
            return "loop"
        elif "TOOL" in name:
            return "tool"
        return "unknown"


@dataclass(frozen=True)
class AuditEvent:
    """
    Immutable audit event with hash chain integrity.

    Each event contains:
    - Unique event ID (UUID)
    - Event type classification
    - Timestamp of occurrence
    - Hash of previous event (chain linkage)
    - Computed hash of current event
    - Context (loop_id, phase, agent_id)
    - Payload (event-specific data)
    - Sequence number (global ordering)

    The frozen=True ensures events cannot be modified after creation,
    providing tamper-evidence through hash chain verification.

    Example:
        >>> event = AuditEvent(
        ...     event_id="evt-001",
        ...     event_type=AuditEventType.PHASE_ENTER,
        ...     timestamp=datetime.now(timezone.utc),
        ...     previous_hash="0" * 64,
        ...     sequence_number=1,
        ...     phase="PLANNING"
        ... )
        >>> event.verify_hash()
        True
    """

    event_id: str
    event_type: AuditEventType
    timestamp: datetime
    previous_hash: str
    sequence_number: int
    current_hash: str = field(default="", init=False)
    loop_id: Optional[str] = None
    phase: Optional[str] = None
    agent_id: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Compute hash after initialization."""
        if not self.current_hash:
            object.__setattr__(self, "current_hash", self.compute_hash())

    def compute_hash(self) -> str:
        """
        Compute cryptographic hash of this event.

        Uses SHA-256 hash of canonical JSON representation to ensure
        deterministic hash computation.

        Returns:
            64-character hexadecimal hash string

        Example:
            >>> event = AuditEvent(...)
            >>> hash1 = event.compute_hash()
            >>> hash2 = event.compute_hash()
            >>> assert hash1 == hash2  # Deterministic
        """
        hash_data = {
            "event_id": self.event_id,
            "event_type": self.event_type.name,
            "timestamp": self.timestamp.isoformat(),
            "previous_hash": self.previous_hash,
            "sequence_number": self.sequence_number,
            "loop_id": self.loop_id,
            "phase": self.phase,
            "agent_id": self.agent_id,
            "payload": json.dumps(self.payload, sort_keys=True),
            "metadata": json.dumps(self.metadata, sort_keys=True),
        }
        canonical = json.dumps(hash_data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def verify_hash(self) -> bool:
        """
        Verify that the stored hash matches computed hash.

        Returns:
            True if hash matches, False if tampering detected

        Example:
            >>> event = AuditEvent(...)
            >>> event.verify_hash()
            True
        """
        return self.current_hash == self.compute_hash()

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert event to dictionary for serialization.

        Returns:
            Dictionary representation with all event fields

        Example:
            >>> event = AuditEvent(...)
            >>> data = event.to_dict()
            >>> assert "event_id" in data
            >>> assert "current_hash" in data
        """
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.name,
            "timestamp": self.timestamp.isoformat(),
            "previous_hash": self.previous_hash,
            "current_hash": self.current_hash,
            "sequence_number": self.sequence_number,
            "loop_id": self.loop_id,
            "phase": self.phase,
            "agent_id": self.agent_id,
            "payload": self.payload,
            "metadata": self.metadata,
        }

    def to_json(self, indent: int = 2) -> str:
        """
        Convert event to JSON string.

        Args:
            indent: JSON indentation level (default: 2)

        Returns:
            JSON string representation

        Example:
            >>> event = AuditEvent(...)
            >>> json_str = event.to_json()
            >>> print(json_str)
        """
        return json.dumps(self.to_dict(), indent=indent)


class IntegrityVerificationError(Exception):
    """
    Raised when hash chain integrity verification fails.

    Provides detailed information about the failure including:
    - Failed event ID
    - Failure type (HASH_MISMATCH, BROKEN_CHAIN, MISSING_EVENT)
    - Expected and actual hash values

    Example:
        >>> try:
        ...     logger.verify_integrity()
        ... except IntegrityVerificationError as e:
        ...     print(f"Failed at: {e.failed_event_id}")
        ...     print(f"Type: {e.failure_type}")
    """

    def __init__(
        self,
        failed_event_id: str,
        failure_type: str,
        expected_hash: Optional[str] = None,
        actual_hash: Optional[str] = None,
        message: Optional[str] = None,
    ):
        """
        Initialize the exception.

        Args:
            failed_event_id: ID of event where verification failed
            failure_type: Type of failure (HASH_MISMATCH, BROKEN_CHAIN, MISSING_EVENT)
            expected_hash: Expected hash value
            actual_hash: Actual computed hash value
            message: Optional custom error message
        """
        self.failed_event_id = failed_event_id
        self.failure_type = failure_type
        self.expected_hash = expected_hash
        self.actual_hash = actual_hash

        if message is None:
            message = self._generate_message()

        super().__init__(message)

    def _generate_message(self) -> str:
        """Generate human-readable error message."""
        if self.failure_type == "HASH_MISMATCH":
            return (
                f"Hash mismatch for event {self.failed_event_id}: "
                f"expected {self.expected_hash}, got {self.actual_hash}"
            )
        elif self.failure_type == "BROKEN_CHAIN":
            return (
                f"Broken hash chain at event {self.failed_event_id}: "
                f"previous hash does not match"
            )
        elif self.failure_type == "MISSING_EVENT":
            return f"Missing event in chain: {self.failed_event_id}"
        else:
            return f"Integrity verification failed at {self.failed_event_id}: {self.failure_type}"

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert exception to dictionary for logging.

        Returns:
            Dictionary with error details
        """
        return {
            "error": "IntegrityVerificationError",
            "failed_event_id": self.failed_event_id,
            "failure_type": self.failure_type,
            "expected_hash": self.expected_hash,
            "actual_hash": self.actual_hash,
            "message": str(self),
        }


class AuditLogger:
    """
    Tamper-proof audit logger with hash chain integrity.

    The AuditLogger provides a cryptographically secure audit trail for
    GAIA pipeline execution. Each event is linked to the previous event
    through a SHA-256 hash chain, making any tampering immediately detectable.

    Features:
        - Hash chain integrity verification
        - Thread-safe concurrent access (RLock protected)
        - Loop-based event isolation for concurrent iterations
        - Multiple export formats (JSON, CSV)
        - Flexible querying and filtering by type, loop, phase, time

    Hash Chain Structure:
        GENESIS_HASH (64 zeros)
               |
               v
        +----------------------------------------------+
        | EVENT 1: PIPELINE_START                      |
        | previous_hash: 0000000000000000...           |
        | current_hash:  sha256(event1_data + prev)    |
        +----------------------------------------------+
               |
               | current_hash becomes next previous_hash
               v
        +----------------------------------------------+
        | EVENT 2: PHASE_ENTER                         |
        | previous_hash: [EVENT 1 current_hash]        |
        | current_hash:  sha256(event2_data + prev)    |
        +----------------------------------------------+

    Example:
        >>> logger = AuditLogger(logger_id="pipeline-001")
        >>> logger.log(AuditEventType.PIPELINE_START, pipeline_id="p1")
        >>> logger.log(AuditEventType.PHASE_ENTER, phase="PLANNING")
        >>> logger.verify_integrity()
        True
        >>> events = logger.get_events(filters={"phase": "PLANNING"})
    """

    # Genesis hash - 64 hex characters representing "zero" hash
    GENESIS_HASH = "0" * 64

    def __init__(
        self,
        logger_id: Optional[str] = None,
        genesis_hash: Optional[str] = None,
    ):
        """
        Initialize audit logger.

        Args:
            logger_id: Unique identifier for this logger instance
            genesis_hash: Optional custom genesis hash (default: 64 zeros)

        Example:
            >>> logger = AuditLogger(logger_id="pipeline-001")
            >>> logger.logger_id
            'pipeline-001'
        """
        self.logger_id = logger_id or f"audit-{datetime.now(timezone.utc).isoformat()}"
        self._events: List[AuditEvent] = []
        self._event_index: Dict[str, AuditEvent] = {}
        self._loop_buckets: Dict[str, List[str]] = {}
        self._sequence_counter = 0
        self._lock = threading.RLock()
        self._genesis_hash = genesis_hash or self.GENESIS_HASH
        self._initialized_at = datetime.now(timezone.utc)

        logger.info(
            "AuditLogger initialized",
            extra={
                "logger_id": self.logger_id,
                "genesis_hash": self._genesis_hash[:16] + "...",
            },
        )

    def log(
        self,
        event_type: AuditEventType,
        loop_id: Optional[str] = None,
        phase: Optional[str] = None,
        agent_id: Optional[str] = None,
        **kwargs: Any,
    ) -> AuditEvent:
        """
        Log a new audit event.

        Creates an immutable AuditEvent with hash chain linkage to the
        previous event. Thread-safe operation protected by RLock.

        Args:
            event_type: Type of event being logged
            loop_id: Optional loop iteration identifier
            phase: Optional pipeline phase name
            agent_id: Optional agent identifier
            **kwargs: Additional payload data

        Returns:
            The created AuditEvent

        Example:
            >>> logger = AuditLogger()
            >>> event = logger.log(
            ...     event_type=AuditEventType.PIPELINE_START,
            ...     pipeline_id="pipe-001",
            ...     user_goal="Build REST API"
            ... )
            >>> print(event.event_type)  # AuditEventType.PIPELINE_START
            >>> print(event.sequence_number)  # 1
        """
        with self._lock:
            previous_hash = self._get_latest_hash()
            self._sequence_counter += 1

            event = AuditEvent(
                event_id=self._generate_event_id(),
                event_type=event_type,
                timestamp=datetime.now(timezone.utc),
                previous_hash=previous_hash,
                sequence_number=self._sequence_counter,
                loop_id=loop_id,
                phase=phase,
                agent_id=agent_id,
                payload=kwargs,
            )

            self._events.append(event)
            self._event_index[event.event_id] = event

            if loop_id:
                if loop_id not in self._loop_buckets:
                    self._loop_buckets[loop_id] = []
                self._loop_buckets[loop_id].append(event.event_id)

            logger.debug(
                f"Logged event: {event.event_type.name}",
                extra={
                    "event_id": event.event_id,
                    "event_type": event.event_type.name,
                    "sequence": event.sequence_number,
                    "loop_id": loop_id,
                    "phase": phase,
                },
            )

            return event

    def verify_integrity(self) -> bool:
        """
        Verify the integrity of the entire hash chain.

        Checks:
        1. Each event's current_hash matches computed hash
        2. Each event's previous_hash matches previous event's current_hash
        3. Chain starts with genesis hash

        Returns:
            True if chain is intact

        Raises:
            IntegrityVerificationError: Details about first failure found

        Example:
            >>> logger = AuditLogger()
            >>> logger.log(AuditEventType.PIPELINE_START)
            >>> logger.log(AuditEventType.PHASE_ENTER, phase="PLANNING")
            >>> logger.verify_integrity()
            True
        """
        with self._lock:
            if not self._events:
                return True

            previous_hash = self._genesis_hash

            for event in self._events:
                # Verify event hash
                if not event.verify_hash():
                    raise IntegrityVerificationError(
                        failed_event_id=event.event_id,
                        failure_type="HASH_MISMATCH",
                        expected_hash=event.current_hash,
                        actual_hash=event.compute_hash(),
                    )

                # Verify chain linkage
                if event.previous_hash != previous_hash:
                    raise IntegrityVerificationError(
                        failed_event_id=event.event_id,
                        failure_type="BROKEN_CHAIN",
                        expected_hash=previous_hash,
                        actual_hash=event.previous_hash,
                    )

                previous_hash = event.current_hash

            return True

    def get_events(
        self,
        filters: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[AuditEvent]:
        """
        Query events with optional filters.

        Supported filters:
            - event_type: Single AuditEventType
            - event_types: List of AuditEventTypes
            - loop_id: Loop iteration identifier
            - phase: Pipeline phase name
            - agent_id: Agent identifier
            - start_time: Minimum timestamp
            - end_time: Maximum timestamp
            - category: Event category (e.g., "lifecycle", "quality")
            - payload_contains: Tuple of (key, value) to find in payload

        Args:
            filters: Dictionary of filter criteria
            limit: Maximum number of events to return
            offset: Number of events to skip

        Returns:
            List of matching AuditEvents in chronological order

        Example:
            >>> events = logger.get_events(filters={"phase": "PLANNING"})
            >>> events = logger.get_events(filters={"category": "quality"})
            >>> events = logger.get_events(filters={"loop_id": "loop-001"}, limit=10)
        """
        with self._lock:
            events = self._events.copy()

            if filters:
                if "event_type" in filters:
                    events = [
                        e for e in events if e.event_type == filters["event_type"]
                    ]

                if "event_types" in filters:
                    events = [
                        e for e in events if e.event_type in filters["event_types"]
                    ]

                if "loop_id" in filters:
                    events = [e for e in events if e.loop_id == filters["loop_id"]]

                if "phase" in filters:
                    events = [e for e in events if e.phase == filters["phase"]]

                if "agent_id" in filters:
                    events = [e for e in events if e.agent_id == filters["agent_id"]]

                if "start_time" in filters:
                    events = [e for e in events if e.timestamp >= filters["start_time"]]

                if "end_time" in filters:
                    events = [e for e in events if e.timestamp <= filters["end_time"]]

                if "category" in filters:
                    events = [
                        e
                        for e in events
                        if e.event_type.category() == filters["category"]
                    ]

                if "payload_contains" in filters:
                    key, value = filters["payload_contains"]
                    events = [
                        e
                        for e in events
                        if key in e.payload and e.payload[key] == value
                    ]

            events = events[offset:]
            if limit:
                events = events[:limit]

            return events

    def export_log(self, format: str = "json", indent: Optional[int] = 2) -> str:
        """
        Export complete audit log to string.

        Args:
            format: Export format ("json" or "csv")
            indent: JSON indentation (None for compact)

        Returns:
            Formatted string of audit log

        Raises:
            ValueError: If unsupported export format

        Example:
            >>> json_export = logger.export_log(format="json")
            >>> csv_export = logger.export_log(format="csv")
        """
        with self._lock:
            if format == "json":
                return self._export_json(indent)
            elif format == "csv":
                return self._export_csv()
            else:
                raise ValueError(f"Unsupported export format: {format}")

    def _export_json(self, indent: Optional[int]) -> str:
        """Export to JSON format."""
        export_data = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "logger_id": self.logger_id,
            "genesis_hash": self._genesis_hash,
            "total_events": len(self._events),
            "integrity_verified": True,
            "events": [event.to_dict() for event in self._events],
        }

        try:
            self.verify_integrity()
            export_data["integrity_verified"] = True
        except IntegrityVerificationError:
            export_data["integrity_verified"] = False
            export_data["integrity_warning"] = (
                "Chain verification failed - possible tampering"
            )

        return json.dumps(export_data, indent=indent)

    def _export_csv(self) -> str:
        """Export to CSV format."""
        output = io.StringIO()

        fieldnames = [
            "sequence_number",
            "event_id",
            "event_type",
            "timestamp",
            "loop_id",
            "phase",
            "agent_id",
            "payload_summary",
            "current_hash",
        ]

        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()

        for event in self._events:
            writer.writerow(
                {
                    "sequence_number": event.sequence_number,
                    "event_id": event.event_id,
                    "event_type": event.event_type.name,
                    "timestamp": event.timestamp.isoformat(),
                    "loop_id": event.loop_id or "",
                    "phase": event.phase or "",
                    "agent_id": event.agent_id or "",
                    "payload_summary": json.dumps(event.payload),
                    "current_hash": event.current_hash[:16] + "...",
                }
            )

        return output.getvalue()

    def get_event(self, event_id: str) -> Optional[AuditEvent]:
        """
        Get specific event by ID.

        Args:
            event_id: Event ID to retrieve

        Returns:
            AuditEvent or None if not found

        Example:
            >>> event = logger.get_event("evt-abc123")
            >>> if event:
            ...     print(event.event_type)
        """
        with self._lock:
            return self._event_index.get(event_id)

    def get_events_by_type(self, event_type: AuditEventType) -> List[AuditEvent]:
        """
        Get all events of a specific type.

        Args:
            event_type: Event type to filter by

        Returns:
            List of events with matching type

        Example:
            >>> phase_exits = logger.get_events_by_type(AuditEventType.PHASE_EXIT)
        """
        with self._lock:
            return [e for e in self._events if e.event_type == event_type]

    def get_events_by_loop(self, loop_id: str) -> List[AuditEvent]:
        """
        Get all events for a specific loop iteration.

        Args:
            loop_id: Loop iteration identifier

        Returns:
            List of events for the specified loop

        Example:
            >>> loop_events = logger.get_events_by_loop("loop-001")
        """
        with self._lock:
            event_ids = self._loop_buckets.get(loop_id, [])
            return [
                self._event_index[eid] for eid in event_ids if eid in self._event_index
            ]

    def get_events_by_phase(self, phase: str) -> List[AuditEvent]:
        """
        Get all events for a specific pipeline phase.

        Args:
            phase: Pipeline phase name

        Returns:
            List of events for the specified phase

        Example:
            >>> planning_events = logger.get_events_by_phase("PLANNING")
        """
        with self._lock:
            return [e for e in self._events if e.phase == phase]

    def get_events_in_range(
        self,
        start: datetime,
        end: datetime,
    ) -> List[AuditEvent]:
        """
        Get events within a time range.

        Args:
            start: Start timestamp (inclusive)
            end: End timestamp (inclusive)

        Returns:
            List of events within the time range

        Example:
            >>> from datetime import timedelta
            >>> hour_ago = datetime.now() - timedelta(hours=1)
            >>> recent = logger.get_events_in_range(hour_ago, datetime.now())
        """
        with self._lock:
            return [e for e in self._events if start <= e.timestamp <= end]

    def get_chain_summary(self) -> Dict[str, Any]:
        """
        Get summary of the audit chain.

        Returns:
            Dictionary with chain statistics including:
            - logger_id: Logger identifier
            - total_events: Total event count
            - by_type: Count by event type
            - by_category: Count by event category
            - first_event: Timestamp of first event
            - last_event: Timestamp of last event
            - genesis_hash: Chain genesis hash
            - latest_hash: Hash of most recent event
            - loop_count: Number of unique loops

        Example:
            >>> summary = logger.get_chain_summary()
            >>> print(f"Total events: {summary['total_events']}")
        """
        with self._lock:
            by_type = {}
            for event in self._events:
                type_name = event.event_type.name
                by_type[type_name] = by_type.get(type_name, 0) + 1

            by_category = {}
            for event in self._events:
                category = event.event_type.category()
                by_category[category] = by_category.get(category, 0) + 1

            first_timestamp = self._events[0].timestamp if self._events else None
            last_timestamp = self._events[-1].timestamp if self._events else None

            return {
                "logger_id": self.logger_id,
                "total_events": len(self._events),
                "by_type": by_type,
                "by_category": by_category,
                "first_event": first_timestamp.isoformat() if first_timestamp else None,
                "last_event": last_timestamp.isoformat() if last_timestamp else None,
                "genesis_hash": self._genesis_hash,
                "latest_hash": self._get_latest_hash(),
                "loop_count": len(self._loop_buckets),
            }

    def get_integrity_report(self) -> Dict[str, Any]:
        """
        Generate detailed integrity verification report.

        Returns:
            Dictionary with integrity report including:
            - is_valid: Overall validity status
            - verified_at: Timestamp of verification
            - total_events: Total events checked
            - genesis_hash: Chain genesis hash
            - latest_hash: Hash of most recent event
            - failure_details: Details if verification failed

        Example:
            >>> report = logger.get_integrity_report()
            >>> if report["is_valid"]:
            ...     print("Chain integrity verified")
        """
        with self._lock:
            report = {
                "is_valid": True,
                "verified_at": datetime.now(timezone.utc).isoformat(),
                "total_events": len(self._events),
                "genesis_hash": self._genesis_hash,
                "latest_hash": self._get_latest_hash(),
                "failure_details": None,
            }

            try:
                self.verify_integrity()
            except IntegrityVerificationError as e:
                report["is_valid"] = False
                report["failure_details"] = e.to_dict()

            return report

    def clear(self) -> None:
        """
        Clear all events and reset logger.

        Use with caution - this removes all audit trail data.

        Example:
            >>> logger.clear()
            >>> assert len(logger.get_events()) == 0
        """
        with self._lock:
            self._events.clear()
            self._event_index.clear()
            self._loop_buckets.clear()
            self._sequence_counter = 0
            logger.warning("AuditLogger cleared", extra={"logger_id": self.logger_id})

    def _get_latest_hash(self) -> str:
        """Get hash of the most recent event (or genesis hash if empty)."""
        if self._events:
            return self._events[-1].current_hash
        return self._genesis_hash

    def _generate_event_id(self) -> str:
        """Generate unique event ID."""
        return f"evt-{uuid.uuid4().hex[:12]}"
