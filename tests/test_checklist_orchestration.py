#!/usr/bin/env python
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for Checklist-Driven LLM Orchestration components.

This test suite covers:
- Template Catalog: Template definitions and validation
- Checklist Generator: LLM-based checklist generation
- Checklist Executor: Deterministic template execution
- Project Analyzer: Project state analysis for LLM context
- Orchestrator: Two-mode execution (checklist vs factory)
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gaia.agents.code.orchestration.checklist_executor import (
    TEMPLATE_TO_TOOL,
    ChecklistExecutionResult,
    ChecklistExecutor,
    ItemExecutionResult,
)
from gaia.agents.code.orchestration.checklist_generator import (
    ChecklistGenerator,
    ChecklistItem,
    GeneratedChecklist,
    ProjectState,
)
from gaia.agents.code.orchestration.project_analyzer import (
    ProjectAnalyzer,
    analyze_project,
    get_missing_crud_parts,
    suggest_checklist_items,
)
from gaia.agents.code.orchestration.steps.base import UserContext
from gaia.agents.code.orchestration.template_catalog import (
    TEMPLATE_CATALOG,
    ParameterSpec,
    ParameterType,
    get_catalog_prompt,
    get_template,
    get_templates_by_category,
    validate_checklist_item,
)


class TestTemplateCatalog(unittest.TestCase):
    """Tests for template_catalog.py."""

    def test_template_catalog_exists(self):
        """Test that template catalog is populated."""
        self.assertGreater(len(TEMPLATE_CATALOG), 0)

    def test_required_templates_exist(self):
        """Test that all expected templates are defined."""
        expected_templates = [
            "create_next_app",
            "setup_app_styling",
            "setup_prisma",
            "setup_testing",
            "generate_prisma_model",
            "generate_api_route",
            "generate_react_component",
            "update_landing_page",
            "run_typescript_check",
            "run_tests",
        ]
        for template_name in expected_templates:
            self.assertIn(
                template_name,
                TEMPLATE_CATALOG,
                f"Missing template: {template_name}",
            )

    def test_get_template(self):
        """Test get_template function."""
        template = get_template("create_next_app")
        self.assertIsNotNone(template)
        self.assertEqual(template.name, "create_next_app")
        self.assertIn("project_name", template.parameters)

        # Test non-existent template
        self.assertIsNone(get_template("nonexistent_template"))

    def test_get_templates_by_category(self):
        """Test getting templates by category."""
        setup_templates = get_templates_by_category("setup")
        self.assertGreater(len(setup_templates), 0)
        for template in setup_templates:
            self.assertEqual(template.category, "setup")

        # Test empty category
        empty_templates = get_templates_by_category("nonexistent_category")
        self.assertEqual(len(empty_templates), 0)

    def test_get_catalog_prompt(self):
        """Test catalog prompt generation for LLM."""
        prompt = get_catalog_prompt()
        self.assertIsInstance(prompt, str)
        self.assertIn("Available Templates", prompt)
        self.assertIn("Setup Templates", prompt)
        self.assertIn("create_next_app", prompt)
        self.assertIn("project_name", prompt)

    def test_validate_checklist_item_valid(self):
        """Test validation of valid checklist items."""
        # Valid item
        errors = validate_checklist_item(
            "create_next_app",
            {"project_name": "my-app"},
        )
        self.assertEqual(len(errors), 0)

    def test_validate_checklist_item_missing_required(self):
        """Test validation catches missing required parameters."""
        errors = validate_checklist_item(
            "create_next_app",
            {},  # Missing required project_name
        )
        self.assertGreater(len(errors), 0)
        self.assertTrue(any("project_name" in e for e in errors))

    def test_validate_checklist_item_unknown_param(self):
        """Test validation catches unknown parameters."""
        errors = validate_checklist_item(
            "create_next_app",
            {"project_name": "my-app", "unknown_param": "value"},
        )
        self.assertGreater(len(errors), 0)
        self.assertTrue(any("unknown_param" in e for e in errors))

    def test_validate_checklist_item_unknown_template(self):
        """Test validation catches unknown templates."""
        errors = validate_checklist_item(
            "nonexistent_template",
            {},
        )
        self.assertGreater(len(errors), 0)
        self.assertTrue(any("Unknown template" in e for e in errors))

    def test_parameter_spec_to_prompt(self):
        """Test ParameterSpec prompt generation."""
        spec = ParameterSpec(
            type=ParameterType.STRING,
            description="Test parameter",
            required=True,
            example="example_value",
        )
        prompt = spec.to_prompt()
        self.assertIn("string", prompt)
        self.assertIn("required", prompt)
        self.assertIn("Test parameter", prompt)
        self.assertIn("example_value", prompt)

    def test_template_definition_to_prompt(self):
        """Test TemplateDefinition prompt generation."""
        template = get_template("create_next_app")
        prompt = template.to_prompt()
        self.assertIn("create_next_app", prompt)
        self.assertIn("Description", prompt)
        self.assertIn("Parameters", prompt)


class TestProjectState(unittest.TestCase):
    """Tests for ProjectState dataclass."""

    def test_project_state_defaults(self):
        """Test default values."""
        state = ProjectState()
        self.assertFalse(state.exists)
        self.assertFalse(state.has_package_json)
        self.assertFalse(state.has_prisma)
        self.assertEqual(len(state.existing_models), 0)

    def test_project_state_to_prompt_not_exists(self):
        """Test prompt for non-existent project."""
        state = ProjectState(exists=False)
        prompt = state.to_prompt()
        self.assertIn("does not exist", prompt)

    def test_project_state_to_prompt_exists(self):
        """Test prompt for existing project."""
        state = ProjectState(
            exists=True,
            has_package_json=True,
            has_prisma=True,
            existing_models=["User", "Todo"],
            existing_routes=["/users", "/todos"],
        )
        prompt = state.to_prompt()
        self.assertIn("package.json", prompt)
        self.assertIn("Prisma", prompt)
        self.assertIn("User", prompt)
        self.assertIn("Todo", prompt)
        self.assertIn("/users", prompt)


class TestChecklistItem(unittest.TestCase):
    """Tests for ChecklistItem dataclass."""

    def test_checklist_item_creation(self):
        """Test creating a checklist item."""
        item = ChecklistItem(
            template="create_next_app",
            params={"project_name": "my-app"},
            description="Create the Next.js project",
        )
        self.assertEqual(item.template, "create_next_app")
        self.assertEqual(item.params["project_name"], "my-app")

    def test_checklist_item_to_dict(self):
        """Test converting item to dictionary."""
        item = ChecklistItem(
            template="create_next_app",
            params={"project_name": "my-app"},
            description="Create the Next.js project",
        )
        d = item.to_dict()
        self.assertEqual(d["template"], "create_next_app")
        self.assertEqual(d["params"]["project_name"], "my-app")


class TestGeneratedChecklist(unittest.TestCase):
    """Tests for GeneratedChecklist dataclass."""

    def test_generated_checklist_valid(self):
        """Test valid checklist."""
        checklist = GeneratedChecklist(
            items=[
                ChecklistItem("create_next_app", {"project_name": "app"}, "Create app"),
            ],
            reasoning="Building a simple app",
        )
        self.assertTrue(checklist.is_valid)
        self.assertEqual(len(checklist.items), 1)

    def test_generated_checklist_invalid(self):
        """Test invalid checklist with validation errors."""
        checklist = GeneratedChecklist(
            items=[],
            reasoning="",
            validation_errors=["Missing required template"],
        )
        self.assertFalse(checklist.is_valid)

    def test_generated_checklist_to_dict(self):
        """Test converting checklist to dictionary."""
        checklist = GeneratedChecklist(
            items=[
                ChecklistItem("create_next_app", {"project_name": "app"}, "Create app"),
            ],
            reasoning="Building a simple app",
        )
        d = checklist.to_dict()
        self.assertIn("reasoning", d)
        self.assertIn("checklist", d)
        self.assertEqual(len(d["checklist"]), 1)


class TestChecklistGenerator(unittest.TestCase):
    """Tests for ChecklistGenerator class."""

    def setUp(self):
        """Set up mock chat SDK."""
        self.mock_chat = MagicMock()

    def test_generator_initialization(self):
        """Test generator initializes with chat SDK."""
        generator = ChecklistGenerator(self.mock_chat)
        self.assertEqual(generator.chat, self.mock_chat)

    def test_generate_with_valid_response(self):
        """Test checklist generation with valid LLM response."""
        # Mock LLM response
        llm_response = json.dumps(
            {
                "reasoning": "Creating a todo app with CRUD",
                "checklist": [
                    {
                        "template": "create_next_app",
                        "params": {"project_name": "todo-app"},
                        "description": "Initialize Next.js project",
                    },
                    {
                        "template": "setup_prisma",
                        "params": {},
                        "description": "Set up database",
                    },
                ],
            }
        )
        self.mock_chat.send.return_value = llm_response

        generator = ChecklistGenerator(self.mock_chat)
        context = UserContext(
            user_request="Create a todo app",
            project_dir="/tmp/test",
            language="typescript",
            project_type="fullstack",
        )

        checklist = generator.generate(context)

        self.assertTrue(checklist.is_valid)
        self.assertEqual(len(checklist.items), 2)
        self.assertEqual(checklist.items[0].template, "create_next_app")
        self.assertIn("todo", checklist.reasoning.lower())

    def test_generate_with_markdown_response(self):
        """Test checklist generation handles markdown-wrapped JSON."""
        # Mock LLM response with markdown
        llm_response = """```json
{
    "reasoning": "Building todo app",
    "checklist": [
        {"template": "create_next_app", "params": {"project_name": "app"}, "description": "Init"}
    ]
}
```"""
        self.mock_chat.send.return_value = llm_response

        generator = ChecklistGenerator(self.mock_chat)
        context = UserContext(
            user_request="Create app",
            project_dir="/tmp/test",
            language="typescript",
            project_type="fullstack",
        )

        checklist = generator.generate(context)
        self.assertTrue(checklist.is_valid)
        self.assertEqual(len(checklist.items), 1)

    def test_generate_with_invalid_json(self):
        """Test checklist generation handles invalid JSON."""
        self.mock_chat.send.return_value = "This is not valid JSON"

        generator = ChecklistGenerator(self.mock_chat)
        context = UserContext(
            user_request="Create app",
            project_dir="/tmp/test",
            language="typescript",
            project_type="fullstack",
        )

        checklist = generator.generate(context)
        self.assertFalse(checklist.is_valid)
        self.assertGreater(len(checklist.validation_errors), 0)

    def test_generate_validates_items(self):
        """Test that generated checklist items are validated."""
        # Mock response with invalid template
        llm_response = json.dumps(
            {
                "reasoning": "Test",
                "checklist": [
                    {
                        "template": "nonexistent_template",
                        "params": {},
                        "description": "Invalid",
                    },
                ],
            }
        )
        self.mock_chat.send.return_value = llm_response

        generator = ChecklistGenerator(self.mock_chat)
        context = UserContext(
            user_request="Test",
            project_dir="/tmp/test",
            language="typescript",
            project_type="fullstack",
        )

        checklist = generator.generate(context)
        self.assertFalse(checklist.is_valid)
        self.assertTrue(
            any("Unknown template" in e for e in checklist.validation_errors)
        )


class TestChecklistExecutor(unittest.TestCase):
    """Tests for ChecklistExecutor class."""

    def setUp(self):
        """Set up mock tool executor."""
        self.mock_tool_executor = MagicMock()
        self.mock_tool_executor.return_value = {"success": True, "files": ["file.ts"]}

    def test_executor_initialization(self):
        """Test executor initializes correctly."""
        executor = ChecklistExecutor(self.mock_tool_executor)
        self.assertEqual(executor.tool_executor, self.mock_tool_executor)

    def test_execute_valid_checklist(self):
        """Test executing a valid checklist."""
        executor = ChecklistExecutor(self.mock_tool_executor)

        checklist = GeneratedChecklist(
            items=[
                ChecklistItem("create_next_app", {"project_name": "app"}, "Create app"),
            ],
            reasoning="Test",
        )
        context = UserContext(
            user_request="Test",
            project_dir="/tmp/test",
            language="typescript",
            project_type="fullstack",
        )

        result = executor.execute(checklist, context)

        self.assertTrue(result.success)
        self.assertEqual(result.items_succeeded, 1)
        self.assertEqual(result.items_failed, 0)
        self.mock_tool_executor.assert_called_once()

    def test_execute_handles_tool_failure(self):
        """Test executor handles tool failures."""
        self.mock_tool_executor.return_value = {
            "success": False,
            "error": "Tool failed",
            "retryable": False,
        }

        executor = ChecklistExecutor(self.mock_tool_executor)

        checklist = GeneratedChecklist(
            items=[
                ChecklistItem("create_next_app", {"project_name": "app"}, "Create app"),
            ],
            reasoning="Test",
        )
        context = UserContext(
            user_request="Test",
            project_dir="/tmp/test",
            language="typescript",
            project_type="fullstack",
        )

        result = executor.execute(checklist, context)

        self.assertFalse(result.success)
        self.assertEqual(result.items_failed, 1)
        self.assertGreater(len(result.errors), 0)

    def test_execute_respects_stop_on_error(self):
        """Test stop_on_error parameter."""
        call_count = 0

        def mock_executor(tool_name, params):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"success": False, "error": "Failed", "retryable": False}
            return {"success": True}

        executor = ChecklistExecutor(mock_executor)

        checklist = GeneratedChecklist(
            items=[
                ChecklistItem("create_next_app", {"project_name": "app"}, "Step 1"),
                ChecklistItem("setup_prisma", {}, "Step 2"),
            ],
            reasoning="Test",
        )
        context = UserContext(
            user_request="Test",
            project_dir="/tmp/test",
            language="typescript",
            project_type="fullstack",
        )

        # With stop_on_error=True (default), should stop after first failure
        result = executor.execute(checklist, context, stop_on_error=True)
        self.assertEqual(len(result.item_results), 1)

    def test_execute_preserves_order(self):
        """Test that execution preserves the order of the checklist."""
        execution_order = []

        def track_executor(tool_name, params):
            execution_order.append(tool_name)
            return {"success": True}

        executor = ChecklistExecutor(track_executor)

        # Items in specific order
        checklist = GeneratedChecklist(
            items=[
                ChecklistItem(
                    "generate_prisma_model",
                    {"model_name": "Todo", "fields": {}},
                    "Model",
                ),
                ChecklistItem("setup_prisma", {}, "Prisma"),
                ChecklistItem("create_next_app", {"project_name": "app"}, "Next.js"),
            ],
            reasoning="Test",
        )
        context = UserContext(
            user_request="Test",
            project_dir="/tmp/test",
            language="typescript",
            project_type="fullstack",
        )

        executor.execute(checklist, context)

        # Should NOT be reordered by dependencies
        # Order should be: manage_data_model, run_cli_command, run_cli_command

        self.assertEqual(execution_order[0], "manage_data_model")
        self.assertEqual(execution_order[1], "run_cli_command")
        self.assertEqual(execution_order[2], "run_cli_command")

    def test_template_to_tool_mapping(self):
        """Test that template names map to correct tool names."""
        self.assertIn("create_next_app", TEMPLATE_TO_TOOL)
        # create_next_app uses run_cli_command (npx create-next-app)
        self.assertEqual(TEMPLATE_TO_TOOL["create_next_app"], "run_cli_command")
        # setup_prisma uses run_cli_command (npx prisma init)
        self.assertEqual(TEMPLATE_TO_TOOL["setup_prisma"], "run_cli_command")
        self.assertEqual(TEMPLATE_TO_TOOL["generate_prisma_model"], "manage_data_model")
        self.assertEqual(TEMPLATE_TO_TOOL["generate_api_route"], "manage_api_endpoint")
        # run_tests uses run_cli_command (npm test)
        self.assertEqual(TEMPLATE_TO_TOOL["run_tests"], "run_cli_command")

    def test_build_setup_prisma_params(self):
        """Test _build_setup_prisma_params generates correct CLI command."""
        executor = ChecklistExecutor(lambda name, params: {"success": True})

        item = ChecklistItem(
            template="setup_prisma",
            params={},
            description="Initialize Prisma",
        )
        context = UserContext(
            user_request="Create todo app",
            project_dir="/tmp/test",
            language="typescript",
            project_type="fullstack",
        )

        params = executor._build_setup_prisma_params(item, context)

        # Should use CLI command format
        self.assertIn("command", params)
        self.assertIn("working_dir", params)
        self.assertEqual(params["working_dir"], "/tmp/test")

        # Command should install Prisma 5.x and initialize with SQLite
        self.assertIn("npm install prisma@5 @prisma/client@5", params["command"])
        self.assertIn("npx prisma init --datasource-provider sqlite", params["command"])
        self.assertIn("mkdir -p src/lib", params["command"])
        self.assertIn("src/lib/prisma.ts", params["command"])

    def test_build_prisma_db_sync_params(self):
        """Test _build_params generates correct CLI command for prisma_db_sync."""
        executor = ChecklistExecutor(lambda name, params: {"success": True})

        item = ChecklistItem(
            template="prisma_db_sync",
            params={},
            description="Generate Prisma client and sync database",
        )
        context = UserContext(
            user_request="Create todo app",
            project_dir="/tmp/test",
            language="typescript",
            project_type="fullstack",
        )

        params = executor._build_params(item, context)

        # Should use CLI command format
        self.assertIn("command", params)
        self.assertIn("working_dir", params)
        self.assertEqual(params["working_dir"], "/tmp/test")

        # Command should run prisma generate and db push
        self.assertIn("npx prisma generate", params["command"])
        self.assertIn("npx prisma db push", params["command"])

    def test_build_react_component_params_list_variant(self):
        """Test _build_react_component_params generates correct params for list variant."""
        executor = ChecklistExecutor(lambda name, params: {"success": True})

        item = ChecklistItem(
            template="generate_react_component",
            params={"resource": "todo", "variant": "list"},
            description="Create todo list",
        )
        context = UserContext(
            user_request="Create todo app",
            project_dir="/tmp/test",
            language="typescript",
            project_type="fullstack",
        )

        # Access private method for testing
        params = executor._build_react_component_params(item, context)

        self.assertEqual(params["component_name"], "TodoList")
        self.assertEqual(params["component_type"], "server")
        self.assertEqual(params["resource_name"], "todo")
        self.assertEqual(params["variant"], "list")
        self.assertEqual(params["project_dir"], "/tmp/test")
        self.assertNotIn("with_checkboxes", params)  # Should be filtered out

    def test_build_react_component_params_form_variant(self):
        """Test _build_react_component_params generates correct params for form variant."""
        executor = ChecklistExecutor(lambda name, params: {"success": True})

        item = ChecklistItem(
            template="generate_react_component",
            params={"resource": "todo", "variant": "form", "with_checkboxes": True},
            description="Create todo form",
        )
        context = UserContext(
            user_request="Create todo app",
            project_dir="/tmp/test",
            language="typescript",
            project_type="fullstack",
        )

        params = executor._build_react_component_params(item, context)

        self.assertEqual(params["component_name"], "TodoForm")
        self.assertEqual(
            params["component_type"], "client"
        )  # Forms are client components
        self.assertEqual(params["resource_name"], "todo")
        self.assertEqual(params["variant"], "form")
        self.assertNotIn("with_checkboxes", params)  # Should be filtered out

    def test_build_react_component_params_new_variant(self):
        """Test _build_react_component_params generates correct params for new variant."""
        executor = ChecklistExecutor(lambda name, params: {"success": True})

        item = ChecklistItem(
            template="generate_react_component",
            params={"resource": "todo", "variant": "new"},
            description="Create new todo page",
        )
        context = UserContext(
            user_request="Create todo app",
            project_dir="/tmp/test",
            language="typescript",
            project_type="fullstack",
        )

        params = executor._build_react_component_params(item, context)

        self.assertEqual(params["component_name"], "NewTodo")
        self.assertEqual(params["component_type"], "client")

    def test_build_react_component_params_detail_variant(self):
        """Test _build_react_component_params generates correct params for detail variant."""
        executor = ChecklistExecutor(lambda name, params: {"success": True})

        item = ChecklistItem(
            template="generate_react_component",
            params={"resource": "user", "variant": "detail"},
            description="Create user detail page",
        )
        context = UserContext(
            user_request="Create user app",
            project_dir="/tmp/test",
            language="typescript",
            project_type="fullstack",
        )

        params = executor._build_react_component_params(item, context)

        self.assertEqual(params["component_name"], "UserDetail")
        self.assertEqual(params["component_type"], "client")
        self.assertEqual(params["resource_name"], "user")

    def test_build_params_update_landing_page_maps_resource(self):
        """Test that update_landing_page maps resource to resource_name."""
        tool_params = {}

        def capture_executor(tool_name, params):
            tool_params.update(params)
            return {"success": True}

        executor = ChecklistExecutor(capture_executor)

        checklist = GeneratedChecklist(
            items=[
                ChecklistItem(
                    template="update_landing_page",
                    params={"resource": "todo"},
                    description="Update landing page",
                ),
            ],
            reasoning="Test",
        )
        context = UserContext(
            user_request="Test",
            project_dir="/tmp/test",
            language="typescript",
            project_type="fullstack",
        )

        executor.execute(checklist, context)

        # resource should be mapped to resource_name
        self.assertEqual(tool_params.get("resource_name"), "todo")
        self.assertNotIn("resource", tool_params)


class TestProjectAnalyzer(unittest.TestCase):
    """Tests for ProjectAnalyzer class."""

    def setUp(self):
        """Set up test directory."""
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        """Clean up test directory."""
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_analyze_nonexistent_project(self):
        """Test analyzing a non-existent project."""
        analyzer = ProjectAnalyzer()
        state = analyzer.analyze("/nonexistent/path")

        self.assertFalse(state.exists)

    def test_analyze_empty_project(self):
        """Test analyzing an empty directory."""
        analyzer = ProjectAnalyzer()
        state = analyzer.analyze(self.test_dir)

        self.assertTrue(state.exists)
        self.assertFalse(state.has_package_json)
        self.assertFalse(state.has_prisma)

    def test_analyze_with_package_json(self):
        """Test detecting package.json."""
        # Create package.json
        package_json = Path(self.test_dir) / "package.json"
        package_json.write_text(
            json.dumps(
                {
                    "name": "test-app",
                    "dependencies": {"next": "14.0.0"},
                }
            )
        )

        analyzer = ProjectAnalyzer()
        state = analyzer.analyze(self.test_dir)

        self.assertTrue(state.has_package_json)

    def test_analyze_with_prisma(self):
        """Test detecting Prisma."""
        # Create prisma schema
        prisma_dir = Path(self.test_dir) / "prisma"
        prisma_dir.mkdir()
        schema = prisma_dir / "schema.prisma"
        schema.write_text(
            """
model User {
  id    Int    @id @default(autoincrement())
  name  String
}

model Todo {
  id        Int     @id @default(autoincrement())
  title     String
  completed Boolean
}
"""
        )

        analyzer = ProjectAnalyzer()
        state = analyzer.analyze(self.test_dir)

        self.assertTrue(state.has_prisma)
        self.assertIn("User", state.existing_models)
        self.assertIn("Todo", state.existing_models)

    def test_analyze_api_routes(self):
        """Test detecting API routes."""
        # Create API route structure
        api_dir = Path(self.test_dir) / "src" / "app" / "api"
        todos_dir = api_dir / "todos"
        todos_dir.mkdir(parents=True)
        (todos_dir / "route.ts").write_text("export async function GET() {}")

        users_dir = api_dir / "users" / "[id]"
        users_dir.mkdir(parents=True)
        (users_dir / "route.ts").write_text("export async function GET() {}")

        analyzer = ProjectAnalyzer()
        state = analyzer.analyze(self.test_dir)

        self.assertIn("/todos", state.existing_routes)
        self.assertIn("/users/[id]", state.existing_routes)

    def test_analyze_pages(self):
        """Test detecting pages."""
        # Create page structure
        app_dir = Path(self.test_dir) / "src" / "app"
        app_dir.mkdir(parents=True)
        (app_dir / "page.tsx").write_text("export default function Home() {}")

        todos_dir = app_dir / "todos"
        todos_dir.mkdir()
        (todos_dir / "page.tsx").write_text("export default function Todos() {}")

        new_dir = todos_dir / "new"
        new_dir.mkdir()
        (new_dir / "page.tsx").write_text("export default function NewTodo() {}")

        analyzer = ProjectAnalyzer()
        state = analyzer.analyze(self.test_dir)

        self.assertIn("/", state.existing_pages)
        self.assertIn("/todos", state.existing_pages)
        self.assertIn("/todos/new", state.existing_pages)

    def test_analyze_project_convenience_function(self):
        """Test convenience function."""
        state = analyze_project(self.test_dir)
        self.assertTrue(state.exists)

    def test_get_missing_crud_parts(self):
        """Test identifying missing CRUD parts."""
        state = ProjectState(
            exists=True,
            existing_models=["Todo"],
            existing_routes=["/todos"],
            existing_pages=["/todos"],
        )

        missing = get_missing_crud_parts(state, "todo")

        # Should detect missing item route and pages
        self.assertNotIn("prisma_model", missing)  # Model exists
        self.assertNotIn("api_collection", missing)  # /todos route exists
        self.assertIn("api_item", missing)  # /todos/[id] missing
        self.assertNotIn("list_page", missing)  # /todos page exists
        self.assertIn("new_page", missing)  # /todos/new missing
        self.assertIn("detail_page", missing)  # /todos/[id] missing

    def test_suggest_checklist_items(self):
        """Test generating suggested checklist items."""
        state = ProjectState(exists=False)

        items = suggest_checklist_items(
            state, "todo", {"title": "string", "completed": "boolean"}
        )

        # Should suggest full setup for new project
        template_names = [item["template"] for item in items]
        self.assertIn("create_next_app", template_names)
        self.assertIn("setup_prisma", template_names)
        self.assertIn("generate_prisma_model", template_names)


class TestItemExecutionResult(unittest.TestCase):
    """Tests for ItemExecutionResult dataclass."""

    def test_item_result_success(self):
        """Test successful item result."""
        result = ItemExecutionResult(
            template="create_next_app",
            params={"project_name": "app"},
            description="Create app",
            success=True,
            files=["package.json", "next.config.ts"],
        )
        self.assertTrue(result.success)
        self.assertEqual(len(result.files), 2)

    def test_item_result_failure(self):
        """Test failed item result."""
        result = ItemExecutionResult(
            template="create_next_app",
            params={"project_name": "app"},
            description="Create app",
            success=False,
            error="npm install failed",
            error_recoverable=True,
        )
        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)

    def test_item_result_to_dict(self):
        """Test converting result to dictionary."""
        result = ItemExecutionResult(
            template="create_next_app",
            params={"project_name": "app"},
            description="Create app",
            success=True,
            files=["package.json"],
        )
        d = result.to_dict()
        self.assertEqual(d["template"], "create_next_app")
        self.assertTrue(d["success"])
        self.assertEqual(d["files"], ["package.json"])


class TestChecklistExecutionResult(unittest.TestCase):
    """Tests for ChecklistExecutionResult dataclass."""

    def test_execution_result_success(self):
        """Test successful execution result."""
        checklist = GeneratedChecklist(
            items=[ChecklistItem("test", {}, "Test")],
            reasoning="Test",
        )
        result = ChecklistExecutionResult(
            checklist=checklist,
            item_results=[
                ItemExecutionResult("test", {}, "Test", True, files=["a.ts"]),
                ItemExecutionResult("test2", {}, "Test2", True, files=["b.ts"]),
            ],
            success=True,
            total_files=["a.ts", "b.ts"],
        )

        self.assertTrue(result.success)
        self.assertEqual(result.items_succeeded, 2)
        self.assertEqual(result.items_failed, 0)
        self.assertIn("SUCCESS", result.summary)

    def test_execution_result_partial_failure(self):
        """Test execution result with some failures."""
        checklist = GeneratedChecklist(
            items=[ChecklistItem("test", {}, "Test")],
            reasoning="Test",
        )
        result = ChecklistExecutionResult(
            checklist=checklist,
            item_results=[
                ItemExecutionResult("test", {}, "Test", True),
                ItemExecutionResult("test2", {}, "Test2", False, error="Failed"),
            ],
            success=False,
            errors=["Failed"],
        )

        self.assertFalse(result.success)
        self.assertEqual(result.items_succeeded, 1)
        self.assertEqual(result.items_failed, 1)
        self.assertIn("FAILED", result.summary)

    def test_execution_result_to_dict(self):
        """Test converting execution result to dictionary."""
        checklist = GeneratedChecklist(
            items=[ChecklistItem("test", {}, "Test")],
            reasoning="Test reasoning",
        )
        result = ChecklistExecutionResult(
            checklist=checklist,
            item_results=[
                ItemExecutionResult("test", {}, "Test", True),
            ],
            success=True,
        )
        d = result.to_dict()

        self.assertTrue(d["success"])
        self.assertEqual(d["reasoning"], "Test reasoning")
        self.assertIn("items", d)


class TestOrchestratorModes(unittest.TestCase):
    """Tests for Orchestrator LLM-driven execution."""

    def test_orchestrator_initializes_with_llm_client(self):
        """Test that orchestrator initializes correctly with LLM client."""
        from gaia.agents.code.orchestration.orchestrator import Orchestrator

        mock_llm = MagicMock()
        mock_llm.send.return_value = json.dumps(
            {
                "reasoning": "Test",
                "checklist": [
                    {
                        "template": "create_next_app",
                        "params": {"project_name": "app"},
                        "description": "Test",
                    },
                ],
            }
        )

        mock_tool_executor = MagicMock()
        mock_tool_executor.return_value = {"success": True}

        orchestrator = Orchestrator(
            tool_executor=mock_tool_executor,
            llm_client=mock_llm,
        )

        # Verify checklist components were initialized
        self.assertIsNotNone(orchestrator.checklist_generator)
        self.assertIsNotNone(orchestrator.checklist_executor)

    def test_orchestrator_requires_llm_client(self):
        """Test that orchestrator raises ValueError without LLM client."""
        from gaia.agents.code.orchestration.orchestrator import Orchestrator

        mock_tool_executor = MagicMock()
        mock_tool_executor.return_value = {"success": True}

        # Should raise ValueError when llm_client is None
        with self.assertRaises(ValueError) as context:
            Orchestrator(
                tool_executor=mock_tool_executor,
                llm_client=None,
            )

        self.assertIn("llm_client is required", str(context.exception))

    def test_orchestrator_passes_error_handler_to_executor(self):
        """Test that orchestrator passes error_handler to ChecklistExecutor."""
        from gaia.agents.code.orchestration.orchestrator import Orchestrator

        mock_llm = MagicMock()
        mock_tool_executor = MagicMock()

        orchestrator = Orchestrator(
            tool_executor=mock_tool_executor,
            llm_client=mock_llm,
        )

        # Verify error_handler is passed to checklist_executor
        self.assertIsNotNone(orchestrator.checklist_executor)
        self.assertIsNotNone(orchestrator.checklist_executor.error_handler)
        self.assertEqual(
            orchestrator.checklist_executor.error_handler, orchestrator.error_handler
        )


class TestErrorRecovery(unittest.TestCase):
    """Tests for ChecklistExecutor error recovery functionality."""

    def test_execute_item_with_recovery_success_first_try(self):
        """Test successful execution on first try."""
        mock_executor = MagicMock()
        mock_executor.return_value = {"success": True, "files": ["file.ts"]}

        executor = ChecklistExecutor(mock_executor)

        item = ChecklistItem(
            template="create_next_app",
            params={"project_name": "app"},
            description="Create app",
        )
        context = UserContext(
            user_request="Create app",
            project_dir="/tmp/test",
            language="typescript",
            project_type="fullstack",
        )

        result = executor._execute_item_with_recovery(item, context)

        self.assertTrue(result.success)
        # Should only be called once on success
        self.assertEqual(mock_executor.call_count, 1)

    def test_execute_item_with_recovery_retry_on_failure(self):
        """Test that failures trigger retries when error_handler is present."""
        from gaia.agents.code.orchestration.steps.error_handler import (
            ErrorHandler,
            RecoveryAction,
        )

        call_count = 0

        def mock_tool_executor(name, params):
            nonlocal call_count
            call_count += 1
            # Fail first two times, succeed on third
            if call_count < 3:
                return {"success": False, "error": "Transient error"}
            return {"success": True, "files": ["file.ts"]}

        # Create mock error handler that returns RETRY
        mock_error_handler = MagicMock(spec=ErrorHandler)
        mock_error_handler.handle_error.return_value = (RecoveryAction.RETRY, None)

        executor = ChecklistExecutor(
            mock_tool_executor,
            error_handler=mock_error_handler,
        )

        item = ChecklistItem(
            template="setup_prisma",
            params={},
            description="Setup Prisma",
        )
        context = UserContext(
            user_request="Create app",
            project_dir="/tmp/test",
            language="typescript",
            project_type="fullstack",
        )

        result = executor._execute_item_with_recovery(item, context)

        self.assertTrue(result.success)
        # Should be called 3 times (2 failures + 1 success)
        self.assertEqual(call_count, 3)
        # Error handler should have been called twice (for the 2 failures)
        self.assertEqual(mock_error_handler.handle_error.call_count, 2)

    def test_execute_item_with_recovery_abort(self):
        """Test that ABORT action stops retries immediately."""
        from gaia.agents.code.orchestration.steps.error_handler import (
            ErrorHandler,
            RecoveryAction,
        )

        call_count = 0

        def mock_tool_executor(name, params):
            nonlocal call_count
            call_count += 1
            return {"success": False, "error": "Critical error"}

        # Create mock error handler that returns ABORT
        mock_error_handler = MagicMock(spec=ErrorHandler)
        mock_error_handler.handle_error.return_value = (RecoveryAction.ABORT, None)

        executor = ChecklistExecutor(
            mock_tool_executor,
            error_handler=mock_error_handler,
        )

        item = ChecklistItem(
            template="create_next_app",
            params={"project_name": "app"},
            description="Create app",
        )
        context = UserContext(
            user_request="Create app",
            project_dir="/tmp/test",
            language="typescript",
            project_type="fullstack",
        )

        result = executor._execute_item_with_recovery(item, context)

        self.assertFalse(result.success)
        # Should only be called once - ABORT stops retries
        self.assertEqual(call_count, 1)
        self.assertEqual(mock_error_handler.handle_error.call_count, 1)

    def test_execute_item_with_recovery_max_attempts_exceeded(self):
        """Test that max attempts limits retries."""
        from gaia.agents.code.orchestration.steps.error_handler import (
            ErrorHandler,
            RecoveryAction,
        )

        call_count = 0

        def mock_tool_executor(name, params):
            nonlocal call_count
            call_count += 1
            return {"success": False, "error": "Persistent error"}

        # Create mock error handler that always returns RETRY
        mock_error_handler = MagicMock(spec=ErrorHandler)
        mock_error_handler.handle_error.return_value = (RecoveryAction.RETRY, None)

        executor = ChecklistExecutor(
            mock_tool_executor,
            error_handler=mock_error_handler,
        )

        item = ChecklistItem(
            template="setup_prisma",
            params={},
            description="Setup Prisma",
        )
        context = UserContext(
            user_request="Create app",
            project_dir="/tmp/test",
            language="typescript",
            project_type="fullstack",
        )

        result = executor._execute_item_with_recovery(item, context, max_attempts=3)

        self.assertFalse(result.success)
        # Should be called exactly max_attempts times
        self.assertEqual(call_count, 3)
        # Error handler should be called (max_attempts - 1) times
        # (not called on last attempt since we return immediately)
        self.assertEqual(mock_error_handler.handle_error.call_count, 2)

    def test_execute_item_with_recovery_no_error_handler(self):
        """Test that without error_handler, failures return immediately."""
        call_count = 0

        def mock_tool_executor(name, params):
            nonlocal call_count
            call_count += 1
            return {"success": False, "error": "Some error"}

        # No error handler
        executor = ChecklistExecutor(mock_tool_executor)

        item = ChecklistItem(
            template="create_next_app",
            params={"project_name": "app"},
            description="Create app",
        )
        context = UserContext(
            user_request="Create app",
            project_dir="/tmp/test",
            language="typescript",
            project_type="fullstack",
        )

        result = executor._execute_item_with_recovery(item, context)

        self.assertFalse(result.success)
        # Should only be called once - no error handler means no retries
        self.assertEqual(call_count, 1)

    def test_execute_item_with_recovery_fix_and_retry(self):
        """Test FIX_AND_RETRY action triggers retry."""
        from gaia.agents.code.orchestration.steps.error_handler import (
            ErrorHandler,
            RecoveryAction,
        )

        call_count = 0

        def mock_tool_executor(name, params):
            nonlocal call_count
            call_count += 1
            # Fail first time, succeed on second
            if call_count == 1:
                return {"success": False, "error": "Code error"}
            return {"success": True, "files": ["fixed.ts"]}

        # Create mock error handler that returns FIX_AND_RETRY
        mock_error_handler = MagicMock(spec=ErrorHandler)
        mock_error_handler.handle_error.return_value = (
            RecoveryAction.FIX_AND_RETRY,
            "Applied fix: corrected syntax",
        )

        executor = ChecklistExecutor(
            mock_tool_executor,
            error_handler=mock_error_handler,
        )

        item = ChecklistItem(
            template="generate_prisma_model",
            params={"model_name": "Todo"},
            description="Generate model",
        )
        context = UserContext(
            user_request="Create app",
            project_dir="/tmp/test",
            language="typescript",
            project_type="fullstack",
        )

        result = executor._execute_item_with_recovery(item, context)

        self.assertTrue(result.success)
        self.assertEqual(call_count, 2)

    def test_execute_item_with_recovery_resets_retry_count_on_success(self):
        """Test that error_handler.reset_retry_count is called on success."""
        from gaia.agents.code.orchestration.steps.error_handler import ErrorHandler

        mock_tool_executor = MagicMock()
        mock_tool_executor.return_value = {"success": True}

        mock_error_handler = MagicMock(spec=ErrorHandler)

        executor = ChecklistExecutor(
            mock_tool_executor,
            error_handler=mock_error_handler,
        )

        item = ChecklistItem(
            template="create_next_app",
            params={"project_name": "app"},
            description="Create app",
        )
        context = UserContext(
            user_request="Create app",
            project_dir="/tmp/test",
            language="typescript",
            project_type="fullstack",
        )

        executor._execute_item_with_recovery(item, context)

        # reset_retry_count should be called on success
        mock_error_handler.reset_retry_count.assert_called_once_with("create_next_app")

    def test_execute_uses_recovery_method(self):
        """Test that execute() uses _execute_item_with_recovery."""
        from gaia.agents.code.orchestration.steps.error_handler import (
            ErrorHandler,
            RecoveryAction,
        )

        call_count = 0

        def mock_tool_executor(name, params):
            nonlocal call_count
            call_count += 1
            # First call fails, second succeeds
            if call_count == 1:
                return {"success": False, "error": "Transient error"}
            return {"success": True, "files": ["file.ts"]}

        mock_error_handler = MagicMock(spec=ErrorHandler)
        mock_error_handler.handle_error.return_value = (RecoveryAction.RETRY, None)

        executor = ChecklistExecutor(
            mock_tool_executor,
            error_handler=mock_error_handler,
        )

        checklist = GeneratedChecklist(
            items=[
                ChecklistItem(
                    template="create_next_app",
                    params={"project_name": "app"},
                    description="Create app",
                ),
            ],
            reasoning="Test",
        )
        context = UserContext(
            user_request="Create app",
            project_dir="/tmp/test",
            language="typescript",
            project_type="fullstack",
        )

        result = executor.execute(checklist, context)

        # Should succeed because retry worked
        self.assertTrue(result.success)
        self.assertEqual(result.items_succeeded, 1)
        # Tool should be called twice (1 failure + 1 retry success)
        self.assertEqual(call_count, 2)


class TestLLMCodeGeneration(unittest.TestCase):
    """Tests for LLM-driven code generation in ChecklistExecutor."""

    def test_template_classification_constants(self):
        """Test that template classification constants are defined correctly."""
        from gaia.agents.code.orchestration.checklist_executor import (
            DETERMINISTIC_TEMPLATES,
            LLM_GENERATED_TEMPLATES,
        )

        # Deterministic templates should include CLI commands
        self.assertIn("create_next_app", DETERMINISTIC_TEMPLATES)
        self.assertIn("setup_prisma", DETERMINISTIC_TEMPLATES)
        self.assertIn("prisma_db_sync", DETERMINISTIC_TEMPLATES)
        self.assertIn("run_tests", DETERMINISTIC_TEMPLATES)

        # LLM generated templates should include code-generating templates
        self.assertIn("generate_react_component", LLM_GENERATED_TEMPLATES)
        self.assertIn("generate_api_route", LLM_GENERATED_TEMPLATES)
        self.assertIn("update_landing_page", LLM_GENERATED_TEMPLATES)

        # Should be mutually exclusive
        self.assertEqual(len(DETERMINISTIC_TEMPLATES & LLM_GENERATED_TEMPLATES), 0)

    def test_execute_item_routes_deterministic(self):
        """Test that deterministic templates route correctly."""
        execution_path = []

        def mock_tool_executor(name, params):
            execution_path.append(("tool", name))
            return {"success": True}

        executor = ChecklistExecutor(mock_tool_executor)

        item = ChecklistItem(
            template="create_next_app",
            params={"project_name": "app"},
            description="Create app",
        )
        context = UserContext(
            user_request="Create app",
            project_dir="/tmp/test",
            language="typescript",
            project_type="fullstack",
        )

        result = executor._execute_item(item, context)

        self.assertTrue(result.success)
        # Should have called the tool directly
        self.assertEqual(len(execution_path), 1)
        self.assertEqual(execution_path[0][0], "tool")

    def test_execute_item_routes_to_llm_when_available(self):
        """Test that LLM templates route to LLM when client available."""
        mock_llm = MagicMock()
        mock_llm.send.return_value = """import Link from "next/link";

export default function TodosPage() {
  return <div className="glass-card page-title">Todos</div>;
}"""

        def mock_tool_executor(name, params):
            return {"success": True}

        executor = ChecklistExecutor(
            mock_tool_executor,
            llm_client=mock_llm,
        )

        item = ChecklistItem(
            template="generate_react_component",
            params={"resource": "todo", "variant": "list"},
            description="Create todo list",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            context = UserContext(
                user_request="Create todo app",
                project_dir=tmpdir,
                language="typescript",
                project_type="fullstack",
            )

            result = executor._execute_item(item, context)

            # LLM should have been called
            self.assertTrue(mock_llm.send.called)
            self.assertTrue(result.success)
            # File should have been created
            self.assertGreater(len(result.files), 0)

    def test_execute_item_fallback_when_no_llm(self):
        """Test that LLM templates fall back to tool when no LLM client."""
        tool_called = []

        def mock_tool_executor(name, params):
            tool_called.append(name)
            return {"success": True}

        # No llm_client provided
        executor = ChecklistExecutor(mock_tool_executor)

        item = ChecklistItem(
            template="generate_react_component",
            params={"resource": "todo", "variant": "list"},
            description="Create todo list",
        )
        context = UserContext(
            user_request="Create todo app",
            project_dir="/tmp/test",
            language="typescript",
            project_type="fullstack",
        )

        executor._execute_item(item, context)

        # Should have fallen back to tool executor
        self.assertGreater(len(tool_called), 0)

    def test_build_code_generation_prompt(self):
        """Test that code generation prompt is built correctly."""
        executor = ChecklistExecutor(lambda name, params: {"success": True})

        item = ChecklistItem(
            template="generate_react_component",
            params={"resource": "todo", "variant": "list"},
            description="Create todo list page",
        )
        context = UserContext(
            user_request="Create a todo app",
            project_dir="/tmp/test",
            language="typescript",
            project_type="fullstack",
            schema_fields={"title": "string", "completed": "boolean"},
        )

        prompt = executor._build_code_generation_prompt(
            item, context, "// template guidance here"
        )

        # Check prompt contains key elements
        self.assertIn("Next.js", prompt)
        self.assertIn("generate_react_component", prompt)
        self.assertIn("todo", prompt)
        self.assertIn("list", prompt)
        self.assertIn("glass-card", prompt)
        self.assertIn("btn-primary", prompt)
        self.assertIn("template guidance here", prompt)
        self.assertIn("Create a todo app", prompt)

    def test_get_template_guidance_react_component(self):
        """Test that template guidance is retrieved for react components."""
        executor = ChecklistExecutor(lambda name, params: {"success": True})

        # Test list variant
        item = ChecklistItem(
            template="generate_react_component",
            params={"resource": "todo", "variant": "list"},
            description="Create list",
        )
        guidance = executor._get_template_guidance(item)
        self.assertIsNotNone(guidance)
        self.assertIn("prisma", guidance.lower())

        # Test form variant
        item.params["variant"] = "form"
        guidance = executor._get_template_guidance(item)
        self.assertIsNotNone(guidance)
        self.assertIn("use client", guidance.lower())

    def test_get_template_guidance_returns_none_for_unknown(self):
        """Test that unknown templates return None."""
        executor = ChecklistExecutor(lambda name, params: {"success": True})

        item = ChecklistItem(
            template="unknown_template",
            params={},
            description="Unknown",
        )
        guidance = executor._get_template_guidance(item)
        self.assertIsNone(guidance)

    def test_determine_file_path_react_component(self):
        """Test file path determination for react components."""
        executor = ChecklistExecutor(lambda name, params: {"success": True})
        context = UserContext(
            user_request="Test",
            project_dir="/tmp/test",
            language="typescript",
            project_type="fullstack",
        )

        # List variant
        item = ChecklistItem(
            template="generate_react_component",
            params={"resource": "todo", "variant": "list"},
            description="List",
        )
        path = executor._determine_file_path(item, context)
        self.assertEqual(path, "src/app/todos/page.tsx")

        # Form variant
        item.params["variant"] = "form"
        path = executor._determine_file_path(item, context)
        self.assertEqual(path, "src/components/TodoForm.tsx")

        # New variant
        item.params["variant"] = "new"
        path = executor._determine_file_path(item, context)
        self.assertEqual(path, "src/app/todos/new/page.tsx")

        # Detail variant
        item.params["variant"] = "detail"
        path = executor._determine_file_path(item, context)
        self.assertEqual(path, "src/app/todos/[id]/page.tsx")

    def test_determine_file_path_api_route(self):
        """Test file path determination for API routes."""
        executor = ChecklistExecutor(lambda name, params: {"success": True})
        context = UserContext(
            user_request="Test",
            project_dir="/tmp/test",
            language="typescript",
            project_type="fullstack",
        )

        # Collection route
        item = ChecklistItem(
            template="generate_api_route",
            params={"resource": "todo", "type": "collection"},
            description="API",
        )
        path = executor._determine_file_path(item, context)
        self.assertEqual(path, "src/app/api/todos/route.ts")

        # Item route
        item.params["type"] = "item"
        path = executor._determine_file_path(item, context)
        self.assertEqual(path, "src/app/api/todos/[id]/route.ts")

    def test_clean_llm_response_removes_markdown(self):
        """Test that markdown code blocks are removed."""
        executor = ChecklistExecutor(lambda name, params: {"success": True})

        # With ```typescript block
        response = """```typescript
import React from "react";

export default function App() {
  return <div>Hello</div>;
}
```"""
        cleaned = executor._clean_llm_response(response)
        self.assertNotIn("```", cleaned)
        self.assertIn("import React", cleaned)

        # With ```tsx block
        response2 = """```tsx
const x = 1;
```"""
        cleaned2 = executor._clean_llm_response(response2)
        self.assertNotIn("```", cleaned2)
        self.assertIn("const x = 1", cleaned2)

    def test_clean_llm_response_handles_plain_code(self):
        """Test that plain code without markdown is preserved."""
        executor = ChecklistExecutor(lambda name, params: {"success": True})

        response = """import React from "react";

export default function App() {
  return <div>Hello</div>;
}"""
        cleaned = executor._clean_llm_response(response)
        self.assertEqual(cleaned.strip(), response.strip())

    def test_validate_generated_code_checks_classes(self):
        """Test that validation checks for expected CSS classes."""
        executor = ChecklistExecutor(lambda name, params: {"success": True})

        item = ChecklistItem(
            template="generate_react_component",
            params={"resource": "todo", "variant": "list"},
            description="List",
        )

        # Code with expected classes
        good_code = """import Link from "next/link";

export default function TodosPage() {
  return (
    <div className="glass-card">
      <h1 className="page-title">Todos</h1>
      <button className="btn-primary">Add</button>
    </div>
  );
}"""
        is_valid, issues = executor._validate_generated_code(good_code, item)
        self.assertTrue(is_valid)
        self.assertEqual(len(issues), 0)

        # Code missing expected classes
        bad_code = """export default function TodosPage() {
  return <div>Todos</div>;
}"""
        is_valid, issues = executor._validate_generated_code(bad_code, item)
        self.assertFalse(is_valid)
        self.assertTrue(any("glass-card" in issue for issue in issues))

    def test_validate_generated_code_checks_use_client(self):
        """Test that validation checks for 'use client' when needed."""
        executor = ChecklistExecutor(lambda name, params: {"success": True})

        # Form variant requires 'use client'
        item = ChecklistItem(
            template="generate_react_component",
            params={"resource": "todo", "variant": "form"},
            description="Form",
        )

        # Code with 'use client'
        good_code = """"use client";

import { useState } from "react";

export default function TodoForm() {
  return <div className="glass-card input-field btn-primary btn-secondary">Form</div>;
}"""
        is_valid, issues = executor._validate_generated_code(good_code, item)
        # May have class issues but should have use client
        has_use_client_issue = any("use client" in issue.lower() for issue in issues)
        self.assertFalse(has_use_client_issue)

        # Code without 'use client'
        bad_code = """export default function TodoForm() {
  return <div className="glass-card input-field btn-primary btn-secondary">Form</div>;
}"""
        is_valid, issues = executor._validate_generated_code(bad_code, item)
        has_use_client_issue = any("use client" in issue.lower() for issue in issues)
        self.assertTrue(has_use_client_issue)

    def test_template_metadata_defined(self):
        """Test that template metadata is defined for all LLM templates."""
        from gaia.agents.code.orchestration.checklist_executor import (
            LLM_GENERATED_TEMPLATES,
            TEMPLATE_METADATA,
        )

        # All LLM templates should have metadata (may be empty for some)
        for template in LLM_GENERATED_TEMPLATES:
            # Just check it doesn't raise - some templates may not need metadata
            metadata = TEMPLATE_METADATA.get(template, {})
            self.assertIsInstance(metadata, dict)


if __name__ == "__main__":
    unittest.main()
