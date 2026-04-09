"""
GAIA Metrics Collector

Thread-safe collection of pipeline execution metrics.

This module provides the MetricsCollector class for collecting, storing,
and retrieving metrics data during pipeline execution. It integrates with
the AuditLogger, DefectRemediationTracker, and PipelineState to automatically
capture relevant metrics.

Example:
    >>> from gaia.metrics.collector import MetricsCollector
    >>> from gaia.metrics.models import MetricType
    >>> collector = MetricsCollector(collector_id="pipeline-001")
    >>> collector.record_metric(
    ...     loop_id="loop-001",
    ...     phase="DEVELOPMENT",
    ...     metric_type=MetricType.TOKEN_EFFICIENCY,
    ...     value=0.85
    ... )
    >>> snapshot = collector.get_latest_snapshot("loop-001", "DEVELOPMENT")
"""

import json
import sqlite3
import statistics
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from gaia.metrics.models import (
    MetricSnapshot,
    MetricsReport,
    MetricStatistics,
    MetricType,
)
from gaia.pipeline.audit_logger import AuditEventType, AuditLogger
from gaia.pipeline.defect_remediation_tracker import (
    DefectRemediationTracker,
    DefectStatus,
)
from gaia.pipeline.state import PipelineSnapshot, PipelineStateMachine
from gaia.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class TokenTracking:
    """
    Tracks token usage for efficiency calculations.

    Attributes:
        tokens_input: Number of input tokens consumed
        tokens_output: Number of output tokens generated
        feature_name: Name of feature being implemented
        completed_at: When the feature was completed

    Example:
        >>> tracking = TokenTracking(
        ...     tokens_input=15000,
        ...     tokens_output=5000,
        ...     feature_name="API endpoint"
        ... )
        >>> tracking.total_tokens()
        20000
    """

    tokens_input: int = 0
    tokens_output: int = 0
    feature_name: str = ""
    completed_at: Optional[datetime] = None

    def total_tokens(self) -> int:
        """Get total tokens consumed."""
        return self.tokens_input + self.tokens_output

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "tokens_input": self.tokens_input,
            "tokens_output": self.tokens_output,
            "total_tokens": self.total_tokens(),
            "feature_name": self.feature_name,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
        }


@dataclass
class ContextTracking:
    """
    Tracks context window utilization.

    Attributes:
        context_window_size: Maximum context window size
        tokens_used: Number of tokens actually used
        effective_tokens: Number of tokens that contributed to output
        timestamp: When the tracking was recorded

    Example:
        >>> ctx = ContextTracking(
        ...     context_window_size=128000,
        ...     tokens_used=96000,
        ...     effective_tokens=80000
        ... )
        >>> ctx.utilization_ratio()
        0.75
    """

    context_window_size: int = 0
    tokens_used: int = 0
    effective_tokens: int = 0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def utilization_ratio(self) -> float:
        """Get context utilization ratio (0-1)."""
        if self.context_window_size == 0:
            return 0.0
        return self.tokens_used / self.context_window_size

    def effectiveness_ratio(self) -> float:
        """Get ratio of effective tokens to total used (0-1)."""
        if self.tokens_used == 0:
            return 0.0
        return self.effective_tokens / self.tokens_used

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "context_window_size": self.context_window_size,
            "tokens_used": self.tokens_used,
            "effective_tokens": self.effective_tokens,
            "utilization_ratio": self.utilization_ratio(),
            "effectiveness_ratio": self.effectiveness_ratio(),
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class QualityIteration:
    """
    Tracks iterations to reach quality threshold.

    Attributes:
        loop_id: Loop iteration identifier
        started_at: When iterations began
        threshold: Required quality threshold (0-1)
        iterations: Number of iterations performed
        quality_scores: Quality scores achieved per iteration
        reached_threshold: Whether threshold was reached

    Example:
        >>> qi = QualityIteration(
        ...     loop_id="loop-001",
        ...     threshold=0.90,
        ...     iterations=3,
        ...     quality_scores=[0.65, 0.78, 0.92]
        ... )
        >>> qi.reached_threshold
        True
    """

    loop_id: str
    threshold: float = 0.90
    iterations: int = 0
    quality_scores: List[float] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def reached_threshold(self) -> bool:
        """Check if quality threshold was reached."""
        if not self.quality_scores:
            return False
        return max(self.quality_scores) >= self.threshold

    def add_score(self, score: float) -> int:
        """
        Add a quality score and return iteration count.

        Args:
            score: Quality score (0-1)

        Returns:
            New iteration count
        """
        self.quality_scores.append(score)
        self.iterations = len(self.quality_scores)
        return self.iterations

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "loop_id": self.loop_id,
            "threshold": self.threshold,
            "iterations": self.iterations,
            "quality_scores": self.quality_scores,
            "reached_threshold": self.reached_threshold,
            "started_at": self.started_at.isoformat(),
        }


class SQLiteConnectionPool:
    """
    Singleton SQLite connection pool with connection pooling (QW-005).

    This class implements a thread-safe singleton connection pool for SQLite
    databases, providing efficient connection reuse and PRAGMA optimization.

    Example:
        >>> pool1 = SQLiteConnectionPool.get_instance("metrics.db")
        >>> pool2 = SQLiteConnectionPool.get_instance("metrics.db")
        >>> pool1 is pool2  # True - singleton pattern
        >>> with pool1.get_connection() as conn:
        ...     cursor = conn.cursor()
        ...     cursor.execute("SELECT 1")
    """

    _instance: Optional["SQLiteConnectionPool"] = None
    _lock = threading.Lock()
    _connection_local = threading.local()

    def __init__(self, db_path: str, pool_size: int = 5):
        """
        Initialize the connection pool (private - use get_instance()).

        Args:
            db_path: Path to SQLite database
            pool_size: Number of connections in the pool
        """
        self.db_path = db_path
        self.pool_size = pool_size
        self._connections: List[sqlite3.Connection] = []
        self._initialized = False

    @classmethod
    def get_instance(cls, db_path: str, pool_size: int = 5) -> "SQLiteConnectionPool":
        """
        Get or create the singleton instance.

        Args:
            db_path: Path to SQLite database
            pool_size: Number of connections in the pool

        Returns:
            SQLiteConnectionPool instance
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(db_path, pool_size)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (for testing)."""
        with cls._lock:
            if cls._instance:
                cls._instance.close_all()
                cls._instance = None

    def _create_connection(self) -> sqlite3.Connection:
        """
        Create a new optimized SQLite connection.

        Returns:
            Configured sqlite3.Connection
        """
        conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            isolation_level=None,  # Autocommit mode
        )

        # Optimize connection with PRAGMAs (QW-005)
        conn.execute("PRAGMA journal_mode=WAL")  # Better concurrency
        conn.execute("PRAGMA synchronous=NORMAL")  # Balanced durability/speed
        conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
        conn.execute("PRAGMA temp_store=MEMORY")  # Memory temp storage
        conn.execute("PRAGMA busy_timeout=5000")  # 5s busy timeout

        return conn

    def _get_connection(self) -> sqlite3.Connection:
        """
        Get a connection from the pool.

        Returns:
            sqlite3.Connection
        """
        # Check if this thread already has a connection
        if (
            not hasattr(self._connection_local, "connection")
            or self._connection_local.connection is None
        ):
            with self._lock:
                if len(self._connections) < self.pool_size:
                    # Create new connection
                    self._connection_local.connection = self._create_connection()
                    self._connections.append(self._connection_local.connection)
                else:
                    # Reuse existing connection (round-robin)
                    # In practice, each thread keeps its own connection
                    self._connection_local.connection = self._create_connection()

        return self._connection_local.connection

    @contextmanager
    def get_connection(self):
        """
        Context manager for getting a connection.

        Yields:
            sqlite3.Connection

        Example:
            >>> with pool.get_connection() as conn:
            ...     cursor = conn.cursor()
            ...     cursor.execute("SELECT 1")
        """
        conn = self._get_connection()
        try:
            yield conn
        finally:
            # Connection is kept for reuse by this thread
            pass

    def close_all(self) -> None:
        """Close all connections in the pool."""
        with self._lock:
            for conn in self._connections:
                try:
                    conn.close()
                except Exception:
                    pass
            self._connections.clear()
            # Clear thread-local connections
            if hasattr(self._connection_local, "connection"):
                self._connection_local.connection = None


class MetricsCollector:
    """
    Thread-safe collector for pipeline execution metrics.

    The MetricsCollector provides comprehensive tracking of pipeline
    metrics including token efficiency, context utilization, quality
    velocity, defect density, MTTR, and audit completeness.

    Integration Points:
        - AuditLogger: Logs metric recording events
        - DefectRemediationTracker: Tracks defects for density and MTTR
        - PipelineState: Associates metrics with pipeline phases

    Thread Safety:
        All public methods are protected by a reentrant lock (RLock),
        making the collector safe for concurrent access.

    Example:
        >>> collector = MetricsCollector(collector_id="pipeline-001")
        >>> collector.record_metric(
        ...     loop_id="loop-001",
        ...     phase="DEVELOPMENT",
        ...     metric_type=MetricType.TOKEN_EFFICIENCY,
        ...     value=0.85
        ... )
        >>> snapshot = collector.get_latest_snapshot("loop-001", "DEVELOPMENT")
        >>> report = collector.generate_report()
    """

    def __init__(
        self,
        collector_id: Optional[str] = None,
        audit_logger: Optional[AuditLogger] = None,
        db_path: Optional[str] = None,
        pool_size: int = 5,
    ):
        """
        Initialize metrics collector.

        Args:
            collector_id: Unique identifier for this collector
            audit_logger: Optional AuditLogger for integration
            db_path: Optional SQLite database path (QW-005 enables connection pooling)
            pool_size: Size of SQLite connection pool (default: 5)

        Example:
            >>> collector = MetricsCollector(collector_id="pipeline-001")
        """
        self.collector_id = collector_id or f"metrics-{uuid.uuid4().hex[:8]}"
        self._audit_logger = audit_logger

        # SQLite connection pool (QW-005)
        self._db_path = db_path
        self._connection_pool: Optional[SQLiteConnectionPool] = None
        if db_path:
            self._connection_pool = SQLiteConnectionPool.get_instance(
                db_path, pool_size
            )

        # Thread-safe storage
        self._lock = threading.RLock()

        # Snapshots indexed by (loop_id, phase)
        self._snapshots: Dict[Tuple[str, str], List[MetricSnapshot]] = {}

        # Token tracking per loop
        self._token_tracking: Dict[str, TokenTracking] = {}

        # Context tracking per loop
        self._context_tracking: Dict[str, ContextTracking] = {}

        # Quality iterations per loop
        self._quality_iterations: Dict[str, QualityIteration] = {}

        # Defect counts per loop (for density calculation)
        self._defect_counts: Dict[str, int] = {}

        # Code volume per loop (KLOC)
        self._code_volume: Dict[str, float] = {}

        # Defect resolution times per loop (for MTTR)
        self._defect_resolution_times: Dict[str, List[float]] = {}

        # Audit events expected vs logged per loop
        self._audit_expected: Dict[str, int] = {}
        self._audit_logged: Dict[str, int] = {}

        logger.info(
            "MetricsCollector initialized",
            extra={"collector_id": self.collector_id},
        )

    def _get_key(self, loop_id: str, phase: str) -> Tuple[str, str]:
        """Create storage key from loop_id and phase."""
        return (loop_id, phase)

    def record_metric(
        self,
        loop_id: str,
        phase: str,
        metric_type: MetricType,
        value: float,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MetricSnapshot:
        """
        Record a metric value.

        Creates or updates a MetricSnapshot for the given loop and phase.

        Args:
            loop_id: Loop iteration identifier
            phase: Pipeline phase name
            metric_type: Type of metric being recorded
            value: Metric value
            metadata: Optional additional metadata

        Returns:
            Updated MetricSnapshot

        Raises:
            ValueError: If value is not a valid number

        Example:
            >>> snapshot = collector.record_metric(
            ...     loop_id="loop-001",
            ...     phase="DEVELOPMENT",
            ...     metric_type=MetricType.TOKEN_EFFICIENCY,
            ...     value=0.85
            ... )
        """
        if not isinstance(value, (int, float)):
            raise ValueError(f"Metric value must be numeric, got {type(value)}")

        with self._lock:
            key = self._get_key(loop_id, phase)

            if key not in self._snapshots:
                self._snapshots[key] = []

            # Get or create current snapshot
            now = datetime.now(timezone.utc)
            if self._snapshots[key]:
                # Update existing latest snapshot
                current = self._snapshots[key][-1]
                snapshot = current.with_metric(metric_type, value)
                if metadata:
                    snapshot = snapshot.with_metadata(**metadata)
            else:
                # Create new snapshot
                snapshot = MetricSnapshot(
                    timestamp=now,
                    loop_id=loop_id,
                    phase=phase,
                    metrics={metric_type: value},
                    metadata=metadata or {},
                )

            self._snapshots[key].append(snapshot)

            # Log to audit logger if configured
            if self._audit_logger:
                self._audit_logger.log(
                    event_type=AuditEventType.TOOL_EXECUTED,
                    loop_id=loop_id,
                    phase=phase,
                    tool_name="metrics_collector",
                    action="record_metric",
                    metric_type=metric_type.name,
                    value=value,
                )

            logger.debug(
                f"Recorded metric: {metric_type.name}={value}",
                extra={
                    "collector_id": self.collector_id,
                    "loop_id": loop_id,
                    "phase": phase,
                    "metric_type": metric_type.name,
                    "value": value,
                },
            )

            return snapshot

    def record_token_usage(
        self,
        loop_id: str,
        tokens_input: int,
        tokens_output: int,
        feature_name: str = "",
    ) -> None:
        """
        Record token usage for efficiency tracking.

        Args:
            loop_id: Loop iteration identifier
            tokens_input: Number of input tokens
            tokens_output: Number of output tokens
            feature_name: Name of feature being implemented

        Example:
            >>> collector.record_token_usage(
            ...     loop_id="loop-001",
            ...     tokens_input=15000,
            ...     tokens_output=5000,
            ...     feature_name="REST API endpoint"
            ... )
        """
        with self._lock:
            if loop_id not in self._token_tracking:
                self._token_tracking[loop_id] = TokenTracking(feature_name=feature_name)

            tracking = self._token_tracking[loop_id]
            tracking.tokens_input += tokens_input
            tracking.tokens_output += tokens_output
            tracking.feature_name = feature_name or tracking.feature_name
            tracking.completed_at = datetime.now(timezone.utc)

            # Calculate and record token efficiency metric
            total_tokens = tracking.total_tokens()
            # Normalize: assume 10000 tokens per feature is baseline (1.0)
            efficiency = min(1.0, 10000 / total_tokens) if total_tokens > 0 else 1.0

            self.record_metric(
                loop_id=loop_id,
                phase="DEVELOPMENT",
                metric_type=MetricType.TOKEN_EFFICIENCY,
                value=efficiency,
                metadata={"tokens_total": total_tokens, "feature": feature_name},
            )

    def record_context_utilization(
        self,
        loop_id: str,
        context_window_size: int,
        tokens_used: int,
        effective_tokens: Optional[int] = None,
    ) -> None:
        """
        Record context window utilization.

        Args:
            loop_id: Loop iteration identifier
            context_window_size: Maximum context window size
            tokens_used: Number of tokens used
            effective_tokens: Tokens that contributed to output (optional)

        Example:
            >>> collector.record_context_utilization(
            ...     loop_id="loop-001",
            ...     context_window_size=128000,
            ...     tokens_used=96000,
            ...     effective_tokens=80000
            ... )
        """
        with self._lock:
            tracking = ContextTracking(
                context_window_size=context_window_size,
                tokens_used=tokens_used,
                effective_tokens=effective_tokens or tokens_used,
            )
            self._context_tracking[loop_id] = tracking

            # Calculate and record context utilization metric
            utilization = tracking.utilization_ratio()

            self.record_metric(
                loop_id=loop_id,
                phase="DEVELOPMENT",
                metric_type=MetricType.CONTEXT_UTILIZATION,
                value=utilization,
                metadata={
                    "context_window": context_window_size,
                    "tokens_used": tokens_used,
                },
            )

    def record_quality_score(
        self,
        loop_id: str,
        quality_score: float,
        threshold: float = 0.90,
    ) -> int:
        """
        Record a quality score iteration.

        Args:
            loop_id: Loop iteration identifier
            quality_score: Quality score achieved (0-1)
            threshold: Required quality threshold

        Returns:
            Current iteration count

        Example:
            >>> collector.record_quality_score("loop-001", 0.85)
            1
            >>> collector.record_quality_score("loop-001", 0.92)
            2
        """
        with self._lock:
            if loop_id not in self._quality_iterations:
                self._quality_iterations[loop_id] = QualityIteration(
                    loop_id=loop_id,
                    threshold=threshold,
                )

            quality_iter = self._quality_iterations[loop_id]
            iterations = quality_iter.add_score(quality_score)

            # Record quality velocity metric (iterations to reach threshold)
            if quality_iter.reached_threshold:
                self.record_metric(
                    loop_id=loop_id,
                    phase="QUALITY",
                    metric_type=MetricType.QUALITY_VELOCITY,
                    value=float(iterations),
                    metadata={
                        "quality_score": quality_score,
                        "threshold": threshold,
                        "reached": True,
                    },
                )

            return iterations

    def record_defect_discovered(self, loop_id: str, kloc: float = 1.0) -> None:
        """
        Record a defect discovery.

        Args:
            loop_id: Loop iteration identifier
            kloc: Thousands of lines of code (for density calculation)

        Example:
            >>> collector.record_defect_discovered("loop-001")
        """
        with self._lock:
            self._defect_counts[loop_id] = self._defect_counts.get(loop_id, 0) + 1

            # Update code volume if provided
            if kloc > 0:
                self._code_volume[loop_id] = kloc

            # Calculate and record defect density
            defect_count = self._defect_counts[loop_id]
            code_volume = self._code_volume.get(loop_id, 1.0)
            defect_density = defect_count / code_volume

            self.record_metric(
                loop_id=loop_id,
                phase="QUALITY",
                metric_type=MetricType.DEFECT_DENSITY,
                value=defect_density,
                metadata={
                    "defect_count": defect_count,
                    "kloc": code_volume,
                },
            )

    def record_defect_discovered_cross_loop(
        self,
        defect_id: str,
        loop_id_discovered: str,
        loop_id_resolved: Optional[str] = None,
        kloc: float = 1.0,
    ) -> None:
        """
        Record a defect discovery with cross-loop tracking support.

        For defects that span multiple loop iterations, this method tracks
        the loop where the defect was discovered separately from where it
        was resolved, enabling accurate cross-loop MTTR calculation.

        Args:
            defect_id: Unique defect identifier
            loop_id_discovered: Loop iteration where defect was discovered
            loop_id_resolved: Loop iteration where defect was resolved (optional)
            kloc: Thousands of lines of code (for density calculation)

        Example:
            >>> collector.record_defect_discovered_cross_loop(
            ...     defect_id="defect-001",
            ...     loop_id_discovered="loop-001",
            ...     loop_id_resolved="loop-003"
            ... )
        """
        with self._lock:
            # Track discovery loop
            self._defect_counts[loop_id_discovered] = (
                self._defect_counts.get(loop_id_discovered, 0) + 1
            )

            # Update code volume if provided
            if kloc > 0:
                self._code_volume[loop_id_discovered] = kloc

            # Calculate and record defect density for discovery loop
            defect_count = self._defect_counts[loop_id_discovered]
            code_volume = self._code_volume.get(loop_id_discovered, 1.0)
            defect_density = defect_count / code_volume

            self.record_metric(
                loop_id=loop_id_discovered,
                phase="QUALITY",
                metric_type=MetricType.DEFECT_DENSITY,
                value=defect_density,
                metadata={
                    "defect_count": defect_count,
                    "kloc": code_volume,
                    "defect_id": defect_id,
                    "cross_loop": loop_id_resolved is not None,
                },
            )

    def record_defect_resolved(
        self,
        loop_id: str,
        defect_id: str,
        discovered_at: datetime,
        resolved_at: Optional[datetime] = None,
        loop_id_discovered: Optional[str] = None,
        loop_id_resolved: Optional[str] = None,
    ) -> None:
        """
        Record defect resolution for MTTR calculation.

        Supports both single-loop and cross-loop defect resolution tracking.
        For cross-loop defects, specify loop_id_discovered and loop_id_resolved
        to accurately track the full resolution timeline.

        Args:
            loop_id: Loop iteration identifier (primary tracking loop)
            defect_id: Unique defect identifier
            discovered_at: When defect was discovered
            resolved_at: When defect was resolved (default: now)
            loop_id_discovered: Loop where defect was discovered (for cross-loop tracking)
            loop_id_resolved: Loop where defect was resolved (for cross-loop tracking)

        Example:
            >>> from datetime import datetime, timezone, timedelta
            >>> discovered = datetime.now(timezone.utc) - timedelta(hours=2)
            >>> collector.record_defect_resolved("loop-001", "defect-001", discovered)

            >>> # Cross-loop defect tracking
            >>> collector.record_defect_resolved(
            ...     loop_id="loop-003",
            ...     defect_id="defect-002",
            ...     discovered_at=discovered,
            ...     loop_id_discovered="loop-001",
            ...     loop_id_resolved="loop-003"
            ... )
        """
        with self._lock:
            # Use provided loop ids or fall back to primary loop_id
            actual_loop_discovered = loop_id_discovered or loop_id
            actual_loop_resolved = loop_id_resolved or loop_id

            if actual_loop_resolved not in self._defect_resolution_times:
                self._defect_resolution_times[actual_loop_resolved] = []

            resolved_at = resolved_at or datetime.now(timezone.utc)
            resolution_time = (
                resolved_at - discovered_at
            ).total_seconds() / 3600  # hours

            # Store resolution time with cross-loop metadata
            resolution_record = {
                "resolution_hours": resolution_time,
                "defect_id": defect_id,
                "loop_discovered": actual_loop_discovered,
                "loop_resolved": actual_loop_resolved,
                "is_cross_loop": actual_loop_discovered != actual_loop_resolved,
            }

            self._defect_resolution_times[actual_loop_resolved].append(
                resolution_record
            )

            # Record MTTR metric
            mttr = self._calculate_mttr(actual_loop_resolved)
            self.record_metric(
                loop_id=actual_loop_resolved,
                phase="DEVELOPMENT",
                metric_type=MetricType.MTTR,
                value=mttr,
                metadata={
                    "defect_id": defect_id,
                    "resolution_hours": resolution_time,
                    "loop_discovered": actual_loop_discovered,
                    "loop_resolved": actual_loop_resolved,
                    "is_cross_loop": actual_loop_discovered != actual_loop_resolved,
                },
            )

    def record_defect_resolved_cross_loop(
        self,
        defect_id: str,
        loop_id_discovered: str,
        loop_id_resolved: str,
        discovered_at: datetime,
        resolved_at: Optional[datetime] = None,
    ) -> Dict[str, float]:
        """
        Record cross-loop defect resolution with detailed MTTR breakdown.

        This method provides explicit cross-loop tracking, recording separate
        MTTR metrics for both the discovery loop and resolution loop, plus
        a cross-loop overhead metric.

        Args:
            defect_id: Unique defect identifier
            loop_id_discovered: Loop where defect was discovered
            loop_id_resolved: Loop where defect was resolved
            discovered_at: When defect was discovered
            resolved_at: When defect was resolved (default: now)

        Returns:
            Dictionary with MTTR breakdown:
                - 'discovery_loop_mttr': MTTR attributed to discovery loop
                - 'resolution_loop_mttr': MTTR attributed to resolution loop
                - 'cross_loop_overhead': Additional time due to cross-loop nature
                - 'total_mttr': Total resolution time in hours

        Example:
            >>> from datetime import datetime, timezone, timedelta
            >>> discovered = datetime.now(timezone.utc) - timedelta(hours=5)
            >>> resolved = datetime.now(timezone.utc)
            >>> mttr_breakdown = collector.record_defect_resolved_cross_loop(
            ...     defect_id="defect-001",
            ...     loop_id_discovered="loop-001",
            ...     loop_id_resolved="loop-003",
            ...     discovered_at=discovered,
            ...     resolved_at=resolved
            ... )
            >>> print(f"Cross-loop overhead: {mttr_breakdown['cross_loop_overhead']:.2f}h")
        """
        with self._lock:
            resolved_at = resolved_at or datetime.now(timezone.utc)
            total_resolution_time = (
                resolved_at - discovered_at
            ).total_seconds() / 3600  # hours

            # Estimate cross-loop overhead (time between loop transitions)
            # This is a heuristic: assume 1 hour overhead per loop transition
            loop_transitions = abs(
                int(loop_id_resolved.split("-")[-1])
                - int(loop_id_discovered.split("-")[-1])
            )
            cross_loop_overhead = loop_transitions * 1.0  # 1 hour per transition

            # Adjusted MTTR values
            discovery_loop_mttr = (
                total_resolution_time * 0.3
            )  # 30% attributed to discovery
            resolution_loop_mttr = (
                total_resolution_time * 0.7
            )  # 70% attributed to resolution

            # Record in discovery loop
            if loop_id_discovered not in self._defect_resolution_times:
                self._defect_resolution_times[loop_id_discovered] = []
            self._defect_resolution_times[loop_id_discovered].append(
                {
                    "resolution_hours": discovery_loop_mttr,
                    "defect_id": defect_id,
                    "loop_discovered": loop_id_discovered,
                    "loop_resolved": loop_id_resolved,
                    "is_cross_loop": True,
                    "cross_loop_overhead": cross_loop_overhead,
                }
            )

            # Record in resolution loop
            if loop_id_resolved not in self._defect_resolution_times:
                self._defect_resolution_times[loop_id_resolved] = []
            self._defect_resolution_times[loop_id_resolved].append(
                {
                    "resolution_hours": resolution_loop_mttr,
                    "defect_id": defect_id,
                    "loop_discovered": loop_id_discovered,
                    "loop_resolved": loop_id_resolved,
                    "is_cross_loop": True,
                    "cross_loop_overhead": cross_loop_overhead,
                }
            )

            # Record cross-loop MTTR metric in resolution loop
            self.record_metric(
                loop_id=loop_id_resolved,
                phase="DEVELOPMENT",
                metric_type=MetricType.MTTR,
                value=resolution_loop_mttr,
                metadata={
                    "defect_id": defect_id,
                    "resolution_hours": resolution_loop_mttr,
                    "loop_discovered": loop_id_discovered,
                    "loop_resolved": loop_id_resolved,
                    "is_cross_loop": True,
                    "cross_loop_overhead": cross_loop_overhead,
                    "total_resolution_hours": total_resolution_time,
                },
            )

            return {
                "discovery_loop_mttr": discovery_loop_mttr,
                "resolution_loop_mttr": resolution_loop_mttr,
                "cross_loop_overhead": cross_loop_overhead,
                "total_mttr": total_resolution_time,
            }

    def record_audit_event(
        self,
        loop_id: str,
        expected: bool = True,
    ) -> None:
        """
        Record an audit event for completeness tracking.

        Args:
            loop_id: Loop iteration identifier
            expected: Whether this event was expected to be logged

        Example:
            >>> collector.record_audit_event("loop-001", expected=True)
        """
        with self._lock:
            if expected:
                self._audit_expected[loop_id] = self._audit_expected.get(loop_id, 0) + 1

            self._audit_logged[loop_id] = self._audit_logged.get(loop_id, 0) + 1

            # Calculate and record audit completeness
            expected_count = self._audit_expected.get(loop_id, 1)
            logged_count = self._audit_logged.get(loop_id, 0)
            completeness = (
                min(1.0, logged_count / expected_count) if expected_count > 0 else 1.0
            )

            self.record_metric(
                loop_id=loop_id,
                phase="REVIEW",
                metric_type=MetricType.AUDIT_COMPLETENESS,
                value=completeness,
                metadata={
                    "expected": expected_count,
                    "logged": logged_count,
                },
            )

    def _calculate_mttr(self, loop_id: str) -> float:
        """Calculate mean time to resolve for a loop.

        Handles both legacy float values and new dictionary records with
        cross-loop metadata.
        """
        if loop_id not in self._defect_resolution_times:
            return 0.0

        records = self._defect_resolution_times[loop_id]
        if not records:
            return 0.0

        # Extract resolution times from records (support both float and dict formats)
        times = []
        for record in records:
            if isinstance(record, dict):
                times.append(record.get("resolution_hours", 0.0))
            else:
                # Legacy format: direct float value
                times.append(float(record))

        if not times:
            return 0.0

        return sum(times) / len(times)

    def get_cross_loop_defects(self) -> List[Dict[str, Any]]:
        """
        Get all cross-loop defects with their resolution details.

        Returns:
            List of dictionaries with cross-loop defect information:
                - defect_id: Unique defect identifier
                - loop_discovered: Loop where defect was discovered
                - loop_resolved: Loop where defect was resolved
                - resolution_hours: Time to resolve in hours
                - cross_loop_overhead: Estimated overhead from cross-loop nature
                - is_cross_loop: Always True for this method's results

        Example:
            >>> cross_loop = collector.get_cross_loop_defects()
            >>> for defect in cross_loop:
            ...     print(f"{defect['defect_id']}: {defect['loop_discovered']} -> {defect['loop_resolved']}")
        """
        with self._lock:
            cross_loop_defects = []

            for loop_id, records in self._defect_resolution_times.items():
                for record in records:
                    if isinstance(record, dict) and record.get("is_cross_loop", False):
                        cross_loop_defects.append(
                            {
                                "defect_id": record.get("defect_id", "unknown"),
                                "loop_discovered": record.get(
                                    "loop_discovered", loop_id
                                ),
                                "loop_resolved": record.get("loop_resolved", loop_id),
                                "resolution_hours": record.get("resolution_hours", 0.0),
                                "cross_loop_overhead": record.get(
                                    "cross_loop_overhead", 0.0
                                ),
                                "is_cross_loop": True,
                            }
                        )

            return cross_loop_defects

    def get_snapshot(
        self,
        loop_id: str,
        phase: str,
        index: int = -1,
    ) -> Optional[MetricSnapshot]:
        """
        Get a specific snapshot by loop_id and phase.

        Args:
            loop_id: Loop iteration identifier
            phase: Pipeline phase name
            index: Snapshot index (-1 for latest)

        Returns:
            MetricSnapshot or None if not found

        Example:
            >>> snapshot = collector.get_snapshot("loop-001", "DEVELOPMENT")
        """
        with self._lock:
            key = self._get_key(loop_id, phase)
            snapshots = self._snapshots.get(key, [])

            if not snapshots:
                return None

            return snapshots[index]

    def get_latest_snapshot(
        self,
        loop_id: str,
        phase: Optional[str] = None,
    ) -> Optional[MetricSnapshot]:
        """
        Get the latest snapshot for a loop.

        Args:
            loop_id: Loop iteration identifier
            phase: Optional specific phase (None for all phases)

        Returns:
            Latest MetricSnapshot or None

        Example:
            >>> snapshot = collector.get_latest_snapshot("loop-001")
        """
        with self._lock:
            latest = None
            latest_time = datetime.min.replace(tzinfo=timezone.utc)

            for (lid, phase_key), snapshots in self._snapshots.items():
                if lid != loop_id:
                    continue
                if phase and phase_key != phase:
                    continue

                if snapshots and snapshots[-1].timestamp > latest_time:
                    latest = snapshots[-1]
                    latest_time = snapshots[-1].timestamp

            return latest

    def get_all_snapshots(
        self,
        loop_id: Optional[str] = None,
        phase: Optional[str] = None,
    ) -> List[MetricSnapshot]:
        """
        Get all snapshots with optional filtering.

        Args:
            loop_id: Optional loop filter
            phase: Optional phase filter

        Returns:
            List of matching snapshots in chronological order

        Example:
            >>> all_snapshots = collector.get_all_snapshots()
            >>> loop_snapshots = collector.get_all_snapshots(loop_id="loop-001")
        """
        with self._lock:
            snapshots = []

            for (lid, phase_key), snapshot_list in self._snapshots.items():
                if loop_id and lid != loop_id:
                    continue
                if phase and phase_key != phase:
                    continue

                snapshots.extend(snapshot_list)

            return sorted(snapshots, key=lambda s: s.timestamp)

    def get_metric_history(
        self,
        metric_type: MetricType,
        loop_id: Optional[str] = None,
    ) -> List[Tuple[datetime, float]]:
        """
        Get historical values for a specific metric.

        Args:
            metric_type: Metric type to retrieve
            loop_id: Optional loop filter

        Returns:
            List of (timestamp, value) tuples

        Example:
            >>> history = collector.get_metric_history(MetricType.TOKEN_EFFICIENCY)
            >>> for timestamp, value in history:
            ...     print(f"{timestamp}: {value}")
        """
        with self._lock:
            history = []

            for snapshot in self.get_all_snapshots(loop_id=loop_id):
                value = snapshot.get(metric_type)
                if value is not None:
                    history.append((snapshot.timestamp, value))

            return sorted(history, key=lambda x: x[0])

    def get_statistics(
        self,
        metric_type: MetricType,
        loop_id: Optional[str] = None,
    ) -> Optional[MetricStatistics]:
        """
        Get statistical analysis for a metric.

        Args:
            metric_type: Metric type to analyze
            loop_id: Optional loop filter

        Returns:
            MetricStatistics or None if no data

        Example:
            >>> stats = collector.get_statistics(MetricType.TOKEN_EFFICIENCY)
            >>> print(f"Mean: {stats.mean:.3f}")
        """
        history = self.get_metric_history(metric_type, loop_id)
        if not history:
            return None

        values = [v for _, v in history]
        return MetricStatistics.from_values(metric_type, values)

    def generate_report(
        self,
        loop_id: Optional[str] = None,
        phase: Optional[str] = None,
    ) -> MetricsReport:
        """
        Generate comprehensive metrics report.

        Args:
            loop_id: Optional loop filter
            phase: Optional phase filter

        Returns:
            MetricsReport with analysis and recommendations

        Example:
            >>> report = collector.generate_report(loop_id="loop-001")
            >>> print(report.summary())
        """
        with self._lock:
            now = datetime.now(timezone.utc)
            snapshots = self.get_all_snapshots(loop_id=loop_id, phase=phase)

            if not snapshots:
                return MetricsReport(
                    generated_at=now,
                    loop_id=loop_id,
                    phase=phase,
                )

            # Compute statistics for each metric type
            metric_stats: Dict[MetricType, MetricStatistics] = {}
            for metric_type in MetricType:
                stats = self.get_statistics(metric_type, loop_id)
                if stats:
                    metric_stats[metric_type] = stats

            # Calculate overall health score
            health_scores = []
            for metric_type, stats in metric_stats.items():
                if metric_type.is_higher_better():
                    # Higher is better - use mean directly (clamped to 0-1)
                    health_scores.append(max(0, min(1, stats.mean)))
                else:
                    # Lower is better - invert normalized value
                    if metric_type == MetricType.QUALITY_VELOCITY:
                        # 1-5 iterations: 1.0 -> 0.2
                        health_scores.append(max(0, min(1, (5 - stats.mean) / 4)))
                    elif metric_type == MetricType.DEFECT_DENSITY:
                        # 0-10 defects/KLOC: 1.0 -> 0.0
                        health_scores.append(max(0, min(1, (10 - stats.mean) / 10)))
                    elif metric_type == MetricType.MTTR:
                        # 0-8 hours: 1.0 -> 0.0
                        health_scores.append(max(0, min(1, (8 - stats.mean) / 8)))

            overall_health = statistics.mean(health_scores) if health_scores else 0.0

            # Generate recommendations
            recommendations = self._generate_recommendations(metric_stats, loop_id)

            return MetricsReport(
                generated_at=now,
                loop_id=loop_id,
                phase=phase,
                snapshot_count=len(snapshots),
                metric_statistics=metric_stats,
                overall_health=overall_health,
                recommendations=recommendations,
            )

    def _generate_recommendations(
        self,
        metric_stats: Dict[MetricType, MetricStatistics],
        loop_id: Optional[str],
    ) -> List[str]:
        """Generate improvement recommendations based on metrics."""
        recommendations = []

        for metric_type, stats in metric_stats.items():
            if metric_type == MetricType.TOKEN_EFFICIENCY and stats.mean < 0.7:
                recommendations.append(
                    "Consider optimizing prompts to reduce token consumption"
                )

            elif metric_type == MetricType.CONTEXT_UTILIZATION and stats.mean < 0.5:
                recommendations.append(
                    "Context window underutilized - consider batching related tasks"
                )

            elif metric_type == MetricType.QUALITY_VELOCITY and stats.mean > 3:
                recommendations.append(
                    "High iteration count - review initial requirements clarity"
                )

            elif metric_type == MetricType.DEFECT_DENSITY and stats.mean > 5:
                recommendations.append(
                    "High defect density - consider additional code review"
                )

            elif metric_type == MetricType.MTTR and stats.mean > 4:
                recommendations.append(
                    "Long MTTR - implement faster feedback mechanisms"
                )

            elif metric_type == MetricType.AUDIT_COMPLETENESS and stats.mean < 0.95:
                recommendations.append(
                    "Audit logging incomplete - ensure all actions are logged"
                )

        return recommendations

    def integrate_with_tracker(
        self,
        tracker: DefectRemediationTracker,
        loop_id: str,
    ) -> None:
        """
        Integrate with DefectRemediationTracker for automatic tracking.

        Scans existing defects in the tracker and updates defect counts
        and resolution times.

        Args:
            tracker: DefectRemediationTracker to integrate with
            loop_id: Loop iteration identifier

        Example:
            >>> tracker = DefectRemediationTracker()
            >>> collector.integrate_with_tracker(tracker, "loop-001")
        """
        with self._lock:
            defects = tracker.get_all_defects()

            for defect in defects:
                # Count defect
                if defect.phase_detected:
                    self.record_defect_discovered(
                        loop_id=loop_id,
                        kloc=self._code_volume.get(loop_id, 1.0),
                    )

                # If resolved, record resolution time
                if defect.status in {DefectStatus.RESOLVED, DefectStatus.VERIFIED}:
                    # Estimate resolution time from history
                    history = tracker.get_defect_history(defect_id=defect.id)
                    if history:
                        first_change = history[0]
                        last_change = history[-1]
                        self.record_defect_resolved(
                            loop_id=loop_id,
                            defect_id=defect.id,
                            discovered_at=first_change.changed_at,
                            resolved_at=last_change.changed_at,
                        )

    def integrate_with_state(
        self,
        state_machine: PipelineStateMachine,
        loop_id: str,
    ) -> None:
        """
        Integrate with PipelineStateMachine for phase tracking.

        Records metrics associated with current pipeline state.

        Args:
            state_machine: PipelineStateMachine to integrate with
            loop_id: Loop iteration identifier

        Example:
            >>> state_machine = PipelineStateMachine(context)
            >>> collector.integrate_with_state(state_machine, "loop-001")
        """
        with self._lock:
            snapshot = state_machine.snapshot

            # Record quality score if available
            if snapshot.quality_score is not None:
                self.record_quality_score(
                    loop_id=loop_id,
                    quality_score=snapshot.quality_score,
                    threshold=state_machine.context.quality_threshold,
                )

    def clear(self) -> None:
        """
        Clear all collected metrics.

        Example:
            >>> collector.clear()
        """
        with self._lock:
            self._snapshots.clear()
            self._token_tracking.clear()
            self._context_tracking.clear()
            self._quality_iterations.clear()
            self._defect_counts.clear()
            self._code_volume.clear()
            self._defect_resolution_times.clear()
            self._audit_expected.clear()
            self._audit_logged.clear()

            logger.info(
                "MetricsCollector cleared",
                extra={"collector_id": self.collector_id},
            )

    def get_summary(self) -> Dict[str, Any]:
        """
        Get summary of collected metrics.

        Returns:
            Dictionary with metric summaries

        Example:
            >>> summary = collector.get_summary()
            >>> print(f"Total snapshots: {summary['total_snapshots']}")
        """
        with self._lock:
            return {
                "collector_id": self.collector_id,
                "total_snapshots": sum(len(v) for v in self._snapshots.values()),
                "loops_tracked": len(set(k[0] for k in self._snapshots.keys())),
                "phases_tracked": len(set(k[1] for k in self._snapshots.keys())),
                "token_tracking": {
                    k: v.to_dict() for k, v in self._token_tracking.items()
                },
                "context_tracking": {
                    k: v.to_dict() for k, v in self._context_tracking.items()
                },
                "quality_iterations": {
                    k: v.to_dict() for k, v in self._quality_iterations.items()
                },
                "defect_counts": self._defect_counts,
                "mttr_by_loop": {
                    k: self._calculate_mttr(k)
                    for k in self._defect_resolution_times.keys()
                },
            }

    def export_to_json(self, filepath: str, include_metadata: bool = True) -> str:
        """
        Export all metrics to a JSON file.

        Creates a comprehensive JSON export of all collected metrics, including
        snapshots, tracking data, and metadata. The exported file can be used for
        historical analysis, reporting, or data migration.

        Args:
            filepath: Path to the output JSON file (absolute or relative)
            include_metadata: Whether to include metadata and tracking data

        Returns:
            Absolute path to the exported file

        Raises:
            IOError: If the file cannot be written

        Example:
            >>> collector.export_to_json("/path/to/metrics_export.json")
            '/path/to/metrics_export.json'
            >>>
            >>> # Export without metadata for smaller file size
            >>> collector.export_to_json("metrics_minimal.json", include_metadata=False)
        """
        with self._lock:
            export_path = Path(filepath).resolve()

            # Build export data structure
            export_data = {
                "export_timestamp": datetime.now(timezone.utc).isoformat(),
                "collector_id": self.collector_id,
                "snapshots": [
                    snapshot.to_dict()
                    for snapshots in self._snapshots.values()
                    for snapshot in snapshots
                ],
                "summary": self.get_summary(),
            }

            if include_metadata:
                export_data["token_tracking"] = {
                    k: v.to_dict() for k, v in self._token_tracking.items()
                }
                export_data["context_tracking"] = {
                    k: v.to_dict() for k, v in self._context_tracking.items()
                }
                export_data["quality_iterations"] = {
                    k: v.to_dict() for k, v in self._quality_iterations.items()
                }
                export_data["defect_counts"] = self._defect_counts
                export_data["defect_resolution_times"] = {
                    k: [
                        r if isinstance(r, dict) else {"resolution_hours": r}
                        for r in records
                    ]
                    for k, records in self._defect_resolution_times.items()
                }
                export_data["audit_tracking"] = {
                    "expected": self._audit_expected,
                    "logged": self._audit_logged,
                }
                export_data["cross_loop_defects"] = self.get_cross_loop_defects()

            # Ensure parent directory exists
            export_path.parent.mkdir(parents=True, exist_ok=True)

            # Write JSON file
            with open(export_path, "w", encoding="utf-8") as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)

            logger.info(
                f"Exported metrics to JSON: {export_path}",
                extra={
                    "collector_id": self.collector_id,
                    "export_path": str(export_path),
                    "snapshot_count": len(export_data["snapshots"]),
                },
            )

            return str(export_path)

    def export_to_sqlite(self, db_path: str, include_metadata: bool = True) -> str:
        """
        Export all metrics to a SQLite database.

        Creates a SQLite database with normalized tables for efficient querying
        and historical analysis. Creates tables if they don't exist and appends
        new data.

        Schema:
            - snapshots: Core metric snapshots
            - snapshot_metrics: Individual metric values per snapshot
            - token_tracking: Token usage records
            - context_tracking: Context utilization records
            - quality_iterations: Quality iteration records
            - defects: Defect tracking records
            - cross_loop_defects: Cross-loop defect resolution records
            - exports: Export metadata/history

        Args:
            db_path: Path to the SQLite database file
            include_metadata: Whether to include metadata tables

        Returns:
            Absolute path to the database file

        Raises:
            sqlite3.Error: If database operation fails

        Example:
            >>> collector.export_to_sqlite("/path/to/metrics.db")
            '/path/to/metrics.db'
            >>>
            >>> # Query exported data
            >>> import sqlite3
            >>> conn = sqlite3.connect("metrics.db")
            >>> cursor = conn.execute(
            ...     "SELECT AVG(value) FROM snapshot_metrics WHERE metric_type='TOKEN_EFFICIENCY'"
            ... )
        """
        with self._lock:
            db_path_obj = Path(db_path).resolve()
            db_path_obj.parent.mkdir(parents=True, exist_ok=True)

            # QW-005: Use connection pool if available, otherwise create direct connection
            if self._connection_pool and db_path == self._db_path:
                # Use pooled connection
                with self._connection_pool.get_connection() as conn:
                    cursor = conn.cursor()
                    self._export_to_sqlite_cursor(
                        cursor, db_path_obj, include_metadata, export_as_new_file=False
                    )
            else:
                # Create direct connection for one-time export
                conn = sqlite3.connect(str(db_path_obj))
                cursor = conn.cursor()
                try:
                    self._export_to_sqlite_cursor(
                        cursor, db_path_obj, include_metadata, export_as_new_file=True
                    )
                    conn.commit()
                except sqlite3.Error as e:
                    conn.rollback()
                    logger.error(
                        f"SQLite export failed: {e}",
                        extra={"collector_id": self.collector_id},
                    )
                    raise
                finally:
                    conn.close()

    def _create_sqlite_tables(
        self, cursor: sqlite3.Cursor, include_metadata: bool
    ) -> None:
        """Create SQLite tables if they don't exist."""

        # Core snapshots table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                export_timestamp TEXT NOT NULL,
                collector_id TEXT NOT NULL,
                snapshot_timestamp TEXT NOT NULL,
                loop_id TEXT NOT NULL,
                phase TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """)

        # Individual metric values
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS snapshot_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL,
                metric_type TEXT NOT NULL,
                value REAL NOT NULL,
                FOREIGN KEY (snapshot_id) REFERENCES snapshots(id)
            )
            """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_metric_type ON snapshot_metrics(metric_type)"
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_loop_id ON snapshots(loop_id)")

        if include_metadata:
            # Token tracking
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS token_tracking (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    export_timestamp TEXT NOT NULL,
                    loop_id TEXT NOT NULL,
                    tokens_input INTEGER DEFAULT 0,
                    tokens_output INTEGER DEFAULT 0,
                    total_tokens INTEGER,
                    feature_name TEXT,
                    completed_at TEXT
                )
                """)

            # Context tracking
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS context_tracking (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    export_timestamp TEXT NOT NULL,
                    loop_id TEXT NOT NULL,
                    context_window_size INTEGER DEFAULT 0,
                    tokens_used INTEGER DEFAULT 0,
                    effective_tokens INTEGER DEFAULT 0,
                    utilization_ratio REAL,
                    effectiveness_ratio REAL,
                    tracked_at TEXT
                )
                """)

            # Quality iterations
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS quality_iterations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    export_timestamp TEXT NOT NULL,
                    loop_id TEXT NOT NULL,
                    threshold REAL DEFAULT 0.90,
                    iterations INTEGER DEFAULT 0,
                    quality_scores TEXT,
                    reached_threshold INTEGER DEFAULT 0,
                    started_at TEXT
                )
                """)

            # Defects table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS defects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    export_timestamp TEXT NOT NULL,
                    loop_id TEXT NOT NULL,
                    defect_count INTEGER DEFAULT 0,
                    kloc REAL DEFAULT 1.0,
                    defect_density REAL,
                    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """)

            # Cross-loop defects
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cross_loop_defects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    export_timestamp TEXT NOT NULL,
                    defect_id TEXT NOT NULL,
                    loop_discovered TEXT NOT NULL,
                    loop_resolved TEXT NOT NULL,
                    resolution_hours REAL,
                    cross_loop_overhead REAL,
                    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """)

            # Export history
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS export_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    export_timestamp TEXT NOT NULL,
                    collector_id TEXT NOT NULL,
                    total_snapshots INTEGER,
                    include_metadata INTEGER,
                    export_path TEXT,
                    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """)

    def _export_tracking_to_sqlite(
        self,
        cursor: sqlite3.Cursor,
        export_timestamp: datetime,
    ) -> None:
        """Export tracking data to SQLite tables."""

        # Token tracking
        for loop_id, tracking in self._token_tracking.items():
            cursor.execute(
                """
                INSERT INTO token_tracking (
                    export_timestamp, loop_id, tokens_input, tokens_output,
                    total_tokens, feature_name, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    export_timestamp.isoformat(),
                    loop_id,
                    tracking.tokens_input,
                    tracking.tokens_output,
                    tracking.total_tokens(),
                    tracking.feature_name,
                    (
                        tracking.completed_at.isoformat()
                        if tracking.completed_at
                        else None
                    ),
                ),
            )

        # Context tracking
        for loop_id, tracking in self._context_tracking.items():
            cursor.execute(
                """
                INSERT INTO context_tracking (
                    export_timestamp, loop_id, context_window_size, tokens_used,
                    effective_tokens, utilization_ratio, effectiveness_ratio, tracked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    export_timestamp.isoformat(),
                    loop_id,
                    tracking.context_window_size,
                    tracking.tokens_used,
                    tracking.effective_tokens,
                    tracking.utilization_ratio(),
                    tracking.effectiveness_ratio(),
                    tracking.timestamp.isoformat(),
                ),
            )

        # Quality iterations
        for loop_id, qi in self._quality_iterations.items():
            cursor.execute(
                """
                INSERT INTO quality_iterations (
                    export_timestamp, loop_id, threshold, iterations,
                    quality_scores, reached_threshold, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    export_timestamp.isoformat(),
                    loop_id,
                    qi.threshold,
                    qi.iterations,
                    json.dumps(qi.quality_scores),
                    1 if qi.reached_threshold else 0,
                    qi.started_at.isoformat(),
                ),
            )

        # Defect counts
        for loop_id, count in self._defect_counts.items():
            kloc = self._code_volume.get(loop_id, 1.0)
            density = count / kloc if kloc > 0 else 0.0
            cursor.execute(
                """
                INSERT INTO defects (
                    export_timestamp, loop_id, defect_count, kloc, defect_density
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (export_timestamp.isoformat(), loop_id, count, kloc, density),
            )

        # Cross-loop defects
        for defect in self.get_cross_loop_defects():
            cursor.execute(
                """
                INSERT INTO cross_loop_defects (
                    export_timestamp, defect_id, loop_discovered, loop_resolved,
                    resolution_hours, cross_loop_overhead
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    export_timestamp.isoformat(),
                    defect["defect_id"],
                    defect["loop_discovered"],
                    defect["loop_resolved"],
                    defect["resolution_hours"],
                    defect["cross_loop_overhead"],
                ),
            )

    def _export_to_sqlite_cursor(
        self,
        cursor: sqlite3.Cursor,
        db_path_obj: Path,
        include_metadata: bool,
        export_as_new_file: bool = True,
    ) -> str:
        """
        Internal method to export metrics to SQLite using provided cursor (QW-005).

        Args:
            cursor: SQLite cursor to use
            db_path_obj: Path to SQLite database
            include_metadata: Whether to include metadata tables
            export_as_new_file: Whether to create tables (True) or assume existing schema

        Returns:
            Absolute path to the database file
        """
        if export_as_new_file:
            # Create tables
            self._create_sqlite_tables(cursor, include_metadata)

        # Export snapshots
        export_timestamp = datetime.now(timezone.utc)

        for (loop_id, phase), snapshots in self._snapshots.items():
            for snapshot in snapshots:
                # Insert snapshot record
                cursor.execute(
                    """
                    INSERT INTO snapshots (
                        export_timestamp, collector_id, snapshot_timestamp,
                        loop_id, phase, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        export_timestamp.isoformat(),
                        self.collector_id,
                        snapshot.timestamp.isoformat(),
                        loop_id,
                        phase,
                        json.dumps(snapshot.metadata),
                    ),
                )

                snapshot_id = cursor.lastrowid

                # Insert individual metrics
                for metric_type, value in snapshot.metrics.items():
                    cursor.execute(
                        """
                        INSERT INTO snapshot_metrics (
                            snapshot_id, metric_type, value
                        ) VALUES (?, ?, ?)
                        """,
                        (snapshot_id, metric_type.name, value),
                    )

        # Export tracking data if requested
        if include_metadata:
            self._export_tracking_to_sqlite(cursor, export_timestamp)

        logger.info(
            f"Exported metrics to SQLite: {db_path_obj}",
            extra={
                "collector_id": self.collector_id,
                "db_path": str(db_path_obj),
            },
        )

        return str(db_path_obj)

    def shutdown(self) -> None:
        """
        Shutdown the MetricsCollector and release resources (QW-005).

        Closes the SQLite connection pool if one was configured.
        """
        if self._connection_pool:
            self._connection_pool.close_all()
            logger.info("MetricsCollector connection pool shutdown complete")
