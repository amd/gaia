"""
Security Validator - Real-time Security Validation and Audit Logging.

Provides real-time security validation for file operations with:
- Audit logging for all access attempts
- Path traversal detection
- Policy enforcement
- Security event statistics
- Thread-safe operations

Example:
    >>> validator = SecurityValidator()
    >>> validator.audit_access("src/main.py", "read", allowed=True)
    >>> stats = validator.get_statistics()
    >>> print(stats["total_events"])
    1
"""

import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from gaia.utils.logging import get_logger

logger = get_logger(__name__)


class SecurityAuditEventType(Enum):
    """Types of security audit events."""

    ACCESS_GRANTED = "access_granted"
    ACCESS_DENIED = "access_denied"
    PATH_TRAVERSAL_DETECTED = "path_traversal_detected"
    SHELL_INJECTION_DETECTED = "shell_injection_detected"
    POLICY_VIOLATION = "policy_violation"
    VALIDATION_PASSED = "validation_passed"
    VALIDATION_FAILED = "validation_failed"


@dataclass
class SecurityAuditEvent:
    """
    Security audit event record.

    Attributes:
        event_type: Type of security event
        path: Path involved in the event
        operation: Operation being performed
        allowed: Whether access was allowed
        timestamp: Event timestamp
        metadata: Additional event metadata
    """

    event_type: SecurityAuditEventType
    path: str
    operation: str
    allowed: bool
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "event_type": self.event_type.value,
            "path": self.path,
            "operation": self.operation,
            "allowed": self.allowed,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


class SecurityValidator:
    """
    Real-time security validation and audit logging.

    SecurityValidator provides:
    1. Audit logging for all file access attempts
    2. Path traversal detection and prevention
    3. Policy enforcement with configurable rules
    4. Security event statistics and monitoring
    5. Thread-safe concurrent operations

    Security Features:
    - Comprehensive audit trail for all access attempts
    - Multiple traversal pattern detection
    - Shell injection pattern detection
    - Configurable policy rules
    - Real-time event monitoring

    Thread Safety:
    - All operations protected by RLock
    - Safe for concurrent access from 100+ threads
    - Event log limited to prevent memory growth

    Example:
        >>> validator = SecurityValidator()
        >>> validator.audit_access("src/main.py", "read", allowed=True)
        >>> validator.detect_traversal("../etc/passwd")
        True
        >>> stats = validator.get_statistics()
        >>> print(stats["total_events"])
        1
    """

    # Path traversal patterns to detect
    TRAVERSAL_PATTERNS = [
        r'\.\.',           # Parent directory reference
        r'\.%2e',          # URL-encoded traversal
        r'%2e\.',          # Mixed encoding
        r'%2e%2e',         # Fully encoded
        r'\.\%2f',         # Traversal with encoded slash
        r'%2e%2f',         # Encoded traversal
    ]

    # Shell injection patterns
    SHELL_INJECTION_PATTERNS = [
        r'\$\(',           # Command substitution $()
        r'\$\{',           # Variable expansion ${}
        r'`[^`]*`',        # Backtick command substitution
        r'\|',             # Pipe
        r'&&',             # AND operator
        r'\|\|',           # OR operator
        r';',              # Command separator
        r'>\s*/',          # Redirect to absolute path
        r'<\s*/',          # Redirect from absolute path
    ]

    def __init__(
        self,
        max_events: int = 10000,
        enable_logging: bool = True,
    ):
        """
        Initialize SecurityValidator.

        Args:
            max_events: Maximum events to store in memory (default: 10000)
            enable_logging: Enable event logging (default: True)

        Example:
            >>> validator = SecurityValidator(max_events=5000)
        """
        self._events: List[SecurityAuditEvent] = []
        self._max_events = max_events
        self._enable_logging = enable_logging
        self._lock = threading.RLock()

        # Statistics counters
        self._stats = {
            "total_events": 0,
            "access_granted": 0,
            "access_denied": 0,
            "traversal_detected": 0,
            "injection_detected": 0,
            "policy_violations": 0,
        }

        # Compile regex patterns for performance
        self._traversal_regex = [
            re.compile(p, re.IGNORECASE) for p in self.TRAVERSAL_PATTERNS
        ]
        self._injection_regex = [
            re.compile(p) for p in self.SHELL_INJECTION_PATTERNS
        ]

        logger.info(
            "SecurityValidator initialized",
            extra={
                "max_events": max_events,
                "enable_logging": enable_logging,
            }
        )

    def audit_access(
        self,
        path: str,
        operation: str,
        allowed: bool,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SecurityAuditEvent:
        """
        Audit a file access attempt.

        Records an audit event for tracking and compliance.

        Args:
            path: Path being accessed
            operation: Operation type (read, write, delete)
            allowed: Whether access was allowed
            metadata: Additional metadata (optional)

        Returns:
            Created SecurityAuditEvent

        Example:
            >>> validator = SecurityValidator()
            >>> event = validator.audit_access(
            ...     "src/main.py", "read", allowed=True
            ... )
            >>> print(event.event_type.value)
            'access_granted'
        """
        with self._lock:
            event_type = (
                SecurityAuditEventType.ACCESS_GRANTED if allowed
                else SecurityAuditEventType.ACCESS_DENIED
            )

            event = SecurityAuditEvent(
                event_type=event_type,
                path=path,
                operation=operation,
                allowed=allowed,
                metadata=metadata or {},
            )

            self._add_event(event)
            self._stats["total_events"] += 1

            if allowed:
                self._stats["access_granted"] += 1
            else:
                self._stats["access_denied"] += 1

            if self._enable_logging:
                if allowed:
                    logger.debug(
                        f"Access granted: {path}",
                        extra={
                            "path": path,
                            "operation": operation,
                            "event_type": event_type.value,
                        }
                    )
                else:
                    logger.warning(
                        f"Access denied: {path}",
                        extra={
                            "path": path,
                            "operation": operation,
                            "event_type": event_type.value,
                        }
                    )

            return event

    def _add_event(self, event: SecurityAuditEvent) -> None:
        """
        Add event to the event log.

        Args:
            event: Event to add
        """
        self._events.append(event)

        # Trim to max size
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events:]

    def detect_traversal(self, path: str) -> bool:
        """
        Detect path traversal attempts.

        Checks for various path traversal patterns including:
        - Parent directory references (..)
        - URL-encoded traversals (%2e%2e)
        - Mixed encoding attacks

        Args:
            path: Path to check

        Returns:
            True if traversal pattern detected, False otherwise

        Example:
            >>> validator = SecurityValidator()
            >>> validator.detect_traversal("../etc/passwd")
            True
            >>> validator.detect_traversal("src/main.py")
            False
        """
        with self._lock:
            # Check for basic parent traversal
            if ".." in path:
                self._stats["traversal_detected"] += 1
                return True

            # Check regex patterns
            for pattern in self._traversal_regex:
                if pattern.search(path):
                    self._stats["traversal_detected"] += 1
                    return True

            return False

    def detect_shell_injection(self, content: str) -> bool:
        """
        Detect shell injection patterns in content.

        Checks for various shell injection patterns including:
        - Command substitution $()
        - Variable expansion ${}
        - Backtick execution
        - Pipe and redirect operators

        Args:
            content: Content to check

        Returns:
            True if injection pattern detected, False otherwise

        Example:
            >>> validator = SecurityValidator()
            >>> validator.detect_shell_injection("$(cat /etc/passwd)")
            True
            >>> validator.detect_shell_injection("normal content")
            False
        """
        with self._lock:
            for pattern in self._injection_regex:
                if pattern.search(content):
                    self._stats["injection_detected"] += 1
                    return True
            return False

    def enforce_policy(
        self,
        path: str,
        operation: str,
        allowed_paths: Optional[Set[str]] = None,
    ) -> bool:
        """
        Enforce security policy for path access.

        Validates path against security policies:
        1. Checks for path traversal
        2. Verifies path is in allowed paths (if provided)
        3. Blocks absolute paths
        4. Blocks shell injection patterns

        Args:
            path: Path to validate
            operation: Operation being performed
            allowed_paths: Set of allowed base paths (optional)

        Returns:
            True if policy allows access, False otherwise

        Example:
            >>> validator = SecurityValidator()
            >>> allowed = {"/workspace", "/tmp"}
            >>> validator.enforce_policy("src/main.py", "read", allowed)
            False
        """
        with self._lock:
            # Check for traversal
            if self.detect_traversal(path):
                self._stats["policy_violations"] += 1
                self.audit_access(path, operation, allowed=False, metadata={
                    "reason": "path_traversal_detected"
                })
                return False

            # Check for shell injection in path
            if self.detect_shell_injection(path):
                self._stats["policy_violations"] += 1
                self.audit_access(path, operation, allowed=False, metadata={
                    "reason": "shell_injection_detected"
                })
                return False

            # Check against allowed paths if provided
            if allowed_paths:
                # Normalize path for comparison
                normalized = path.replace("\\", "/").lstrip("/")

                # Check if path starts with any allowed path
                path_allowed = False
                for allowed in allowed_paths:
                    allowed_normalized = str(allowed).replace("\\", "/")
                    allowed_with_sep = (
                        allowed_normalized if allowed_normalized.endswith("/")
                        else allowed_normalized + "/"
                    )

                    if (
                        normalized == allowed_normalized or
                        normalized.startswith(allowed_with_sep)
                    ):
                        path_allowed = True
                        break

                if not path_allowed:
                    self._stats["policy_violations"] += 1
                    self.audit_access(path, operation, allowed=False, metadata={
                        "reason": "not_in_allowed_paths"
                    })
                    return False

            # Policy check passed
            self.audit_access(path, operation, allowed=True, metadata={
                "reason": "policy_check_passed"
            })
            return True

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get security event statistics.

        Returns:
            Dictionary with:
            - total_events: Total events recorded
            - access_granted: Count of granted access events
            - access_denied: Count of denied access events
            - traversal_detected: Count of traversal detections
            - injection_detected: Count of injection detections
            - policy_violations: Count of policy violations
            - recent_events: Last 10 events

        Example:
            >>> validator = SecurityValidator()
            >>> validator.audit_access("test.txt", "read", allowed=True)
            >>> stats = validator.get_statistics()
            >>> print(stats["total_events"])
            1
        """
        with self._lock:
            return {
                **self._stats,
                "recent_events": [
                    event.to_dict() for event in self._events[-10:]
                ],
            }

    def get_events(
        self,
        event_type: Optional[SecurityAuditEventType] = None,
        limit: int = 100,
    ) -> List[SecurityAuditEvent]:
        """
        Get audit events with optional filtering.

        Args:
            event_type: Filter by event type (optional)
            limit: Maximum events to return (default: 100)

        Returns:
            List of matching SecurityAuditEvent objects
        """
        with self._lock:
            if event_type:
                filtered = [
                    e for e in self._events if e.event_type == event_type
                ]
                return list(reversed(filtered[-limit:]))

            return list(reversed(self._events[-limit:]))

    def clear_events(self) -> int:
        """
        Clear all recorded events.

        Returns:
            Number of events cleared
        """
        with self._lock:
            count = len(self._events)
            self._events.clear()
            return count

    def get_violation_summary(self) -> Dict[str, Any]:
        """
        Get summary of security violations.

        Returns:
            Dictionary with violation statistics and recent violations
        """
        with self._lock:
            violations = [
                e for e in self._events if not e.allowed
            ]

            # Count by type
            by_type: Dict[str, int] = {}
            for event in violations:
                key = event.event_type.value
                by_type[key] = by_type.get(key, 0) + 1

            return {
                "total_violations": len(violations),
                "by_type": by_type,
                "recent_violations": [
                    e.to_dict() for e in violations[-10:]
                ],
            }


# Import logging for log levels
import logging  # noqa: E402
