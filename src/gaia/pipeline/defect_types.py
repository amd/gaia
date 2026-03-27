"""
GAIA Defect Type Taxonomy

Comprehensive defect type classification system for the GAIA pipeline.
Provides standardized defect categorization and keyword-based detection.
"""

from enum import Enum, auto
from typing import Dict, List, Optional, Any


class DefectType(Enum):
    """
    Comprehensive defect type taxonomy.

    Each defect type represents a category of issues that can be detected
    during quality evaluation. Types are mapped to keywords for automatic
    detection and to specialist agents for remediation.

    Categories:
    - SECURITY: Security vulnerabilities and risks
    - PERFORMANCE: Performance and efficiency issues
    - TESTING: Test coverage and quality issues
    - DOCUMENTATION: Missing or incorrect documentation
    - CODE_QUALITY: Code structure and maintainability issues
    - REQUIREMENTS: Requirements alignment issues
    - ARCHITECTURE: Architectural consistency issues
    - ACCESSIBILITY: Accessibility compliance issues
    - COMPATIBILITY: Cross-platform/browser compatibility issues
    - DATA_INTEGRITY: Data handling and integrity issues
    """

    # Security defects (highest priority)
    SECURITY = auto()
    """Security vulnerabilities, injection risks, authentication issues"""

    # Performance defects
    PERFORMANCE = auto()
    """Performance bottlenecks, memory leaks, inefficient algorithms"""

    # Testing defects
    TESTING = auto()
    """Missing tests, insufficient coverage, flaky tests"""

    # Documentation defects
    DOCUMENTATION = auto()
    """Missing docstrings, outdated docs, unclear comments"""

    # Code quality defects
    CODE_QUALITY = auto()
    """Code style, complexity, duplication, maintainability"""

    # Requirements defects
    REQUIREMENTS = auto()
    """Missing requirements, incorrect implementation, scope issues"""

    # Architecture defects
    ARCHITECTURE = auto()
    """Architecture violations, circular dependencies, tight coupling"""

    # Accessibility defects
    ACCESSIBILITY = auto()
    """WCAG compliance, screen reader support, keyboard navigation"""

    # Compatibility defects
    COMPATIBILITY = auto()
    """Cross-browser, cross-platform, version compatibility"""

    # Data integrity defects
    DATA_INTEGRITY = auto()
    """Data validation, type safety, data loss risks"""

    # Unknown/unclassified
    UNKNOWN = auto()
    """Unclassified defects requiring manual review"""


# Keyword mappings for defect type detection
# Each defect type maps to a set of keywords/phrases for detection
DEFECT_KEYWORDS: Dict[DefectType, List[str]] = {
    DefectType.SECURITY: [
        "sql injection",
        "xss",
        "cross-site scripting",
        "csrf",
        "authentication bypass",
        "authorization issue",
        "access control",
        "security vulnerability",
        "security risk",
        "injection attack",
        "buffer overflow",
        "privilege escalation",
        "session hijacking",
        "data breach",
        "encryption",
        "credential",
        "token exposure",
        "api key leak",
        "secret exposure",
        "vulnerability",
        "exploit",
        "malicious input",
        "input validation",
        "sanitize",
    ],
    DefectType.PERFORMANCE: [
        "slow query",
        "performance issue",
        "memory leak",
        "memory consumption",
        "cpu usage",
        "inefficient algorithm",
        "time complexity",
        "space complexity",
        "optimization needed",
        "bottleneck",
        "latency",
        "response time",
        "throughput",
        "caching",
        "database performance",
        "n+1 query",
        "redundant computation",
        "heavy resource usage",
        "gc pressure",
        "allocation",
    ],
    DefectType.TESTING: [
        "missing tests",
        "test coverage",
        "insufficient coverage",
        "flaky test",
        "test failure",
        "assertion error",
        "mock needed",
        "integration test",
        "unit test",
        "e2e test",
        "regression test",
        "test case",
        "test suite",
        "code coverage",
        "branch coverage",
        "path coverage",
        "untested",
        "no tests",
    ],
    DefectType.DOCUMENTATION: [
        "missing docstring",
        "documentation missing",
        "outdated documentation",
        "unclear comment",
        "missing comment",
        "api documentation",
        "readme",
        "user guide",
        "technical specification",
        "inline comment",
        "code comment",
        "function description",
        "parameter documentation",
        "return value documentation",
        "example missing",
        "usage example",
    ],
    DefectType.CODE_QUALITY: [
        "code style",
        "code smell",
        "complexity",
        "cyclomatic complexity",
        "duplicate code",
        "code duplication",
        "long function",
        "long method",
        "god class",
        "magic number",
        "hardcoded value",
        "naming convention",
        "pep 8",
        "linting error",
        "code formatting",
        "refactor needed",
        "technical debt",
        "maintainability",
        "readability",
        "coupling",
        "cohesion",
        "solid principle",
    ],
    DefectType.REQUIREMENTS: [
        "missing requirement",
        "requirement not met",
        "incorrect implementation",
        "scope creep",
        "feature missing",
        "user story",
        "acceptance criteria",
        "functional requirement",
        "non-functional requirement",
        "business logic",
        "expected behavior",
        "specification mismatch",
        "requirement gap",
        "incomplete feature",
        "edge case not handled",
        "incorrect feature",
        "feature behavior",
    ],
    DefectType.ARCHITECTURE: [
        "architecture violation",
        "architectural pattern",
        "circular dependency",
        "tight coupling",
        "loose coupling",
        "dependency injection",
        "inversion of control",
        "layer violation",
        "boundary crossing",
        "module dependency",
        "package structure",
        "design pattern",
        "singleton",
        "factory",
        "observer pattern",
        "mvc",
        "microservice",
        "monolith",
        "coupling between",
        "architectural",
    ],
    DefectType.ACCESSIBILITY: [
        "accessibility",
        "wcag",
        "screen reader",
        "keyboard navigation",
        "alt text",
        "aria label",
        "color contrast",
        "focus indicator",
        "tab order",
        "accessible",
        "disability",
        "assistive technology",
        "a11y",
        "semantic html",
        "heading structure",
    ],
    DefectType.COMPATIBILITY: [
        "compatibility",
        "cross-browser",
        "cross-platform",
        "browser compatibility",
        "version compatibility",
        "backwards compatible",
        "forwards compatible",
        "legacy support",
        "deprecated api",
        "breaking change",
        "polyfill",
        "transpile",
        "responsive design",
        "mobile compatibility",
        "ios",
        "android",
        "not working on",
        "safari",
        "chrome",
        "firefox",
        "edge browser",
    ],
    DefectType.DATA_INTEGRITY: [
        "data integrity",
        "data validation",
        "type safety",
        "data loss",
        "data corruption",
        "null pointer",
        "undefined behavior",
        "race condition",
        "concurrency issue",
        "transaction",
        "atomic operation",
        "data consistency",
        "referential integrity",
        "foreign key",
        "constraint violation",
        "schema mismatch",
        "type error",
        "cast error",
    ],
    DefectType.UNKNOWN: [
        "unknown issue",
        "unclassified",
        "needs review",
        "manual inspection",
    ],
}


# Reverse mapping: keyword -> DefectType (for fast lookup)
_KEYWORD_TO_DEFECT: Dict[str, DefectType] = {}


def _build_keyword_index() -> None:
    """Build reverse keyword index for fast lookup."""
    for defect_type, keywords in DEFECT_KEYWORDS.items():
        for keyword in keywords:
            # Store with lowercase key for case-insensitive matching
            _KEYWORD_TO_DEFECT[keyword.lower()] = defect_type


# Build index on module load
_build_keyword_index()


# Specialist agent mappings
# Maps each defect type to preferred specialist agent(s)
DEFECT_SPECIALISTS: Dict[DefectType, List[str]] = {
    DefectType.SECURITY: ["security-auditor", "senior-developer"],
    DefectType.PERFORMANCE: ["performance-analyst", "senior-developer"],
    DefectType.TESTING: ["test-coverage-analyzer", "quality-reviewer"],
    DefectType.DOCUMENTATION: ["technical-writer", "senior-developer"],
    DefectType.CODE_QUALITY: ["quality-reviewer", "senior-developer"],
    DefectType.REQUIREMENTS: ["software-program-manager", "planning-analysis-strategist"],
    DefectType.ARCHITECTURE: ["solutions-architect", "senior-developer"],
    DefectType.ACCESSIBILITY: ["accessibility-reviewer", "frontend-specialist"],
    DefectType.COMPATIBILITY: ["frontend-specialist", "devops-engineer"],
    DefectType.DATA_INTEGRITY: ["backend-specialist", "data-engineer"],
    DefectType.UNKNOWN: ["senior-developer"],
}


def defect_type_from_string(text: str) -> DefectType:
    """
    Detect defect type from text using keyword matching.

    Performs case-insensitive matching against known keywords
    for each defect type. Returns the first matching type, or
    UNKNOWN if no match found.

    Performance Optimization:
    - Uses pre-built _KEYWORD_TO_DEFECT index for O(1) keyword lookup
    - Early exit on first match to avoid unnecessary iterations
    - Short-circuits multi-word keyword matching on first success

    Args:
        text: Text to analyze (defect description, error message, etc.)

    Returns:
        Detected DefectType, or UNKNOWN if no match

    Example:
        >>> defect_type_from_string("SQL injection vulnerability found")
        DefectType.SECURITY
        >>> defect_type_from_string("Slow query detected")
        DefectType.PERFORMANCE
        >>> defect_type_from_string("Random issue")
        DefectType.UNKNOWN
    """
    if not text:
        return DefectType.UNKNOWN

    text_lower = text.lower()

    # Try exact keyword match first (fast path) - O(1) lookup per keyword
    # Early exit on first match for performance
    for keyword, defect_type in _KEYWORD_TO_DEFECT.items():
        if keyword in text_lower:
            return defect_type

    # Try partial matching for compound keywords
    # (e.g., "sql" + "injection" should match "sql injection")
    # Short-circuit: return immediately when match found
    for defect_type, keywords in DEFECT_KEYWORDS.items():
        for keyword in keywords:
            if " " in keyword:
                # Multi-word keyword - check if all parts are present
                parts = keyword.split()
                if all(part in text_lower for part in parts):
                    return defect_type

    return DefectType.UNKNOWN


def get_defect_keywords(defect_type: DefectType) -> List[str]:
    """
    Get list of keywords for a defect type.

    Args:
        defect_type: Defect type to get keywords for

    Returns:
        List of keywords associated with the defect type
    """
    return DEFECT_KEYWORDS.get(defect_type, [])


def get_defect_specialists(defect_type: DefectType) -> List[str]:
    """
    Get list of specialist agents for a defect type.

    Returns agents in order of preference (most specialist first).

    Args:
        defect_type: Defect type to get specialists for

    Returns:
        List of agent IDs that can handle this defect type
    """
    return DEFECT_SPECIALISTS.get(defect_type, ["senior-developer"])


def detect_defect_types(texts: List[str]) -> Dict[str, DefectType]:
    """
    Detect defect types for multiple texts.

    Convenience function for batch processing.

    Args:
        texts: List of texts to analyze

    Returns:
        Dictionary mapping each text to its detected defect type
    """
    return {text: defect_type_from_string(text) for text in texts}


def get_all_defect_types() -> List[DefectType]:
    """
    Get list of all defect types.

    Returns:
        List of all DefectType enum values (excluding UNKNOWN)
    """
    return [t for t in DefectType if t != DefectType.UNKNOWN]


def get_defect_type_info(defect_type: DefectType) -> Dict[str, Any]:
    """
    Get comprehensive information about a defect type.

    Args:
        defect_type: Defect type to get info for

    Returns:
        Dictionary with type info including name, keywords, and specialists
    """
    return {
        "name": defect_type.name,
        "value": defect_type.value,
        "keywords": get_defect_keywords(defect_type),
        "specialists": get_defect_specialists(defect_type),
        "keyword_count": len(get_defect_keywords(defect_type)),
    }
