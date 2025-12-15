# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Python project factory.

Creates workflow phases for Python applications.
"""

from pathlib import Path
from typing import List

from ..steps.base import UserContext
from ..workflows.base import WorkflowPhase
from ..workflows.python import create_python_workflow
from .base import ProjectFactory


class PythonFactory(ProjectFactory):
    """Factory for creating Python development workflows."""

    @property
    def project_type(self) -> str:
        """Return the project type."""
        return "python"

    def detect_project(self, project_dir: str) -> bool:
        """Check if this is a Python project or should be one.

        Detection logic:
        1. Existing Python project: has setup.py, pyproject.toml, or requirements.txt
        2. Has .py files
        3. Empty directory: default to Python

        Args:
            project_dir: Path to project directory

        Returns:
            True if this factory should handle the project
        """
        project_path = Path(project_dir)

        # Check for Python project markers
        if (project_path / "setup.py").exists():
            return True
        if (project_path / "pyproject.toml").exists():
            return True
        if (project_path / "requirements.txt").exists():
            return True

        # Check for .py files
        if list(project_path.glob("*.py")):
            return True
        if list(project_path.glob("**/*.py")):
            return True

        # Empty directory defaults to Python (script mode)
        if project_path.exists() and not any(project_path.iterdir()):
            return True

        return False

    def create_workflow(self, context: UserContext) -> List[WorkflowPhase]:
        """Create Python workflow phases.

        Args:
            context: User context with request details

        Returns:
            List of workflow phases
        """
        return create_python_workflow(context)

    def get_validation_config(self, phase_name: str) -> dict:
        """Get validation configuration for a phase.

        Args:
            phase_name: Name of the phase

        Returns:
            Validation configuration dict
        """
        configs = {
            "creation": {
                "run_lint": False,
                "run_typecheck": False,
                "run_tests": False,
            },
            "validation": {
                "run_lint": False,
                "run_typecheck": False,
                "run_tests": False,
            },
            "quality": {
                "run_lint": True,
                "run_typecheck": False,
                "run_tests": False,
                "lint_command": "pylint",
            },
            "testing": {
                "run_lint": False,
                "run_typecheck": False,
                "run_tests": True,
                "test_command": "pytest",
            },
        }
        return configs.get(phase_name, {})
