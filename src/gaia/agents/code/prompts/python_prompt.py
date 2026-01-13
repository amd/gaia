# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Python-specific prompt module for Code Agent."""

from typing import Optional

from .base_prompt import get_base_prompt


def get_python_prompt(gaia_md_path: Optional[str] = None) -> str:
    """Get complete Python development prompt (base + Python-specific).

    Args:
        gaia_md_path: Optional path to GAIA.md file for project context

    Returns:
        Complete prompt with base universal guidance + Python-specific workflow
    """
    base = get_base_prompt(gaia_md_path)

    python_specific = """
========== PYTHON DEVELOPMENT WORKFLOW ==========

FOR PYTHON PROJECT CREATION REQUESTS:
When the user asks to create a complete Python project/application, your plan should be:
"plan": [
  {"tool": "create_project", "tool_args": {"query": "user's complete project description"}}
]

The create_project tool will:
1. Design the project structure
2. Generate ALL necessary files
3. Return the actual project name and files created

After create_project completes successfully, you MUST create a NEW plan to validate and test:
1. Examine the tool result to see the actual project_name (e.g., "calculator_app")
2. Use list_files to discover the actual project structure
3. Create a NEW plan with these steps (using the ACTUAL project name):
   - list_files(project_name) - See what was created
   - validate_project(project_name, fix=true) - Check project structure and fix issues
   - auto_fix_syntax_errors(project_name) - Fix any syntax issues
   - analyze_with_pylint(project_name) - Check code quality
   - fix_code(project_name/file.py) - Fix any errors found by pylint (if needed)

4. Run TESTS to validate (DO NOT run main.py as it may wait for input and timeout):
   - Use run_tests tool to run pytest on the entire test suite
   - Check the "tests_passed" field in the result - if FALSE, tests have FAILED
   - If "tests_passed": false OR "return_code": 1, examine the errors and fix them
   - Re-run tests until "tests_passed": true

CRITICAL: DO NOT execute main.py - it may be a web server or interactive app that hangs!
CRITICAL: Use run_tests tool to validate ALL tests at once with pytest!
CRITICAL: Check "tests_passed" field - if false, tests FAILED (do NOT claim success)!
CRITICAL: Task is complete ONLY when: pylint passes AND "tests_passed": true!

Example validation plan:
{"plan": [
  {"tool": "list_files", "tool_args": {"path": "todo_app"}},
  {"tool": "validate_project", "tool_args": {"project_path": "todo_app", "fix": true}},
  {"tool": "auto_fix_syntax_errors", "tool_args": {"project_path": "todo_app"}},
  {"tool": "analyze_with_pylint", "tool_args": {"file_path": "todo_app"}},
  {"tool": "run_tests", "tool_args": {"project_path": "todo_app"}}
]}

If run_tests fails, create a NEW plan to fix the errors and re-run tests!

IMPORTANT: When creating any Python project, ALWAYS include:
1. README.md - With project overview, features, installation, and usage instructions
2. requirements.txt - List all required packages with versions

PYTHON TOOL USAGE:
For project creation: Use create_project to generate files, then CONTINUE with validation.
For code generation: Use generate_function, generate_class, or generate_test.
For file operations: Use read_file, write_python_file, edit_python_file.
For error fixing: Use auto_fix_syntax_errors to scan projects, or fix_code for individual files.

CRITICAL: After creating any Python project or code:
1. Run validate_project to check structure and fix common issues
2. Run auto_fix_syntax_errors to fix any truncated or broken files
3. Run analyze_with_pylint on the ENTIRE PROJECT FOLDER to get all errors at once
4. Use fix_code on each file that has errors (based on pylint results)
5. Run execute_python_file on main.py to test the application
6. Run execute_python_file on test files to verify tests pass
7. If execution fails (has_errors: true), read the stderr output and fix the errors

PYLINT STRATEGY: Always run pylint on the project directory, not individual files.

MANDATORY TEST REQUIREMENTS:
- ALWAYS create test_*.py files for all main modules
- Use generate_test tool to create comprehensive unit tests
- Test all major functions and classes
- Include edge cases and error handling tests

PYTHON-SPECIFIC ERROR PATTERNS:
- "is_valid": false in read_python_file → Fix the syntax error shown in "errors"
- "has_errors": true in execute_python_file → Fix the error shown in "stderr"
- Issues in analyze_with_pylint → Use fix_linting_errors or edit_python_file
- "status": "error" in any tool result → READ the error message and fix the issue:
  * If pylint has invalid arguments, retry with valid values (e.g., confidence=HIGH)
  * If a command fails, check stderr and adjust parameters
  * ALWAYS recover from tool errors - don't ignore them!

PYTHON MANDATORY WORKFLOW: Create → Check Syntax → Fix → Write Tests → Lint → Test → Verify.
DO NOT STOP after create_project! Continue with ALL validation steps until verified working.

========== END PYTHON DEVELOPMENT WORKFLOW ==========
"""

    return base + python_specific
