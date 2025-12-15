# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Project Analyzer for Understanding Current Project State.

This module analyzes the current state of a project directory to provide
context for the LLM during checklist generation. It detects:
- Whether the project exists
- What framework/tools are configured
- What models/routes/pages already exist

This information helps the LLM generate a checklist that:
- Doesn't recreate things that already exist
- Builds on existing infrastructure
- Fills in missing pieces
"""

import json
import logging
import re
from pathlib import Path
from typing import List

from .checklist_generator import ProjectState

logger = logging.getLogger(__name__)


class ProjectAnalyzer:
    """Analyze project state for LLM context.

    Provides detailed information about what exists in a project
    so the LLM can generate appropriate checklists.
    """

    def analyze(self, project_dir: str) -> ProjectState:
        """Analyze a project directory.

        Args:
            project_dir: Path to project directory

        Returns:
            ProjectState with analysis results
        """
        project_path = Path(project_dir)

        if not project_path.exists():
            logger.info(f"Project directory does not exist: {project_dir}")
            return ProjectState(exists=False)

        state = ProjectState(exists=True)

        # Check for package.json (Node.js project)
        package_json = project_path / "package.json"
        if package_json.exists():
            state.has_package_json = True
            self._analyze_package_json(package_json, state)

        # Check for Next.js config
        next_config = project_path / "next.config.ts"
        if not next_config.exists():
            next_config = project_path / "next.config.js"
        if not next_config.exists():
            next_config = project_path / "next.config.mjs"
        state.has_next_config = next_config.exists()

        # Check for Prisma
        prisma_schema = project_path / "prisma" / "schema.prisma"
        if prisma_schema.exists():
            state.has_prisma = True
            state.existing_models = self._analyze_prisma_schema(prisma_schema)

        # Analyze existing routes
        api_dir = project_path / "src" / "app" / "api"
        if api_dir.exists():
            state.existing_routes = self._analyze_api_routes(api_dir)

        # Analyze existing pages
        app_dir = project_path / "src" / "app"
        if app_dir.exists():
            state.existing_pages = self._analyze_pages(app_dir)

        logger.debug(f"Project analysis complete: {state.to_prompt()}")
        return state

    def _analyze_package_json(self, package_path: Path, state: ProjectState) -> None:
        """Analyze package.json for dependencies.

        Args:
            package_path: Path to package.json
            state: ProjectState to update
        """
        try:
            content = json.loads(package_path.read_text())
            deps = content.get("dependencies", {})
            dev_deps = content.get("devDependencies", {})
            all_deps = {**deps, **dev_deps}

            # Check for common dependencies
            if "prisma" in all_deps or "@prisma/client" in all_deps:
                state.has_prisma = True

        except json.JSONDecodeError:
            logger.warning(f"Could not parse package.json: {package_path}")

    def _analyze_prisma_schema(self, schema_path: Path) -> List[str]:
        """Extract model names from Prisma schema.

        Args:
            schema_path: Path to schema.prisma

        Returns:
            List of model names
        """
        models = []
        try:
            content = schema_path.read_text()

            # Find all model definitions
            model_pattern = r"model\s+(\w+)\s*\{"
            matches = re.findall(model_pattern, content)
            models = list(matches)

            logger.debug(f"Found Prisma models: {models}")

        except Exception as e:
            logger.warning(f"Could not parse Prisma schema: {e}")

        return models

    def _analyze_api_routes(self, api_dir: Path) -> List[str]:
        """Find existing API routes.

        Args:
            api_dir: Path to src/app/api directory

        Returns:
            List of route paths (e.g., ["/todos", "/users"])
        """
        routes = []

        try:
            for route_file in api_dir.rglob("route.ts"):
                # Extract route path from file location
                relative = route_file.relative_to(api_dir)
                parts = list(relative.parts[:-1])  # Remove "route.ts"

                if parts:
                    route_path = "/" + "/".join(parts)
                    routes.append(route_path)

            logger.debug(f"Found API routes: {routes}")

        except Exception as e:
            logger.warning(f"Could not analyze API routes: {e}")

        return routes

    def _analyze_pages(self, app_dir: Path) -> List[str]:
        """Find existing pages.

        Args:
            app_dir: Path to src/app directory

        Returns:
            List of page paths (e.g., ["/", "/todos", "/todos/new"])
        """
        pages = []

        try:
            for page_file in app_dir.rglob("page.tsx"):
                # Extract page path from file location
                relative = page_file.relative_to(app_dir)
                parts = list(relative.parts[:-1])  # Remove "page.tsx"

                if not parts:
                    pages.append("/")
                else:
                    # Skip API routes
                    if parts[0] == "api":
                        continue
                    page_path = "/" + "/".join(parts)
                    pages.append(page_path)

            logger.debug(f"Found pages: {pages}")

        except Exception as e:
            logger.warning(f"Could not analyze pages: {e}")

        return pages


def analyze_project(project_dir: str) -> ProjectState:
    """Convenience function to analyze a project.

    Args:
        project_dir: Path to project directory

    Returns:
        ProjectState with analysis results
    """
    analyzer = ProjectAnalyzer()
    return analyzer.analyze(project_dir)


def get_missing_crud_parts(
    state: ProjectState,
    resource_name: str,
) -> List[str]:
    """Determine what CRUD parts are missing for a resource.

    Args:
        state: Current project state
        resource_name: Resource to check (singular, lowercase)

    Returns:
        List of missing parts (e.g., ["api_collection", "list_page"])
    """
    missing = []
    resource_plural = resource_name + "s"  # Simple pluralization

    # Check model
    model_name = resource_name.capitalize()
    if model_name not in state.existing_models:
        missing.append("prisma_model")

    # Check API routes
    collection_route = f"/{resource_plural}"
    item_route = f"/{resource_plural}/[id]"

    if collection_route not in state.existing_routes:
        missing.append("api_collection")
    if item_route not in state.existing_routes:
        missing.append("api_item")

    # Check pages
    list_page = f"/{resource_plural}"
    new_page = f"/{resource_plural}/new"
    detail_page = f"/{resource_plural}/[id]"

    if list_page not in state.existing_pages:
        missing.append("list_page")
    if new_page not in state.existing_pages:
        missing.append("new_page")
    if detail_page not in state.existing_pages:
        missing.append("detail_page")

    return missing


def suggest_checklist_items(
    state: ProjectState,
    resource_name: str,
    fields: dict,
) -> List[dict]:
    """Suggest checklist items based on project state.

    This is a helper for testing/comparison with LLM-generated checklists.

    Args:
        state: Current project state
        resource_name: Resource to create
        fields: Field definitions for the resource

    Returns:
        List of suggested checklist item dictionaries
    """
    items = []
    resource = resource_name.lower()
    Resource = resource_name.capitalize()

    # Check what's missing
    missing = get_missing_crud_parts(state, resource)

    # Setup items (if project doesn't exist or is incomplete)
    if not state.has_package_json:
        items.append(
            {
                "template": "create_next_app",
                "params": {"project_name": f"{resource}-app"},
                "description": "Initialize Next.js project",
            }
        )
        items.append(
            {
                "template": "setup_app_styling",
                "params": {"app_title": f"{Resource} App"},
                "description": "Configure modern styling",
            }
        )

    if not state.has_prisma:
        items.append(
            {
                "template": "setup_prisma",
                "params": {},
                "description": "Initialize Prisma ORM",
            }
        )

    # Data model
    if "prisma_model" in missing:
        items.append(
            {
                "template": "generate_prisma_model",
                "params": {"model_name": Resource, "fields": fields},
                "description": f"Define {Resource} database model",
            }
        )

    # API routes
    if "api_collection" in missing:
        items.append(
            {
                "template": "generate_api_route",
                "params": {
                    "resource": resource,
                    "operations": ["GET", "POST"],
                    "type": "collection",
                },
                "description": f"Create API for listing and creating {resource}s",
            }
        )

    if "api_item" in missing:
        items.append(
            {
                "template": "generate_api_route",
                "params": {
                    "resource": resource,
                    "operations": ["GET", "PATCH", "DELETE"],
                    "type": "item",
                },
                "description": f"Create API for single {resource} operations",
            }
        )

    # UI components
    if "list_page" in missing:
        items.append(
            {
                "template": "generate_react_component",
                "params": {"resource": resource, "variant": "list"},
                "description": f"List page showing all {resource}s",
            }
        )

    # Form component (always needed if any UI is missing)
    if any(p in missing for p in ["new_page", "detail_page"]):
        items.append(
            {
                "template": "generate_react_component",
                "params": {"resource": resource, "variant": "form"},
                "description": "Form component for create/edit",
            }
        )

    if "new_page" in missing:
        items.append(
            {
                "template": "generate_react_component",
                "params": {"resource": resource, "variant": "new"},
                "description": f"Create new {resource} page",
            }
        )

    if "detail_page" in missing:
        items.append(
            {
                "template": "generate_react_component",
                "params": {"resource": resource, "variant": "detail"},
                "description": f"View/edit {resource} page",
            }
        )
        items.append(
            {
                "template": "generate_react_component",
                "params": {"resource": resource, "variant": "actions"},
                "description": "Edit/delete buttons component",
            }
        )

    # Landing page update
    items.append(
        {
            "template": "update_landing_page",
            "params": {"resource": resource},
            "description": f"Add navigation to {resource}s",
        }
    )

    return items
