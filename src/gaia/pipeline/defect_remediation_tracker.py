"""
GAIA DefectRemediationTracker

Tracks defect status across loop iterations with full audit trail.

This module provides comprehensive tracking and management of defects throughout
the GAIA pipeline's recursive loop iterations. It enables:

- Status lifecycle management - Track defects from discovery through verification
- Audit trail - Complete history of all status changes with timestamps and reasons
- Concurrent loop support - Thread-safe operations for parallel loop iterations
- Analytics and reporting - Real-time visibility into defect resolution progress

Status Lifecycle:
    OPEN -> IN_PROGRESS -> RESOLVED -> VERIFIED (success path)
    OPEN -> DEFERRED (blocked or low priority)
    OPEN -> CANNOT_FIX (fundamental limitation)

Example:
    >>> from gaia.pipeline.defect_router import Defect, DefectType, DefectSeverity
    >>> from gaia.pipeline.defect_remediation_tracker import DefectRemediationTracker
    >>>
    >>> tracker = DefectRemediationTracker(tracker_id="loop-001")
    >>> defect = Defect(
    ...     id="defect-001",
    ...     type=DefectType.MISSING_TESTS,
    ...     severity=DefectSeverity.HIGH,
    ...     description="No unit tests for new module"
    ... )
    >>> tracker.add_defect(defect, phase="QUALITY")
    >>> tracker.start_fix("defect-001")  # OPEN -> IN_PROGRESS
    >>> tracker.mark_resolved("defect-001", "Added 15 unit tests")  # IN_PROGRESS -> RESOLVED
    >>> tracker.mark_verified("defect-001", "Quality check passed")  # RESOLVED -> VERIFIED
    >>> summary = tracker.get_summary()
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional, Any, Set
from datetime import datetime, timezone
import threading
import copy

from gaia.pipeline.defect_router import Defect, DefectType, DefectSeverity, DefectStatus as RouterDefectStatus
from gaia.utils.logging import get_logger


logger = get_logger(__name__)


class DefectStatus(Enum):
    """
    Status of defect in remediation lifecycle.

    Extends the base DefectStatus from defect_router.py with additional states
    for complete lifecycle management.

    Lifecycle:
        OPEN -> IN_PROGRESS -> RESOLVED -> VERIFIED (success path)
        OPEN -> DEFERRED (blocked or low priority)
        OPEN -> CANNOT_FIX (fundamental limitation)

    Attributes:
        OPEN: Newly discovered defect, awaiting action
        IN_PROGRESS: Currently being fixed
        RESOLVED: Fix implemented, awaiting verification
        VERIFIED: Fix confirmed by quality check
        DEFERRED: Cannot fix now (with reason)
        CANNOT_FIX: Fundamental limitation preventing fix
    """

    OPEN = auto()
    IN_PROGRESS = auto()
    RESOLVED = auto()
    VERIFIED = auto()
    DEFERRED = auto()
    CANNOT_FIX = auto()

    def is_terminal(self) -> bool:
        """
        Check if this is a terminal status (no further transitions expected).

        Terminal statuses are VERIFIED, DEFERRED, and CANNOT_FIX.

        Returns:
            True if this is a terminal status, False otherwise

        Example:
            >>> DefectStatus.VERIFIED.is_terminal()
            True
            >>> DefectStatus.OPEN.is_terminal()
            False
        """
        return self in {DefectStatus.VERIFIED, DefectStatus.DEFERRED, DefectStatus.CANNOT_FIX}

    def is_active(self) -> bool:
        """
        Check if defect is actively being worked.

        Active statuses are OPEN and IN_PROGRESS.

        Returns:
            True if defect is active, False otherwise

        Example:
            >>> DefectStatus.IN_PROGRESS.is_active()
            True
            >>> DefectStatus.RESOLVED.is_active()
            False
        """
        return self in {DefectStatus.OPEN, DefectStatus.IN_PROGRESS}


class DefectStatusTransition(Enum):
    """
    Valid status transitions for defects.

    This enum defines all valid transitions between defect statuses,
    providing type-safe transition validation.

    Example:
        >>> transition = DefectStatusTransition.OPEN_TO_IN_PROGRESS
        >>> print(transition.from_status)  # DefectStatus.OPEN
        >>> print(transition.to_status)    # DefectStatus.IN_PROGRESS
    """

    OPEN_TO_IN_PROGRESS = auto()
    OPEN_TO_DEFERRED = auto()
    OPEN_TO_CANNOT_FIX = auto()
    IN_PROGRESS_TO_RESOLVED = auto()
    IN_PROGRESS_TO_OPEN = auto()
    IN_PROGRESS_TO_DEFERRED = auto()
    RESOLVED_TO_VERIFIED = auto()
    RESOLVED_TO_IN_PROGRESS = auto()
    RESOLVED_TO_OPEN = auto()
    VERIFIED_TO_IN_PROGRESS = auto()
    DEFERRED_TO_OPEN = auto()
    DEFERRED_TO_IN_PROGRESS = auto()
    CANNOT_FIX_TO_OPEN = auto()

    @property
    def from_status(self) -> DefectStatus:
        """Get the source status for this transition."""
        return TRANSITION_FROM_STATUS[self]

    @property
    def to_status(self) -> DefectStatus:
        """Get the target status for this transition."""
        return TRANSITION_TO_STATUS[self]


# Mapping of transitions to their source and target statuses
TRANSITION_FROM_STATUS: Dict[DefectStatusTransition, DefectStatus] = {
    DefectStatusTransition.OPEN_TO_IN_PROGRESS: DefectStatus.OPEN,
    DefectStatusTransition.OPEN_TO_DEFERRED: DefectStatus.OPEN,
    DefectStatusTransition.OPEN_TO_CANNOT_FIX: DefectStatus.OPEN,
    DefectStatusTransition.IN_PROGRESS_TO_RESOLVED: DefectStatus.IN_PROGRESS,
    DefectStatusTransition.IN_PROGRESS_TO_OPEN: DefectStatus.IN_PROGRESS,
    DefectStatusTransition.IN_PROGRESS_TO_DEFERRED: DefectStatus.IN_PROGRESS,
    DefectStatusTransition.RESOLVED_TO_VERIFIED: DefectStatus.RESOLVED,
    DefectStatusTransition.RESOLVED_TO_IN_PROGRESS: DefectStatus.RESOLVED,
    DefectStatusTransition.RESOLVED_TO_OPEN: DefectStatus.RESOLVED,
    DefectStatusTransition.VERIFIED_TO_IN_PROGRESS: DefectStatus.VERIFIED,
    DefectStatusTransition.DEFERRED_TO_OPEN: DefectStatus.DEFERRED,
    DefectStatusTransition.DEFERRED_TO_IN_PROGRESS: DefectStatus.DEFERRED,
    DefectStatusTransition.CANNOT_FIX_TO_OPEN: DefectStatus.CANNOT_FIX,
}

TRANSITION_TO_STATUS: Dict[DefectStatusTransition, DefectStatus] = {
    DefectStatusTransition.OPEN_TO_IN_PROGRESS: DefectStatus.IN_PROGRESS,
    DefectStatusTransition.OPEN_TO_DEFERRED: DefectStatus.DEFERRED,
    DefectStatusTransition.OPEN_TO_CANNOT_FIX: DefectStatus.CANNOT_FIX,
    DefectStatusTransition.IN_PROGRESS_TO_RESOLVED: DefectStatus.RESOLVED,
    DefectStatusTransition.IN_PROGRESS_TO_OPEN: DefectStatus.OPEN,
    DefectStatusTransition.IN_PROGRESS_TO_DEFERRED: DefectStatus.DEFERRED,
    DefectStatusTransition.RESOLVED_TO_VERIFIED: DefectStatus.VERIFIED,
    DefectStatusTransition.RESOLVED_TO_IN_PROGRESS: DefectStatus.IN_PROGRESS,
    DefectStatusTransition.RESOLVED_TO_OPEN: DefectStatus.OPEN,
    DefectStatusTransition.VERIFIED_TO_IN_PROGRESS: DefectStatus.IN_PROGRESS,
    DefectStatusTransition.DEFERRED_TO_OPEN: DefectStatus.OPEN,
    DefectStatusTransition.DEFERRED_TO_IN_PROGRESS: DefectStatus.IN_PROGRESS,
    DefectStatusTransition.CANNOT_FIX_TO_OPEN: DefectStatus.OPEN,
}


@dataclass
class DefectStatusChange:
    """
    Immutable record of a defect status change.

    Captures the complete context of a status transition for audit purposes.
    This dataclass is immutable after creation to ensure audit trail integrity.

    Attributes:
        defect_id: Unique defect identifier
        old_status: Previous status value
        new_status: New status value
        changed_at: Timestamp of change (defaults to current UTC time)
        changed_by: Optional identifier of who/what made the change
        description: Optional description of the change
        metadata: Additional contextual information

    Example:
        >>> change = DefectStatusChange(
        ...     defect_id="defect-001",
        ...     old_status=DefectStatus.OPEN,
        ...     new_status=DefectStatus.IN_PROGRESS,
        ...     description="Starting fix in DEVELOPMENT phase",
        ...     changed_by="senior-developer"
        ... )
        >>> print(change.to_dict())
        {'defect_id': 'defect-001', 'old_status': 'OPEN', 'new_status': 'IN_PROGRESS', ...}
    """

    defect_id: str
    old_status: DefectStatus
    new_status: DefectStatus
    changed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    changed_by: Optional[str] = None
    description: Optional[str] = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """
        Validate status change after initialization.

        Logs a warning if the status change is a no-op (same old and new status).
        """
        if self.old_status == self.new_status:
            logger.warning(
                f"Status change from {self.old_status} to {self.new_status} is a no-op",
                extra={"defect_id": self.defect_id},
            )

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dictionary for serialization.

        Returns:
            Dictionary representation of the status change with ISO format timestamp

        Example:
            >>> change = DefectStatusChange(
            ...     defect_id="defect-001",
            ...     old_status=DefectStatus.OPEN,
            ...     new_status=DefectStatus.IN_PROGRESS
            ... )
            >>> data = change.to_dict()
            >>> assert data["defect_id"] == "defect-001"
            >>> assert data["old_status"] == "OPEN"
        """
        return {
            "defect_id": self.defect_id,
            "old_status": self.old_status.name,
            "new_status": self.new_status.name,
            "changed_at": self.changed_at.isoformat(),
            "changed_by": self.changed_by,
            "description": self.description,
            "metadata": self.metadata,
        }

    def to_audit_entry(self) -> Dict[str, Any]:
        """
        Convert to audit log entry format.

        Returns:
            Audit log formatted entry with event type and action description

        Example:
            >>> change = DefectStatusChange(
            ...     defect_id="defect-001",
            ...     old_status=DefectStatus.OPEN,
            ...     new_status=DefectStatus.IN_PROGRESS,
            ...     changed_by="developer"
            ... )
            >>> entry = change.to_audit_entry()
            >>> assert entry["event_type"] == "DEFECT_STATUS_CHANGE"
            >>> assert entry["action"] == "OPEN -> IN_PROGRESS"
        """
        return {
            "event_type": "DEFECT_STATUS_CHANGE",
            "defect_id": self.defect_id,
            "timestamp": self.changed_at.isoformat(),
            "actor": self.changed_by,
            "action": f"{self.old_status.name} -> {self.new_status.name}",
            "description": self.description,
            "metadata": self.metadata,
        }


class InvalidStatusTransitionError(Exception):
    """
    Raised when an invalid status transition is attempted.

    This exception provides detailed information about the attempted
    invalid transition, including the current status, requested status,
    and allowed transitions.

    Attributes:
        defect_id: Defect that had the invalid transition
        current_status: Current status value
        requested_status: Requested new status
        allowed_transitions: List of allowed next statuses

    Example:
        >>> try:
        ...     tracker.mark_verified("defect-001", "QA passed")  # From OPEN
        ... except InvalidStatusTransitionError as e:
        ...     print(f"Cannot transition from {e.current_status} to {e.requested_status}")
        ...     print(f"Allowed: {e.allowed_transitions}")
    """

    def __init__(
        self,
        defect_id: str,
        current_status: DefectStatus,
        requested_status: DefectStatus,
        allowed_transitions: List[DefectStatus],
    ):
        """
        Initialize the exception.

        Args:
            defect_id: Defect that had the invalid transition
            current_status: Current status value
            requested_status: Requested new status
            allowed_transitions: List of allowed next statuses
        """
        self.defect_id = defect_id
        self.current_status = current_status
        self.requested_status = requested_status
        self.allowed_transitions = allowed_transitions

        super().__init__(
            f"Invalid status transition for {defect_id}: "
            f"{current_status.name} -> {requested_status.name}. "
            f"Allowed transitions: {[s.name for s in allowed_transitions]}",
            {
                "defect_id": defect_id,
                "current_status": current_status.name,
                "requested_status": requested_status.name,
                "allowed_transitions": [s.name for s in allowed_transitions],
            },
        )

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert exception to dictionary for logging.

        Returns:
            Dictionary representation of the exception
        """
        return {
            "error": "InvalidStatusTransitionError",
            "defect_id": self.defect_id,
            "current_status": self.current_status.name,
            "requested_status": self.requested_status.name,
            "allowed_transitions": [s.name for s in self.allowed_transitions],
            "message": str(self),
        }


class DefectRemediationTracker:
    """
    Tracks defect status across loop iterations with full audit trail.

    The DefectRemediationTracker manages the complete lifecycle of defects
    from discovery through verification. It enforces valid status transitions,
    maintains an immutable audit trail, and supports concurrent loop execution.

    Status Lifecycle:
        OPEN -> IN_PROGRESS -> RESOLVED -> VERIFIED
          |                              |
          |                              +-> (Quality check confirms fix)
          |
          +-> DEFERRED (blocked, low priority, or waiting on dependency)
          |
          +-> CANNOT_FIX (fundamental limitation or technical constraint)

    Thread Safety:
        All operations are protected by a reentrant lock (RLock), making
        the tracker safe for concurrent access across multiple loop iterations.

    Example:
        >>> tracker = DefectRemediationTracker(tracker_id="loop-001")
        >>> defect = Defect(
        ...     id="defect-001",
        ...     type=DefectType.MISSING_TESTS,
        ...     severity=DefectSeverity.HIGH,
        ...     description="No unit tests for new module"
        ... )
        >>> tracker.add_defect(defect, phase="QUALITY")
        >>> tracker.start_fix("defect-001")  # OPEN -> IN_PROGRESS
        >>> tracker.mark_resolved("defect-001", "Added 15 unit tests")
        >>> tracker.mark_verified("defect-001", "Quality check passed")
        >>> pending = tracker.get_pending_defects()
        >>> summary = tracker.get_summary()
        >>> analytics = tracker.get_analytics()
    """

    # Valid status transitions map
    ALLOWED_TRANSITIONS: Dict[DefectStatus, List[DefectStatus]] = {
        DefectStatus.OPEN: [
            DefectStatus.IN_PROGRESS,
            DefectStatus.DEFERRED,
            DefectStatus.CANNOT_FIX,
        ],
        DefectStatus.IN_PROGRESS: [
            DefectStatus.RESOLVED,
            DefectStatus.OPEN,  # Can reopen if not ready
            DefectStatus.DEFERRED,
        ],
        DefectStatus.RESOLVED: [
            DefectStatus.VERIFIED,
            DefectStatus.IN_PROGRESS,  # Reopen for more work
            DefectStatus.OPEN,
        ],
        DefectStatus.VERIFIED: [
            DefectStatus.IN_PROGRESS,  # Regression found
        ],
        DefectStatus.DEFERRED: [
            DefectStatus.OPEN,  # Can be reopened
            DefectStatus.IN_PROGRESS,
        ],
        DefectStatus.CANNOT_FIX: [
            DefectStatus.OPEN,  # Can be reopened if workaround found
        ],
    }

    def __init__(self, tracker_id: Optional[str] = None):
        """
        Initialize defect remediation tracker.

        Args:
            tracker_id: Optional unique identifier for this tracker instance
                       (useful for tracking per-loop or per-phase)

        Example:
            >>> tracker = DefectRemediationTracker(tracker_id="loop-001")
            >>> tracker.tracker_id
            'loop-001'
            >>> tracker2 = DefectRemediationTracker()  # Auto-generated ID
        """
        self.tracker_id = tracker_id or f"tracker-{datetime.now(timezone.utc).isoformat()}"
        self._defects: Dict[str, Defect] = {}
        self._history: List[DefectStatusChange] = []
        self._phase_buckets: Dict[str, Set[str]] = {}  # phase -> set of defect IDs
        self._lock = threading.RLock()

        logger.info(
            "DefectRemediationTracker initialized",
            extra={"tracker_id": self.tracker_id},
        )

    def add_defect(self, defect: Defect, phase: str) -> None:
        """
        Add a new defect to the tracker.

        The defect must have OPEN status when added. Automatically
        creates a status change record for the audit trail.
        If a defect with non-OPEN status is provided, it will be
        reset to OPEN with a warning logged.

        Args:
            defect: Defect to track
            phase: Pipeline phase where defect was detected

        Raises:
            ValueError: If defect is None

        Example:
            >>> defect = Defect(id="d1", type=DefectType.MISSING_TESTS, ...)
            >>> tracker.add_defect(defect, phase="QUALITY")
            >>> tracker.add_defect(defect, phase="DEVELOPMENT")  # Duplicate ID ignored with warning
        """
        if defect is None:
            raise ValueError("Defect cannot be None")

        with self._lock:
            # Check for duplicate
            if defect.id in self._defects:
                logger.warning(
                    f"Defect {defect.id} already exists, ignoring duplicate add",
                    extra={"defect_id": defect.id},
                )
                return

            # Store original status for audit trail
            original_status = defect.status

            # Validate initial status - must be OPEN
            if defect.status != DefectStatus.OPEN:
                logger.warning(
                    f"Defect {defect.id} added with non-OPEN status: {defect.status.name}. "
                    f"Setting to OPEN.",
                    extra={"defect_id": defect.id, "original_status": defect.status.name},
                )
                # Create a deep copy to avoid modifying the original
                defect = copy.deepcopy(defect)
                defect.status = DefectStatus.OPEN

            # Add defect
            self._defects[defect.id] = defect

            # Add to phase bucket
            if phase not in self._phase_buckets:
                self._phase_buckets[phase] = set()
            self._phase_buckets[phase].add(defect.id)

            # Record initial status change
            change = DefectStatusChange(
                defect_id=defect.id,
                old_status=DefectStatus.OPEN,
                new_status=DefectStatus.OPEN,
                description=f"Defect discovered in {phase} phase",
                metadata={"phase_detected": phase},
            )
            self._history.append(change)

            logger.info(
                f"Added defect: {defect.id} ({defect.type.name}, {defect.severity.name})",
                extra={
                    "defect_id": defect.id,
                    "phase": phase,
                    "severity": defect.severity.name,
                },
            )

    def start_fix(self, defect_id: str, changed_by: Optional[str] = None) -> DefectStatusChange:
        """
        Start working on a defect (OPEN -> IN_PROGRESS).

        Args:
            defect_id: ID of defect to start fixing
            changed_by: Optional identifier of who/what is making the change

        Returns:
            DefectStatusChange record

        Raises:
            InvalidStatusTransitionError: If current status doesn't allow transition
            KeyError: If defect not found

        Example:
            >>> tracker.add_defect(defect, "QUALITY")
            >>> change = tracker.start_fix("defect-001", changed_by="senior-developer")
            >>> print(change.description)  # "Starting fix"
            >>> print(change.new_status)   # DefectStatus.IN_PROGRESS
        """
        return self._transition_status(
            defect_id=defect_id,
            new_status=DefectStatus.IN_PROGRESS,
            description="Starting fix",
            changed_by=changed_by,
        )

    def mark_resolved(
        self,
        defect_id: str,
        description: str,
        changed_by: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DefectStatusChange:
        """
        Mark a defect as resolved (IN_PROGRESS -> RESOLVED).

        The fix has been implemented but awaits verification by quality check.

        Args:
            defect_id: ID of defect to mark resolved
            description: Description of the fix implemented
            changed_by: Optional identifier of who/what made the change
            metadata: Optional additional metadata about the fix

        Returns:
            DefectStatusChange record

        Raises:
            InvalidStatusTransitionError: If current status doesn't allow transition
            KeyError: If defect not found

        Example:
            >>> tracker.start_fix("defect-001")
            >>> change = tracker.mark_resolved(
            ...     "defect-001",
            ...     description="Added 15 unit tests with 95% coverage",
            ...     changed_by="senior-developer",
            ...     metadata={"tests_added": 15, "coverage": 0.95}
            ... )
        """
        return self._transition_status(
            defect_id=defect_id,
            new_status=DefectStatus.RESOLVED,
            description=description,
            changed_by=changed_by,
            metadata=metadata or {},
        )

    def mark_verified(
        self,
        defect_id: str,
        notes: str,
        changed_by: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DefectStatusChange:
        """
        Verify a defect fix (RESOLVED -> VERIFIED).

        Called after quality check confirms the fix is effective.

        Args:
            defect_id: ID of defect to verify
            notes: Verification notes from quality check
            changed_by: Optional identifier of who/what made the change
            metadata: Optional additional metadata about verification

        Returns:
            DefectStatusChange record

        Raises:
            InvalidStatusTransitionError: If current status doesn't allow transition
            KeyError: If defect not found

        Example:
            >>> tracker.mark_resolved("defect-001", "Fix implemented")
            >>> change = tracker.mark_verified(
            ...     "defect-001",
            ...     notes="Quality check passed - tests run successfully",
            ...     changed_by="quality-reviewer",
            ...     metadata={"quality_score": 0.95}
            ... )
        """
        return self._transition_status(
            defect_id=defect_id,
            new_status=DefectStatus.VERIFIED,
            description=notes,
            changed_by=changed_by,
            metadata=metadata or {},
        )

    def mark_deferred(
        self,
        defect_id: str,
        reason: str,
        changed_by: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DefectStatusChange:
        """
        Defer a defect (OPEN/IN_PROGRESS -> DEFERRED).

        Used when a defect cannot or should not be fixed in the current iteration.

        Args:
            defect_id: ID of defect to defer
            reason: Reason for deferral
            changed_by: Optional identifier of who/what made the change
            metadata: Optional additional metadata

        Returns:
            DefectStatusChange record

        Raises:
            InvalidStatusTransitionError: If current status doesn't allow transition
            KeyError: If defect not found

        Example:
            >>> tracker.mark_deferred(
            ...     "defect-001",
            ...     reason="Low priority, deferring to next sprint",
            ...     changed_by="product-owner",
            ...     metadata={"defer_reason": "low_priority"}
            ... )
        """
        return self._transition_status(
            defect_id=defect_id,
            new_status=DefectStatus.DEFERRED,
            description=reason,
            changed_by=changed_by,
            metadata={**(metadata or {}), "defer_reason": reason},
        )

    def mark_cannot_fix(
        self,
        defect_id: str,
        reason: str,
        changed_by: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DefectStatusChange:
        """
        Mark a defect as unfixable (OPEN/IN_PROGRESS -> CANNOT_FIX).

        Used when a fundamental limitation prevents fixing the defect.

        Args:
            defect_id: ID of defect to mark as unfixable
            reason: Reason why it cannot be fixed
            changed_by: Optional identifier of who/what made the change
            metadata: Optional additional metadata

        Returns:
            DefectStatusChange record

        Raises:
            InvalidStatusTransitionError: If current status doesn't allow transition
            KeyError: If defect not found

        Example:
            >>> tracker.mark_cannot_fix(
            ...     "defect-001",
            ...     reason="Platform limitation - cannot be resolved",
            ...     changed_by="tech-lead",
            ...     metadata={"limitation": "platform"}
            ... )
        """
        return self._transition_status(
            defect_id=defect_id,
            new_status=DefectStatus.CANNOT_FIX,
            description=reason,
            changed_by=changed_by,
            metadata={**(metadata or {}), "cannot_fix_reason": reason},
        )

    def _transition_status(
        self,
        defect_id: str,
        new_status: DefectStatus,
        description: str = "",
        changed_by: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DefectStatusChange:
        """
        Internal method to transition defect status.

        This method validates the transition against ALLOWED_TRANSITIONS,
        updates the defect status, and records the change in the audit trail.

        Args:
            defect_id: ID of defect to transition
            new_status: New status value
            description: Description of the transition
            changed_by: Who/what made the change
            metadata: Additional metadata

        Returns:
            DefectStatusChange record

        Raises:
            InvalidStatusTransitionError: If transition is not allowed
            KeyError: If defect not found
        """
        with self._lock:
            if defect_id not in self._defects:
                raise KeyError(f"Defect not found: {defect_id}")

            defect = self._defects[defect_id]
            old_status = defect.status

            # Validate transition
            allowed = self.ALLOWED_TRANSITIONS.get(old_status, [])
            if new_status not in allowed:
                raise InvalidStatusTransitionError(
                    defect_id=defect_id,
                    current_status=old_status,
                    requested_status=new_status,
                    allowed_transitions=allowed,
                )

            # Update defect status
            defect.status = new_status

            # Record status change
            change = DefectStatusChange(
                defect_id=defect_id,
                old_status=old_status,
                new_status=new_status,
                description=description,
                changed_by=changed_by,
                metadata=metadata or {},
            )
            self._history.append(change)

            logger.info(
                f"Defect {defect_id} status changed: {old_status.name} -> {new_status.name}",
                extra={
                    "defect_id": defect_id,
                    "old_status": old_status.name,
                    "new_status": new_status.name,
                    "changed_by": changed_by,
                },
            )

            return change

    def get_pending_defects(self) -> List[Defect]:
        """
        Get all defects that are not in terminal status.

        Returns defects with status: OPEN, IN_PROGRESS, or RESOLVED.
        Results are sorted by severity (CRITICAL first, then HIGH, MEDIUM, LOW).

        Returns:
            List of pending defects sorted by severity

        Example:
            >>> pending = tracker.get_pending_defects()
            >>> print(f"{len(pending)} defects need attention")
            >>> for defect in pending:
            ...     print(f"  - {defect.id}: {defect.description}")
        """
        with self._lock:
            pending = [
                d for d in self._defects.values()
                if d.status in {DefectStatus.OPEN, DefectStatus.IN_PROGRESS, DefectStatus.RESOLVED}
            ]
            # Sort by severity (CRITICAL=1, HIGH=2, MEDIUM=3, LOW=4)
            pending.sort(key=lambda d: d.severity.value)
            return pending

    def get_summary(self) -> Dict[str, Any]:
        """
        Generate summary statistics for all tracked defects.

        Returns comprehensive statistics including counts by status, severity,
        type, and phase, plus resolution rate metrics.

        Returns:
            Dictionary with summary statistics including:
            - total: Total number of defects
            - by_status: Count by status
            - by_severity: Count by severity
            - by_type: Count by defect type
            - by_phase: Count by phase detected
            - pending_count: Number not in terminal status
            - verified_count: Number verified as fixed
            - resolution_rate: Percentage resolved/verified

        Example:
            >>> summary = tracker.get_summary()
            >>> print(f"Total: {summary['total']}, Pending: {summary['pending_count']}")
            >>> print(f"Resolution rate: {summary['resolution_rate']:.1%}")
        """
        with self._lock:
            summary = {
                "total": len(self._defects),
                "by_status": {},
                "by_severity": {},
                "by_type": {},
                "by_phase": {},
                "pending_count": 0,
                "verified_count": 0,
                "deferred_count": 0,
                "cannot_fix_count": 0,
                "resolution_rate": 0.0,
            }

            for defect in self._defects.values():
                # Count by status
                status_name = defect.status.name
                summary["by_status"][status_name] = summary["by_status"].get(status_name, 0) + 1

                # Count pending vs terminal
                if defect.status == DefectStatus.VERIFIED:
                    summary["verified_count"] += 1
                elif defect.status == DefectStatus.DEFERRED:
                    summary["deferred_count"] += 1
                elif defect.status == DefectStatus.CANNOT_FIX:
                    summary["cannot_fix_count"] += 1
                else:
                    summary["pending_count"] += 1

                # Count by severity
                severity_name = defect.severity.name
                summary["by_severity"][severity_name] = (
                    summary["by_severity"].get(severity_name, 0) + 1
                )

                # Count by type
                type_name = defect.type.name
                summary["by_type"][type_name] = summary["by_type"].get(type_name, 0) + 1

                # Count by phase (from metadata)
                phase = defect.phase_detected or "UNKNOWN"
                summary["by_phase"][phase] = summary["by_phase"].get(phase, 0) + 1

            # Calculate resolution rate
            resolved_or_verified = (
                summary["verified_count"] + summary["deferred_count"] + summary["cannot_fix_count"]
            )
            if summary["total"] > 0:
                summary["resolution_rate"] = resolved_or_verified / summary["total"]

            return summary

    def get_defect_history(
        self,
        defect_id: Optional[str] = None,
        status_filter: Optional[DefectStatus] = None,
    ) -> List[DefectStatusChange]:
        """
        Get defect status change history.

        Args:
            defect_id: Optional filter for specific defect
            status_filter: Optional filter for specific new status

        Returns:
            List of status changes (chronological order)

        Example:
            >>> all_history = tracker.get_defect_history()
            >>> single_defect = tracker.get_defect_history("defect-001")
            >>> verified_only = tracker.get_defect_history(status_filter=DefectStatus.VERIFIED)
        """
        with self._lock:
            history = self._history.copy()

            if defect_id:
                history = [h for h in history if h.defect_id == defect_id]

            if status_filter:
                history = [h for h in history if h.new_status == status_filter]

            return history

    def get_defects_by_phase(self, phase: str) -> List[Defect]:
        """
        Get all defects detected in a specific phase.

        Args:
            phase: Phase name to filter by

        Returns:
            List of defects from that phase

        Example:
            >>> quality_defects = tracker.get_defects_by_phase("QUALITY")
            >>> print(f"Found {len(quality_defects)} defects in QUALITY phase")
        """
        with self._lock:
            phase_defect_ids = self._phase_buckets.get(phase, set())
            return [
                self._defects[did] for did in phase_defect_ids if did in self._defects
            ]

    def get_defects_by_status(self, status: DefectStatus) -> List[Defect]:
        """
        Get all defects with a specific status.

        Args:
            status: Status to filter by

        Returns:
            List of defects with that status

        Example:
            >>> open_defects = tracker.get_defects_by_status(DefectStatus.OPEN)
            >>> in_progress = tracker.get_defects_by_status(DefectStatus.IN_PROGRESS)
        """
        with self._lock:
            return [d for d in self._defects.values() if d.status == status]

    def get_defect(self, defect_id: str) -> Optional[Defect]:
        """
        Get a specific defect by ID.

        Args:
            defect_id: Defect ID to retrieve

        Returns:
            Defect or None if not found

        Example:
            >>> defect = tracker.get_defect("defect-001")
            >>> if defect:
            ...     print(f"Status: {defect.status.name}")
        """
        with self._lock:
            return self._defects.get(defect_id)

    def get_all_defects(self) -> List[Defect]:
        """
        Get all tracked defects.

        Returns:
            List of all defects

        Example:
            >>> all_defects = tracker.get_all_defects()
            >>> for defect in all_defects:
            ...     print(f"{defect.id}: {defect.status.name}")
        """
        with self._lock:
            return list(self._defects.values())

    def export_audit_log(self) -> List[Dict[str, Any]]:
        """
        Export complete audit log of all status changes.

        Returns:
            List of audit entries in chronological order

        Example:
            >>> audit_log = tracker.export_audit_log()
            >>> for entry in audit_log:
            ...     print(f"{entry['timestamp']}: {entry['action']}")
        """
        with self._lock:
            return [change.to_audit_entry() for change in self._history]

    def get_analytics(self) -> Dict[str, Any]:
        """
        Generate advanced analytics for defect remediation.

        Calculates metrics such as Mean Time To Resolve (MTTR) and
        Mean Time To Verify (MTTV), plus distribution statistics.

        Returns:
            Dictionary with analytics including:
            - mean_time_to_resolve: Average time from OPEN to RESOLVED (in hours)
            - mean_time_to_verify: Average time from RESOLVED to VERIFIED (in hours)
            - defects_by_severity_priority: Defects sorted by severity
            - phase_distribution: Defects per phase
            - status_trend: Status distribution

        Example:
            >>> analytics = tracker.get_analytics()
            >>> print(f"MTTR: {analytics['mean_time_to_resolve']:.2f} hours")
            >>> print(f"MTTV: {analytics['mean_time_to_verify']:.2f} hours")
        """
        with self._lock:
            analytics = {
                "mean_time_to_resolve": None,
                "mean_time_to_verify": None,
                "defects_by_severity_priority": {},
                "phase_distribution": {},
                "status_trend": {},
            }

            # Calculate mean time to resolve
            resolve_times = []
            verify_times = []

            for defect_id in self._defects:
                defect_history = [h for h in self._history if h.defect_id == defect_id]

                # Find OPEN -> IN_PROGRESS -> RESOLVED -> VERIFIED transitions
                open_time = None
                resolve_time = None
                verified_time = None

                for change in defect_history:
                    if change.new_status == DefectStatus.OPEN and open_time is None:
                        open_time = change.changed_at
                    elif change.new_status == DefectStatus.RESOLVED:
                        resolve_time = change.changed_at
                    elif change.new_status == DefectStatus.VERIFIED:
                        verified_time = change.changed_at

                if open_time and resolve_time:
                    resolve_times.append((resolve_time - open_time).total_seconds() / 3600)

                if resolve_time and verified_time:
                    verify_times.append((verified_time - resolve_time).total_seconds() / 3600)

            if resolve_times:
                analytics["mean_time_to_resolve"] = sum(resolve_times) / len(resolve_times)

            if verify_times:
                analytics["mean_time_to_verify"] = sum(verify_times) / len(verify_times)

            # Severity priority distribution
            for severity in DefectSeverity:
                count = sum(1 for d in self._defects.values() if d.severity == severity)
                if count > 0:
                    analytics["defects_by_severity_priority"][severity.name] = count

            # Phase distribution
            for phase, defect_ids in self._phase_buckets.items():
                analytics["phase_distribution"][phase] = len(defect_ids)

            # Status trend
            analytics["status_trend"] = {
                "OPEN": len([d for d in self._defects.values() if d.status == DefectStatus.OPEN]),
                "IN_PROGRESS": len([d for d in self._defects.values() if d.status == DefectStatus.IN_PROGRESS]),
                "RESOLVED": len([d for d in self._defects.values() if d.status == DefectStatus.RESOLVED]),
                "VERIFIED": len([d for d in self._defects.values() if d.status == DefectStatus.VERIFIED]),
                "DEFERRED": len([d for d in self._defects.values() if d.status == DefectStatus.DEFERRED]),
                "CANNOT_FIX": len([d for d in self._defects.values() if d.status == DefectStatus.CANNOT_FIX]),
            }

            return analytics

    def clear(self) -> None:
        """
        Clear all tracked defects and history.

        Use with caution - this removes all audit trail data.

        Example:
            >>> tracker.clear()  # Reset tracker
            >>> assert len(tracker.get_all_defects()) == 0
        """
        with self._lock:
            self._defects.clear()
            self._history.clear()
            self._phase_buckets.clear()
            logger.info("DefectRemediationTracker cleared", extra={"tracker_id": self.tracker_id})
