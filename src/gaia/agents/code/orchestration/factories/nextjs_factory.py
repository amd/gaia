# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Next.js project factory.

Creates workflow phases for Next.js CRUD applications.
"""

from pathlib import Path
from typing import List

from ..steps.base import UserContext
from ..workflows.base import WorkflowPhase
from ..workflows.nextjs import create_nextjs_workflow
from .base import ProjectFactory


class NextJSFactory(ProjectFactory):
    """Factory for creating Next.js CRUD workflows."""

    @property
    def project_type(self) -> str:
        """Return the project type."""
        return "nextjs"

    def detect_project(self, project_dir: str) -> bool:
        """Check if this is a Next.js project or should be one.

        Detection logic:
        1. Existing Next.js project: has next.config.* file
        2. Empty directory: can be initialized as Next.js
        3. Has package.json with next dependency

        Args:
            project_dir: Path to project directory

        Returns:
            True if this factory should handle the project
        """
        project_path = Path(project_dir)

        # Check for next.config.js or next.config.mjs
        if (project_path / "next.config.js").exists():
            return True
        if (project_path / "next.config.mjs").exists():
            return True
        if (project_path / "next.config.ts").exists():
            return True

        # Check package.json for next dependency
        package_json = project_path / "package.json"
        if package_json.exists():
            try:
                import json

                data = json.loads(package_json.read_text())
                deps = data.get("dependencies", {})
                dev_deps = data.get("devDependencies", {})
                if "next" in deps or "next" in dev_deps:
                    return True
            except (json.JSONDecodeError, IOError):
                # Ignore errors reading/parsing package.json; treat as not a Next.js project
                pass

        # Empty directory can be initialized as Next.js
        if project_path.exists() and not any(project_path.iterdir()):
            return True

        return False

    def create_workflow(self, context: UserContext) -> List[WorkflowPhase]:
        """Create Next.js workflow phases.

        Args:
            context: User context with request details

        Returns:
            List of workflow phases
        """
        return create_nextjs_workflow(context)

    def get_validation_config(self, phase_name: str) -> dict:
        """Get validation configuration for a phase.

        Args:
            phase_name: Name of the phase

        Returns:
            Validation configuration dict
        """
        configs = {
            "initialization": {
                "run_lint": False,
                "run_typecheck": False,
                "run_tests": False,
            },
            "data_layer": {
                "run_lint": False,
                "run_typecheck": True,
                "run_tests": False,
            },
            "ui_components": {
                "run_lint": False,
                "run_typecheck": True,
                "run_tests": False,
            },
            "validation": {
                "run_lint": True,
                "run_typecheck": True,
                "run_tests": True,
            },
            "testing": {
                "run_lint": False,
                "run_typecheck": False,
                "run_tests": True,
            },
        }
        return configs.get(phase_name, {})
