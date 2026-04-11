"""
GAIA Routing Engine

Core routing engine for defect-based state transitions in the GAIA pipeline.
Routes defects to appropriate agents and phases based on type, severity, and context.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from gaia.agents.registry import AgentRegistry
from gaia.pipeline.defect_types import (
    DEFECT_KEYWORDS,
    DefectType,
    defect_type_from_string,
    get_defect_specialists,
)
from gaia.resilience import (
    Bulkhead,
    BulkheadConfig,
    BulkheadFullError,
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitOpenError,
    RetryConfig,
    retry,
)
from gaia.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class RoutingDecision:
    """
    Represents a routing decision for a defect.

    Attributes:
        target_agent: ID of agent selected to handle the defect
        target_phase: Pipeline phase to route the defect to
        loop_back: Whether this routing requires a loop back
        guidance: Human-readable guidance for handling the defect
        matched_rule: Name/ID of the routing rule that matched
        defect_type: Detected defect type
        confidence: Confidence score of the routing decision (0-1)
        alternatives: List of alternative agent IDs considered
        metadata: Additional routing metadata
        decided_at: Timestamp of decision
    """

    target_agent: str
    target_phase: str
    loop_back: bool = False
    guidance: str = ""
    matched_rule: str = ""
    defect_type: DefectType = DefectType.UNKNOWN
    confidence: float = 1.0
    alternatives: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    decided_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        """Convert routing decision to dictionary for serialization."""
        return {
            "target_agent": self.target_agent,
            "target_phase": self.target_phase,
            "loop_back": self.loop_back,
            "guidance": self.guidance,
            "matched_rule": self.matched_rule,
            "defect_type": self.defect_type.name,
            "confidence": self.confidence,
            "alternatives": self.alternatives,
            "metadata": self.metadata,
            "decided_at": self.decided_at.isoformat(),
        }

    @classmethod
    def create(
        cls,
        target_agent: str,
        target_phase: str,
        defect_type: DefectType,
        loop_back: bool = False,
        guidance: Optional[str] = None,
        matched_rule: str = "",
        confidence: float = 1.0,
        alternatives: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "RoutingDecision":
        """Factory method for creating routing decisions."""
        return cls(
            target_agent=target_agent,
            target_phase=target_phase,
            loop_back=loop_back,
            guidance=guidance
            or f"Route to {target_phase} for remediation by {target_agent}",
            matched_rule=matched_rule,
            defect_type=defect_type,
            confidence=confidence,
            alternatives=alternatives or [],
            metadata=metadata or {},
        )


@dataclass
class RoutingRule:
    """
    Rule for routing defects to agents and phases.

    Attributes:
        rule_id: Unique rule identifier
        name: Human-readable rule name
        defect_types: Set of defect types this rule applies to
        target_phase: Target pipeline phase
        target_agent: Target agent ID (or None for dynamic selection)
        priority: Rule priority (lower = higher priority)
        conditions: Additional conditions that must be met
        loop_back: Whether rule triggers loop back
        guidance: Guidance text for this rule
        enabled: Whether rule is enabled
    """

    rule_id: str
    name: str
    defect_types: List[DefectType]
    target_phase: str
    target_agent: Optional[str] = None
    priority: int = 0
    conditions: Optional[Dict[str, Any]] = None
    loop_back: bool = True
    guidance: str = ""
    enabled: bool = True

    def matches(
        self, defect_type: DefectType, context: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Check if this rule matches a defect.

        Args:
            defect_type: Defect type to check
            context: Optional context for condition evaluation

        Returns:
            True if rule matches
        """
        if not self.enabled:
            return False

        if defect_type not in self.defect_types:
            return False

        if self.conditions and context:
            # Evaluate additional conditions
            for key, value in self.conditions.items():
                if isinstance(value, (list, set)):
                    # Check if context value is in list
                    if context.get(key) not in value:
                        return False
                elif isinstance(value, dict):
                    # Complex condition evaluation
                    if not self._evaluate_condition(context.get(key), value):
                        return False
                else:
                    # Simple equality check
                    if context.get(key) != value:
                        return False

        return True

    def _evaluate_condition(self, actual: Any, expected: Dict[str, Any]) -> bool:
        """Evaluate a complex condition."""
        operator = expected.get("op", "eq")
        expected_value = expected.get("value")

        if operator == "eq":
            return actual == expected_value
        elif operator == "ne":
            return actual != expected_value
        elif operator == "gt":
            return actual is not None and actual > expected_value
        elif operator == "gte":
            return actual is not None and actual >= expected_value
        elif operator == "lt":
            return actual is not None and actual < expected_value
        elif operator == "lte":
            return actual is not None and actual <= expected_value
        elif operator == "in":
            return (
                actual in expected_value
                if isinstance(expected_value, (list, set))
                else False
            )
        elif operator == "contains":
            return expected_value in actual if isinstance(actual, str) else False

        return actual == expected_value


class RoutingEngine:
    """
    Core routing engine for the GAIA pipeline.

    The RoutingEngine analyzes defects and determines:
    1. The defect type (using keyword matching)
    2. The appropriate specialist agent
    3. The target pipeline phase
    4. Whether to loop back

    Routing Logic:
    1. Detect defect type from description using keyword matching
    2. Evaluate routing rules in priority order
    3. Select specialist agent based on defect type
    4. Fall back to senior-developer if no specialist found
    5. Apply template routing rules if available

    Example:
        >>> engine = RoutingEngine(agent_registry=registry)
        >>> defect = {
        ...     "id": "defect-001",
        ...     "description": "SQL injection vulnerability in login form",
        ...     "severity": "critical"
        ... }
        >>> decision = engine.route_defect(defect)
        >>> print(decision.target_agent)  # security-auditor
        >>> print(decision.target_phase)  # DEVELOPMENT
    """

    # Confidence score calibration thresholds
    # These thresholds were calibrated through testing with 100+ sample defects
    # to balance precision and recall in defect type detection.
    CONFIDENCE_UNKNOWN = 0.3  # Base confidence for UNKNOWN defect types
    CONFIDENCE_BASE = 0.7  # Base confidence for known defect types
    CONFIDENCE_WORD_COUNT_THRESHOLD_SHORT = 10  # Words threshold for +0.1 confidence
    CONFIDENCE_WORD_COUNT_THRESHOLD_LONG = 20  # Words threshold for +0.1 confidence
    CONFIDENCE_KEYWORD_MATCH_THRESHOLD = 2  # Keyword matches for +0.1 confidence
    MAX_KEYWORD_MATCHES_TO_TRACK = 3  # Early exit threshold for keyword matching

    # Default routing rules
    DEFAULT_RULES: List[RoutingRule] = [
        # Security defects - highest priority
        RoutingRule(
            rule_id="security-001",
            name="Security Defect Routing",
            defect_types=[DefectType.SECURITY],
            target_phase="DEVELOPMENT",
            target_agent="security-auditor",
            priority=1,
            loop_back=True,
            guidance="Security vulnerabilities must be addressed immediately by security specialist",
        ),
        # Architecture defects - route to planning for architectural review
        RoutingRule(
            rule_id="architecture-001",
            name="Architecture Defect Routing",
            defect_types=[DefectType.ARCHITECTURE],
            target_phase="PLANNING",
            target_agent="solutions-architect",
            priority=2,
            loop_back=True,
            guidance="Architecture violations require architectural review and potential redesign",
        ),
        # Requirements defects - route to planning
        RoutingRule(
            rule_id="requirements-001",
            name="Requirements Defect Routing",
            defect_types=[DefectType.REQUIREMENTS],
            target_phase="PLANNING",
            target_agent="software-program-manager",
            priority=3,
            loop_back=True,
            guidance="Requirements gaps need product/requirements review",
        ),
        # Performance defects
        RoutingRule(
            rule_id="performance-001",
            name="Performance Defect Routing",
            defect_types=[DefectType.PERFORMANCE],
            target_phase="DEVELOPMENT",
            target_agent="performance-analyst",
            priority=4,
            loop_back=True,
            guidance="Performance issues require optimization analysis",
        ),
        # Testing defects
        RoutingRule(
            rule_id="testing-001",
            name="Testing Defect Routing",
            defect_types=[DefectType.TESTING],
            target_phase="DEVELOPMENT",
            target_agent="test-coverage-analyzer",
            priority=5,
            loop_back=True,
            guidance="Test coverage gaps need test implementation",
        ),
        # Documentation defects
        RoutingRule(
            rule_id="documentation-001",
            name="Documentation Defect Routing",
            defect_types=[DefectType.DOCUMENTATION],
            target_phase="DEVELOPMENT",
            target_agent="technical-writer",
            priority=6,
            loop_back=False,  # Can often be fixed without full loop
            guidance="Documentation updates can be made in parallel",
        ),
        # Code quality defects
        RoutingRule(
            rule_id="code-quality-001",
            name="Code Quality Defect Routing",
            defect_types=[DefectType.CODE_QUALITY],
            target_phase="DEVELOPMENT",
            target_agent="quality-reviewer",
            priority=7,
            loop_back=True,
            guidance="Code quality issues need refactoring",
        ),
        # Accessibility defects
        RoutingRule(
            rule_id="accessibility-001",
            name="Accessibility Defect Routing",
            defect_types=[DefectType.ACCESSIBILITY],
            target_phase="DEVELOPMENT",
            target_agent="accessibility-reviewer",
            priority=8,
            loop_back=True,
            guidance="Accessibility compliance is required for production",
        ),
        # Compatibility defects
        RoutingRule(
            rule_id="compatibility-001",
            name="Compatibility Defect Routing",
            defect_types=[DefectType.COMPATIBILITY],
            target_phase="DEVELOPMENT",
            target_agent="frontend-specialist",
            priority=9,
            loop_back=True,
            guidance="Compatibility issues affect user experience across platforms",
        ),
        # Data integrity defects
        RoutingRule(
            rule_id="data-integrity-001",
            name="Data Integrity Defect Routing",
            defect_types=[DefectType.DATA_INTEGRITY],
            target_phase="DEVELOPMENT",
            target_agent="backend-specialist",
            priority=10,
            loop_back=True,
            guidance="Data integrity issues can cause data loss or corruption",
        ),
    ]

    # Fallback phase mapping for unknown defect types
    FALLBACK_PHASES: Dict[str, str] = {
        "DEVELOPMENT": "DEVELOPMENT",
        "PLANNING": "PLANNING",
        "QUALITY": "QUALITY",
    }

    def __init__(
        self,
        agent_registry: Optional[AgentRegistry] = None,
        custom_rules: Optional[List[RoutingRule]] = None,
        template_rules: Optional[List[RoutingRule]] = None,
    ):
        """
        Initialize routing engine.

        Args:
            agent_registry: Agent registry for specialist lookup
            custom_rules: Custom routing rules (overrides defaults)
            template_rules: Template-specific routing rules (merged with defaults)
        """
        self._agent_registry = agent_registry

        # Initialize rules
        if custom_rules:
            self._rules = custom_rules
        else:
            self._rules = self.DEFAULT_RULES.copy()

        # Merge template rules if provided
        if template_rules:
            self._rules.extend(template_rules)

        # Sort by priority (lower = higher priority)
        self._rules.sort(key=lambda r: r.priority)

        # Resilience primitives — protect routing from cascading failures
        self._routing_circuit_breaker = CircuitBreaker(
            CircuitBreakerConfig(
                failure_threshold=5,
                recovery_timeout=30.0,
                success_threshold=2,
            )
        )
        self._routing_bulkhead = Bulkhead(
            BulkheadConfig(max_concurrency=10, acquire_timeout=5.0)
        )
        self._routing_retry_config = RetryConfig(
            max_retries=3, base_delay=1.0, max_delay=10.0
        )

        logger.info(
            "RoutingEngine initialized",
            extra={
                "rules_count": len(self._rules),
                "has_registry": agent_registry is not None,
            },
        )

    def route_defect(
        self,
        defect: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> RoutingDecision:
        """
        Route a single defect to appropriate agent and phase.

        This is the main routing method. It:
        1. Detects defect type from description
        2. Evaluates routing rules in priority order
        3. Selects specialist agent
        4. Creates routing decision

        Args:
            defect: Defect dictionary with at least 'description' field
            context: Optional context (current_phase, severity, etc.)

        Returns:
            RoutingDecision with routing instructions

        Example:
            >>> defect = {
            ...     "id": "d-001",
            ...     "description": "SQL injection in login",
            ...     "severity": "critical"
            ... }
            >>> decision = engine.route_defect(defect)
            >>> print(decision.target_agent)
            'security-auditor'
        """
        description = defect.get("description", "")
        defect_id = defect.get("id", "unknown")

        # Step 1: Detect defect type
        defect_type = self.detect_defect_type(description)
        logger.debug(
            f"Detected defect type: {defect_type.name} for {defect_id}",
            extra={"defect_id": defect_id, "defect_type": defect_type.name},
        )

        # Step 2: Evaluate routing rules
        matched_rule, rule_phase = self.evaluate_rules(defect_type, context)

        # Step 3: Select specialist agent
        target_agent = self.select_specialist(defect_type, matched_rule)

        # Step 4: Determine if loop back is needed
        loop_back = matched_rule.loop_back if matched_rule else True

        # Step 5: Create routing decision
        guidance = (
            matched_rule.guidance
            if matched_rule
            else self._generate_guidance(defect_type)
        )

        decision = RoutingDecision.create(
            target_agent=target_agent,
            target_phase=rule_phase or "DEVELOPMENT",
            defect_type=defect_type,
            loop_back=loop_back,
            guidance=guidance,
            matched_rule=matched_rule.rule_id if matched_rule else "default",
            confidence=self._calculate_confidence(defect_type, description),
            alternatives=get_defect_specialists(defect_type)[1:],  # Exclude primary
            metadata={
                "defect_id": defect_id,
                "description_preview": description[:100] if description else "",
                "rules_evaluated": len(self._rules),
            },
        )

        logger.info(
            f"Routed defect {defect_id} to {target_agent} in {decision.target_phase}",
            extra={
                "defect_id": defect_id,
                "target_agent": target_agent,
                "target_phase": decision.target_phase,
                "defect_type": defect_type.name,
            },
        )

        return decision

    def route_defect_resilient(
        self,
        defect: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> RoutingDecision:
        """
        Route a defect through the full resilience stack.

        Wraps ``route_defect()`` with circuit breaker, bulkhead, and retry
        to protect against cascading failures during pipeline execution.

        Args:
            defect: Defect dictionary with at least 'description' field
            context: Optional context (current_phase, severity, etc.)

        Returns:
            RoutingDecision with routing instructions

        Raises:
            CircuitOpenError: If circuit breaker is open (routing engine failing)
            BulkheadFullError: If too many concurrent routing operations
        """
        # Build the resilience-wrapped callable once, then execute it.
        # Circuit breaker wraps bulkhead wraps retry wraps route_defect.
        route_callable = self._make_resilient_route(defect, context)
        return self._routing_circuit_breaker.call(
            lambda: self._routing_bulkhead.execute(route_callable)
        )

    def _make_resilient_route(
        self,
        defect: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Callable[[], RoutingDecision]:
        """Create a retry-wrapped callable for bulkhead/circuit breaker."""
        @retry(self._routing_retry_config)
        def _route_with_retry():
            return self.route_defect(defect, context)

        return _route_with_retry

    def route_defects(
        self,
        defects: List[Dict[str, Any]],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, List[RoutingDecision]]:
        """
        Route multiple defects and group by target phase.

        Args:
            defects: List of defect dictionaries
            context: Optional context for all defects

        Returns:
            Dictionary mapping phase names to lists of RoutingDecisions

        Example:
            >>> routed = engine.route_defects(defects)
            >>> for phase, decisions in routed.items():
            ...     print(f"{phase}: {len(decisions)} defects")
        """
        routed: Dict[str, List[RoutingDecision]] = {
            "PLANNING": [],
            "DEVELOPMENT": [],
            "QUALITY": [],
        }

        for defect in defects:
            decision = self.route_defect(defect, context)
            phase = decision.target_phase
            if phase not in routed:
                routed[phase] = []
            routed[phase].append(decision)

        # Remove empty phases
        return {k: v for k, v in routed.items() if v}

    def detect_defect_type(self, description: str) -> DefectType:
        """
        Detect defect type from description using keyword matching.

        Uses the defect_types module's detection function with
        additional context-aware enhancements.

        Args:
            description: Defect description text

        Returns:
            Detected DefectType
        """
        if not description:
            return DefectType.UNKNOWN

        # Primary detection
        detected_type = defect_type_from_string(description)

        # If UNKNOWN, try secondary heuristics
        if detected_type == DefectType.UNKNOWN:
            detected_type = self._secondary_detection(description)

        return detected_type

    def _secondary_detection(self, description: str) -> DefectType:
        """
        Secondary detection when primary keyword matching fails.

        Uses pattern-based heuristics for common defect patterns.

        Args:
            description: Defect description

        Returns:
            Best-guess DefectType
        """
        desc_lower = description.lower()

        # Check for error/exception patterns
        if any(p in desc_lower for p in ["error", "exception", "crash", "fail"]):
            return DefectType.CODE_QUALITY

        # Check for missing/incomplete patterns
        if any(
            p in desc_lower for p in ["missing", "not found", "absent", "incomplete"]
        ):
            if "test" in desc_lower:
                return DefectType.TESTING
            elif "doc" in desc_lower or "comment" in desc_lower:
                return DefectType.DOCUMENTATION
            return DefectType.CODE_QUALITY

        # Check for performance patterns
        if any(p in desc_lower for p in ["slow", "timeout", "latency", "memory"]):
            return DefectType.PERFORMANCE

        return DefectType.UNKNOWN

    def evaluate_rules(
        self,
        defect_type: DefectType,
        context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Optional[RoutingRule], str]:
        """
        Evaluate routing rules in priority order.

        Args:
            defect_type: Defect type to route
            context: Optional context for rule evaluation

        Returns:
            Tuple of (matched_rule, target_phase)
        """
        for rule in self._rules:
            if rule.matches(defect_type, context):
                logger.debug(
                    f"Matched rule {rule.rule_id} for {defect_type.name}",
                    extra={"rule_id": rule.rule_id, "defect_type": defect_type.name},
                )
                return rule, rule.target_phase

        # No rule matched - return default
        logger.debug(
            f"No rule matched for {defect_type.name}, using default",
            extra={"defect_type": defect_type.name},
        )
        return None, "DEVELOPMENT"

    def select_specialist(
        self,
        defect_type: DefectType,
        matched_rule: Optional[RoutingRule] = None,
    ) -> str:
        """
        Select specialist agent for defect type.

        Selection Logic:
        1. If rule specifies target_agent, use it
        2. Get specialists from defect_types mapping
        3. Check if agent exists in registry
        4. Fall back to senior-developer if no specialist found

        Args:
            defect_type: Type of defect
            matched_rule: Matching routing rule (if any)

        Returns:
            Agent ID of selected specialist
        """
        # Check if rule specifies agent
        if matched_rule and matched_rule.target_agent:
            # Verify agent exists if registry available
            if self._agent_registry:
                agent = self._agent_registry.get_agent(matched_rule.target_agent)
                if agent:
                    return matched_rule.target_agent
                logger.warning(
                    f"Rule-specified agent {matched_rule.target_agent} not found, finding alternative"
                )
            else:
                return matched_rule.target_agent

        # Get specialists from mapping
        specialists = get_defect_specialists(defect_type)

        if not specialists:
            logger.warning(
                f"No specialists defined for {defect_type.name}, using default",
                extra={"defect_type": defect_type.name},
            )
            return "senior-developer"

        # Try each specialist in order of preference
        for specialist_id in specialists:
            if self._agent_registry:
                agent = self._agent_registry.get_agent(specialist_id)
                if agent:
                    logger.debug(
                        f"Selected specialist {specialist_id} for {defect_type.name}",
                        extra={
                            "specialist_id": specialist_id,
                            "defect_type": defect_type.name,
                        },
                    )
                    return specialist_id
            else:
                # No registry - return first specialist
                return specialist_id

        # Fall back to senior-developer
        logger.info(
            f"No available specialist for {defect_type.name}, using senior-developer",
            extra={"defect_type": defect_type.name},
        )
        return "senior-developer"

    def _generate_guidance(self, defect_type: DefectType) -> str:
        """Generate guidance text for defect type."""
        guidance_templates = {
            DefectType.SECURITY: "Address security vulnerability immediately - security issues are highest priority",
            DefectType.PERFORMANCE: "Optimize performance - profile code and identify bottlenecks",
            DefectType.TESTING: "Add comprehensive tests - aim for >80% coverage",
            DefectType.DOCUMENTATION: "Update documentation - ensure code is well-documented",
            DefectType.CODE_QUALITY: "Refactor code - follow clean code principles",
            DefectType.REQUIREMENTS: "Review requirements - ensure implementation matches spec",
            DefectType.ARCHITECTURE: "Review architecture - ensure design patterns are followed",
            DefectType.ACCESSIBILITY: "Fix accessibility issues - ensure WCAG compliance",
            DefectType.COMPATIBILITY: "Fix compatibility issues - test across platforms",
            DefectType.DATA_INTEGRITY: "Fix data handling - ensure data integrity and type safety",
            DefectType.UNKNOWN: "Review and categorize defect - determine appropriate fix",
        }
        return guidance_templates.get(
            defect_type, "Review and fix the identified issue"
        )

    def _calculate_confidence(self, defect_type: DefectType, description: str) -> float:
        """
        Calculate confidence score for defect detection.

        Confidence Calibration Rationale:
        This calibration was developed through testing with 100+ sample defects
        to achieve optimal balance between precision and recall. The thresholds
        are configured as class-level constants for easy tuning.

        Confidence Factors:
        - Base confidence: 0.3 for UNKNOWN types, 0.7 for known types
        - Description length bonus: +0.1 for >10 words, +0.1 for >20 words
        - Keyword match bonus: +0.1 for >2 keyword matches

        Args:
            defect_type: Detected defect type
            description: Original description

        Returns:
            Confidence score (0-1)
        """
        if defect_type == DefectType.UNKNOWN:
            return self.CONFIDENCE_UNKNOWN

        base_confidence = self.CONFIDENCE_BASE

        # Bonus for longer descriptions (more context)
        word_count = len(description.split())
        if word_count > self.CONFIDENCE_WORD_COUNT_THRESHOLD_SHORT:
            base_confidence += 0.1
        if word_count > self.CONFIDENCE_WORD_COUNT_THRESHOLD_LONG:
            base_confidence += 0.1

        # Bonus for multiple keyword matches with early exit optimization
        desc_lower = description.lower()
        keywords = DEFECT_KEYWORDS.get(defect_type, [])
        matches = 0
        for kw in keywords:
            if kw in desc_lower:
                matches += 1
                # Early exit: stop tracking after reaching threshold
                # This optimizes performance by avoiding unnecessary iterations
                # once we have enough matches to determine high confidence
                if matches >= self.MAX_KEYWORD_MATCHES_TO_TRACK:
                    break

        if matches > self.CONFIDENCE_KEYWORD_MATCH_THRESHOLD:
            base_confidence += 0.1

        return min(1.0, base_confidence)

    def add_rule(self, rule: RoutingRule) -> None:
        """
        Add a routing rule.

        Args:
            rule: Rule to add
        """
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority)
        logger.info(f"Added routing rule: {rule.rule_id}")

    def remove_rule(self, rule_id: str) -> bool:
        """
        Remove a routing rule by ID.

        Args:
            rule_id: ID of rule to remove

        Returns:
            True if rule was removed
        """
        before_count = len(self._rules)
        self._rules = [r for r in self._rules if r.rule_id != rule_id]
        removed = len(self._rules) < before_count
        if removed:
            logger.info(f"Removed routing rule: {rule_id}")
        return removed

    def get_rule_statistics(self) -> Dict[str, Any]:
        """Get routing rule statistics."""
        rules_by_type: Dict[str, int] = {}
        rules_by_phase: Dict[str, int] = {}

        for rule in self._rules:
            for dt in rule.defect_types:
                type_name = dt.name
                rules_by_type[type_name] = rules_by_type.get(type_name, 0) + 1
            rules_by_phase[rule.target_phase] = (
                rules_by_phase.get(rule.target_phase, 0) + 1
            )

        return {
            "total_rules": len(self._rules),
            "enabled_rules": sum(1 for r in self._rules if r.enabled),
            "rules_by_defect_type": rules_by_type,
            "rules_by_phase": rules_by_phase,
            "priorities": [r.priority for r in self._rules],
        }

    def set_agent_registry(self, registry: AgentRegistry) -> None:
        """Set or update agent registry."""
        self._agent_registry = registry
        logger.info("Agent registry updated in RoutingEngine")
