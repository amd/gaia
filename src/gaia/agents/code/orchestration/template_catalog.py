# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Template Catalog for LLM-Driven Checklist Generation.

This module defines the catalog of available templates that the LLM sees
during checklist generation. Each template definition includes:
- Name: The template identifier used in checklist items
- Description: Human-readable description for the LLM
- Parameters: Expected parameters with their types and descriptions
- Dependencies: Other templates that should be executed first
- Produces: Files/artifacts this template creates

The catalog is used by ChecklistGenerator to build the system prompt
that tells the LLM what templates are available and how to use them.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ParameterType(Enum):
    """Supported parameter types for template parameters."""

    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    LIST = "list"
    DICT = "dict"


@dataclass
class ParameterSpec:
    """Specification for a template parameter."""

    type: ParameterType
    description: str
    required: bool = True
    default: Optional[Any] = None
    example: Optional[Any] = None

    def to_prompt(self) -> str:
        """Generate prompt-friendly description of this parameter."""
        type_str = self.type.value
        req_str = "required" if self.required else "optional"
        desc = f"{type_str}, {req_str}: {self.description}"
        if self.example is not None:
            desc += f" (e.g., {self.example})"
        return desc


@dataclass
class TemplateDefinition:
    """Definition of a template for the catalog.

    Each template represents an executable unit of code generation
    that the LLM can include in its checklist.
    """

    name: str
    description: str
    parameters: Dict[str, ParameterSpec]
    dependencies: List[str] = field(default_factory=list)
    produces: List[str] = field(default_factory=list)
    category: str = "general"
    semantic_hints: List[str] = field(default_factory=list)

    def to_prompt(self) -> str:
        """Generate prompt-friendly description of this template."""
        lines = [f"### {self.name}"]
        lines.append(f"**Description**: {self.description}")

        if self.semantic_hints:
            lines.append(f"**When to use**: {', '.join(self.semantic_hints)}")

        lines.append("**Parameters**:")
        for param_name, param_spec in self.parameters.items():
            lines.append(f"  - `{param_name}`: {param_spec.to_prompt()}")

        if self.dependencies:
            lines.append(f"**Requires**: {', '.join(self.dependencies)}")

        if self.produces:
            lines.append(f"**Creates**: {', '.join(self.produces)}")

        return "\n".join(lines)


# ============================================================================
# Template Catalog Definition
# ============================================================================

TEMPLATE_CATALOG: Dict[str, TemplateDefinition] = {
    # ========== Project Setup Templates ==========
    "create_next_app": TemplateDefinition(
        name="create_next_app",
        description="Initialize a new Next.js project with TypeScript, Tailwind CSS, and App Router",
        parameters={
            "project_name": ParameterSpec(
                type=ParameterType.STRING,
                description="Name of the project (used for directory)",
                example="my-todo-app",
            ),
        },
        dependencies=[],
        produces=[
            "package.json",
            "next.config.ts",
            "src/app/layout.tsx",
            "src/app/page.tsx",
        ],
        category="setup",
        semantic_hints=["Always use this first for new Next.js projects"],
    ),
    "setup_app_styling": TemplateDefinition(
        name="setup_app_styling",
        description="Configure app-wide styling with modern dark theme design system",
        parameters={
            "app_title": ParameterSpec(
                type=ParameterType.STRING,
                description="Application title for metadata",
                example="Todo App",
            ),
            "app_description": ParameterSpec(
                type=ParameterType.STRING,
                description="Application description for metadata",
                required=False,
                default="A modern web application",
            ),
        },
        dependencies=["create_next_app"],
        produces=["src/app/layout.tsx", "src/app/globals.css"],
        category="setup",
        semantic_hints=["Sets up dark theme with glass morphism effects"],
    ),
    "setup_prisma": TemplateDefinition(
        name="setup_prisma",
        description="Install Prisma dependencies, initialize database with SQLite, and create client singleton",
        parameters={
            "database_url": ParameterSpec(
                type=ParameterType.STRING,
                description="Database connection URL",
                required=False,
                default="file:./dev.db",
            ),
        },
        dependencies=["create_next_app"],
        produces=["prisma/schema.prisma", "src/lib/prisma.ts", ".env"],
        category="setup",
        semantic_hints=[
            "Installs Prisma 5.x, @prisma/client, and Zod dependencies",
            "Runs 'npx prisma init --datasource-provider sqlite' to create schema.prisma",
            "Creates src/lib/prisma.ts singleton for database client access",
            "MUST complete before creating any Prisma models or database operations",
        ],
    ),
    "setup_testing": TemplateDefinition(
        name="setup_testing",
        description="Set up Vitest testing infrastructure with React Testing Library",
        parameters={
            "resource_name": ParameterSpec(
                type=ParameterType.STRING,
                description="Optional resource name for customized Prisma mocks",
                required=False,
            ),
        },
        dependencies=["create_next_app"],
        produces=["vitest.config.ts", "tests/setup.ts"],
        category="setup",
        semantic_hints=["Enables running tests with npm test"],
    ),
    # ========== Data Model Templates ==========
    "generate_prisma_model": TemplateDefinition(
        name="generate_prisma_model",
        description="Define a database model with fields in Prisma schema",
        parameters={
            "model_name": ParameterSpec(
                type=ParameterType.STRING,
                description="Model name in PascalCase (singular)",
                example="Todo",
            ),
            "fields": ParameterSpec(
                type=ParameterType.DICT,
                description="Field definitions as {name: type} where type is string|number|boolean|date|email|url",
                example={"title": "string", "completed": "boolean"},
            ),
        },
        dependencies=["setup_prisma"],
        produces=["prisma/schema.prisma (updated)"],
        category="data",
        semantic_hints=[
            "Use 'boolean' for todo completion, checklist items",
            "Use 'date' for blog posts, events with dates",
            "id, createdAt, updatedAt are auto-generated",
        ],
    ),
    "prisma_db_sync": TemplateDefinition(
        name="prisma_db_sync",
        description="Generate Prisma client and push schema to database (REQUIRED after generate_prisma_model)",
        parameters={},
        dependencies=["generate_prisma_model"],
        produces=["node_modules/.prisma/client", "prisma/dev.db"],
        category="data",
        semantic_hints=[
            "MUST run after generate_prisma_model before any API routes",
            "Runs 'prisma generate' to create TypeScript types",
            "Runs 'prisma db push' to create database tables",
        ],
    ),
    # ========== API Templates ==========
    "generate_api_route": TemplateDefinition(
        name="generate_api_route",
        description="Create REST API endpoints with Prisma queries and Zod validation",
        parameters={
            "resource": ParameterSpec(
                type=ParameterType.STRING,
                description="Resource name in lowercase (singular)",
                example="todo",
            ),
            "operations": ParameterSpec(
                type=ParameterType.LIST,
                description="HTTP methods to implement",
                example=["GET", "POST"],
            ),
            "type": ParameterSpec(
                type=ParameterType.STRING,
                description="Route type: 'collection' for /api/todos or 'item' for /api/todos/[id]",
                example="collection",
            ),
            "enable_pagination": ParameterSpec(
                type=ParameterType.BOOLEAN,
                description="Whether to add pagination to GET endpoint",
                required=False,
                default=False,
            ),
        },
        dependencies=["prisma_db_sync"],
        produces=[
            "src/app/api/{resource}s/route.ts",
            "src/app/api/{resource}s/[id]/route.ts",
        ],
        category="api",
        semantic_hints=[
            "Collection routes handle GET (list) and POST (create)",
            "Item routes handle GET (single), PATCH (update), DELETE",
        ],
    ),
    # ========== UI Component Templates ==========
    "generate_react_component": TemplateDefinition(
        name="generate_react_component",
        description="Create React components for displaying and managing resources",
        parameters={
            "resource": ParameterSpec(
                type=ParameterType.STRING,
                description="Resource name in lowercase (singular)",
                example="todo",
            ),
            "variant": ParameterSpec(
                type=ParameterType.STRING,
                description="Component variant: list|form|new|detail|actions",
                example="list",
            ),
            "with_checkboxes": ParameterSpec(
                type=ParameterType.BOOLEAN,
                description="Add checkbox UI for boolean fields (e.g., todo completion)",
                required=False,
                default=False,
            ),
        },
        dependencies=["generate_api_route"],
        produces=["src/app/{resource}s/page.tsx", "src/components/{Resource}Form.tsx"],
        category="ui",
        semantic_hints=[
            "Use 'list' for main page showing all items",
            "Use 'form' for reusable create/edit form component",
            "Use 'new' for /resource/new page",
            "Use 'detail' for /resource/[id] EDIT page with pre-populated form",
            "Add with_checkboxes=true for todo apps",
        ],
    ),
    "update_landing_page": TemplateDefinition(
        name="update_landing_page",
        description="Update the home page with navigation links to resource pages",
        parameters={
            "resource": ParameterSpec(
                type=ParameterType.STRING,
                description="Resource name to link to",
                example="todo",
            ),
            "description": ParameterSpec(
                type=ParameterType.STRING,
                description="Description text for the link",
                required=False,
            ),
        },
        dependencies=["generate_react_component"],
        produces=["src/app/page.tsx (updated)"],
        category="ui",
        semantic_hints=["Add after creating resource pages so users can navigate"],
    ),
    # ========== Validation Templates ==========
    "run_typescript_check": TemplateDefinition(
        name="run_typescript_check",
        description="Run TypeScript compiler to check for type errors",
        parameters={},
        dependencies=[],
        produces=[],
        category="validation",
        semantic_hints=["Use after generating code to catch type errors early"],
    ),
    "validate_styles": TemplateDefinition(
        name="validate_styles",
        description="Validate CSS files for content integrity and design system consistency",
        parameters={
            "_resource_name": ParameterSpec(
                type=ParameterType.STRING,
                description="Optional resource name for component class checks",
                required=False,
            ),
        },
        dependencies=["setup_app_styling", "run_typescript_check"],
        produces=[],
        category="validation",
        semantic_hints=[
            "Run AFTER run_typescript_check to validate styling",
            "Catches TypeScript code accidentally written to CSS files (Issue #1002)",
            "Validates globals.css has Tailwind directives",
            "Checks layout.tsx imports globals.css",
        ],
    ),
    "generate_style_tests": TemplateDefinition(
        name="generate_style_tests",
        description="Generate CSS and styling tests for the project",
        parameters={
            "resource_name": ParameterSpec(
                type=ParameterType.STRING,
                description="Resource name for component styling tests",
                required=True,
            ),
        },
        dependencies=["setup_testing", "setup_app_styling"],
        produces=["tests/styles.test.ts", "tests/styling/{Resource}Styling.test.tsx"],
        category="testing",
        semantic_hints=[
            "Generate after setup_testing to create CSS validation tests",
            "Tests check CSS file integrity (no TypeScript in CSS)",
            "Tests validate design system class definitions",
            "Tests verify layout.tsx imports globals.css",
        ],
    ),
    # ========== Remediation Templates ==========
    "fix_code": TemplateDefinition(
        name="fix_code",
        description="Use the LLM fixer to repair an existing source file based on an error description.",
        parameters={
            "file_path": ParameterSpec(
                type=ParameterType.STRING,
                description="Path to the file that needs to be fixed.",
                example="src/app/api/todos/route.ts",
            ),
            "error_description": ParameterSpec(
                type=ParameterType.STRING,
                description="Short summary of the failure or lint error that needs to be resolved.",
                required=False,
            ),
        },
        dependencies=[],
        produces=["Updated file with fixes applied"],
        category="remediation",
        semantic_hints=[
            "Use when a validation log references a specific file with errors.",
            "Provide the exact error message (TypeScript, lint, or runtime) to guide the fixer.",
        ],
    ),
}


def get_template(name: str) -> Optional[TemplateDefinition]:
    """Get a template definition by name.

    Args:
        name: Template name to look up

    Returns:
        TemplateDefinition if found, None otherwise
    """
    return TEMPLATE_CATALOG.get(name)


def get_templates_by_category(category: str) -> List[TemplateDefinition]:
    """Get all templates in a specific category.

    Args:
        category: Category name (setup, data, api, ui, testing, validation, remediation)

    Returns:
        List of templates in the category
    """
    return [t for t in TEMPLATE_CATALOG.values() if t.category == category]


def get_catalog_prompt() -> str:
    """Generate the complete template catalog prompt for LLM.

    This is included in the system prompt so the LLM knows what
    templates are available and how to use them.

    Returns:
        Formatted markdown string describing all templates
    """
    lines = ["# Available Templates", ""]
    lines.append(
        "Use these templates to generate a checklist for the user's request. "
        "Each template has specific parameters and dependencies."
    )
    lines.append("")

    # Group by category
    categories = ["setup", "data", "api", "ui", "testing", "validation", "remediation"]

    for category in categories:
        templates = get_templates_by_category(category)
        if templates:
            lines.append(f"## {category.title()} Templates")
            lines.append("")
            for template in templates:
                lines.append(template.to_prompt())
                lines.append("")

    return "\n".join(lines)


def validate_checklist_item(template_name: str, params: Dict[str, Any]) -> List[str]:
    """Validate a checklist item against the template definition.

    Args:
        template_name: Name of the template
        params: Parameters provided for the template

    Returns:
        List of validation error messages (empty if valid)
    """
    errors = []

    template = get_template(template_name)
    if not template:
        errors.append(f"Unknown template: {template_name}")
        return errors

    # Check required parameters
    for param_name, param_spec in template.parameters.items():
        if param_spec.required and param_name not in params:
            errors.append(
                f"Missing required parameter '{param_name}' for {template_name}"
            )

    # Check for unknown parameters
    valid_params = set(template.parameters.keys())
    for param_name in params:
        if param_name not in valid_params:
            errors.append(f"Unknown parameter '{param_name}' for {template_name}")

    return errors
