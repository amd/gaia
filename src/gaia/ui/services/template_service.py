# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Template service for pipeline template management.

Provides business logic for loading, saving, and validating pipeline templates.
"""

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from gaia.ui.schemas.pipeline_templates import (
    PipelineTemplateSchema,
    RoutingRuleSchema,
)

logger = logging.getLogger(__name__)

# Default templates directory
DEFAULT_TEMPLATES_DIR = (
    Path(__file__).parent.parent.parent.parent.parent / "config" / "pipeline_templates"
)


class TemplateValidationError(Exception):
    """Exception raised when template validation fails."""

    def __init__(self, errors: List[str]):
        self.errors = errors
        super().__init__(f"Template validation failed: {', '.join(errors)}")


class TemplateService:
    """
    Service for managing pipeline templates.

    The TemplateService provides:
    - Load templates from YAML files
    - Save templates to YAML files
    - Validate template structure
    - List available templates
    - Create, read, update, delete operations
    - Path traversal prevention for security
    """

    def __init__(self, templates_dir: Optional[Path] = None):
        """
        Initialize template service.

        Args:
            templates_dir: Directory containing template YAML files.
                          Defaults to config/pipeline_templates.
        """
        self.templates_dir = templates_dir or DEFAULT_TEMPLATES_DIR
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("TemplateService initialized with dir: %s", self.templates_dir)

    def _sanitize_template_name(self, name: str) -> str:
        """
        Sanitize template name to prevent path traversal.

        Args:
            name: Template name to sanitize

        Returns:
            Sanitized name with only safe characters

        Raises:
            ValueError: If name contains invalid characters
        """
        if not name:
            raise ValueError("Template name cannot be empty")

        # Only allow alphanumeric, underscore, hyphen
        if not re.match(r"^[a-zA-Z0-9_-]+$", name):
            raise ValueError(
                "Template name can only contain letters, numbers, underscores, and hyphens"
            )

        # Prevent path traversal
        if ".." in name or "/" in name or "\\" in name:
            raise ValueError("Invalid template name")

        return name

    def _get_template_path(self, name: str) -> Path:
        """
        Get the full path for a template file.

        Args:
            name: Template name

        Returns:
            Full path to template YAML file

        Raises:
            ValueError: If name is invalid
        """
        sanitized = self._sanitize_template_name(name)
        return self.templates_dir / f"{sanitized}.yaml"

    def list_templates(self) -> List[PipelineTemplateSchema]:
        """
        List all available templates.

        Returns:
            List of PipelineTemplateSchema objects
        """
        templates = []

        if not self.templates_dir.exists():
            return templates

        for yaml_file in self.templates_dir.glob("*.yaml"):
            try:
                template = self._load_yaml_file(yaml_file)
                if template:
                    templates.append(template)
            except Exception as e:
                logger.warning("Failed to load template %s: %s", yaml_file, e)

        return templates

    def get_template(self, name: str) -> PipelineTemplateSchema:
        """
        Get a template by name.

        Args:
            name: Template name

        Returns:
            PipelineTemplateSchema object

        Raises:
            FileNotFoundError: If template not found
            TemplateValidationError: If template is invalid
        """
        template_path = self._get_template_path(name)

        if not template_path.exists():
            raise FileNotFoundError(f"Template '{name}' not found")

        return self._load_yaml_file(template_path)

    def get_template_raw(self, name: str) -> str:
        """
        Get raw YAML content for a template.

        Args:
            name: Template name

        Returns:
            Raw YAML content as string

        Raises:
            FileNotFoundError: If template not found
        """
        template_path = self._get_template_path(name)

        if not template_path.exists():
            raise FileNotFoundError(f"Template '{name}' not found")

        return template_path.read_text(encoding="utf-8")

    def _load_yaml_file(self, path: Path) -> PipelineTemplateSchema:
        """
        Load and parse a YAML template file.

        Args:
            path: Path to YAML file

        Returns:
            PipelineTemplateSchema object

        Raises:
            TemplateValidationError: If YAML is invalid
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)

            if not isinstance(data, dict):
                raise TemplateValidationError(["Template must be a YAML dictionary"])

            # Convert routing rules if present
            routing_rules = []
            if "routing_rules" in data and data["routing_rules"]:
                for rule in data["routing_rules"]:
                    routing_rules.append(RoutingRuleSchema(**rule))

            # Build schema
            schema = PipelineTemplateSchema(
                name=data.get("name", path.stem),
                description=data.get("description", ""),
                quality_threshold=data.get("quality_threshold", 0.90),
                max_iterations=data.get("max_iterations", 10),
                agent_categories=data.get("agent_categories", {}),
                routing_rules=routing_rules,
                quality_weights=data.get("quality_weights", {}),
            )

            return schema

        except yaml.YAMLError as e:
            raise TemplateValidationError([f"Invalid YAML: {str(e)}"])
        except TemplateValidationError:
            raise
        except Exception as e:
            raise TemplateValidationError([f"Failed to load template: {str(e)}"])

    def create_template(
        self,
        name: str,
        description: str = "",
        quality_threshold: float = 0.90,
        max_iterations: int = 10,
        agent_categories: Optional[Dict[str, List[str]]] = None,
        routing_rules: Optional[List[Dict[str, Any]]] = None,
        quality_weights: Optional[Dict[str, float]] = None,
    ) -> PipelineTemplateSchema:
        """
        Create a new template.

        Args:
            name: Template name
            description: Template description
            quality_threshold: Quality threshold (0-1)
            max_iterations: Maximum iterations
            agent_categories: Map of categories to agent lists
            routing_rules: List of routing rules
            quality_weights: Quality dimension weights

        Returns:
            Created PipelineTemplateSchema

        Raises:
            ValueError: If template already exists or validation fails
        """
        template_path = self._get_template_path(name)

        if template_path.exists():
            raise ValueError(f"Template '{name}' already exists")

        # Build data structure
        data = {
            "name": name,
            "description": description,
            "quality_threshold": quality_threshold,
            "max_iterations": max_iterations,
        }

        if agent_categories:
            data["agent_categories"] = agent_categories

        if routing_rules:
            data["routing_rules"] = routing_rules

        if quality_weights:
            data["quality_weights"] = quality_weights

        # Validate by creating schema
        schema = self._validate_data(data)

        # Save to file
        self._save_yaml_file(template_path, data)
        logger.info("Created template: %s", name)

        return schema

    def update_template(
        self,
        name: str,
        description: Optional[str] = None,
        quality_threshold: Optional[float] = None,
        max_iterations: Optional[int] = None,
        agent_categories: Optional[Dict[str, List[str]]] = None,
        routing_rules: Optional[List[Dict[str, Any]]] = None,
        quality_weights: Optional[Dict[str, float]] = None,
    ) -> PipelineTemplateSchema:
        """
        Update an existing template.

        Args:
            name: Template name
            description: Template description
            quality_threshold: Quality threshold (0-1)
            max_iterations: Maximum iterations
            agent_categories: Map of categories to agent lists
            routing_rules: List of routing rules
            quality_weights: Quality dimension weights

        Returns:
            Updated PipelineTemplateSchema

        Raises:
            FileNotFoundError: If template not found
            TemplateValidationError: If updated template is invalid
        """
        template_path = self._get_template_path(name)

        if not template_path.exists():
            raise FileNotFoundError(f"Template '{name}' not found")

        # Load existing template
        existing = self._load_yaml_file(template_path)

        # Update fields (only update if not None)
        data = {
            "name": existing.name,
            "description": (
                description if description is not None else existing.description
            ),
            "quality_threshold": (
                quality_threshold
                if quality_threshold is not None
                else existing.quality_threshold
            ),
            "max_iterations": (
                max_iterations
                if max_iterations is not None
                else existing.max_iterations
            ),
            "agent_categories": (
                agent_categories
                if agent_categories is not None
                else existing.agent_categories
            ),
            "routing_rules": (
                [r.model_dump() for r in existing.routing_rules]
                if routing_rules is None
                else routing_rules
            ),
            "quality_weights": (
                quality_weights
                if quality_weights is not None
                else existing.quality_weights
            ),
        }

        # Validate updated data
        self._validate_data(data)

        # Save to file
        self._save_yaml_file(template_path, data)
        logger.info("Updated template: %s", name)

        # Reload and return
        return self._load_yaml_file(template_path)

    def delete_template(self, name: str) -> bool:
        """
        Delete a template.

        Args:
            name: Template name

        Returns:
            True if deleted, False if not found

        Raises:
            ValueError: If name is invalid
        """
        template_path = self._get_template_path(name)

        if not template_path.exists():
            return False

        template_path.unlink()
        logger.info("Deleted template: %s", name)
        return True

    def validate_template(self, name: str) -> Tuple[bool, List[str], List[str]]:
        """
        Validate a template.

        Args:
            name: Template name

        Returns:
            Tuple of (is_valid, errors, warnings)

        Raises:
            FileNotFoundError: If template not found
        """
        template_path = self._get_template_path(name)

        if not template_path.exists():
            raise FileNotFoundError(f"Template '{name}' not found")

        errors = []
        warnings = []

        try:
            with open(template_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            return (False, [f"Invalid YAML syntax: {str(e)}"], [])

        if not isinstance(data, dict):
            return (False, ["Template must be a YAML dictionary"], [])

        # Required fields check
        if "name" not in data:
            warnings.append("Missing 'name' field (using filename)")

        # Validate quality_threshold
        threshold = data.get("quality_threshold", 0.90)
        if not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
            errors.append("quality_threshold must be a number between 0 and 1")

        # Validate max_iterations
        max_iter = data.get("max_iterations", 10)
        if not isinstance(max_iter, int) or max_iter < 1:
            errors.append("max_iterations must be a positive integer")

        # Validate agent_categories structure
        agent_cats = data.get("agent_categories", {})
        if not isinstance(agent_cats, dict):
            errors.append("agent_categories must be a dictionary")
        else:
            for cat_name, agents in agent_cats.items():
                if not isinstance(agents, list):
                    errors.append(f"agent_categories.{cat_name} must be a list")

        # Validate routing_rules structure
        routing_rules = data.get("routing_rules", [])
        if not isinstance(routing_rules, list):
            errors.append("routing_rules must be a list")
        else:
            for i, rule in enumerate(routing_rules):
                if not isinstance(rule, dict):
                    errors.append(f"routing_rules[{i}] must be a dictionary")
                elif "condition" not in rule:
                    errors.append(f"routing_rules[{i}] missing 'condition' field")
                elif "route_to" not in rule:
                    errors.append(f"routing_rules[{i}] missing 'route_to' field")

        # Validate quality_weights sum to ~1.0
        weights = data.get("quality_weights", {})
        if weights:
            if not isinstance(weights, dict):
                errors.append("quality_weights must be a dictionary")
            else:
                total = sum(weights.values())
                if abs(total - 1.0) > 0.05:
                    errors.append(f"quality_weights must sum to 1.0, got {total}")

        # Check for empty agent categories (warning only)
        if agent_cats and not any(agent_cats.values()):
            warnings.append("No agents defined in any category")

        return (len(errors) == 0, errors, warnings)

    def _validate_data(self, data: Dict[str, Any]) -> PipelineTemplateSchema:
        """
        Validate template data by creating a schema.

        Args:
            data: Template data dictionary

        Returns:
            Validated PipelineTemplateSchema

        Raises:
            TemplateValidationError: If validation fails
        """
        try:
            routing_rules = []
            if "routing_rules" in data and data["routing_rules"]:
                for rule in data["routing_rules"]:
                    routing_rules.append(RoutingRuleSchema(**rule))

            return PipelineTemplateSchema(
                name=data.get("name", ""),
                description=data.get("description", ""),
                quality_threshold=data.get("quality_threshold", 0.90),
                max_iterations=data.get("max_iterations", 10),
                agent_categories=data.get("agent_categories", {}),
                routing_rules=routing_rules,
                quality_weights=data.get("quality_weights", {}),
            )
        except Exception as e:
            raise TemplateValidationError([str(e)])

    def _save_yaml_file(self, path: Path, data: Dict[str, Any]) -> None:
        """
        Save data to a YAML file.

        Args:
            path: Path to save to
            data: Data to save
        """
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(
                data, f, default_flow_style=False, sort_keys=False, allow_unicode=True
            )
