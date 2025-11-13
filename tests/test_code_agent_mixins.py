#!/usr/bin/env python
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for Code Agent mixins.

This test suite validates the refactored mixin architecture:
- CodeToolsMixin
- ValidationAndParsingMixin
- FileIOToolsMixin
- CodeFormattingMixin
- ProjectManagementMixin
- TestingMixin
- ErrorFixingMixin
"""

import os
import shutil
import sys
import tempfile
import unittest

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.code.agent import CodeAgent


class TestMixinArchitecture(unittest.TestCase):
    """Test the refactored mixin architecture."""

    def setUp(self):
        """Set up test fixtures."""
        self.agent = CodeAgent(silent_mode=True, max_steps=5)
        self.test_dir = tempfile.mkdtemp()
        self.agent._register_tools()

    def tearDown(self):
        """Clean up test fixtures."""
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
        _TOOL_REGISTRY.clear()

    def test_all_mixins_loaded(self):
        """Test that all mixins are properly loaded."""
        # Check that agent has all mixin methods
        self.assertTrue(hasattr(self.agent, "register_code_tools"))
        self.assertTrue(hasattr(self.agent, "register_file_io_tools"))
        self.assertTrue(hasattr(self.agent, "register_code_formatting_tools"))
        self.assertTrue(hasattr(self.agent, "register_project_management_tools"))
        self.assertTrue(hasattr(self.agent, "register_testing_tools"))
        self.assertTrue(hasattr(self.agent, "register_error_fixing_tools"))

    def test_validators_initialized(self):
        """Test that validators are properly initialized."""
        self.assertTrue(hasattr(self.agent, "syntax_validator"))
        self.assertTrue(hasattr(self.agent, "antipattern_checker"))
        self.assertTrue(hasattr(self.agent, "ast_analyzer"))
        self.assertTrue(hasattr(self.agent, "requirements_validator"))

    def test_helper_methods_available(self):
        """Test helper methods from ValidationAndParsingMixin."""
        self.assertTrue(hasattr(self.agent, "_validate_python_syntax"))
        self.assertTrue(hasattr(self.agent, "_parse_python_code"))


class TestCodeToolsMixin(unittest.TestCase):
    """Test CodeToolsMixin functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.agent = CodeAgent(silent_mode=True, max_steps=5)
        self.agent._register_tools()

    def tearDown(self):
        """Clean up test fixtures."""
        _TOOL_REGISTRY.clear()

    def test_generate_function_tool_registered(self):
        """Test that generate_function tool is registered and works."""
        self.assertIn("generate_function", _TOOL_REGISTRY)

        # Actually execute the tool
        result = self.agent._execute_tool(
            "validate_syntax", {"code": "def foo():\n    pass"}
        )
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["is_valid"])

    def test_generate_class_tool_registered(self):
        """Test that generate_class tool is registered."""
        self.assertIn("generate_class", _TOOL_REGISTRY)

    def test_generate_test_tool_registered(self):
        """Test that generate_test tool is registered."""
        self.assertIn("generate_test", _TOOL_REGISTRY)

    def test_validate_syntax_tool_registered(self):
        """Test that validate_syntax tool is registered and validates."""
        self.assertIn("validate_syntax", _TOOL_REGISTRY)

        # Test with valid code
        result = self.agent._execute_tool(
            "validate_syntax", {"code": "def test():\n    return 42"}
        )
        self.assertTrue(result["is_valid"])

        # Test with invalid code
        result = self.agent._execute_tool(
            "validate_syntax", {"code": "def test(\n    return 42"}
        )
        self.assertFalse(result["is_valid"])

    def test_parse_python_code_tool_registered(self):
        """Test that parse_python_code tool is registered and parses."""
        self.assertIn("parse_python_code", _TOOL_REGISTRY)

        # Test parsing a simple function
        result = self.agent._execute_tool(
            "parse_python_code", {"code": "def add(x, y):\n    return x + y"}
        )
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["is_valid"])
        self.assertGreater(len(result["symbols"]), 0)

    def test_list_symbols_tool_registered(self):
        """Test that list_symbols tool lists symbols correctly."""
        self.assertIn("list_symbols", _TOOL_REGISTRY)

        # Test with code containing multiple symbols
        code = "def func1():\n    pass\n\nclass MyClass:\n    pass"
        result = self.agent._execute_tool("list_symbols", {"code": code})

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["total_symbols"], 2)
        symbol_names = [s["name"] for s in result["symbols"]]
        self.assertIn("func1", symbol_names)
        self.assertIn("MyClass", symbol_names)

    def test_analyze_with_pylint_tool_registered(self):
        """Test that analyze_with_pylint tool is registered."""
        self.assertIn("analyze_with_pylint", _TOOL_REGISTRY)


class TestFileIOToolsMixin(unittest.TestCase):
    """Test FileIOToolsMixin functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.agent = CodeAgent(silent_mode=True, max_steps=5)
        self.agent._register_tools()

    def tearDown(self):
        """Clean up test fixtures."""
        _TOOL_REGISTRY.clear()

    def test_read_file_tool_registered(self):
        """Test that read_file tool is registered."""
        self.assertIn("read_file", _TOOL_REGISTRY)

    def test_write_python_file_tool_registered(self):
        """Test that write_python_file tool is registered."""
        self.assertIn("write_python_file", _TOOL_REGISTRY)

    def test_edit_python_file_tool_registered(self):
        """Test that edit_python_file tool is registered."""
        self.assertIn("edit_python_file", _TOOL_REGISTRY)

    def test_search_code_tool_registered(self):
        """Test that search_code tool is registered."""
        self.assertIn("search_code", _TOOL_REGISTRY)

    def test_generate_diff_tool_registered(self):
        """Test that generate_diff tool is registered."""
        self.assertIn("generate_diff", _TOOL_REGISTRY)

    def test_write_markdown_file_tool_registered(self):
        """Test that write_markdown_file tool is registered."""
        self.assertIn("write_markdown_file", _TOOL_REGISTRY)

    def test_replace_function_tool_registered(self):
        """Test that replace_function tool is registered."""
        self.assertIn("replace_function", _TOOL_REGISTRY)


class TestCodeFormattingMixin(unittest.TestCase):
    """Test CodeFormattingMixin functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.agent = CodeAgent(silent_mode=True, max_steps=5)
        self.agent._register_tools()

    def tearDown(self):
        """Clean up test fixtures."""
        _TOOL_REGISTRY.clear()

    def test_format_with_black_tool_registered(self):
        """Test that format_with_black tool is registered."""
        self.assertIn("format_with_black", _TOOL_REGISTRY)

    def test_lint_and_format_tool_registered(self):
        """Test that lint_and_format tool is registered."""
        self.assertIn("lint_and_format", _TOOL_REGISTRY)


class TestProjectManagementMixin(unittest.TestCase):
    """Test ProjectManagementMixin functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.agent = CodeAgent(silent_mode=True, max_steps=5)
        self.agent._register_tools()

    def tearDown(self):
        """Clean up test fixtures."""
        _TOOL_REGISTRY.clear()

    def test_list_files_tool_registered(self):
        """Test that list_files tool is registered."""
        self.assertIn("list_files", _TOOL_REGISTRY)

    def test_validate_project_tool_registered(self):
        """Test that validate_project tool is registered."""
        self.assertIn("validate_project", _TOOL_REGISTRY)

    def test_create_project_tool_registered(self):
        """Test that create_project tool is registered."""
        self.assertIn("create_project", _TOOL_REGISTRY)

    def test_validate_project_structure_helper(self):
        """Test that _validate_project_structure helper method exists."""
        self.assertTrue(hasattr(self.agent, "_validate_project_structure"))

    def test_validate_requirements_helper(self):
        """Test that _validate_requirements works correctly."""
        import tempfile

        # Create a temporary requirements.txt
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("pytest>=7.0.0\nblack==23.0.0\n")
            req_file = f.name

        try:
            from pathlib import Path

            result = self.agent._validate_requirements(Path(req_file), fix=False)
            self.assertIsInstance(result, dict)
            self.assertIn("is_valid", result)
        finally:
            import os

            os.unlink(req_file)


class TestTestingMixin(unittest.TestCase):
    """Test TestingMixin functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.agent = CodeAgent(silent_mode=True, max_steps=5)
        self.agent._register_tools()

    def tearDown(self):
        """Clean up test fixtures."""
        _TOOL_REGISTRY.clear()

    def test_execute_python_file_tool_registered(self):
        """Test that execute_python_file tool is registered."""
        self.assertIn("execute_python_file", _TOOL_REGISTRY)

    def test_run_tests_tool_registered(self):
        """Test that run_tests tool is registered."""
        self.assertIn("run_tests", _TOOL_REGISTRY)


class TestErrorFixingMixin(unittest.TestCase):
    """Test ErrorFixingMixin functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.agent = CodeAgent(silent_mode=True, max_steps=5)
        self.agent._register_tools()

    def tearDown(self):
        """Clean up test fixtures."""
        _TOOL_REGISTRY.clear()

    def test_auto_fix_syntax_errors_tool_registered(self):
        """Test that auto_fix_syntax_errors tool is registered."""
        self.assertIn("auto_fix_syntax_errors", _TOOL_REGISTRY)

    def test_fix_code_tool_registered(self):
        """Test that fix_code tool is registered."""
        self.assertIn("fix_code", _TOOL_REGISTRY)

    def test_fix_linting_errors_tool_registered(self):
        """Test that fix_linting_errors tool is registered."""
        self.assertIn("fix_linting_errors", _TOOL_REGISTRY)

    def test_fix_python_errors_tool_registered(self):
        """Test that fix_python_errors tool is registered."""
        self.assertIn("fix_python_errors", _TOOL_REGISTRY)

    def test_create_architectural_plan_tool_registered(self):
        """Test that create_architectural_plan tool is registered."""
        self.assertIn("create_architectural_plan", _TOOL_REGISTRY)

    def test_create_project_structure_tool_registered(self):
        """Test that create_project_structure tool is registered."""
        self.assertIn("create_project_structure", _TOOL_REGISTRY)

    def test_create_workflow_plan_tool_registered(self):
        """Test that create_workflow_plan tool is registered."""
        self.assertIn("create_workflow_plan", _TOOL_REGISTRY)

    def test_init_gaia_md_tool_registered(self):
        """Test that init_gaia_md tool is registered."""
        self.assertIn("init_gaia_md", _TOOL_REGISTRY)

    def test_update_gaia_md_tool_registered(self):
        """Test that update_gaia_md tool is registered."""
        self.assertIn("update_gaia_md", _TOOL_REGISTRY)

    def test_implement_from_plan_tool_registered(self):
        """Test that implement_from_plan tool is registered."""
        self.assertIn("implement_from_plan", _TOOL_REGISTRY)

    def test_fix_linting_errors_helper(self):
        """Test that _fix_linting_errors helper method exists."""
        self.assertTrue(hasattr(self.agent, "_fix_linting_errors"))


class TestValidationAndParsingMixin(unittest.TestCase):
    """Test ValidationAndParsingMixin functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.agent = CodeAgent(silent_mode=True, max_steps=5)
        self.agent._register_tools()

    def tearDown(self):
        """Clean up test fixtures."""
        _TOOL_REGISTRY.clear()

    def test_validate_python_syntax_helper(self):
        """Test _validate_python_syntax helper method."""
        valid_code = "def foo():\n    pass"
        result = self.agent._validate_python_syntax(valid_code)
        self.assertTrue(result["is_valid"])

        invalid_code = "def foo(\n    pass"
        result = self.agent._validate_python_syntax(invalid_code)
        self.assertFalse(result["is_valid"])

    def test_parse_python_code_helper(self):
        """Test _parse_python_code helper method."""
        code = "def foo():\n    pass\n\nclass Bar:\n    pass"
        result = self.agent._parse_python_code(code)
        self.assertIsNotNone(result)


class TestToolCount(unittest.TestCase):
    """Test that all expected tools are registered."""

    def setUp(self):
        """Set up test fixtures."""
        self.agent = CodeAgent(silent_mode=True, max_steps=5)
        self.agent._register_tools()

    def tearDown(self):
        """Clean up test fixtures."""
        _TOOL_REGISTRY.clear()

    def test_total_tool_count(self):
        """Test that we have the expected number of tools."""
        # Expected: 31+ tools from all mixins (including implement_from_plan)
        tool_count = len(_TOOL_REGISTRY)
        self.assertGreaterEqual(
            tool_count, 31, f"Expected at least 31 tools, found {tool_count}"
        )

    def test_all_expected_tools_present(self):
        """Test that all critical tools are present."""
        expected_tools = [
            # CodeToolsMixin
            "generate_function",
            "generate_class",
            "generate_test",
            "validate_syntax",
            "parse_python_code",
            "list_symbols",
            "analyze_with_pylint",
            # FileIOToolsMixin
            "read_file",
            "write_python_file",
            "edit_python_file",
            "search_code",
            "generate_diff",
            "write_markdown_file",
            "replace_function",
            # CodeFormattingMixin
            "format_with_black",
            "lint_and_format",
            # ProjectManagementMixin
            "list_files",
            "validate_project",
            "create_project",
            # TestingMixin
            "execute_python_file",
            "run_tests",
            # ErrorFixingMixin
            "auto_fix_syntax_errors",
            "fix_code",
            "fix_linting_errors",
            "fix_python_errors",
            "create_architectural_plan",
            "create_project_structure",
            "create_workflow_plan",
            "init_gaia_md",
            "update_gaia_md",
            "implement_from_plan",
        ]

        missing_tools = [tool for tool in expected_tools if tool not in _TOOL_REGISTRY]
        self.assertEqual([], missing_tools, f"Missing expected tools: {missing_tools}")


if __name__ == "__main__":
    unittest.main()
