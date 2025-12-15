# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Next.js step implementations.

Steps wrap the existing Code Agent tools with standardized interfaces.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .base import BaseStep, ErrorCategory, StepResult, UserContext

# Package versions (matching nextjs_prompt.py)
NEXTJS_VERSION = "14.2.33"
PRISMA_VERSION = "5.22.0"
ZOD_VERSION = "3.23.8"


@dataclass
class CreateNextAppStep(BaseStep):
    """Step to create a new Next.js application."""

    name: str = "create_next_app"
    description: str = "Initialize Next.js project"

    def should_skip(self, context: UserContext) -> Optional[str]:
        """Skip if package.json already exists."""
        package_json = Path(context.project_dir) / "package.json"
        if package_json.exists():
            return "package.json already exists, project already initialized"
        return None

    def get_tool_invocation(
        self, context: UserContext
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Return run_cli_command invocation."""
        return (
            "run_cli_command",
            {
                "command": f"npx -y create-next-app@{NEXTJS_VERSION} . --typescript --tailwind --eslint --app --src-dir --yes",
                "working_dir": context.project_dir,
                "timeout": 1200,
            },
        )

    def handle_result(self, result: Any, context: UserContext) -> StepResult:
        """Convert tool result to StepResult."""
        if isinstance(result, dict):
            if result.get("success") or result.get("return_code") == 0:
                return StepResult.ok(
                    "Next.js project created successfully",
                    files=["package.json", "tsconfig.json", "src/app/page.tsx"],
                )
            return StepResult.make_error(
                "Failed to create Next.js project",
                result.get("error") or result.get("stderr", "Unknown error"),
                ErrorCategory.COMPILATION,
            )
        return StepResult.make_error(
            "Unexpected result format", str(result), ErrorCategory.UNKNOWN
        )


@dataclass
class SetupAppStylingStep(BaseStep):
    """Step to set up app-wide modern styling.

    Creates the root layout and globals.css with a modern dark theme
    design system that all pages inherit.
    """

    name: str = "setup_styling"
    description: str = "Set up modern app styling"
    app_title: str = "My App"
    app_description: str = "A modern web application"

    def get_tool_invocation(
        self, context: UserContext
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Return setup_app_styling invocation."""
        # Derive app title from entity name or use default
        title = f"{context.entity_name or 'My'} App"
        description = f"A modern {(context.entity_name or 'web').lower()} application"

        return (
            "setup_app_styling",
            {
                "project_dir": context.project_dir,
                "app_title": title,
                "app_description": description,
            },
        )

    def handle_result(self, result: Any, context: UserContext) -> StepResult:
        """Convert tool result to StepResult."""
        if isinstance(result, dict):
            if result.get("success"):
                return StepResult.ok(
                    "App styling configured with modern design system",
                    files=result.get("files", []),
                )
            return StepResult.make_error(
                "Failed to set up app styling",
                result.get("error", "Unknown error"),
                ErrorCategory.CONFIGURATION,
            )
        return StepResult.make_error(
            "Unexpected result format", str(result), ErrorCategory.UNKNOWN
        )


@dataclass
class InstallDependenciesStep(BaseStep):
    """Step to install additional dependencies."""

    name: str = "install_deps"
    description: str = "Install Prisma and Zod"

    def should_skip(self, context: UserContext) -> Optional[str]:
        """Skip if prisma is already in package.json."""
        package_json = Path(context.project_dir) / "package.json"
        if package_json.exists():
            content = package_json.read_text()
            if "prisma" in content and "@prisma/client" in content:
                return "Dependencies already installed"
        return None

    def get_tool_invocation(
        self, context: UserContext
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Return run_cli_command invocation."""
        return (
            "run_cli_command",
            {
                "command": f"npm install prisma@^{PRISMA_VERSION} @prisma/client@^{PRISMA_VERSION} zod@^{ZOD_VERSION}",
                "working_dir": context.project_dir,
                "timeout": 1200,
            },
        )

    def handle_result(self, result: Any, context: UserContext) -> StepResult:
        """Convert tool result to StepResult."""
        if isinstance(result, dict):
            if result.get("success") or result.get("return_code") == 0:
                return StepResult.ok("Dependencies installed successfully")
            return StepResult.make_error(
                "Failed to install dependencies",
                result.get("error") or result.get("stderr", "Unknown error"),
                ErrorCategory.DEPENDENCY,
            )
        return StepResult.make_error(
            "Unexpected result format", str(result), ErrorCategory.UNKNOWN
        )


@dataclass
class PrismaInitStep(BaseStep):
    """Step to initialize Prisma with SQLite."""

    name: str = "prisma_init"
    description: str = "Initialize Prisma"

    def should_skip(self, context: UserContext) -> Optional[str]:
        """Skip if prisma directory already exists."""
        prisma_dir = Path(context.project_dir) / "prisma"
        if prisma_dir.exists() and (prisma_dir / "schema.prisma").exists():
            return "Prisma already initialized"
        return None

    def get_tool_invocation(
        self, context: UserContext
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Return run_cli_command invocation."""
        return (
            "run_cli_command",
            {
                "command": "npx -y prisma init --datasource-provider sqlite",
                "working_dir": context.project_dir,
                "timeout": 600,
            },
        )

    def handle_result(self, result: Any, context: UserContext) -> StepResult:
        """Convert tool result to StepResult."""
        if isinstance(result, dict):
            if result.get("success") or result.get("return_code") == 0:
                return StepResult.ok(
                    "Prisma initialized with SQLite",
                    files=["prisma/schema.prisma"],
                )
            return StepResult.make_error(
                "Failed to initialize Prisma",
                result.get("error") or result.get("stderr", "Unknown error"),
                ErrorCategory.CONFIGURATION,
            )
        return StepResult.make_error(
            "Unexpected result format", str(result), ErrorCategory.UNKNOWN
        )


@dataclass
class ManageDataModelStep(BaseStep):
    """Step to create Prisma data model."""

    name: str = "data_model"
    description: str = "Create Prisma model"
    entity_name: str = "Item"
    fields: Dict[str, str] = field(default_factory=lambda: {"title": "string"})

    def validate_preconditions(self, context: UserContext) -> Optional[str]:
        """Check that Prisma is initialized before managing data model."""
        schema_path = Path(context.project_dir) / "prisma" / "schema.prisma"
        if not schema_path.exists():
            return (
                "Prisma not initialized. The prisma/schema.prisma file must exist. "
                "Run 'npx prisma init --datasource-provider sqlite' first."
            )
        return None

    def get_tool_invocation(
        self, context: UserContext
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Return manage_data_model invocation."""
        return (
            "manage_data_model",
            {
                "project_dir": context.project_dir,
                "model_name": self.entity_name,
                "fields": self.fields,
            },
        )

    def handle_result(self, result: Any, context: UserContext) -> StepResult:
        """Convert tool result to StepResult."""
        if isinstance(result, dict):
            if result.get("success"):
                # Store generated files in context
                files = result.get("files", [])
                return StepResult.ok(
                    f"Prisma model {self.entity_name} created",
                    files=files,
                    model_name=self.entity_name,
                )
            return StepResult.make_error(
                f"Failed to create {self.entity_name} model",
                result.get("error", "Unknown error"),
                ErrorCategory.COMPILATION,
            )
        return StepResult.make_error(
            "Unexpected result format", str(result), ErrorCategory.UNKNOWN
        )


@dataclass
class SetupPrismaStep(BaseStep):
    """Step to set up Prisma client after schema changes.

    This creates the Prisma singleton, generates client types, and pushes to DB.
    Must run AFTER ManageDataModelStep and BEFORE API endpoint steps.
    """

    name: str = "setup_prisma"
    description: str = "Set up Prisma client and database"

    def validate_preconditions(self, context: UserContext) -> Optional[str]:
        """Check that Prisma schema exists before setup."""
        schema_path = Path(context.project_dir) / "prisma" / "schema.prisma"
        if not schema_path.exists():
            return (
                "Prisma schema not found. The prisma/schema.prisma file must exist. "
                "Run 'npx prisma init' first."
            )
        return None

    def get_tool_invocation(
        self, context: UserContext
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Return setup_prisma invocation."""
        return (
            "setup_prisma",
            {
                "project_dir": context.project_dir,
                "regenerate": True,
                "push_db": True,
            },
        )

    def handle_result(self, result: Any, context: UserContext) -> StepResult:
        """Convert tool result to StepResult."""
        if isinstance(result, dict):
            if result.get("success"):
                files = []
                if result.get("singleton_path"):
                    files.append(result["singleton_path"])
                return StepResult.ok(
                    "Prisma client set up successfully",
                    files=files,
                    generated=result.get("generated", False),
                    pushed=result.get("pushed", False),
                )
            return StepResult.make_error(
                "Failed to set up Prisma",
                result.get("error", "Unknown error"),
                ErrorCategory.COMPILATION,
            )
        return StepResult.make_error(
            "Unexpected result format", str(result), ErrorCategory.UNKNOWN
        )


@dataclass
class ManageApiEndpointStep(BaseStep):
    """Step to create collection API endpoint (GET, POST)."""

    name: str = "api_collection"
    description: str = "Create collection API"
    entity_name: str = "Item"
    fields: Dict[str, str] = field(default_factory=lambda: {"title": "string"})

    def validate_preconditions(self, context: UserContext) -> Optional[str]:
        """Check that Prisma singleton exists before creating API endpoints."""
        prisma_lib = Path(context.project_dir) / "src" / "lib" / "prisma.ts"
        if not prisma_lib.exists():
            return (
                "Prisma client not set up. The src/lib/prisma.ts file must exist. "
                "Run 'setup_prisma' tool first to create the Prisma singleton."
            )
        return None

    def get_tool_invocation(
        self, context: UserContext
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Return manage_api_endpoint invocation."""
        return (
            "manage_api_endpoint",
            {
                "project_dir": context.project_dir,
                "resource_name": self.entity_name.lower(),
                "operations": ["GET", "POST"],
                "fields": self.fields,
            },
        )

    def handle_result(self, result: Any, context: UserContext) -> StepResult:
        """Convert tool result to StepResult."""
        if isinstance(result, dict):
            if result.get("success"):
                files = result.get("files", [])
                return StepResult.ok(
                    f"Collection API for {self.entity_name} created",
                    files=files,
                )
            return StepResult.make_error(
                f"Failed to create collection API for {self.entity_name}",
                result.get("error", "Unknown error"),
                ErrorCategory.COMPILATION,
            )
        return StepResult.make_error(
            "Unexpected result format", str(result), ErrorCategory.UNKNOWN
        )


@dataclass
class ManageApiEndpointDynamicStep(BaseStep):
    """Step to create dynamic API endpoint (GET, PATCH, DELETE for [id])."""

    name: str = "api_item"
    description: str = "Create item API"
    entity_name: str = "Item"
    fields: Dict[str, str] = field(default_factory=lambda: {"title": "string"})

    def get_tool_invocation(
        self, context: UserContext
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Return manage_api_endpoint invocation for dynamic route.

        Note: The tool automatically creates the [id]/route.ts file when
        PATCH/DELETE operations are requested.
        """
        return (
            "manage_api_endpoint",
            {
                "project_dir": context.project_dir,
                "resource_name": self.entity_name.lower(),
                "operations": ["GET", "PATCH", "DELETE"],
                "fields": self.fields,
            },
        )

    def handle_result(self, result: Any, context: UserContext) -> StepResult:
        """Convert tool result to StepResult."""
        if isinstance(result, dict):
            if result.get("success"):
                files = result.get("files", [])
                return StepResult.ok(
                    f"Item API for {self.entity_name} created",
                    files=files,
                )
            return StepResult.make_error(
                f"Failed to create item API for {self.entity_name}",
                result.get("error", "Unknown error"),
                ErrorCategory.COMPILATION,
            )
        return StepResult.make_error(
            "Unexpected result format", str(result), ErrorCategory.UNKNOWN
        )


@dataclass
class ManageReactComponentStep(BaseStep):
    """Step to create React component."""

    name: str = "component"
    description: str = "Create React component"
    entity_name: str = "Item"
    variant: str = "list"  # list, form, new, detail, actions
    fields: Dict[str, str] = field(default_factory=lambda: {"title": "string"})

    def get_tool_invocation(
        self, context: UserContext
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Return manage_react_component invocation.

        Note: resource_name is REQUIRED for the tool to generate correct paths.
        Without it, all variants fall back to src/components/{component_name}.tsx.
        """
        # For "form" variant, validation expects TodoForm.tsx (not Todo.tsx)
        if self.variant == "form":
            component_name = f"{self.entity_name}Form"
        else:
            component_name = self.entity_name

        return (
            "manage_react_component",
            {
                "project_dir": context.project_dir,
                "component_name": component_name,
                "resource_name": self.entity_name.lower(),  # Required for path generation
                "variant": self.variant,
                "fields": self.fields,
            },
        )

    def handle_result(self, result: Any, context: UserContext) -> StepResult:
        """Convert tool result to StepResult."""
        if isinstance(result, dict):
            if result.get("success"):
                files = result.get("files", [])
                return StepResult.ok(
                    f"{self.entity_name} {self.variant} component created",
                    files=files,
                    variant=self.variant,
                )
            return StepResult.make_error(
                f"Failed to create {self.entity_name} {self.variant}",
                result.get("error", "Unknown error"),
                ErrorCategory.COMPILATION,
            )
        return StepResult.make_error(
            "Unexpected result format", str(result), ErrorCategory.UNKNOWN
        )


@dataclass
class ValidateCrudStructureStep(BaseStep):
    """Step to validate CRUD structure."""

    name: str = "validate_structure"
    description: str = "Validate CRUD structure"
    entity_name: str = "Item"

    def get_tool_invocation(
        self, context: UserContext
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Return validate_crud_structure invocation."""
        return (
            "validate_crud_structure",
            {
                "project_dir": context.project_dir,
                "resource_name": self.entity_name.lower(),
            },
        )

    def handle_result(self, result: Any, context: UserContext) -> StepResult:
        """Convert tool result to StepResult."""
        if isinstance(result, dict):
            if result.get("success") or result.get("valid"):
                return StepResult.ok("CRUD structure validated successfully")
            missing = result.get("missing_files", [])
            return StepResult.make_error(
                "CRUD structure validation failed",
                f"Missing files: {missing}",
                ErrorCategory.VALIDATION,
            )
        return StepResult.make_error(
            "Unexpected result format", str(result), ErrorCategory.UNKNOWN
        )


@dataclass
class ValidateTypescriptStep(BaseStep):
    """Step to validate TypeScript."""

    name: str = "validate_typescript"
    description: str = "Run TypeScript validation"

    def get_tool_invocation(
        self, context: UserContext
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Return validate_typescript invocation."""
        return (
            "validate_typescript",
            {
                "project_dir": context.project_dir,
            },
        )

    def handle_result(self, result: Any, context: UserContext) -> StepResult:
        """Convert tool result to StepResult."""
        if isinstance(result, dict):
            if result.get("success") or result.get("valid"):
                return StepResult.ok("TypeScript validation passed")
            errors = result.get("errors", [])
            return StepResult.make_error(
                "TypeScript validation failed",
                "\n".join(errors) if errors else result.get("error", "Unknown error"),
                ErrorCategory.COMPILATION,
            )
        return StepResult.make_error(
            "Unexpected result format", str(result), ErrorCategory.UNKNOWN
        )


@dataclass
class TestCrudApiStep(BaseStep):
    """Step to test CRUD API."""

    name: str = "test_api"
    description: str = "Test CRUD operations"
    entity_name: str = "Item"

    def get_tool_invocation(
        self, context: UserContext
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Return test_crud_api invocation."""
        return (
            "test_crud_api",
            {
                "project_dir": context.project_dir,
                "model_name": self.entity_name,
            },
        )

    def handle_result(self, result: Any, context: UserContext) -> StepResult:
        """Convert tool result to StepResult."""
        if isinstance(result, dict):
            if result.get("success"):
                return StepResult.ok(
                    "CRUD API tests passed",
                    tests_passed=result.get("tests_passed", 0),
                )
            # Test failures are warnings, not hard errors
            # The code was generated - tests may fail due to database/server issues
            test_result = result.get("result", {})
            passed = test_result.get("tests_passed", 0)
            failed = test_result.get("tests_failed", 0)
            details = test_result.get("results", {})
            # Build summary of which tests failed
            failed_tests = [k for k, v in details.items() if not v.get("pass")]
            return StepResult.warning(
                f"API tests: {passed} passed, {failed} failed ({', '.join(failed_tests)})",
                tests_passed=passed,
                tests_failed=failed,
                failed_tests=failed_tests,
            )
        return StepResult.make_error(
            "Unexpected result format", str(result), ErrorCategory.UNKNOWN
        )


@dataclass
class UpdateLandingPageStep(BaseStep):
    """Step to update landing page with navigation."""

    name: str = "update_landing"
    description: str = "Update landing page"
    entity_name: str = "Item"

    def get_tool_invocation(
        self, context: UserContext
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Return update_landing_page invocation."""
        return (
            "update_landing_page",
            {
                "project_dir": context.project_dir,
                "resource_name": self.entity_name.lower(),
            },
        )

    def handle_result(self, result: Any, context: UserContext) -> StepResult:
        """Convert tool result to StepResult."""
        if isinstance(result, dict):
            if result.get("success"):
                return StepResult.ok("Landing page updated with navigation link")
            return StepResult.make_error(
                "Failed to update landing page",
                result.get("error", "Unknown error"),
                ErrorCategory.COMPILATION,
            )
        return StepResult.make_error(
            "Unexpected result format", str(result), ErrorCategory.UNKNOWN
        )


@dataclass
class SetupTestingStep(BaseStep):
    """Step to set up testing infrastructure."""

    name: str = "setup_testing"
    description: str = "Set up Vitest and testing libraries"

    def should_skip(self, context: UserContext) -> Optional[str]:
        """Skip if vitest is already configured."""
        vitest_config = Path(context.project_dir) / "vitest.config.ts"
        if vitest_config.exists():
            return "Vitest already configured"
        return None

    def get_tool_invocation(
        self, context: UserContext
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Return setup_nextjs_testing invocation."""
        return (
            "setup_nextjs_testing",
            {
                "project_dir": context.project_dir,
            },
        )

    def handle_result(self, result: Any, context: UserContext) -> StepResult:
        """Convert tool result to StepResult."""
        if isinstance(result, dict):
            if result.get("success"):
                return StepResult.ok(
                    "Testing infrastructure set up",
                    files=result.get("files", []),
                )
            return StepResult.make_error(
                "Failed to set up testing",
                result.get("error", "Unknown error"),
                ErrorCategory.CONFIGURATION,
            )
        return StepResult.make_error(
            "Unexpected result format", str(result), ErrorCategory.UNKNOWN
        )


@dataclass
class RunTestsStep(BaseStep):
    """Step to run all tests."""

    name: str = "run_tests"
    description: str = "Run npm test"

    def get_tool_invocation(
        self, context: UserContext
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Return run_cli_command invocation for npm test."""
        return (
            "run_cli_command",
            {
                "command": "npm test",
                "working_dir": context.project_dir,
                "timeout": 1200,
            },
        )

    def handle_result(self, result: Any, context: UserContext) -> StepResult:
        """Convert tool result to StepResult."""
        if isinstance(result, dict):
            if result.get("success") or result.get("return_code") == 0:
                return StepResult.ok("All tests passed")
            # Tests failing is a warning, not a hard error
            return StepResult.warning(
                "Some tests failed",
                stderr=result.get("stderr", ""),
                stdout=result.get("stdout", ""),
            )
        return StepResult.make_error(
            "Unexpected result format", str(result), ErrorCategory.UNKNOWN
        )


@dataclass
class ValidateStylesStep(BaseStep):
    """Step to validate CSS files and design system consistency.

    This step validates:
    1. CSS files contain valid CSS (not TypeScript/JavaScript) - CRITICAL
    2. globals.css has Tailwind directives
    3. layout.tsx imports globals.css
    4. Custom classes used in components are defined in globals.css

    Addresses Issue #1002: CSS file contains TypeScript code instead of CSS.
    """

    name: str = "validate_styles"
    description: str = "Validate CSS files and design system"
    resource_name: Optional[str] = None

    def validate_preconditions(self, context: UserContext) -> Optional[str]:
        """Check that styling files exist before validation."""
        globals_css = Path(context.project_dir) / "src" / "app" / "globals.css"
        if not globals_css.exists():
            return (
                "globals.css not found. The src/app/globals.css file must exist. "
                "Run 'setup_app_styling' first."
            )
        return None

    def get_tool_invocation(
        self, context: UserContext
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Return validate_styles invocation."""
        params = {
            "project_dir": context.project_dir,
        }
        if self.resource_name:
            params["_resource_name"] = self.resource_name
        return ("validate_styles", params)

    def handle_result(self, result: Any, context: UserContext) -> StepResult:
        """Convert tool result to StepResult."""
        if isinstance(result, dict):
            if result.get("success") or result.get("is_valid"):
                warnings = result.get("warnings", [])
                if warnings:
                    return StepResult.warning(
                        "Styling validated with warnings",
                        warnings=warnings,
                    )
                return StepResult.ok("Styling validated successfully")

            errors = result.get("errors", [])
            # Check if any errors are CRITICAL (blocking)
            critical_errors = [e for e in errors if "CRITICAL" in e]
            if critical_errors:
                return StepResult.make_error(
                    "CRITICAL styling validation failed",
                    "\n".join(critical_errors),
                    ErrorCategory.VALIDATION,
                    retryable=True,  # Allow LLM to retry with correct CSS
                )
            return StepResult.make_error(
                "Styling validation failed",
                "\n".join(errors) if errors else result.get("error", "Unknown error"),
                ErrorCategory.VALIDATION,
            )
        return StepResult.make_error(
            "Unexpected result format", str(result), ErrorCategory.UNKNOWN
        )


@dataclass
class GenerateStyleTestsStep(BaseStep):
    """Step to generate CSS and styling tests for the project.

    Creates test files that validate:
    1. CSS file integrity (no TypeScript in CSS)
    2. Tailwind directive presence
    3. Design system class definitions
    4. Layout imports globals.css

    Tests are placed in the project's /tests directory.
    """

    name: str = "generate_style_tests"
    description: str = "Generate CSS and styling tests"
    resource_name: str = "Item"

    def should_skip(self, context: UserContext) -> Optional[str]:
        """Skip if style tests already exist."""
        styles_test = Path(context.project_dir) / "tests" / "styles.test.ts"
        if styles_test.exists():
            return "Style tests already exist"
        return None

    def validate_preconditions(self, context: UserContext) -> Optional[str]:
        """Check that testing is set up before generating style tests."""
        vitest_config = Path(context.project_dir) / "vitest.config.ts"
        if not vitest_config.exists():
            return (
                "Vitest not configured. Run 'setup_testing' first to set up "
                "the testing infrastructure."
            )
        return None

    def get_tool_invocation(
        self, context: UserContext
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Return generate_style_tests invocation."""
        return (
            "generate_style_tests",
            {
                "project_dir": context.project_dir,
                "resource_name": self.resource_name,
            },
        )

    def handle_result(self, result: Any, context: UserContext) -> StepResult:
        """Convert tool result to StepResult."""
        if isinstance(result, dict):
            if result.get("success"):
                return StepResult.ok(
                    "Style tests generated successfully",
                    files=result.get("files", []),
                )
            return StepResult.make_error(
                "Failed to generate style tests",
                result.get("error", "Unknown error"),
                ErrorCategory.COMPILATION,
            )
        return StepResult.make_error(
            "Unexpected result format", str(result), ErrorCategory.UNKNOWN
        )
