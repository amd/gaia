"""
GAIA Quality Templates

Template configurations for different quality thresholds and use cases.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class QualityTemplate:
    """
    Quality template configuration.

    Templates define quality thresholds and agent sequences
    for different types of work.

    Attributes:
        name: Template name (STANDARD, RAPID, ENTERPRISE, DOCUMENTATION)
        threshold: Required quality score (0-1)
        auto_pass: Score at or above which work auto-passes
        manual_review_range: Score range requiring manual review (min, max)
        auto_fail: Score below which work auto-fails
        agent_sequence: Ordered list of agent IDs to execute
        use_case: Description of when to use this template
    """

    name: str
    threshold: float  # 0-1
    auto_pass: float  # 0-1
    manual_review_range: tuple  # (min, max)
    auto_fail: float  # 0-1
    agent_sequence: List[str]
    use_case: str

    def __post_init__(self) -> None:
        """Validate template configuration."""
        if not 0 <= self.threshold <= 1:
            raise ValueError("threshold must be between 0 and 1")
        if not 0 <= self.auto_pass <= 1:
            raise ValueError("auto_pass must be between 0 and 1")
        if not 0 <= self.auto_fail <= 1:
            raise ValueError("auto_fail must be between 0 and 1")
        if self.auto_fail >= self.threshold:
            raise ValueError("auto_fail must be less than threshold")
        if self.auto_pass <= self.threshold:
            raise ValueError("auto_pass must be greater than threshold")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "name": self.name,
            "threshold": self.threshold,
            "auto_pass": self.auto_pass,
            "manual_review_range": self.manual_review_range,
            "auto_fail": self.auto_fail,
            "agent_sequence": self.agent_sequence,
            "use_case": self.use_case,
        }

    def requires_manual_review(self, score: float) -> bool:
        """
        Check if a score requires manual review.

        Args:
            score: Quality score (0-1)

        Returns:
            True if manual review is required
        """
        min_review, max_review = self.manual_review_range
        return min_review <= score < max_review

    def should_auto_pass(self, score: float) -> bool:
        """
        Check if a score should auto-pass.

        Args:
            score: Quality score (0-1)

        Returns:
            True if work should auto-pass
        """
        return score >= self.auto_pass

    def should_auto_fail(self, score: float) -> bool:
        """
        Check if a score should auto-fail.

        Args:
            score: Quality score (0-1)

        Returns:
            True if work should auto-fail
        """
        return score < self.auto_fail


# Predefined quality templates
QUALITY_TEMPLATES: Dict[str, QualityTemplate] = {
    "STANDARD": QualityTemplate(
        name="STANDARD",
        threshold=0.90,  # 90%
        auto_pass=0.95,  # Auto-pass if >= 95%
        manual_review_range=(0.85, 0.94),
        auto_fail=0.85,  # Auto-fail if < 85%
        agent_sequence=[
            "planning-analysis-strategist",
            "senior-developer",
            "quality-reviewer",
            "software-program-manager",
        ],
        use_case="Features, APIs, general development",
    ),
    "RAPID": QualityTemplate(
        name="RAPID",
        threshold=0.75,  # 75%
        auto_pass=0.80,
        manual_review_range=(0.70, 0.79),
        auto_fail=0.70,
        agent_sequence=[
            "planning-analysis-strategist",
            "senior-developer",
            "quality-reviewer",
        ],
        use_case="Prototypes, MVPs, quick iterations",
    ),
    "ENTERPRISE": QualityTemplate(
        name="ENTERPRISE",
        threshold=0.95,  # 95%
        auto_pass=0.98,
        manual_review_range=(0.90, 0.97),
        auto_fail=0.90,
        agent_sequence=[
            "planning-analysis-strategist",
            "senior-developer",
            "quality-reviewer",
            "security-auditor",
            "performance-analyst",
            "software-program-manager",
        ],
        use_case="Production systems, security-critical",
    ),
    "DOCUMENTATION": QualityTemplate(
        name="DOCUMENTATION",
        threshold=0.85,  # 85%
        auto_pass=0.90,
        manual_review_range=(0.80, 0.89),
        auto_fail=0.80,
        agent_sequence=[
            "technical-writer",
            "quality-reviewer",
            "senior-developer",
        ],
        use_case="API docs, guides, documentation",
    ),
}


def get_template(template_name: str) -> QualityTemplate:
    """
    Get a quality template by name.

    Args:
        template_name: Name of the template

    Returns:
        QualityTemplate instance

    Raises:
        KeyError: If template not found
    """
    if template_name not in QUALITY_TEMPLATES:
        raise KeyError(
            f"Template '{template_name}' not found. "
            f"Available templates: {list(QUALITY_TEMPLATES.keys())}"
        )
    return QUALITY_TEMPLATES[template_name]


def get_template_names() -> List[str]:
    """Get list of available template names."""
    return list(QUALITY_TEMPLATES.keys())


def create_custom_template(
    name: str,
    threshold: float,
    agent_sequence: List[str],
    use_case: str,
    auto_pass: Optional[float] = None,
    auto_fail: Optional[float] = None,
) -> QualityTemplate:
    """
    Create a custom quality template.

    Args:
        name: Template name
        threshold: Required quality threshold (0-1)
        agent_sequence: Agent execution sequence
        use_case: Description of when to use
        auto_pass: Auto-pass threshold (default: threshold + 0.05)
        auto_fail: Auto-fail threshold (default: threshold - 0.05)

    Returns:
        QualityTemplate instance
    """
    if auto_pass is None:
        auto_pass = min(1.0, threshold + 0.05)
    if auto_fail is None:
        auto_fail = max(0.0, threshold - 0.05)

    manual_min = auto_fail
    manual_max = auto_pass

    return QualityTemplate(
        name=name,
        threshold=threshold,
        auto_pass=auto_pass,
        manual_review_range=(manual_min, manual_max),
        auto_fail=auto_fail,
        agent_sequence=agent_sequence,
        use_case=use_case,
    )
