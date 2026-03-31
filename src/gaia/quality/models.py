"""
GAIA Quality Models

Data models for quality scoring results.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

# Import DefectType for defect categorization support
try:
    from gaia.pipeline.defect_types import DefectType
except ImportError:
    # DefectType not available - define fallback enum
    from enum import auto

    class DefectType(Enum):
        """Fallback DefectType enum when pipeline module not available."""

        SECURITY = auto()
        PERFORMANCE = auto()
        TESTING = auto()
        DOCUMENTATION = auto()
        CODE_QUALITY = auto()
        REQUIREMENTS = auto()
        ARCHITECTURE = auto()
        ACCESSIBILITY = auto()
        COMPATIBILITY = auto()
        DATA_INTEGRITY = auto()
        UNKNOWN = auto()


class CertificationStatus(Enum):
    """
    Certification status based on quality score.

    Statuses represent quality levels:
    - EXCELLENT: 95%+ (Production ready with excellence)
    - GOOD: 85%+ (Production ready)
    - ACCEPTABLE: 75%+ (Acceptable for most use cases)
    - NEEDS_IMPROVEMENT: 65%+ (Needs refinement)
    - FAIL: <65% (Not acceptable)
    """

    EXCELLENT = "excellent"  # 95%+
    GOOD = "good"  # 85%+
    ACCEPTABLE = "acceptable"  # 75%+
    NEEDS_IMPROVEMENT = "needs_improvement"  # 65%+
    FAIL = "fail"  # <65%

    @classmethod
    def from_score(cls, score: float) -> "CertificationStatus":
        """
        Determine certification status from score.

        Args:
            score: Quality score (0-100)

        Returns:
            Appropriate CertificationStatus
        """
        if score >= 95:
            return cls.EXCELLENT
        elif score >= 85:
            return cls.GOOD
        elif score >= 75:
            return cls.ACCEPTABLE
        elif score >= 65:
            return cls.NEEDS_IMPROVEMENT
        else:
            return cls.FAIL


@dataclass
class CategoryScore:
    """
    Score for a single validation category.

    Each category represents one of the 27 validation checks
    across the 6 quality dimensions.

    Attributes:
        category_id: Unique identifier (e.g., "CQ-01", "TS-02")
        category_name: Human-readable name
        weight: Category weight in overall scoring (0-1)
        raw_score: Raw score percentage (0-100)
        weighted_score: weight * raw_score contribution
        validation_details: Detailed validation results
        defects: List of defects found in this category
    """

    category_id: str
    category_name: str
    weight: float
    raw_score: float  # 0-100
    weighted_score: float
    validation_details: Dict[str, Any] = field(default_factory=dict)
    defects: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "category_id": self.category_id,
            "category_name": self.category_name,
            "weight": self.weight,
            "raw_score": self.raw_score,
            "weighted_score": self.weighted_score,
            "validation_details": self.validation_details,
            "defects_count": len(self.defects),
            "defects": self.defects,
        }

    @property
    def passed(self) -> bool:
        """Check if category passed (score >= 70%)."""
        return self.raw_score >= 70

    @property
    def has_defects(self) -> bool:
        """Check if category has any defects."""
        return len(self.defects) > 0


@dataclass
class DimensionScore:
    """
    Aggregated score for a quality dimension.

    Dimensions group related categories:
    - code_quality: 7 categories (25% total weight)
    - requirements: 4 categories (25% total weight)
    - testing: 4 categories (20% total weight)
    - documentation: 4 categories (15% total weight)
    - best_practices: 5 categories (15% total weight)
    - additional: 3 categories (7% total weight)

    Attributes:
        dimension_name: Name of the dimension
        total_weight: Sum of category weights in this dimension
        earned_score: Weighted score percentage (0-100)
        category_scores: Individual category scores
    """

    dimension_name: str
    total_weight: float
    earned_score: float  # 0-100
    category_scores: List[CategoryScore] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "dimension_name": self.dimension_name,
            "total_weight": self.total_weight,
            "earned_score": self.earned_score,
            "categories_count": len(self.category_scores),
            "category_scores": [cs.to_dict() for cs in self.category_scores],
        }

    @property
    def passed(self) -> bool:
        """Check if dimension passed (earned_score >= 70%)."""
        return self.earned_score >= 70


@dataclass
class QualityReport:
    """
    Complete quality assessment report.

    The QualityReport is the primary output of the QualityScorer,
    containing comprehensive evaluation results across all 27
    validation categories.

    Attributes:
        overall_score: Overall weighted score (0-100)
        certification_status: Status based on overall score
        dimension_scores: Scores for each quality dimension
        category_scores: Individual category scores
        total_defects: Total number of defects found
        critical_defects: Number of critical defects
        tests_run: Number of validation tests executed
        tests_passed: Number of validation tests passed
        metadata: Additional report metadata
        evaluated_at: Timestamp of evaluation
    """

    overall_score: float  # 0-100
    certification_status: CertificationStatus
    dimension_scores: List[DimensionScore] = field(default_factory=list)
    category_scores: List[CategoryScore] = field(default_factory=list)
    total_defects: int = 0
    critical_defects: int = 0
    tests_run: int = 0
    tests_passed: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "overall_score": self.overall_score,
            "certification_status": self.certification_status.value,
            "dimension_scores": [ds.to_dict() for ds in self.dimension_scores],
            "category_scores": [cs.to_dict() for cs in self.category_scores],
            "total_defects": self.total_defects,
            "critical_defects": self.critical_defects,
            "tests_run": self.tests_run,
            "tests_passed": self.tests_passed,
            "pass_rate": (
                self.tests_passed / self.tests_run if self.tests_run > 0 else 0
            ),
            "metadata": self.metadata,
            "evaluated_at": self.evaluated_at.isoformat(),
        }

    @property
    def passed(self) -> bool:
        """Check if overall quality passed (score >= 75%)."""
        return self.overall_score >= 75

    @property
    def is_excellent(self) -> bool:
        """Check if quality is excellent (score >= 95%)."""
        return self.overall_score >= 95

    def get_dimension_score(self, dimension_name: str) -> Optional[DimensionScore]:
        """
        Get score for a specific dimension.

        Args:
            dimension_name: Name of dimension to find

        Returns:
            DimensionScore or None if not found
        """
        for ds in self.dimension_scores:
            if ds.dimension_name == dimension_name:
                return ds
        return None

    def get_category_score(self, category_id: str) -> Optional[CategoryScore]:
        """
        Get score for a specific category.

        Args:
            category_id: Category ID to find

        Returns:
            CategoryScore or None if not found
        """
        for cs in self.category_scores:
            if cs.category_id == category_id:
                return cs
        return None

    def get_defects_by_severity(self, severity: str) -> List[Dict[str, Any]]:
        """
        Get all defects of a specific severity.

        Args:
            severity: Severity level (critical, high, medium, low)

        Returns:
            List of defects with matching severity
        """
        defects = []
        for cs in self.category_scores:
            for defect in cs.defects:
                if defect.get("severity", "").lower() == severity.lower():
                    defects.append(defect)
        return defects

    def get_defects_by_type(self, defect_type: str) -> List[Dict[str, Any]]:
        """
        Get all defects of a specific type.

        Args:
            defect_type: Defect type name (e.g., "SECURITY", "PERFORMANCE")
                or DefectType enum value

        Returns:
            List of defects with matching type
        """
        defects = []
        target_type = (
            defect_type.upper() if isinstance(defect_type, str) else defect_type.name
        )

        for cs in self.category_scores:
            for defect in cs.defects:
                defect_type_value = defect.get("defect_type", "")
                if isinstance(defect_type_value, str):
                    if defect_type_value.upper() == target_type:
                        defects.append(defect)
                elif hasattr(defect_type_value, "name"):
                    if defect_type_value.name == target_type:
                        defects.append(defect)
        return defects

    def get_routing_decisions(self) -> List[Dict[str, Any]]:
        """
        Get defects with routing information.

        Returns list of defects that have routing decision metadata,
        including target_agent, target_phase, and loop_back flag.

        Returns:
            List of defects with routing decisions
        """
        defects = []
        for cs in self.category_scores:
            for defect in cs.defects:
                if "routing" in defect or "target_phase" in defect:
                    defects.append(defect)
        return defects

    def summary(self) -> str:
        """
        Generate a human-readable summary.

        Returns:
            Summary string
        """
        status = self.certification_status.value
        pass_pct = (
            f"({self.tests_passed/self.tests_run*100:.1f}%)"
            if self.tests_run > 0
            else "(N/A)"
        )
        return (
            f"Quality Report: {self.overall_score:.1f}% ({status})\n"
            f"  Defects: {self.total_defects} total, {self.critical_defects} critical\n"
            f"  Tests: {self.tests_passed}/{self.tests_run} passed {pass_pct}"
        )


@dataclass
class QualityWeightConfig:
    """
    Configuration for quality dimension weights.

    QualityWeightConfig defines how much each quality dimension
    contributes to the overall score. Weights must sum to 1.0.

    Attributes:
        name: Configuration profile name
        weights: Dictionary mapping dimension names to weights (must sum to 1.0)
        category_overrides: Optional per-category weight overrides
        description: Human-readable description
    """

    name: str
    weights: Dict[str, float]
    category_overrides: Dict[str, Dict[str, float]] = field(default_factory=dict)
    description: str = ""

    def validate(self, tolerance: float = 0.01) -> bool:
        """
        Validate that weights sum to 1.0 within tolerance.

        Args:
            tolerance: Acceptable deviation from 1.0 (default: +/-0.01)

        Returns:
            True if weights are valid

        Raises:
            ValueError: If weights don't sum to 1.0 within tolerance
        """
        total = sum(self.weights.values())
        if abs(total - 1.0) > tolerance:
            raise ValueError(
                f"Weights for profile '{self.name}' sum to {total}, "
                f"not 1.0 (tolerance: {tolerance})"
            )
        return True

    def get_weight(self, dimension: str) -> float:
        """
        Get weight for a specific dimension.

        Args:
            dimension: Dimension name

        Returns:
            Weight value (0-1) or 0.0 if dimension not found
        """
        return self.weights.get(dimension, 0.0)

    def get_category_weight(
        self, dimension: str, category_id: str, default_weight: float
    ) -> float:
        """
        Get weight for a specific category with override support.

        Args:
            dimension: Dimension name
            category_id: Category ID (e.g., "CQ-01")
            default_weight: Default weight for this category

        Returns:
            Overridden weight if category override exists, otherwise default_weight
        """
        if dimension in self.category_overrides:
            overrides = self.category_overrides[dimension]
            if category_id in overrides:
                return overrides[category_id]
        return default_weight

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "name": self.name,
            "weights": self.weights,
            "category_overrides": self.category_overrides,
            "description": self.description,
            "total_weight": sum(self.weights.values()),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QualityWeightConfig":
        """
        Create QualityWeightConfig from dictionary.

        Args:
            data: Dictionary with config data

        Returns:
            QualityWeightConfig instance
        """
        return cls(
            name=data.get("name", "custom"),
            weights=data.get("weights", {}),
            category_overrides=data.get("category_overrides", {}),
            description=data.get("description", ""),
        )
