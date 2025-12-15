# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Base workflow definitions for orchestration.

Workflows are composed of phases, each containing steps.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from ..steps.base import BaseStep


@dataclass
class ValidationConfig:
    """Configuration for validation after each step/phase.

    Ensures linting, TypeScript validation, and tests run at appropriate times.
    """

    run_lint: bool = True
    run_typecheck: bool = True
    run_tests: bool = True
    lint_command: str = "npm run lint"
    typecheck_command: str = "npx -y tsc --noEmit"
    test_command: str = "npm test"
    fail_on_lint_error: bool = False  # Warnings OK, errors fail
    fail_on_type_error: bool = True
    fail_on_test_error: bool = True


@dataclass
class WorkflowPhase:
    """A phase in the workflow containing multiple steps.

    Phases group related steps together:
    - initialization: Project setup, dependencies
    - data_layer: Database schema, migrations
    - api: API routes, handlers
    - ui: Components, pages
    - validation: Testing, linting
    """

    name: str
    description: str
    steps: List[BaseStep] = field(default_factory=list)
    validation: Optional[ValidationConfig] = None
    required: bool = True  # If False, phase can be skipped

    def add_step(self, step: BaseStep) -> "WorkflowPhase":
        """Add a step to this phase (fluent interface)."""
        self.steps.append(step)
        return self

    def with_validation(
        self,
        lint: bool = True,
        typecheck: bool = True,
        tests: bool = True,
        fail_on_type_error: bool = True,
        fail_on_test_error: bool = True,
    ) -> "WorkflowPhase":
        """Configure validation for this phase (fluent interface).

        Args:
            lint: Run linting
            typecheck: Run TypeScript type checking
            tests: Run tests
            fail_on_type_error: Stop workflow if type check fails (default: True)
            fail_on_test_error: Stop workflow if tests fail (default: True)
        """
        self.validation = ValidationConfig(
            run_lint=lint,
            run_typecheck=typecheck,
            run_tests=tests,
            fail_on_type_error=fail_on_type_error,
            fail_on_test_error=fail_on_test_error,
        )
        return self
