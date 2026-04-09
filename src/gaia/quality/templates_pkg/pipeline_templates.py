"""
GAIA Pipeline Templates

Pre-configured pipeline templates for different use cases.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PipelineTemplate:
    """
    Pipeline template configuration.

    Attributes:
        name: Template name
        description: Description of use case
        quality_threshold: Required quality score (0-1)
        max_iterations: Maximum loop iterations
        agent_sequence: Ordered list of agent IDs
        enabled_validators: List of validator categories to enable
        hooks: List of hooks to enable
    """

    name: str
    description: str
    quality_threshold: float = 0.90
    max_iterations: int = 10
    agent_sequence: List[str] = field(default_factory=list)
    enabled_validators: List[str] = field(default_factory=list)
    hooks: List[str] = field(default_factory=list)


# Predefined pipeline templates
PIPELINE_TEMPLATES: Dict[str, PipelineTemplate] = {
    "standard": PipelineTemplate(
        name="standard",
        description="Standard development workflow for features and APIs",
        quality_threshold=0.90,
        max_iterations=10,
        agent_sequence=[
            "planning-analysis-strategist",
            "senior-developer",
            "quality-reviewer",
            "software-program-manager",
        ],
        enabled_validators=["all"],
        hooks=["validation", "context_injection", "quality_gate"],
    ),
    "rapid": PipelineTemplate(
        name="rapid",
        description="Rapid prototyping and MVP development",
        quality_threshold=0.75,
        max_iterations=5,
        agent_sequence=[
            "planning-analysis-strategist",
            "senior-developer",
            "quality-reviewer",
        ],
        enabled_validators=["code_quality", "testing", "requirements"],
        hooks=["validation", "quality_gate"],
    ),
    "enterprise": PipelineTemplate(
        name="enterprise",
        description="Enterprise-grade production systems",
        quality_threshold=0.95,
        max_iterations=15,
        agent_sequence=[
            "planning-analysis-strategist",
            "solutions-architect",
            "senior-developer",
            "security-auditor",
            "performance-analyst",
            "quality-reviewer",
            "software-program-manager",
        ],
        enabled_validators=["all"],
        hooks=["validation", "context_injection", "quality_gate", "notification"],
    ),
    "documentation": PipelineTemplate(
        name="documentation",
        description="Documentation and content generation",
        quality_threshold=0.85,
        max_iterations=8,
        agent_sequence=[
            "technical-writer",
            "quality-reviewer",
            "senior-developer",
        ],
        enabled_validators=["documentation", "best_practices"],
        hooks=["validation", "output_processing"],
    ),
}


def get_pipeline_template(name: str) -> PipelineTemplate:
    """
    Get a pipeline template by name.

    Args:
        name: Template name

    Returns:
        PipelineTemplate instance

    Raises:
        KeyError: If template not found
    """
    if name not in PIPELINE_TEMPLATES:
        raise KeyError(
            f"Template '{name}' not found. "
            f"Available: {list(PIPELINE_TEMPLATES.keys())}"
        )
    return PIPELINE_TEMPLATES[name]
