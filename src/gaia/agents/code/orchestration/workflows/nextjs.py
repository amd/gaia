# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Next.js workflow definition.

Defines the phases and steps for building Next.js CRUD applications.
"""

from typing import List

from ..steps.base import UserContext
from ..steps.nextjs import (
    CreateNextAppStep,
    InstallDependenciesStep,
    ManageApiEndpointDynamicStep,
    ManageApiEndpointStep,
    ManageDataModelStep,
    ManageReactComponentStep,
    PrismaInitStep,
    RunTestsStep,
    SetupAppStylingStep,
    SetupPrismaStep,
    SetupTestingStep,
    TestCrudApiStep,
    UpdateLandingPageStep,
    ValidateCrudStructureStep,
    ValidateTypescriptStep,
)
from .base import WorkflowPhase


def create_nextjs_workflow(context: UserContext) -> List[WorkflowPhase]:
    """Create the Next.js CRUD workflow phases.

    Args:
        context: User context with request details

    Returns:
        List of workflow phases
    """
    entity = context.entity_name or "Item"
    fields = context.schema_fields or {"title": "string", "completed": "boolean"}

    # Phase 0: Project Initialization
    # Includes testing deps so they're available when test files are generated
    init_phase = WorkflowPhase(
        name="initialization",
        description="Set up Next.js project with dependencies",
        steps=[
            CreateNextAppStep(
                name="create_next_app",
                description="Initialize Next.js project",
            ),
            SetupAppStylingStep(
                name="setup_styling",
                description="Configure modern app-wide styling",
            ),
            InstallDependenciesStep(
                name="install_deps",
                description="Install Prisma, Zod, and other dependencies",
            ),
            SetupTestingStep(
                name="setup_testing",
                description="Install Vitest and testing libraries",
            ),
            PrismaInitStep(
                name="prisma_init",
                description="Initialize Prisma with SQLite",
            ),
        ],
    )

    # Phase 1: Data Layer
    data_phase = WorkflowPhase(
        name="data_layer",
        description="Create database model and API routes",
        steps=[
            ManageDataModelStep(
                name="data_model",
                description=f"Create {entity} Prisma model",
                entity_name=entity,
                fields=fields,
            ),
            SetupPrismaStep(
                name="setup_prisma",
                description="Generate Prisma client and push to database",
            ),
            ManageApiEndpointStep(
                name="api_collection",
                description=f"Create {entity} collection API (GET, POST)",
                entity_name=entity,
                fields=fields,
            ),
            ManageApiEndpointDynamicStep(
                name="api_item",
                description=f"Create {entity} item API (GET, PATCH, DELETE)",
                entity_name=entity,
                fields=fields,
            ),
        ],
    ).with_validation(lint=False, typecheck=True, tests=False, fail_on_type_error=False)

    # Phase 2: UI Components
    ui_phase = WorkflowPhase(
        name="ui_components",
        description="Create React components and pages",
        steps=[
            ManageReactComponentStep(
                name="list_component",
                description=f"Create {entity} list page",
                entity_name=entity,
                variant="list",
                fields=fields,
            ),
            ManageReactComponentStep(
                name="form_component",
                description=f"Create {entity} form component",
                entity_name=entity,
                variant="form",
                fields=fields,
            ),
            ManageReactComponentStep(
                name="new_page",
                description=f"Create {entity} new page",
                entity_name=entity,
                variant="new",
                fields=fields,
            ),
            # Actions must come before detail_page since detail_page imports it
            ManageReactComponentStep(
                name="actions_component",
                description=f"Create {entity} actions component",
                entity_name=entity,
                variant="actions",
                fields=fields,
            ),
            ManageReactComponentStep(
                name="detail_page",
                description=f"Create {entity} detail page",
                entity_name=entity,
                variant="detail",
                fields=fields,
            ),
        ],
    ).with_validation(lint=False, typecheck=True, tests=False, fail_on_type_error=False)

    # Phase 3: Validation & Polish
    validation_phase = WorkflowPhase(
        name="validation",
        description="Validate structure and run tests",
        steps=[
            ValidateCrudStructureStep(
                name="validate_structure",
                description="Check all required files exist",
                entity_name=entity,
            ),
            ValidateTypescriptStep(
                name="validate_typescript",
                description="Run TypeScript compiler",
            ),
            TestCrudApiStep(
                name="test_api",
                description="Test CRUD operations",
                entity_name=entity,
            ),
            UpdateLandingPageStep(
                name="update_landing",
                description="Add navigation link to landing page",
                entity_name=entity,
            ),
        ],
    )

    # Phase 4: Run Tests
    testing_phase = WorkflowPhase(
        name="testing",
        description="Run all tests",
        steps=[
            RunTestsStep(
                name="run_tests",
                description="Run all tests with npm test",
            ),
        ],
    )

    return [init_phase, data_phase, ui_phase, validation_phase, testing_phase]
