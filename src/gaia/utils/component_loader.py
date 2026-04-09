# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
GAIA Component Loader

Load and manage Component Framework templates.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional
import re
import yaml


class ComponentLoaderError(Exception):
    """Exception raised when component loading fails.

    This exception is raised for various component loading errors including
    missing files, invalid frontmatter, and validation failures.

    Attributes:
        message: The error message.
        component_path: The path to the component that caused the error.
        cause: The original exception that caused this error (if any).
    """

    def __init__(
        self,
        message: str,
        component_path: Optional[str] = None,
        cause: Optional[Exception] = None
    ):
        self.message = message
        self.component_path = component_path
        self.cause = cause

        error_msg = message
        if component_path:
            error_msg = f"{message} (component: {component_path})"
        if cause:
            error_msg = f"{error_msg} - Cause: {cause}"

        super().__init__(error_msg)


class ComponentLoader:
    """Component Framework template loader.

    The ComponentLoader provides:
    - Load component templates from component-framework/ directory
    - Parse YAML frontmatter
    - Render templates with variable substitution
    - Validate template structure
    - List available components by type

    Example Usage:
        ```python
        from gaia.utils.component_loader import ComponentLoader

        loader = ComponentLoader()

        # Load a component
        component = loader.load_component("memory/working-memory.md")
        print(component["frontmatter"]["template_id"])

        # Render with variables
        rendered = loader.render_component(
            "memory/working-memory.md",
            {"{{AGENT_ID}}": "domain-analyzer", "{{TIMESTAMP}}": "2026-04-07T10:00:00Z"}
        )

        # List components
        all_components = loader.list_components()
        memory_components = loader.list_components("memory")

        # Validate
        errors = loader.validate_component("memory/working-memory.md")
        if errors:
            print(f"Validation errors: {errors}")
        ```
    """

    # Valid template types
    VALID_TEMPLATE_TYPES = [
        "memory",
        "knowledge",
        "tasks",
        "commands",
        "documents",
        "checklists",
        "personas",
        "workflows",
        "templates"
    ]

    # Required frontmatter fields
    REQUIRED_FRONTMATTER_FIELDS = [
        "template_id",
        "template_type",
        "version",
        "description"
    ]

    def __init__(self, framework_dir: Optional[Path] = None):
        """Initialize component loader.

        Args:
            framework_dir: Path to component-framework/ directory.
                Defaults to Path("component-framework") relative to current working directory.
        """
        self._framework_dir = framework_dir or Path("component-framework")
        self._loaded_components: Dict[str, Any] = {}

    @property
    def framework_dir(self) -> Path:
        """Get the framework directory path.

        Returns:
            Path to the component-framework directory.
        """
        return self._framework_dir

    def load_component(self, component_path: str) -> Dict[str, Any]:
        """Load a component template.

        Args:
            component_path: Relative path within component-framework/
                (e.g., "memory/working-memory.md")

        Returns:
            Dictionary with keys:
            - path: The component path
            - frontmatter: Parsed YAML frontmatter dictionary
            - content: The markdown body content

        Raises:
            ComponentLoaderError: If component not found, frontmatter is missing
                or invalid, or file cannot be read.
        """
        # Check cache first
        if component_path in self._loaded_components:
            return self._loaded_components[component_path]

        full_path = self._framework_dir / component_path

        if not full_path.exists():
            raise ComponentLoaderError(
                f"Component not found",
                component_path=component_path
            )

        if not full_path.is_file():
            raise ComponentLoaderError(
                f"Component path is not a file",
                component_path=component_path
            )

        try:
            with open(full_path, "r", encoding="utf-8-sig") as f:
                content = f.read()
        except IOError as e:
            raise ComponentLoaderError(
                f"Failed to read component file",
                component_path=component_path,
                cause=e
            )

        # Normalize line endings
        content = content.replace("\r\n", "\n").replace("\r", "\n")

        # Parse frontmatter
        if not content.startswith("---\n"):
            raise ComponentLoaderError(
                "Missing frontmatter delimiter (---)",
                component_path=component_path
            )

        # Find the closing --- delimiter (must be on its own line)
        # Look for \n---\n pattern after the first line
        rest_of_content = content[4:]  # Skip the opening "---\n"
        closing_delim_pos = rest_of_content.find("\n---\n")

        if closing_delim_pos == -1:
            raise ComponentLoaderError(
                "Invalid frontmatter format - missing closing delimiter",
                component_path=component_path
            )

        frontmatter_text = rest_of_content[:closing_delim_pos]
        body = rest_of_content[closing_delim_pos + 5:].strip()  # Skip "\n---\n"

        try:
            frontmatter = yaml.safe_load(frontmatter_text)
        except yaml.YAMLError as e:
            raise ComponentLoaderError(
                f"Invalid YAML in frontmatter",
                component_path=component_path,
                cause=e
            )

        if not isinstance(frontmatter, dict):
            raise ComponentLoaderError(
                f"Frontmatter must be a YAML dictionary",
                component_path=component_path
            )

        result = {
            "path": component_path,
            "frontmatter": frontmatter,
            "content": body
        }

        # Cache the loaded component
        self._loaded_components[component_path] = result

        return result

    def render_component(
        self,
        component_path: str,
        variables: Dict[str, str],
    ) -> str:
        """Render a component template with variable substitution.

        Args:
            component_path: Relative path within component-framework/
            variables: Dictionary of variable mappings. Keys can be with or
                without braces (e.g., "AGENT_ID" or "{{AGENT_ID}}").

        Returns:
            Rendered template content as string.

        Raises:
            ComponentLoaderError: If component cannot be loaded.
        """
        component = self.load_component(component_path)
        content = component["content"]

        # Replace {{VARIABLE}} placeholders
        for key, value in variables.items():
            # Handle both "KEY" and "{{KEY}}" formats
            if not key.startswith("{{"):
                key = f"{{{{{key}}}}}"

            content = content.replace(key, str(value))

        return content

    def list_components(self, component_type: Optional[str] = None) -> List[str]:
        """List available components.

        Args:
            component_type: Optional filter by type (memory, knowledge, tasks, etc.)
                If None, returns all components.

        Returns:
            List of component paths (relative to framework directory).
            Sorted alphabetically.

        Raises:
            ComponentLoaderError: If an invalid component_type is provided.
        """
        if component_type and component_type not in self.VALID_TEMPLATE_TYPES:
            raise ComponentLoaderError(
                f"Invalid component_type: {component_type}. "
                f"Must be one of: {self.VALID_TEMPLATE_TYPES}"
            )

        if not self._framework_dir.exists():
            return []

        components = []
        for md_file in self._framework_dir.rglob("*.md"):
            # Use POSIX-style path separators for consistency
            rel_path = md_file.relative_to(self._framework_dir).as_posix()

            if component_type:
                # Check if component type matches directory
                if rel_path.startswith(component_type + "/") or rel_path.startswith(component_type + "\\"):
                    components.append(rel_path)
            else:
                components.append(rel_path)

        return sorted(components)

    def validate_component(self, component_path: str) -> List[str]:
        """Validate a component template.

        Args:
            component_path: Relative path within component-framework/

        Returns:
            List of validation error messages. Empty list if valid.

        Raises:
            ComponentLoaderError: If component cannot be loaded.
        """
        errors = []

        try:
            component = self.load_component(component_path)
        except ComponentLoaderError as e:
            return [str(e)]

        frontmatter = component["frontmatter"]

        # Check required fields
        for field in self.REQUIRED_FRONTMATTER_FIELDS:
            if field not in frontmatter:
                errors.append(f"Missing required frontmatter field: {field}")

        # Validate template_type value
        if "template_type" in frontmatter:
            template_type = frontmatter["template_type"]
            if template_type not in self.VALID_TEMPLATE_TYPES:
                errors.append(
                    f"Invalid template_type: {template_type}. "
                    f"Must be one of: {self.VALID_TEMPLATE_TYPES}"
                )

        # Validate version format (semver)
        if "version" in frontmatter:
            version = frontmatter["version"]
            if not isinstance(version, str):
                version = str(version)
            # Basic semver check (major.minor.patch)
            semver_pattern = r"^\d+\.\d+\.\d+(-[a-zA-Z0-9]+)?(\+[a-zA-Z0-9]+)?$"
            if not re.match(semver_pattern, version):
                errors.append(
                    f"Version should follow semver format (e.g., 1.0.0): {version}"
                )

        # Validate template_id format
        if "template_id" in frontmatter:
            template_id = frontmatter["template_id"]
            if not isinstance(template_id, str):
                errors.append("template_id must be a string")
            elif not template_id:
                errors.append("template_id cannot be empty")
            elif not re.match(r"^[a-z0-9-]+$", template_id):
                errors.append(
                    f"template_id should be lowercase alphanumeric with hyphens: {template_id}"
                )

        # Check that content is not empty
        if not component["content"].strip():
            errors.append("Component body content is empty")

        return errors

    def get_component_metadata(self, component_path: str) -> Dict[str, Any]:
        """Get metadata for a component without loading full content.

        Args:
            component_path: Relative path within component-framework/

        Returns:
            Dictionary with component metadata including:
            - template_id
            - template_type
            - version
            - description
            - path

        Raises:
            ComponentLoaderError: If component cannot be loaded.
        """
        component = self.load_component(component_path)
        frontmatter = component["frontmatter"]

        return {
            "template_id": frontmatter.get("template_id", "unknown"),
            "template_type": frontmatter.get("template_type", "unknown"),
            "version": frontmatter.get("version", "unknown"),
            "description": frontmatter.get("description", ""),
            "path": component_path
        }

    def save_component(
        self,
        component_path: str,
        content: str,
        frontmatter: Optional[Dict[str, Any]] = None
    ) -> str:
        """Save a component template.

        Args:
            component_path: Relative path within component-framework/
            content: Markdown content for the component body
            frontmatter: Optional dictionary of frontmatter fields

        Returns:
            Full path to saved component

        Raises:
            ComponentLoaderError: If component cannot be saved
        """
        import logging

        logger = logging.getLogger(__name__)

        try:
            # Validate frontmatter if provided
            if frontmatter:
                required_fields = ["template_id", "template_type", "version", "description"]
                for field in required_fields:
                    if field not in frontmatter:
                        raise ComponentLoaderError(
                            f"Missing required frontmatter field: {field}",
                            component_path=component_path
                        )

            full_path = self._framework_dir / component_path

            # Create parent directories if they don't exist
            full_path.parent.mkdir(parents=True, exist_ok=True)

            # Build the full content with frontmatter
            if frontmatter:
                frontmatter_yaml = yaml.dump(
                    frontmatter,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False
                )
                full_content = f"---\n{frontmatter_yaml}---\n{content}"
            else:
                full_content = content

            # Write to file
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(full_content)

            # Invalidate cache for this component
            if component_path in self._loaded_components:
                del self._loaded_components[component_path]

            logger.debug(f"Saved component: {component_path} -> {full_path}")
            return str(full_path)
        except IOError as e:
            raise ComponentLoaderError(
                f"Failed to write component file",
                component_path=component_path,
                cause=e
            )
        except Exception as e:
            raise ComponentLoaderError(
                str(e),
                component_path=component_path
            )

    def clear_cache(self) -> None:
        """Clear the loaded components cache.

        Use this to force reload of components from disk.
        """
        self._loaded_components.clear()

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about loaded components.

        Returns:
            Dictionary with statistics including:
            - total_loaded: Number of components in cache
            - by_type: Count by template type
        """
        stats = {
            "total_loaded": len(self._loaded_components),
            "by_type": {}
        }

        for component in self._loaded_components.values():
            template_type = component["frontmatter"].get("template_type", "unknown")
            stats["by_type"][template_type] = stats["by_type"].get(template_type, 0) + 1

        return stats
