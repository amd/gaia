"""
GAIA Recursive Iterative Pipeline Template

Generic template system for recursive agent-based pipeline execution.
Supports agent categories, conditional routing, and quality-gated loop-back.

Usage:
    from gaia.pipeline.recursive_template import RecursivePipelineTemplate

    template = RecursivePipelineTemplate(
        name="generic",
        agent_categories={
            "planning": ["planning-analysis-strategist"],
            "development": ["senior-developer"],
            "quality": ["quality-reviewer"],
            "decision": ["software-program-manager"],
        },
        quality_threshold=0.90,
        routing_rules=[...],
    )
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from gaia.quality.models import QualityWeightConfig
from gaia.quality.weight_config import get_profile as get_weight_profile


class SelectionMode(Enum):
    """Agent selection mode within a category."""

    AUTO = "auto"  # Auto-select based on task triggers
    SEQUENTIAL = "sequential"  # Run agents one by one
    PARALLEL = "parallel"  # Run agents concurrently (future)


class AgentCategory(Enum):
    """
    Agent categories for organized routing.

    These categories map to the agent registry's AGENT_CATEGORIES.
    """

    PLANNING = "planning"
    DEVELOPMENT = "development"
    REVIEW = "review"
    MANAGEMENT = "management"
    QUALITY = "quality"
    DECISION = "decision"


@dataclass
class RoutingRule:
    """
    Conditional routing rule for defect/task-based agent selection.

    Attributes:
        condition: Condition expression (e.g., "defect_type == 'security'")
        route_to: Target category or specific agent ID
        priority: Rule priority (lower = higher priority)
        loop_back: Whether to loop back to previous phase
        guidance: Optional guidance message for the agent
    """

    condition: str
    route_to: str
    priority: int = 0
    loop_back: bool = False
    guidance: Optional[str] = None

    def matches(self, context: Dict[str, Any]) -> bool:
        """
        Check if this rule matches the current context.

        Args:
            context: Current pipeline context with defect info, quality score, etc.

        Returns:
            True if condition is satisfied
        """
        # Simple condition evaluation (can be extended with more complex parsing)
        condition = self.condition.lower()

        # Check defect type conditions
        if "defect_type" in condition:
            defect_type = context.get("defect_type", "").lower()
            if f"'{defect_type}'" in condition or f'"{defect_type}"' in condition:
                return True
            if defect_type in condition:
                return True

        # Check quality score conditions
        if "quality_score" in condition:
            quality = context.get("quality_score", 1.0)
            threshold = context.get("quality_threshold", 0.9)
            if ">=" in condition and quality >= threshold:
                return True
            if "<" in condition and quality < threshold:
                return True

        # Check task type conditions
        if "task_type" in condition:
            task_type = context.get("task_type", "").lower()
            if task_type in condition:
                return True

        return False


@dataclass
class PhaseConfig:
    """
    Configuration for a single pipeline phase.

    Attributes:
        name: Phase name (e.g., "PLANNING", "DEVELOPMENT")
        category: Agent category for this phase
        selection_mode: How to select agent(s)
        agents: List of agent IDs in this category
        exit_criteria: Conditions to exit this phase
    """

    name: str
    category: AgentCategory
    selection_mode: SelectionMode = SelectionMode.AUTO
    agents: List[str] = field(default_factory=list)
    exit_criteria: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RecursivePipelineTemplate:
    """
    Generic recursive pipeline template.

    Implements the recursive iterative workflow:
    PLANNING -> DEVELOPMENT -> REVIEW -> MANAGEMENT
              ^              |
              +-- loop_back -+

    Attributes:
        name: Template name
        description: Template description
        quality_threshold: Required quality score (0-1)
        max_iterations: Maximum recursive iterations
        agent_categories: Map of categories to agent lists
        phases: Ordered list of phase configurations
        routing_rules: Conditional routing rules
        quality_weights: Weights for quality scoring dimensions
        weight_config: QualityWeightConfig object for advanced weight management
    """

    name: str
    description: str = ""
    quality_threshold: float = 0.90
    max_iterations: int = 10
    agent_categories: Dict[str, List[str]] = field(default_factory=dict)
    phases: List[PhaseConfig] = field(default_factory=list)
    routing_rules: List[RoutingRule] = field(default_factory=list)
    quality_weights: Dict[str, float] = field(default_factory=dict)
    weight_config: Optional[QualityWeightConfig] = None

    def __post_init__(self):
        """Validate template configuration."""
        if not 0 <= self.quality_threshold <= 1:
            raise ValueError("quality_threshold must be between 0 and 1")
        if self.max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")

        # Default phases if not provided
        if not self.phases:
            self.phases = self._create_default_phases()

        # Handle quality weights and weight_config
        if self.weight_config is not None:
            # Use weight_config if provided, validate and extract weights
            self.weight_config.validate()
            self.quality_weights = self.weight_config.weights.copy()
        elif not self.quality_weights:
            # Default quality weights if not provided
            self.quality_weights = {
                "code_quality": 0.25,
                "requirements_coverage": 0.25,
                "testing": 0.20,
                "documentation": 0.15,
                "best_practices": 0.15,
            }

    def _create_default_phases(self) -> List[PhaseConfig]:
        """Create default 4-phase pipeline."""
        return [
            PhaseConfig(
                name="PLANNING",
                category=AgentCategory.PLANNING,
                selection_mode=SelectionMode.AUTO,
                agents=self.agent_categories.get("planning", []),
                exit_criteria={"artifact": "technical_plan"},
            ),
            PhaseConfig(
                name="DEVELOPMENT",
                category=AgentCategory.DEVELOPMENT,
                selection_mode=SelectionMode.AUTO,
                agents=self.agent_categories.get("development", []),
                exit_criteria={"artifact": "implementation"},
            ),
            PhaseConfig(
                name="QUALITY",
                category=AgentCategory.QUALITY,
                selection_mode=SelectionMode.AUTO,
                agents=self.agent_categories.get("quality", []),
                exit_criteria={"artifact": "quality_report"},
            ),
            PhaseConfig(
                name="DECISION",
                category=AgentCategory.DECISION,
                selection_mode=SelectionMode.AUTO,
                agents=self.agent_categories.get("decision", []),
                exit_criteria={"artifact": "decision"},
            ),
        ]

    def get_phase(self, phase_name: str) -> Optional[PhaseConfig]:
        """Get phase configuration by name."""
        for phase in self.phases:
            if phase.name == phase_name:
                return phase
        return None

    def get_next_phase(self, current_phase: str) -> Optional[PhaseConfig]:
        """Get the next phase in sequence."""
        for i, phase in enumerate(self.phases):
            if phase.name == current_phase:
                if i + 1 < len(self.phases):
                    return self.phases[i + 1]
        return None

    def get_previous_phase(self, current_phase: str) -> Optional[PhaseConfig]:
        """Get the previous phase in sequence."""
        for i, phase in enumerate(self.phases):
            if phase.name == current_phase:
                if i > 0:
                    return self.phases[i - 1]
        return None

    def evaluate_routing_rules(self, context: Dict[str, Any]) -> Optional[RoutingRule]:
        """
        Evaluate routing rules against current context.

        Args:
            context: Current pipeline context

        Returns:
            First matching routing rule, or None
        """
        # Sort by priority and evaluate
        sorted_rules = sorted(self.routing_rules, key=lambda r: r.priority)
        for rule in sorted_rules:
            if rule.matches(context):
                return rule
        return None

    def should_loop_back(
        self, quality_score: float, iteration: int, has_defects: bool = True
    ) -> bool:
        """
        Determine if pipeline should loop back.

        Args:
            quality_score: Current quality score
            iteration: Current iteration count
            has_defects: Whether defects were found

        Returns:
            True if should loop back to PLANNING
        """
        if iteration >= self.max_iterations:
            return False  # Max iterations reached

        if quality_score < self.quality_threshold and has_defects:
            return True

        return False

    def set_weight_profile(self, profile_name: str) -> None:
        """
        Set quality weights from a pre-defined profile.

        Args:
            profile_name: Profile name (balanced, security_heavy, speed_heavy, documentation_heavy)

        Raises:
            KeyError: If profile not found
        """
        profile = get_weight_profile(profile_name)
        self.quality_weights = profile.weights.copy()
        self.weight_config = profile

    def get_weight_config(self) -> QualityWeightConfig:
        """
        Get or create QualityWeightConfig from current weights.

        Returns:
            QualityWeightConfig instance
        """
        if self.weight_config is not None:
            return self.weight_config

        return QualityWeightConfig(
            name=f"{self.name}_weights",
            weights=self.quality_weights.copy(),
            description=f"Weight config for template {self.name}",
        )

    def apply_weight_overrides(self, overrides: Dict[str, float]) -> None:
        """
        Apply weight overrides to current configuration.

        Args:
            overrides: Dictionary of dimension -> new weight
        """
        from gaia.quality.weight_config import get_manager

        manager = get_manager()
        base_config = self.get_weight_config()
        merged = manager.merge_weights(base_config, overrides)
        self.quality_weights = merged.weights.copy()
        self.weight_config = merged

    def validate_weights(self, tolerance: float = 0.01) -> bool:
        """
        Validate that quality weights sum to 1.0.

        Args:
            tolerance: Acceptable deviation from 1.0

        Returns:
            True if valid

        Raises:
            ValueError: If weights don't sum to 1.0 within tolerance
        """
        total = sum(self.quality_weights.values())
        if abs(total - 1.0) > tolerance:
            raise ValueError(f"Template '{self.name}' weights sum to {total}, not 1.0")
        return True


# Pre-built template instances

GENERIC_TEMPLATE = RecursivePipelineTemplate(
    name="generic",
    description="Generic recursive pipeline for most development tasks",
    quality_threshold=0.90,
    max_iterations=10,
    agent_categories={
        "planning": ["planning-analysis-strategist"],
        "development": ["senior-developer"],
        "quality": ["quality-reviewer"],
        "decision": ["software-program-manager"],
    },
    routing_rules=[
        RoutingRule(
            condition="defect_type == 'security'",
            route_to="security-auditor",
            priority=1,
            loop_back=True,
            guidance="Address security vulnerability before proceeding",
        ),
        RoutingRule(
            condition="defect_type == 'missing_tests'",
            route_to="DEVELOPMENT",
            priority=2,
            loop_back=True,
            guidance="Add unit tests for new functionality",
        ),
        RoutingRule(
            condition="quality_score < 0.75",
            route_to="PLANNING",
            priority=3,
            loop_back=True,
            guidance="Significant rework needed - revisit requirements",
        ),
    ],
)

RAPID_TEMPLATE = RecursivePipelineTemplate(
    name="rapid",
    description="Rapid iteration for prototypes and quick tasks",
    quality_threshold=0.75,
    max_iterations=5,
    agent_categories={
        "planning": ["planning-analysis-strategist"],
        "development": ["senior-developer"],
        "quality": ["quality-reviewer"],
    },
    routing_rules=[
        RoutingRule(
            condition="defect_severity == 'critical'",
            route_to="QUALITY",
            priority=1,
            loop_back=True,
        ),
    ],
)

ENTERPRISE_TEMPLATE = RecursivePipelineTemplate(
    name="enterprise",
    description="Enterprise-grade pipeline with comprehensive review",
    quality_threshold=0.95,
    max_iterations=15,
    agent_categories={
        "planning": ["planning-analysis-strategist", "solutions-architect"],
        "development": ["senior-developer"],
        "quality": ["quality-reviewer", "security-auditor", "performance-analyst"],
        "decision": ["software-program-manager"],
    },
    routing_rules=[
        RoutingRule(
            condition="defect_type == 'security'",
            route_to="security-auditor",
            priority=1,
            loop_back=True,
        ),
        RoutingRule(
            condition="defect_type == 'performance'",
            route_to="performance-analyst",
            priority=2,
            loop_back=True,
        ),
    ],
)


# Template registry
RECURSIVE_TEMPLATES: Dict[str, RecursivePipelineTemplate] = {
    "generic": GENERIC_TEMPLATE,
    "rapid": RAPID_TEMPLATE,
    "enterprise": ENTERPRISE_TEMPLATE,
}


def get_recursive_template(name: str) -> RecursivePipelineTemplate:
    """
    Get a recursive pipeline template by name.

    Args:
        name: Template name

    Returns:
        RecursivePipelineTemplate instance

    Raises:
        KeyError: If template not found
    """
    if name not in RECURSIVE_TEMPLATES:
        raise KeyError(
            f"Template '{name}' not found. "
            f"Available: {list(RECURSIVE_TEMPLATES.keys())}"
        )
    return RECURSIVE_TEMPLATES[name]


# Default templates directory for YAML files
DEFAULT_TEMPLATES_DIR = (
    Path(__file__).parent.parent.parent.parent / "config" / "pipeline_templates"
)


def load_template_from_yaml(
    name: str, templates_dir: Optional[Path] = None
) -> RecursivePipelineTemplate:
    """
    Load a pipeline template from a YAML file.

    Args:
        name: Template name (filename without .yaml extension)
        templates_dir: Directory containing template YAML files.
                      Defaults to config/pipeline_templates.

    Returns:
        RecursivePipelineTemplate instance

    Raises:
        FileNotFoundError: If template YAML file not found
        yaml.YAMLError: If YAML is invalid
        ValueError: If template data is invalid
    """
    templates_dir = templates_dir or DEFAULT_TEMPLATES_DIR
    yaml_path = templates_dir / f"{name}.yaml"

    if not yaml_path.exists():
        raise FileNotFoundError(f"Template YAML file not found: {yaml_path}")

    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Template YAML must contain a dictionary: {yaml_path}")

    # Convert routing rules from list of dicts to RoutingRule objects
    routing_rules = []
    if "routing_rules" in data and data["routing_rules"]:
        for rule_data in data["routing_rules"]:
            routing_rules.append(RoutingRule(**rule_data))

    # Create template instance
    template = RecursivePipelineTemplate(
        name=data.get("name", name),
        description=data.get("description", ""),
        quality_threshold=data.get("quality_threshold", 0.90),
        max_iterations=data.get("max_iterations", 10),
        agent_categories=data.get("agent_categories", {}),
        routing_rules=routing_rules,
        quality_weights=data.get("quality_weights", {}),
    )

    return template


def load_all_templates_from_directory(
    templates_dir: Optional[Path] = None,
) -> Dict[str, RecursivePipelineTemplate]:
    """
    Load all template YAML files from a directory.

    Args:
        templates_dir: Directory containing template YAML files.
                      Defaults to config/pipeline_templates.

    Returns:
        Dictionary mapping template names to RecursivePipelineTemplate instances
    """
    templates_dir = templates_dir or DEFAULT_TEMPLATES_DIR
    templates = {}

    if not templates_dir.exists():
        return templates

    for yaml_file in templates_dir.glob("*.yaml"):
        try:
            name = yaml_file.stem
            templates[name] = load_template_from_yaml(name, templates_dir)
        except Exception as e:
            # Log warning but continue loading other templates
            print(f"Warning: Failed to load template {yaml_file}: {e}")

    return templates
