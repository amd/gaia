"""
GAIA Base Validator

Base class for all quality validators.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Any, Optional


@dataclass
class ValidationResult:
    """
    Result from a single validator execution.

    Attributes:
        score: Raw score (0-100)
        tests_run: Number of tests executed
        tests_passed: Number of tests passed
        details: Detailed validation results
        defects: List of defects found
    """

    score: float  # 0-100
    tests_run: int = 0
    tests_passed: int = 0
    details: Dict[str, Any] = field(default_factory=dict)
    defects: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Check if validation passed (score >= 70%)."""
        return self.score >= 70

    @property
    def pass_rate(self) -> float:
        """Get test pass rate."""
        if self.tests_run == 0:
            return 100.0
        return (self.tests_passed / self.tests_run) * 100


class BaseValidator(ABC):
    """
    Abstract base class for all quality validators.

    Each validation category (CQ-01 through AC-03) has a corresponding
    validator that extends this base class and implements specific
    validation logic.

    Subclasses must:
    1. Set class attributes: category_id, category_name
    2. Implement the validate() async method

    Example:
        class SyntaxValidator(BaseValidator):
            category_id = "CQ-01"
            category_name = "Syntax Validity"

            async def validate(self, artifact, context):
                # Implementation
                return ValidationResult(score=95.0, defects=[])
    """

    category_id: str = "base"
    category_name: str = "Base Validator"

    async def validate(
        self,
        artifact: Any,
        context: Dict[str, Any],
    ) -> ValidationResult:
        """
        Validate an artifact.

        Args:
            artifact: The artifact to validate (code, docs, etc.)
            context: Validation context including:
                - requirements: List of requirements
                - language: Programming language
                - user_story: User story being addressed

        Returns:
            ValidationResult with score and defects

        Raises:
            NotImplementedError: If subclass doesn't implement
        """
        raise NotImplementedError("Subclasses must implement validate()")

    def _create_defect(
        self,
        description: str,
        severity: str = "medium",
        category: Optional[str] = None,
        location: Optional[str] = None,
        suggestion: Optional[str] = None,
        code_snippet: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a standardized defect record.

        Args:
            description: Human-readable description of the issue
            severity: Severity level (critical, high, medium, low)
            category: Defect category (defaults to validator's category)
            location: Where the issue was found (file:line)
            suggestion: Suggested fix
            code_snippet: Relevant code snippet

        Returns:
            Defect dictionary with standardized fields
        """
        return {
            "category": category or self.category_id,
            "description": description,
            "severity": severity,
            "location": location,
            "suggestion": suggestion,
            "code_snippet": code_snippet,
            "timestamp": datetime.utcnow().isoformat(),
            "validator": self.category_name,
        }

    def _create_validation_result(
        self,
        score: float,
        tests_run: int = 1,
        tests_passed: int = 1,
        details: Optional[Dict[str, Any]] = None,
        defects: Optional[List[Dict[str, Any]]] = None,
    ) -> ValidationResult:
        """
        Create a validation result.

        Args:
            score: Raw score (0-100)
            tests_run: Number of tests executed
            tests_passed: Number of tests passed
            details: Optional detailed results
            defects: Optional list of defects

        Returns:
            ValidationResult instance
        """
        return ValidationResult(
            score=score,
            tests_run=tests_run,
            tests_passed=tests_passed,
            details=details or {},
            defects=defects or [],
        )

    def _score_from_checks(self, checks: List[bool]) -> float:
        """
        Calculate score from a list of boolean checks.

        Args:
            checks: List of check results (True = passed)

        Returns:
            Score as percentage (0-100)
        """
        if not checks:
            return 100.0
        passed = sum(1 for c in checks if c)
        return (passed / len(checks)) * 100

    def _score_from_weights(
        self, weighted_checks: List[tuple]
    ) -> float:
        """
        Calculate score from weighted checks.

        Args:
            weighted_checks: List of (passed, weight) tuples

        Returns:
            Weighted score (0-100)
        """
        if not weighted_checks:
            return 100.0

        total_weight = sum(w for _, w in weighted_checks)
        earned_weight = sum(w for passed, w in weighted_checks if passed)

        return (earned_weight / total_weight) * 100 if total_weight > 0 else 100.0

    async def _validate_syntax(
        self, code: str, language: str = "python"
    ) -> tuple[bool, str]:
        """
        Validate syntax for a code snippet.

        Args:
            code: Code to validate
            language: Programming language

        Returns:
            Tuple of (is_valid, error_message)
        """
        if language == "python":
            try:
                compile(code, "<string>", "exec")
                return True, ""
            except SyntaxError as e:
                return False, f"Syntax error: {e}"
        # For other languages, would use appropriate parser
        return True, ""

    async def _check_imports(self, code: str) -> List[Dict[str, Any]]:
        """
        Check for import-related issues.

        Args:
            code: Code to check

        Returns:
            List of defects found
        """
        defects = []

        # Check for wildcard imports
        if "import *" in code:
            defects.append(
                self._create_defect(
                    description="Wildcard import detected (import *)",
                    severity="medium",
                    suggestion="Use explicit imports for better maintainability",
                )
            )

        return defects

    async def _check_hardcoded_values(
        self, code: str
    ) -> List[Dict[str, Any]]:
        """
        Check for hardcoded values that should be configuration.

        Args:
            code: Code to check

        Returns:
            List of defects found
        """
        defects = []

        # Simple pattern checks
        patterns = [
            ("http://", "Hardcoded HTTP URL - consider using environment variable"),
            ("password =", "Hardcoded password detected"),
            ("secret =", "Hardcoded secret detected"),
            ("api_key =", "Hardcoded API key detected"),
        ]

        for pattern, message in patterns:
            if pattern in code.lower():
                defects.append(
                    self._create_defect(
                        description=message,
                        severity="high",
                        category="security",
                        suggestion="Move sensitive values to environment variables",
                    )
                )

        return defects

    def get_info(self) -> Dict[str, Any]:
        """
        Get validator information.

        Returns:
            Dictionary with validator metadata
        """
        return {
            "category_id": self.category_id,
            "category_name": self.category_name,
            "description": self.__doc__ or "",
        }
