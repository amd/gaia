"""
GAIA Template Loader

YAML template loading and parsing for recursive pipeline configurations.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml

from gaia.agents.registry import AgentRegistry
from gaia.exceptions import AgentLoadError
from gaia.pipeline.recursive_template import (
    AgentCategory,
    PhaseConfig,
    RecursivePipelineTemplate,
    RoutingRule,
    SelectionMode,
)
from gaia.quality.models import QualityWeightConfig
from gaia.utils.logging import get_logger

logger = get_logger(__name__)


class TemplateValidationError(Exception):
    """Raised when template validation fails."""

    pass


class TemplateLoader:
    """
    YAML template loader for GAIA pipeline configurations.

    The TemplateLoader provides:
    - Load YAML template files from disk or string
    - Parse templates into RecursivePipelineTemplate objects
    - Validate template structure and agent references
    - Support for multiple templates in a single YAML file

    Example:
        >>> loader = TemplateLoader()
        >>> templates = loader.load_from_file("templates.yml")
        >>> template = templates["standard"]
        >>> print(template.name)

        >>> # Or load from string
        >>> yaml_str = '''
        ... templates:
        ...   custom:
        ...     name: "Custom Template"
        ...     configuration:
        ...       quality_threshold: 85
        ... '''
        >>> templates = loader.load_from_string(yaml_str)
    """

    # Default template path - can be overridden
    DEFAULT_TEMPLATE_DIR = Path(__file__).parent.parent.parent / "templates"

    def __init__(self, template_dir: Optional[Union[str, Path]] = None):
        """
        Initialize template loader.

        Args:
            template_dir: Directory containing template YAML files
        """
        self._template_dir = (
            Path(template_dir) if template_dir else self.DEFAULT_TEMPLATE_DIR
        )
        self._loaded_templates: Dict[str, RecursivePipelineTemplate] = {}

        logger.info(
            "TemplateLoader initialized",
            extra={"template_dir": str(self._template_dir)},
        )

    def load_from_file(
        self, file_path: Union[str, Path]
    ) -> Dict[str, RecursivePipelineTemplate]:
        """
        Load templates from a YAML file.

        Args:
            file_path: Path to YAML template file

        Returns:
            Dictionary of template name -> RecursivePipelineTemplate

        Raises:
            FileNotFoundError: If template file doesn't exist
            TemplateValidationError: If template parsing fails
        """
        file_path = Path(file_path)

        if not file_path.exists():
            raise FileNotFoundError(f"Template file not found: {file_path}")

        logger.info(f"Loading templates from {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        return self._parse_yaml(data, source=str(file_path))

    def load_from_string(
        self, yaml_string: str
    ) -> Dict[str, RecursivePipelineTemplate]:
        """
        Load templates from a YAML string.

        Args:
            yaml_string: YAML content as string

        Returns:
            Dictionary of template name -> RecursivePipelineTemplate

        Raises:
            TemplateValidationError: If template parsing fails
        """
        try:
            data = yaml.safe_load(yaml_string)
        except yaml.YAMLError as e:
            raise TemplateValidationError(f"Invalid YAML: {e}")

        return self._parse_yaml(data, source="string")

    def load_template(
        self,
        template_name: str,
        file_path: Optional[Union[str, Path]] = None,
    ) -> RecursivePipelineTemplate:
        """
        Load a single template by name.

        Args:
            template_name: Name of template to load
            file_path: Optional specific file to load from

        Returns:
            RecursivePipelineTemplate instance

        Raises:
            KeyError: If template not found
            FileNotFoundError: If template file doesn't exist
        """
        # Check cache first
        if template_name in self._loaded_templates:
            logger.debug(f"Template '{template_name}' found in cache")
            return self._loaded_templates[template_name]

        # Load from specified file or default directory
        if file_path:
            templates = self.load_from_file(file_path)
        else:
            # Search in template directory
            templates = self._load_all_templates()

        if template_name not in templates:
            raise KeyError(
                f"Template '{template_name}' not found. "
                f"Available: {list(templates.keys())}"
            )

        # Cache the loaded template
        self._loaded_templates[template_name] = templates[template_name]
        logger.info(f"Loaded template: {template_name}")

        return templates[template_name]

    def _load_all_templates(self) -> Dict[str, RecursivePipelineTemplate]:
        """Load all templates from template directory."""
        all_templates = {}

        if not self._template_dir.exists():
            logger.warning(f"Template directory not found: {self._template_dir}")
            return all_templates

        yaml_files = list(self._template_dir.glob("*.yml"))
        yaml_files.extend(self._template_dir.glob("*.yaml"))

        for yaml_file in yaml_files:
            try:
                templates = self.load_from_file(yaml_file)
                all_templates.update(templates)
                logger.debug(f"Loaded {len(templates)} templates from {yaml_file.name}")
            except Exception as e:
                logger.error(f"Failed to load templates from {yaml_file}: {e}")

        return all_templates

    def _parse_yaml(
        self,
        data: Dict[str, Any],
        source: str = "unknown",
    ) -> Dict[str, RecursivePipelineTemplate]:
        """
        Parse YAML data into template objects.

        Args:
            data: Parsed YAML data
            source: Source identifier for logging

        Returns:
            Dictionary of template name -> RecursivePipelineTemplate

        Raises:
            TemplateValidationError: If parsing fails
        """
        if not data:
            raise TemplateValidationError(f"Empty YAML content from {source}")

        templates = {}

        # Extract agent categories (top-level definition)
        agent_categories_def = data.get("agent_categories", {})

        # Extract templates section
        templates_data = data.get("templates", {})

        if not templates_data:
            raise TemplateValidationError(f"No 'templates' section found in {source}")

        for template_name, template_config in templates_data.items():
            try:
                template = self._build_template(
                    name=template_name,
                    config=template_config,
                    agent_categories_def=agent_categories_def,
                )
                templates[template_name] = template
                logger.debug(f"Parsed template: {template_name}")
            except Exception as e:
                logger.error(f"Failed to parse template '{template_name}': {e}")
                raise TemplateValidationError(
                    f"Error parsing template '{template_name}': {e}"
                )

        return templates

    def _build_template(
        self,
        name: str,
        config: Dict[str, Any],
        agent_categories_def: Dict[str, Any],
    ) -> RecursivePipelineTemplate:
        """
        Build RecursivePipelineTemplate from config.

        Args:
            name: Template name
            config: Template configuration dictionary
            agent_categories_def: Agent category definitions

        Returns:
            RecursivePipelineTemplate instance
        """
        # Extract configuration
        configuration = config.get("configuration", {})
        quality_threshold = configuration.get("quality_threshold", 0.90)
        # Only divide by 100 if value is in percentage scale (> 1.0)
        if quality_threshold > 1.0:
            quality_threshold = quality_threshold / 100.0
        max_iterations = configuration.get("max_iterations", 10)

        # Extract description
        description = config.get("description", "")

        # Build agent categories mapping
        agent_categories = self._build_agent_categories(
            phases=config.get("phases", []),
            agent_categories_def=agent_categories_def,
        )

        # Build phases
        phases = self._build_phases(config.get("phases", []))

        # Build routing rules
        routing_rules = self._build_routing_rules(
            config.get("routing_rules", []),
            agent_categories_def=agent_categories_def,
        )

        # Extract quality weights and build QualityWeightConfig
        quality_weights_data = config.get("quality_weights", {})
        weight_config = None
        quality_weights = {}

        if quality_weights_data:
            # Handle both simple dict format and full QualityWeightConfig format
            if isinstance(quality_weights_data, dict):
                if "weights" in quality_weights_data:
                    # Full format with name, weights, category_overrides
                    weight_config = QualityWeightConfig(
                        name=quality_weights_data.get("name", f"{name}_weights"),
                        weights=quality_weights_data.get("weights", {}),
                        category_overrides=quality_weights_data.get(
                            "category_overrides", {}
                        ),
                        description=quality_weights_data.get("description", ""),
                    )
                    weight_config.validate()
                    quality_weights = weight_config.weights.copy()
                else:
                    # Simple format - just weights dict
                    quality_weights = quality_weights_data
                    weight_config = QualityWeightConfig(
                        name=f"{name}_weights",
                        weights=quality_weights,
                        description=f"Weight config for template {name}",
                    )

        return RecursivePipelineTemplate(
            name=name,
            description=description,
            quality_threshold=quality_threshold,
            max_iterations=max_iterations,
            agent_categories=agent_categories,
            phases=phases,
            routing_rules=routing_rules,
            quality_weights=quality_weights,
            weight_config=weight_config,
        )

    def _build_agent_categories(
        self,
        phases: List[Dict[str, Any]],
        agent_categories_def: Dict[str, Any],
    ) -> Dict[str, List[str]]:
        """
        Build agent categories mapping from phase definitions.

        Args:
            phases: Phase configurations
            agent_categories_def: Agent category definitions from top-level YAML

        Returns:
            Dictionary mapping category name to agent IDs
        """
        categories = {}

        # First, populate categories from agent_categories_def (top-level definition)
        # This supports both simple list format and detailed object format
        for category_name, category_config in agent_categories_def.items():
            cat_lower = category_name.lower()
            if isinstance(category_config, list):
                # Simple format: list of agent IDs
                categories[cat_lower] = [str(a) for a in category_config if a]
            elif isinstance(category_config, dict):
                # Detailed format: dict with 'agents' key or list of objects with 'id' key
                if "agents" in category_config:
                    categories[cat_lower] = [
                        str(a) for a in category_config["agents"] if a
                    ]
                else:
                    # List of objects with 'id' field
                    agents = category_config.get(
                        "items", category_config.get("agents_list", [])
                    )
                    if (
                        isinstance(agents, list)
                        and len(agents) > 0
                        and isinstance(agents[0], dict)
                    ):
                        categories[cat_lower] = [
                            str(agent.get("id", ""))
                            for agent in agents
                            if agent.get("id")
                        ]
                    else:
                        categories[cat_lower] = [str(a) for a in agents if a]

        # Then, merge/override with phase-based categories
        # Phases can add agents to existing categories or create new ones
        for phase in phases:
            category = phase.get("category", "")
            agents = phase.get("agents", [])

            if category and agents:
                cat_lower = category.lower()
                phase_agents = [str(a) for a in agents if a]

                # Merge with existing category if present, otherwise create new
                if cat_lower in categories:
                    # Merge unique agents from both sources
                    existing = set(categories[cat_lower])
                    merged = list(existing)
                    for agent in phase_agents:
                        if agent not in existing:
                            merged.append(agent)
                    categories[cat_lower] = merged
                else:
                    categories[cat_lower] = phase_agents

        return categories

    def _build_phases(self, phases_config: List[Dict[str, Any]]) -> List[PhaseConfig]:
        """
        Build PhaseConfig list from YAML config.

        Args:
            phases_config: List of phase configurations

        Returns:
            List of PhaseConfig objects
        """
        phases = []

        for phase_config in phases_config:
            category_str = phase_config.get("category", "")
            selection_str = phase_config.get("selection", "auto")
            agents = phase_config.get("agents", [])
            output = phase_config.get("output", "")

            # Map category string to enum
            try:
                category = AgentCategory[category_str.upper()]
            except KeyError:
                # Default to PLANNING if unknown
                logger.warning(
                    f"Unknown category '{category_str}', defaulting to PLANNING"
                )
                category = AgentCategory.PLANNING

            # Map selection mode
            selection_mode = SelectionMode.AUTO
            if selection_str.lower() == "sequential":
                selection_mode = SelectionMode.SEQUENTIAL
            elif selection_str.lower() == "parallel":
                selection_mode = SelectionMode.PARALLEL

            # Build exit criteria from output
            exit_criteria = {}
            if output:
                exit_criteria["artifact"] = output

            phases.append(
                PhaseConfig(
                    name=category.value.upper(),
                    category=category,
                    selection_mode=selection_mode,
                    agents=agents,
                    exit_criteria=exit_criteria,
                )
            )

        return phases

    def _build_routing_rules(
        self,
        rules_config: List[Dict[str, Any]],
        agent_categories_def: Dict[str, Any],
    ) -> List[RoutingRule]:
        """
        Build RoutingRule list from YAML config.

        Args:
            rules_config: List of routing rule configurations
            agent_categories_def: Agent category definitions

        Returns:
            List of RoutingRule objects
        """
        rules = []

        for rule_config in rules_config:
            condition = rule_config.get("condition", "")
            route_to = rule_config.get("route_to", {})
            guidance = rule_config.get("guidance", None)
            loop_back = rule_config.get("loop_back", False)
            priority = rule_config.get("priority", 0)

            # Handle route_to being a dict with category/agent or just a string
            if isinstance(route_to, dict):
                route_target = route_to.get("agent", route_to.get("category", ""))
            else:
                route_target = str(route_to)

            rules.append(
                RoutingRule(
                    condition=condition,
                    route_to=route_target,
                    priority=priority,
                    loop_back=loop_back,
                    guidance=guidance,
                )
            )

        return rules

    def validate_template(
        self,
        template: RecursivePipelineTemplate,
        agent_registry: AgentRegistry,
    ) -> List[str]:
        """
        Validate template against agent registry.

        Checks that all referenced agents exist in the registry.

        Args:
            template: Template to validate
            agent_registry: Agent registry for lookups

        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []

        # Validate agents in agent_categories
        for category, agent_ids in template.agent_categories.items():
            for agent_id in agent_ids:
                if not agent_registry.get_agent(agent_id):
                    errors.append(
                        f"Agent '{agent_id}' not found in category '{category}'"
                    )

        # Validate agents in phases
        for phase in template.phases:
            for agent_id in phase.agents:
                if not agent_registry.get_agent(agent_id):
                    errors.append(
                        f"Agent '{agent_id}' not found in phase '{phase.name}'"
                    )

        # Validate routing rules reference valid agents
        for rule in template.routing_rules:
            if not agent_registry.get_agent(rule.route_to):
                # Check if it's a category reference
                if rule.route_to.upper() not in AgentCategory.__members__:
                    errors.append(
                        f"Routing rule references unknown agent/category '{rule.route_to}'"
                    )

        # Validate quality threshold
        if not 0 <= template.quality_threshold <= 1:
            errors.append(
                f"Invalid quality_threshold: {template.quality_threshold} (must be 0-1)"
            )

        # Validate max iterations
        if template.max_iterations < 1:
            errors.append(
                f"Invalid max_iterations: {template.max_iterations} (must be >= 1)"
            )

        if errors:
            logger.warning(
                f"Template validation failed with {len(errors)} errors",
                extra={"errors": errors},
            )
        else:
            logger.info(f"Template '{template.name}' validated successfully")

        return errors

    def get_available_templates(
        self,
        file_path: Optional[Union[str, Path]] = None,
    ) -> List[str]:
        """
        Get list of available template names.

        Args:
            file_path: Optional specific file to scan

        Returns:
            List of template names
        """
        if file_path:
            templates = self.load_from_file(file_path)
            return list(templates.keys())

        templates = self._load_all_templates()
        return list(templates.keys())

    def clear_cache(self) -> None:
        """Clear cached templates."""
        self._loaded_templates.clear()
        logger.debug("Template cache cleared")
