# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Base factory for creating project workflows.

Factories define project-specific step sequences and configurations.
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from ..steps.base import UserContext
from ..workflows.base import WorkflowPhase


class ProjectFactory(ABC):
    """Abstract factory for creating project-specific workflows.

    Subclasses implement create_workflow() to return phases appropriate
    for their project type (Next.js, Python, etc.).
    """

    @property
    @abstractmethod
    def project_type(self) -> str:
        """Return the project type this factory handles."""

    @abstractmethod
    def create_workflow(self, context: UserContext) -> List[WorkflowPhase]:
        """Create workflow phases for the given context.

        Args:
            context: User context with request details and accumulated state

        Returns:
            List of WorkflowPhases to execute in order
        """

    @abstractmethod
    def detect_project(self, project_dir: str) -> bool:
        """Check if this factory should handle the given project.

        Args:
            project_dir: Path to project directory

        Returns:
            True if this factory can handle the project
        """

    def get_validation_config(self, phase_name: str) -> Optional[dict]:  # noqa: ARG002
        """Get validation configuration for a specific phase.

        Override to customize validation per phase.

        Args:
            phase_name: Name of the phase

        Returns:
            Dict with validation settings or None for defaults
        """
        # Default: no custom config. Subclasses override per phase.
        del phase_name  # Unused in base class
        return None
