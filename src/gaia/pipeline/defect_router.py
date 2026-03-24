"""
GAIA DefectRouter

Routes defects to appropriate pipeline phases based on defect type, severity, and context.
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional, Any, Set


class DefectType(Enum):
    """Categories of defects that can be detected."""

    # Code quality defects
    CODE_STYLE = auto()
    CODE_COMPLEXITY = auto()
    MISSING_DOCSTRING = auto()
    DUPLICATE_CODE = auto()

    # Testing defects
    MISSING_TESTS = auto()
    INSUFFICIENT_COVERAGE = auto()
    FLAKY_TESTS = auto()

    # Security defects
    SECURITY_VULNERABILITY = auto()
    INJECTION_RISK = auto()
    AUTHORIZATION_ISSUE = auto()

    # Requirements defects
    MISSING_REQUIREMENT = auto()
    INCORRECT_IMPLEMENTATION = auto()
    EDGE_CASE_NOT_HANDLED = auto()

    # Performance defects
    PERFORMANCE_ISSUE = auto()
    MEMORY_LEAK = auto()
    INEFFICIENT_ALGORITHM = auto()

    # Architecture defects
    ARCHITECTURE_VIOLATION = auto()
    CIRCULAR_DEPENDENCY = auto()
    TIGHT_COUPLING = auto()

    # Unknown/unclassified
    UNKNOWN = auto()


class DefectSeverity(Enum):
    """Severity levels for defects."""

    CRITICAL = 1  # Must fix before any progress
    HIGH = 2  # Should fix in current iteration
    MEDIUM = 3  # Should fix eventually
    LOW = 4  # Nice to fix


class DefectStatus(Enum):
    """Status of defect in remediation tracking."""

    OPEN = auto()
    IN_PROGRESS = auto()
    RESOLVED = auto()
    VERIFIED = auto()
    DEFERRED = auto()


@dataclass
class Defect:
    """
    Represents a single defect found during quality evaluation.

    Attributes:
        id: Unique defect identifier
        type: Defect type enumeration
        severity: Defect severity level
        status: Current remediation status
        description: Human-readable description
        phase_detected: Pipeline phase where defect was found
        target_phase: Pipeline phase that should fix this defect
        location: File/line location if applicable
        metadata: Additional defect information
    """

    id: str
    type: DefectType
    severity: DefectSeverity
    status: DefectStatus = DefectStatus.OPEN
    description: str = ""
    phase_detected: str = ""
    target_phase: str = ""
    location: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert defect to dictionary for serialization."""
        return {
            "id": self.id,
            "type": self.type.name,
            "severity": self.severity.name,
            "status": self.status.name,
            "description": self.description,
            "phase_detected": self.phase_detected,
            "target_phase": self.target_phase,
            "location": self.location,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Defect":
        """Create defect from dictionary."""
        return cls(
            id=data.get("id", ""),
            type=DefectType[data.get("type", "UNKNOWN")],
            severity=DefectSeverity[data.get("severity", "MEDIUM")],
            status=DefectStatus[data.get("status", "OPEN")],
            description=data.get("description", ""),
            phase_detected=data.get("phase_detected", ""),
            target_phase=data.get("target_phase", ""),
            location=data.get("location"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class RoutingRule:
    """
    Rule for routing defects to phases.

    Attributes:
        defect_types: Defect types this rule applies to
        target_phase: Phase to route defects to
        priority: Rule priority (lower = higher priority)
        conditions: Additional conditions for routing
    """

    defect_types: Set[DefectType]
    target_phase: str
    priority: int = 0
    conditions: Optional[Dict[str, Any]] = None

    def matches(self, defect: Defect) -> bool:
        """Check if this rule matches a defect."""
        if defect.type not in self.defect_types:
            return False

        if self.conditions:
            # Evaluate additional conditions
            for key, value in self.conditions.items():
                if defect.metadata.get(key) != value:
                    return False

        return True


class DefectRouter:
    """
    Routes defects to appropriate pipeline phases.

    The DefectRouter analyzes defects and determines which pipeline phase
    should address them. This enables intelligent loop-back where defects
    are routed to the most appropriate phase for remediation.

    Example:
        >>> router = DefectRouter()
        >>> defect = Defect(
        ...     id="defect-001",
        ...     type=DefectType.MISSING_TESTS,
        ...     severity=DefectSeverity.HIGH,
        ...     description="No unit tests for new module"
        ... )
        >>> target_phase = router.route_defect(defect)
        >>> print(target_phase)  # "DEVELOPMENT"
    """

    # Default routing rules
    DEFAULT_RULES: List[RoutingRule] = [
        # Testing defects → DEVELOPMENT
        RoutingRule(
            defect_types={
                DefectType.MISSING_TESTS,
                DefectType.INSUFFICIENT_COVERAGE,
                DefectType.FLAKY_TESTS,
            },
            target_phase="DEVELOPMENT",
            priority=1,
        ),
        # Code quality defects → DEVELOPMENT
        RoutingRule(
            defect_types={
                DefectType.CODE_STYLE,
                DefectType.CODE_COMPLEXITY,
                DefectType.MISSING_DOCSTRING,
                DefectType.DUPLICATE_CODE,
            },
            target_phase="DEVELOPMENT",
            priority=2,
        ),
        # Security defects → DEVELOPMENT (or REVIEW for complex)
        RoutingRule(
            defect_types={
                DefectType.SECURITY_VULNERABILITY,
                DefectType.INJECTION_RISK,
                DefectType.AUTHORIZATION_ISSUE,
            },
            target_phase="DEVELOPMENT",
            priority=1,
        ),
        # Requirements defects → PLANNING (may need re-scoping)
        RoutingRule(
            defect_types={
                DefectType.MISSING_REQUIREMENT,
                DefectType.INCORRECT_IMPLEMENTATION,
            },
            target_phase="PLANNING",
            priority=1,
        ),
        # Edge cases → DEVELOPMENT
        RoutingRule(
            defect_types={DefectType.EDGE_CASE_NOT_HANDLED},
            target_phase="DEVELOPMENT",
            priority=2,
        ),
        # Performance defects → DEVELOPMENT
        RoutingRule(
            defect_types={
                DefectType.PERFORMANCE_ISSUE,
                DefectType.MEMORY_LEAK,
                DefectType.INEFFICIENT_ALGORITHM,
            },
            target_phase="DEVELOPMENT",
            priority=2,
        ),
        # Architecture defects → PLANNING (architectural changes)
        RoutingRule(
            defect_types={
                DefectType.ARCHITECTURE_VIOLATION,
                DefectType.CIRCULAR_DEPENDENCY,
                DefectType.TIGHT_COUPLING,
            },
            target_phase="PLANNING",
            priority=1,
        ),
    ]

    def __init__(self, custom_rules: Optional[List[RoutingRule]] = None):
        """
        Initialize defect router.

        Args:
            custom_rules: Optional custom routing rules (overrides defaults)
        """
        self._rules = custom_rules or self.DEFAULT_RULES.copy()
        # Sort rules by priority (lower priority number = higher priority)
        self._rules.sort(key=lambda r: r.priority)

    def route_defect(self, defect: Defect) -> str:
        """
        Determine target phase for a defect.

        Args:
            defect: Defect to route

        Returns:
            Target phase name
        """
        # Try each rule in priority order
        for rule in self._rules:
            if rule.matches(defect):
                return rule.target_phase

        # Default: route to DEVELOPMENT
        return "DEVELOPMENT"

    def route_defects(
        self, defects: List[Dict[str, Any]]
    ) -> Dict[str, List[Defect]]:
        """
        Route multiple defects to their target phases.

        Args:
            defects: List of defect dictionaries

        Returns:
            Dictionary mapping phase names to lists of defects
        """
        routed: Dict[str, List[Defect]] = {
            "PLANNING": [],
            "DEVELOPMENT": [],
            "QUALITY": [],
            "REVIEW": [],
        }

        for defect_data in defects:
            # Convert to Defect if needed
            if isinstance(defect_data, dict):
                defect = Defect.from_dict(defect_data)
            else:
                defect = defect_data

            # Set target phase if not already set
            if not defect.target_phase:
                defect.target_phase = self.route_defect(defect)

            # Add to appropriate phase bucket
            target = defect.target_phase
            if target not in routed:
                routed[target] = []
            routed[target].append(defect)

        # Remove empty buckets
        return {k: v for k, v in routed.items() if v}

    def get_defect_summary(
        self, defects: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Generate summary statistics for defects.

        Args:
            defects: List of defect dictionaries

        Returns:
            Summary statistics
        """
        summary = {
            "total": len(defects),
            "by_type": {},
            "by_severity": {},
            "by_phase": {},
            "critical_count": 0,
            "high_count": 0,
        }

        for defect_data in defects:
            if isinstance(defect_data, dict):
                defect = Defect.from_dict(defect_data)
            else:
                defect = defect_data

            # Count by type
            type_name = defect.type.name
            summary["by_type"][type_name] = summary["by_type"].get(type_name, 0) + 1

            # Count by severity
            severity_name = defect.severity.name
            summary["by_severity"][severity_name] = (
                summary["by_severity"].get(severity_name, 0) + 1
            )

            # Count critical and high severity
            if defect.severity == DefectSeverity.CRITICAL:
                summary["critical_count"] += 1
            elif defect.severity == DefectSeverity.HIGH:
                summary["high_count"] += 1

            # Count by target phase
            phase = defect.target_phase or self.route_defect(defect)
            summary["by_phase"][phase] = summary["by_phase"].get(phase, 0) + 1

        return summary

    def add_rule(self, rule: RoutingRule) -> None:
        """Add a custom routing rule."""
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority)

    def remove_rule(self, defect_type: DefectType) -> None:
        """Remove routing rules for a specific defect type."""
        self._rules = [
            r for r in self._rules if defect_type not in r.defect_types
        ]


def create_defect(
    defect_type: str,
    description: str,
    severity: str = "MEDIUM",
    phase_detected: str = "",
    location: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Defect:
    """
    Helper function to create a defect.

    Args:
        defect_type: Type name (e.g., "MISSING_TESTS")
        description: Human-readable description
        severity: Severity level ("CRITICAL", "HIGH", "MEDIUM", "LOW")
        phase_detected: Phase where defect was found
        location: File/line location
        metadata: Additional metadata

    Returns:
        Defect instance
    """
    import uuid

    return Defect(
        id=f"defect-{uuid.uuid4().hex[:8]}",
        type=DefectType[defect_type],
        severity=DefectSeverity[severity],
        description=description,
        phase_detected=phase_detected,
        location=location,
        metadata=metadata or {},
    )
