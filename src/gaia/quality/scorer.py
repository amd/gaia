"""
GAIA Quality Scorer

Evaluates artifacts across 27 validation categories organized into 6 dimensions.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass

from gaia.quality.models import (
    CategoryScore,
    DimensionScore,
    QualityReport,
    CertificationStatus,
    QualityWeightConfig,
)
from gaia.quality.templates import QualityTemplate, get_template
from gaia.quality.weight_config import QualityWeightConfigManager, get_profile as get_weight_profile
from gaia.exceptions import (
    QualityScoringError,
    InvalidQualityThresholdError,
    ValidatorNotFoundError,
)
from gaia.utils.logging import get_logger


logger = get_logger(__name__)


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
    details: Dict[str, Any] = None
    defects: List[Dict[str, Any]] = None

    def __post_init__(self):
        if self.details is None:
            self.details = {}
        if self.defects is None:
            self.defects = []


class BaseValidator:
    """
    Base class for category validators.

    Each validation category (CQ-01 through AC-03) has a corresponding
    validator that implements the validation logic.
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
            artifact: Artifact to validate
            context: Validation context

        Returns:
            ValidationResult with score and defects
        """
        raise NotImplementedError("Subclasses must implement validate()")

    def _create_defect(
        self,
        description: str,
        severity: str = "medium",
        category: Optional[str] = None,
        location: Optional[str] = None,
        suggestion: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a defect record.

        Args:
            description: Description of the issue
            severity: Severity level (critical, high, medium, low)
            category: Defect category
            location: Where the issue was found
            suggestion: Suggested fix

        Returns:
            Defect dictionary
        """
        return {
            "category": category or self.category_id,
            "description": description,
            "severity": severity,
            "location": location,
            "suggestion": suggestion,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


class QualityScorer:
    """
    Evaluates artifacts across 27 validation categories.

    The QualityScorer is the main entry point for quality evaluation.
    It coordinates validation across all categories and aggregates
    results into a comprehensive QualityReport.

    Quality Dimensions:
    - Code Quality (25%): 7 categories
    - Requirements Coverage (25%): 4 categories
    - Testing (20%): 4 categories
    - Documentation (15%): 4 categories
    - Best Practices (15%): 5 categories
    - Additional (7%): 3 categories

    Example:
        >>> scorer = QualityScorer()
        >>> report = await scorer.evaluate(
        ...     artifact=code_string,
        ...     context={"requirements": ["Build API"]}
        ... )
        >>> print(f"Score: {report.overall_score:.1f}%")
    """

    # Category definitions with weights and dimensions
    CATEGORIES: Dict[str, Dict[str, Any]] = {
        # Code Quality (25%)
        "CQ-01": {
            "name": "Syntax Validity",
            "weight": 0.05,
            "dimension": "code_quality",
        },
        "CQ-02": {
            "name": "Code Style Consistency",
            "weight": 0.03,
            "dimension": "code_quality",
        },
        "CQ-03": {
            "name": "Cyclomatic Complexity",
            "weight": 0.03,
            "dimension": "code_quality",
        },
        "CQ-04": {
            "name": "DRY Principle Adherence",
            "weight": 0.04,
            "dimension": "code_quality",
        },
        "CQ-05": {
            "name": "SOLID Principles",
            "weight": 0.05,
            "dimension": "code_quality",
        },
        "CQ-06": {
            "name": "Error Handling",
            "weight": 0.03,
            "dimension": "code_quality",
        },
        "CQ-07": {
            "name": "Type Safety",
            "weight": 0.02,
            "dimension": "code_quality",
        },
        # Requirements Coverage (25%)
        "RC-01": {
            "name": "Feature Completeness",
            "weight": 0.08,
            "dimension": "requirements",
        },
        "RC-02": {
            "name": "Edge Case Handling",
            "weight": 0.05,
            "dimension": "requirements",
        },
        "RC-03": {
            "name": "Acceptance Criteria Met",
            "weight": 0.07,
            "dimension": "requirements",
        },
        "RC-04": {
            "name": "User Story Alignment",
            "weight": 0.05,
            "dimension": "requirements",
        },
        # Testing (20%)
        "TS-01": {
            "name": "Unit Test Coverage",
            "weight": 0.08,
            "dimension": "testing",
        },
        "TS-02": {
            "name": "Integration Test Coverage",
            "weight": 0.05,
            "dimension": "testing",
        },
        "TS-03": {
            "name": "Test Quality/Assertions",
            "weight": 0.04,
            "dimension": "testing",
        },
        "TS-04": {
            "name": "Mock/Stub Appropriateness",
            "weight": 0.03,
            "dimension": "testing",
        },
        # Documentation (15%)
        "DC-01": {
            "name": "Docstrings/Comments",
            "weight": 0.05,
            "dimension": "documentation",
        },
        "DC-02": {
            "name": "README Quality",
            "weight": 0.04,
            "dimension": "documentation",
        },
        "DC-03": {
            "name": "API Documentation",
            "weight": 0.03,
            "dimension": "documentation",
        },
        "DC-04": {
            "name": "Usage Examples",
            "weight": 0.03,
            "dimension": "documentation",
        },
        # Best Practices (15%)
        "BP-01": {
            "name": "Security Practices",
            "weight": 0.05,
            "dimension": "best_practices",
        },
        "BP-02": {
            "name": "Performance Optimization",
            "weight": 0.04,
            "dimension": "best_practices",
        },
        "BP-03": {
            "name": "Accessibility Compliance",
            "weight": 0.02,
            "dimension": "best_practices",
        },
        "BP-04": {
            "name": "Logging/Monitoring",
            "weight": 0.02,
            "dimension": "best_practices",
        },
        "BP-05": {
            "name": "Configuration Management",
            "weight": 0.02,
            "dimension": "best_practices",
        },
        # Additional (7%)
        "AC-01": {
            "name": "Dependency Management",
            "weight": 0.03,
            "dimension": "additional",
        },
        "AC-02": {
            "name": "Build/Deployment Readiness",
            "weight": 0.02,
            "dimension": "additional",
        },
        "AC-03": {
            "name": "Backward Compatibility",
            "weight": 0.02,
            "dimension": "additional",
        },
    }

    # Dimension display names
    DIMENSION_NAMES: Dict[str, str] = {
        "code_quality": "Code Quality",
        "requirements": "Requirements Coverage",
        "testing": "Testing",
        "documentation": "Documentation",
        "best_practices": "Best Practices",
        "additional": "Additional Categories",
    }

    def __init__(self, validators: Optional[Dict[str, BaseValidator]] = None, max_workers: int = 4):
        """
        Initialize the quality scorer.

        Args:
            validators: Optional dict mapping category IDs to validators.
                       If not provided, default validators are used.
            max_workers: Maximum number of parallel workers for validation (QW-004).
        """
        self._validators: Dict[str, BaseValidator] = validators or {}
        self._max_workers = max_workers
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._register_default_validators()
        logger.info(f"QualityScorer initialized with {len(self._validators)} validators and {max_workers} workers")

    def _register_default_validators(self) -> None:
        """
        Register default validators for each category.

        In a full implementation, each category would have a specific
        validator. For now, we register a default validator that
        provides a baseline score.
        """
        for category_id in self.CATEGORIES:
            if category_id not in self._validators:
                self._validators[category_id] = self._create_default_validator(
                    category_id,
                    self.CATEGORIES[category_id]["name"],
                )

    def _create_default_validator(
        self,
        category_id: str,
        category_name: str,
    ) -> BaseValidator:
        """
        Create a default validator for a category.

        Args:
            category_id: Category ID
            category_name: Category name

        Returns:
            BaseValidator instance
        """

        class DefaultValidator(BaseValidator):
            def __init__(self, cat_id: str, cat_name: str):
                self.category_id = cat_id
                self.category_name = cat_name

            async def validate(
                self,
                artifact: Any,
                context: Dict[str, Any],
            ) -> ValidationResult:
                # Default validator provides a baseline score
                # In production, this would be replaced with actual validation
                return ValidationResult(
                    score=85.0,  # Default passing score
                    tests_run=1,
                    tests_passed=1,
                    details={"validator": "default", "category": self.category_id},
                    defects=[],
                )

        return DefaultValidator(category_id, category_name)

    async def evaluate(
        self,
        artifact: Any,
        context: Dict[str, Any],
        weight_config: Optional[QualityWeightConfig] = None,
    ) -> QualityReport:
        """
        Evaluate an artifact across all 27 categories.

        This is the main evaluation method. It runs all validators
        concurrently via ThreadPoolExecutor and aggregates results
        into a QualityReport.

        Args:
            artifact: The artifact to evaluate (code, docs, etc.)
            context: Evaluation context including:
                - requirements: List of requirements
                - language: Programming language
                - template: Quality template name
                - user_story: User story being addressed
                - weight_profile: Optional named weight profile to load
            weight_config: Optional QualityWeightConfig specifying dimension and
                category weight overrides. When None, hardcoded CATEGORIES weights
                are used. Supplied profiles are recorded in report.metadata["weight_profile"].

        Returns:
            QualityReport with comprehensive evaluation results

        Example:
            >>> scorer = QualityScorer()
            >>> report = await scorer.evaluate(
            ...     artifact="def add(a, b): return a + b",
            ...     context={"requirements": ["Add two numbers"]}
            ... )
            >>> print(report.certification_status)
        """
        logger.info(
            "Starting quality evaluation",
            extra={"artifact_type": type(artifact).__name__},
        )

        # Apply weight profile from context if provided (QW-weight-profile)
        if weight_config is None and "weight_profile" in context:
            try:
                weight_config = get_weight_profile(context["weight_profile"])
                logger.info(f"Using weight profile: {context['weight_profile']}")
            except KeyError:
                logger.warning(f"Unknown weight profile: {context['weight_profile']}, using defaults")

        category_scores: List[CategoryScore] = []
        dimension_data: Dict[str, Dict[str, Any]] = {}
        total_defects = 0
        critical_defects = 0
        tests_run = 0
        tests_passed = 0

        # Evaluate each category concurrently via ThreadPoolExecutor (QW-004)
        loop = asyncio.get_running_loop()
        futures = []
        ordered_category_ids = []
        for category_id, category_def in self.CATEGORIES.items():
            validator = self._validators.get(category_id)
            if not validator:
                logger.warning(f"No validator for category {category_id}")
                continue

            future = loop.run_in_executor(
                self._executor,
                self._evaluate_category_sync,
                category_id,
                category_def,
                validator,
                artifact,
                context,
            )
            futures.append(future)
            ordered_category_ids.append(category_id)

        # Gather results from executor futures
        results = await asyncio.gather(*futures, return_exceptions=True)

        # Process results
        for i, result in enumerate(results):
            category_id = ordered_category_ids[i]
            category_def = self.CATEGORIES[category_id]

            # Compute effective weight, applying profile overrides if provided
            base_weight = category_def["weight"]
            if weight_config is not None:
                dimension = category_def["dimension"]
                dim_weight = weight_config.get_weight(dimension)
                if dim_weight > 0:
                    dim_categories = [
                        cid for cid, cdef in self.CATEGORIES.items()
                        if cdef["dimension"] == dimension
                    ]
                    base_weight = dim_weight / len(dim_categories)
                base_weight = weight_config.get_category_weight(dimension, category_id, base_weight)

            if isinstance(result, Exception):
                logger.error(
                    f"Validator {category_id} failed: {result}",
                    extra={"category": category_id},
                )
                # Create a failed score for this category
                category_score = CategoryScore(
                    category_id=category_id,
                    category_name=category_def["name"],
                    weight=base_weight,
                    raw_score=0.0,
                    weighted_score=0.0,
                    defects=[
                        {
                            "category": category_id,
                            "description": f"Validator error: {result}",
                            "severity": "high",
                        }
                    ],
                )
            else:
                # Rebuild CategoryScore with effective weight when override is active
                if weight_config is not None:
                    category_score = CategoryScore(
                        category_id=result.category_id,
                        category_name=result.category_name,
                        weight=base_weight,
                        raw_score=result.raw_score,
                        weighted_score=result.raw_score * base_weight,
                        validation_details=result.validation_details,
                        defects=result.defects,
                    )
                else:
                    category_score = result

            category_scores.append(category_score)

            # Aggregate by dimension
            dimension = category_def["dimension"]
            if dimension not in dimension_data:
                dimension_data[dimension] = {
                    "name": self.DIMENSION_NAMES.get(dimension, dimension),
                    "total_weight": 0.0,
                    "earned_score": 0.0,
                    "categories": [],
                }

            dimension_data[dimension]["total_weight"] += base_weight
            dimension_data[dimension]["earned_score"] += category_score.weighted_score
            dimension_data[dimension]["categories"].append(category_score)

            # Count defects
            total_defects += len(category_score.defects)
            critical_defects += sum(
                1 for d in category_score.defects if d.get("severity") == "critical"
            )

            # Count tests
            tests_run += category_score.validation_details.get("tests_run", 1)
            tests_passed += category_score.validation_details.get("tests_passed", 1)

        # Calculate overall score
        # weighted_score is already raw_score * weight, so sum gives us 0-100 score
        overall_score = sum(cs.weighted_score for cs in category_scores)

        # Determine certification status
        certification_status = CertificationStatus.from_score(overall_score)

        # Build dimension scores
        dimension_scores: List[DimensionScore] = []
        for dim_data in dimension_data.values():
            dim_score = DimensionScore(
                dimension_name=dim_data["name"],
                total_weight=dim_data["total_weight"],
                earned_score=(
                    dim_data["earned_score"] / dim_data["total_weight"]
                    if dim_data["total_weight"] > 0
                    else 0.0
                ),
                category_scores=dim_data["categories"],
            )
            dimension_scores.append(dim_score)

        # Build report
        report = QualityReport(
            overall_score=overall_score,
            certification_status=certification_status,
            dimension_scores=dimension_scores,
            category_scores=category_scores,
            total_defects=total_defects,
            critical_defects=critical_defects,
            tests_run=tests_run,
            tests_passed=tests_passed,
            metadata={
                "categories_evaluated": len(category_scores),
                "dimensions_evaluated": len(dimension_scores),
                "weight_profile": weight_config.name if weight_config else "default",
            },
        )

        logger.info(
            f"Quality evaluation complete: {overall_score:.1f}% ({certification_status.value})",
            extra={
                "overall_score": overall_score,
                "total_defects": total_defects,
                "critical_defects": critical_defects,
            },
        )

        return report

    async def _evaluate_category(
        self,
        category_id: str,
        category_def: Dict[str, Any],
        validator: BaseValidator,
        artifact: Any,
        context: Dict[str, Any],
    ) -> CategoryScore:
        """
        Evaluate a single category.

        Args:
            category_id: Category ID
            category_def: Category definition
            validator: Validator to use
            artifact: Artifact to evaluate
            context: Evaluation context

        Returns:
            CategoryScore for this category
        """
        try:
            result = await validator.validate(artifact, context)

            return CategoryScore(
                category_id=category_id,
                category_name=category_def["name"],
                weight=category_def["weight"],
                raw_score=result.score,
                weighted_score=result.score * category_def["weight"],
                validation_details={
                    **result.details,
                    "tests_run": result.tests_run,
                    "tests_passed": result.tests_passed,
                },
                defects=result.defects,
            )
        except Exception as e:
            logger.exception(f"Validator {category_id} error: {e}")
            raise

    def _evaluate_category_sync(
        self,
        category_id: str,
        category_def: Dict[str, Any],
        validator: BaseValidator,
        artifact: Any,
        context: Dict[str, Any],
    ) -> CategoryScore:
        """
        Synchronous wrapper for _evaluate_category (for ThreadPoolExecutor execution, QW-004).

        Wraps the async _evaluate_category to allow parallel execution
        using ThreadPoolExecutor.

        Args:
            category_id: Category ID
            category_def: Category definition
            validator: Validator to use
            artifact: Artifact to evaluate
            context: Evaluation context

        Returns:
            CategoryScore for this category
        """
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(validator.validate(artifact, context))
            finally:
                loop.close()

            return CategoryScore(
                category_id=category_id,
                category_name=category_def["name"],
                weight=category_def["weight"],
                raw_score=result.score,
                weighted_score=result.score * category_def["weight"],
                validation_details={
                    **result.details,
                    "tests_run": result.tests_run,
                    "tests_passed": result.tests_passed,
                },
                defects=result.defects,
            )
        except Exception as e:
            logger.exception(f"Validator {category_id} error: {e}")
            raise

    def get_template_config(self, template_name: str) -> QualityTemplate:
        """
        Get quality template configuration.

        Args:
            template_name: Template name (STANDARD, RAPID, etc.)

        Returns:
            QualityTemplate configuration

        Raises:
            KeyError: If template not found
        """
        return get_template(template_name)

    def get_category_info(self, category_id: str) -> Optional[Dict[str, Any]]:
        """
        Get information about a category.

        Args:
            category_id: Category ID

        Returns:
            Category information or None if not found
        """
        return self.CATEGORIES.get(category_id)

    def get_categories_by_dimension(
        self, dimension: str
    ) -> List[Dict[str, Any]]:
        """
        Get all categories in a dimension.

        Args:
            dimension: Dimension name

        Returns:
            List of category definitions
        """
        return [
            {"id": cid, **cdef}
            for cid, cdef in self.CATEGORIES.items()
            if cdef["dimension"] == dimension
        ]

    def get_dimension_weight(self, dimension: str) -> float:
        """
        Get total weight for a dimension.

        Args:
            dimension: Dimension name

        Returns:
            Total weight (sum of category weights)
        """
        return sum(
            cdef["weight"]
            for cdef in self.CATEGORIES.values()
            if cdef["dimension"] == dimension
        )

    def register_validator(
        self, category_id: str, validator: BaseValidator
    ) -> None:
        """
        Register a custom validator for a category.

        Args:
            category_id: Category ID
            validator: Validator instance

        Raises:
            ValidatorNotFoundError: If category not found
        """
        if category_id not in self.CATEGORIES:
            raise ValidatorNotFoundError(category_id)

        self._validators[category_id] = validator
        logger.info(f"Registered custom validator for {category_id}")

    def get_validator(self, category_id: str) -> Optional[BaseValidator]:
        """
        Get validator for a category.

        Args:
            category_id: Category ID

        Returns:
            Validator or None if not found
        """
        return self._validators.get(category_id)

    def shutdown(self, wait: bool = True) -> None:
        """
        Shutdown the QualityScorer and release resources (QW-004).

        Args:
            wait: Whether to wait for pending tasks to complete
        """
        if hasattr(self, '_executor') and self._executor:
            self._executor.shutdown(wait=wait)
            logger.info("QualityScorer executor shutdown complete")
