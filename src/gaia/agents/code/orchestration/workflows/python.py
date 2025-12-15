# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Python workflow definition.

Defines the phases and steps for building Python applications.
"""

from typing import List

from ..steps.base import UserContext
from ..steps.python import (
    AnalyzePylintStep,
    AutoFixSyntaxStep,
    CreateProjectStep,
    FixLintingStep,
    ListFilesStep,
    RunPytestStep,
    ValidateProjectStep,
)
from .base import WorkflowPhase


def create_python_workflow(context: UserContext) -> List[WorkflowPhase]:
    """Create the Python development workflow phases.

    Args:
        context: User context with request details

    Returns:
        List of workflow phases
    """
    # Phase 1: Project Creation
    creation_phase = WorkflowPhase(
        name="creation",
        description="Create Python project structure",
        steps=[
            CreateProjectStep(
                name="create_project",
                description="Generate project files",
                user_request=context.user_request,
            ),
            ListFilesStep(
                name="list_files",
                description="Discover created files",
            ),
        ],
    )

    # Phase 2: Validation
    validation_phase = WorkflowPhase(
        name="validation",
        description="Validate and fix project",
        steps=[
            ValidateProjectStep(
                name="validate_project",
                description="Check project structure",
            ),
            AutoFixSyntaxStep(
                name="auto_fix_syntax",
                description="Fix syntax errors",
            ),
        ],
    )

    # Phase 3: Code Quality
    quality_phase = WorkflowPhase(
        name="quality",
        description="Lint and format code",
        steps=[
            AnalyzePylintStep(
                name="analyze_pylint",
                description="Run pylint analysis",
            ),
            FixLintingStep(
                name="fix_linting",
                description="Fix linting issues",
            ),
        ],
    )

    # Phase 4: Testing
    testing_phase = WorkflowPhase(
        name="testing",
        description="Run tests",
        steps=[
            RunPytestStep(
                name="run_tests",
                description="Run pytest",
            ),
        ],
    )

    return [creation_phase, validation_phase, quality_phase, testing_phase]
