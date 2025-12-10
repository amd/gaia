# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Project management tools mixin for Code Agent."""

import ast
import os
from pathlib import Path
from typing import Any, Dict, List


class ProjectManagementMixin:
    """Mixin providing project-level management and creation tools.

    This mixin provides tools for:
    - Creating complete project structures from requirements
    - Listing and validating project files
    - Comprehensive project validation (structure, requirements, code quality)
    - Multi-language support (Python, JavaScript/TypeScript, CSS, HTML)

    Tools provided:
    - list_files: List files and directories in a path
    - validate_project: Comprehensive project validation with auto-fix capability
    - create_project: Generate complete project from natural language description

    Helper methods:
    - _validate_project_structure: Validate project structure for consistency
    """

    def register_project_management_tools(self) -> None:
        """Register project management tools."""
        from gaia.agents.base.tools import tool

        @tool
        def list_files(path: str = ".") -> Dict[str, Any]:
            """List files and directories in the specified path.

            Args:
                path: Directory path to list (default: current directory)

            Returns:
                Dictionary with list of files and directories
            """
            try:
                items = os.listdir(path)
                files = [
                    item for item in items if os.path.isfile(os.path.join(path, item))
                ]
                dirs = [
                    item for item in items if os.path.isdir(os.path.join(path, item))
                ]

                return {
                    "status": "success",
                    "path": path,
                    "files": sorted(files),
                    "directories": sorted(dirs),
                    "total": len(items),
                }
            except FileNotFoundError:
                return {"status": "error", "error": f"Directory not found: {path}"}
            except PermissionError:
                return {"status": "error", "error": f"Permission denied: {path}"}
            except Exception as e:
                return {"status": "error", "error": str(e)}

        @tool
        def validate_project(project_path: str, fix: bool = False) -> Dict[str, Any]:
            """Comprehensive project validation for all file types and quality checks.

            This is the unified validation entry point that checks:
            - Python files: pylint, anti-patterns, Black formatting
            - JavaScript/TypeScript: ESLint (if available)
            - requirements.txt: hallucination detection
            - Project structure: entry points, essential files
            - CSS/HTML: basic validation

            Args:
                project_path: Path to the project directory
                fix: Whether to auto-fix issues where possible

            Returns:
                Dictionary with comprehensive validation results
            """
            try:
                path = Path(project_path)
                if not path.exists():
                    return {
                        "status": "error",
                        "error": f"Project not found: {project_path}",
                    }

                results = {
                    "status": "success",
                    "project": str(path),
                    "validations": {},
                    "total_errors": 0,
                    "total_warnings": 0,
                    "is_valid": True,
                }

                # Get all project files
                all_files = list(path.rglob("*"))
                py_files = [f for f in all_files if f.suffix == ".py"]
                js_files = [
                    f for f in all_files if f.suffix in [".js", ".jsx", ".ts", ".tsx"]
                ]
                css_files = [f for f in all_files if f.suffix in [".css", ".scss"]]
                html_files = [f for f in all_files if f.suffix in [".html", ".htm"]]

                # 1. Check project structure
                structure_result = self._validate_project_structure(path, all_files)
                results["validations"]["structure"] = structure_result
                results["total_errors"] += len(structure_result.get("errors", []))
                results["total_warnings"] += len(structure_result.get("warnings", []))

                # 2. Validate requirements.txt
                req_file = path / "requirements.txt"
                if req_file.exists():
                    req_result = self._validate_requirements(req_file, fix)
                    results["validations"]["requirements"] = req_result
                    results["total_errors"] += len(req_result.get("errors", []))
                    results["total_warnings"] += len(req_result.get("warnings", []))

                # 3. Validate Python files
                if py_files:
                    py_result = self._validate_python_files(py_files, fix)
                    results["validations"]["python"] = py_result
                    results["total_errors"] += py_result.get("total_errors", 0)
                    results["total_warnings"] += py_result.get("total_warnings", 0)

                # 4. Validate JavaScript/TypeScript files
                if js_files:
                    js_result = self._validate_javascript_files(js_files, fix)
                    results["validations"]["javascript"] = js_result
                    results["total_errors"] += js_result.get("total_errors", 0)
                    results["total_warnings"] += js_result.get("total_warnings", 0)

                # 5. Basic validation for CSS files
                if css_files:
                    css_result = self._validate_css_files(css_files)
                    results["validations"]["css"] = css_result
                    results["total_warnings"] += css_result.get("warnings", 0)

                # 6. Basic validation for HTML files
                if html_files:
                    html_result = self._validate_html_files(html_files)
                    results["validations"]["html"] = html_result
                    results["total_warnings"] += html_result.get("warnings", 0)

                # Overall status
                results["is_valid"] = results["total_errors"] == 0
                if results["is_valid"]:
                    if results["total_warnings"] > 0:
                        results["message"] = (
                            f"Validation passed with {results['total_warnings']} warnings"
                        )
                    else:
                        results["message"] = "All validations passed!"
                else:
                    results["message"] = (
                        f"Validation failed: {results['total_errors']} errors, "
                        f"{results['total_warnings']} warnings"
                    )

                return results

            except Exception as e:
                return {"status": "error", "error": str(e)}

        @tool
        def create_project(query: str) -> Dict[str, Any]:
            """Create a complete Python project from requirements.

            Workflow:
            1. Generate detailed plan with architecture and class/function outlines
            2. Implement files one-by-one with validation
            3. Generate comprehensive tests
            4. Run tests and fix issues
            5. Final validation and fixes
            6. Summary report

            Args:
                query: The project requirements/description

            Returns:
                Dictionary with project creation results
            """
            try:
                self.console.print_header("🚀 Starting Project Generation")

                # Phase 1: Generate detailed architectural plan
                self.console.print_info("📋 Phase 1: Creating architectural plan...")

                plan_prompt = f"""Create a detailed architectural plan for: {query}

Generate a comprehensive JSON response with:
{{
  "project_name": "short_snake_case_name",
  "architecture": {{
    "overview": "Detailed description of what this application does",
    "patterns": ["List", "of", "design", "patterns"],
    "technologies": ["List", "of", "frameworks", "libraries", "databases"]
  }},
  "modules": [
    {{
      "name": "filename.py",
      "purpose": "What this module does",
      "classes": [
        {{"name": "ClassName", "purpose": "What this class handles", "methods": ["method1", "method2"]}}
      ],
      "functions": [
        {{"name": "function_name", "signature": "function_name(args) -> ReturnType", "purpose": "What it does"}}
      ]
    }}
  ],
  "tests": [
    {{"name": "test_filename.py", "coverage": "What this test file covers"}}
  ]
}}

IMPORTANT: Include ALL modules needed for a complete, working application.
For the given requirements, think about:
- Entry points and main application flow
- Data models and database structure
- Business logic and services
- API endpoints or user interface
- Authentication and security
- Configuration and utilities
- Comprehensive test coverage

Return ONLY valid JSON."""

                plan_response = self.chat.send(plan_prompt, max_tokens=3000).text

                import json

                try:
                    # Clean the response if it has markdown code blocks
                    if "```json" in plan_response:
                        plan_response = plan_response.split("```json")[1].split("```")[
                            0
                        ]
                    elif "```" in plan_response:
                        plan_response = plan_response.split("```")[1].split("```")[0]

                    plan_data = json.loads(plan_response)
                    project_name = plan_data.get("project_name", "my_project")

                    # Basic sanitization - lowercase and replace spaces
                    project_name = (
                        project_name.lower().strip().replace(" ", "_").replace("-", "_")
                    )

                    # Check if name is valid (not too long, no special chars, doesn't already exist)
                    max_retries = 3
                    for retry in range(max_retries):
                        issues = []

                        # Check if folder already exists
                        if os.path.exists(project_name):
                            issues.append(f"folder '{project_name}' already exists")

                        # Check if name is too long
                        if len(project_name) > 30:
                            issues.append(
                                f"name too long ({len(project_name)} chars, max 30)"
                            )

                        # Check if name has invalid characters
                        if not project_name.replace("_", "").replace(".", "").isalnum():
                            issues.append(
                                "name contains invalid characters (only a-z, 0-9, _ allowed)"
                            )

                        # Check if name is empty
                        if not project_name or project_name == "_":
                            issues.append("name is empty or invalid")

                        # If valid, break
                        if not issues:
                            break

                        # Ask LLM to fix the name
                        if retry < max_retries - 1:
                            fix_prompt = f"""The project name "{project_name}" has issues: {', '.join(issues)}

Please provide a new, valid project name that:
- Is short and descriptive (max 30 characters)
- Uses snake_case (lowercase with underscores)
- Doesn't already exist
- Only contains letters, numbers, and underscores

Original project description: {query}

Respond with ONLY the new project name, nothing else."""

                            new_name = self.chat.send(
                                fix_prompt, max_tokens=50
                            ).text.strip()
                            # Clean the response
                            new_name = (
                                new_name.lower()
                                .strip()
                                .replace(" ", "_")
                                .replace("-", "_")
                            )
                            # Remove quotes if present
                            new_name = new_name.strip('"').strip("'")

                            self.console.print_warning(
                                f"⚠️  Project name '{project_name}' invalid: {', '.join(issues)}"
                            )
                            self.console.print_info(f"  Retrying with: '{new_name}'")
                            project_name = new_name
                        else:
                            # Last resort - generate unique name
                            import random

                            project_name = f"project_{random.randint(1000, 9999)}"
                            self.console.print_warning(
                                f"⚠️  Using fallback name: '{project_name}'"
                            )
                            break

                    modules = plan_data.get("modules", [])
                    test_modules = plan_data.get("tests", [])
                    architecture = plan_data.get("architecture", {})

                    # Ensure we have at least basic files
                    if not any(m["name"] == "requirements.txt" for m in modules):
                        modules.append(
                            {
                                "name": "requirements.txt",
                                "purpose": "Python dependencies",
                            }
                        )
                    if not any(m["name"] == "README.md" for m in modules):
                        modules.append(
                            {"name": "README.md", "purpose": "Project documentation"}
                        )

                except (json.JSONDecodeError, TypeError) as e:
                    self.console.print_warning(
                        f"⚠️ Could not parse JSON plan, using fallback: {str(e)[:100]}"
                    )
                    # Fallback plan
                    project_name = "my_project"
                    modules = [
                        {"name": "main.py", "purpose": "Entry point"},
                        {"name": "core.py", "purpose": "Core logic"},
                        {"name": "requirements.txt", "purpose": "Dependencies"},
                        {"name": "README.md", "purpose": "Documentation"},
                    ]
                    test_modules = [{"name": "test_main.py", "coverage": "main tests"}]
                    architecture = {"overview": query}

                # Create project directory
                os.makedirs(project_name, exist_ok=True)
                created_files = []
                implementation_issues = []
                test_results = {}

                # Write detailed PLAN.md with architecture and outlines
                plan_content = f"""# {project_name} - Architectural Plan

## Project Overview
{architecture.get('overview', query)}

## Architecture
- **Patterns**: {', '.join(architecture.get('patterns', ['Modular']))}
- **Technologies**: {', '.join(architecture.get('technologies', ['Python']))}

## Implementation Tasks

### Phase 1: Project Setup
- [ ] Create project structure
- [ ] Generate PLAN.md
- [ ] Set up requirements.txt

### Phase 2: Core Modules
"""
                for module in modules:
                    plan_content += f"- [ ] Implement `{module['name']}` - {module.get('purpose', 'Core functionality')}\n"

                plan_content += "\n### Phase 3: Test Suite\n"
                for test in test_modules:
                    plan_content += f"- [ ] Generate `{test['name']}` - {test.get('coverage', 'Unit tests')}\n"

                plan_content += """
### Phase 4: Quality Assurance
- [ ] Run all tests
- [ ] Fix any test failures
- [ ] Apply Black formatting
- [ ] Fix linting issues

### Phase 5: Documentation
- [ ] Verify README.md completeness
- [ ] Add usage examples
- [ ] Document API if applicable

## Module Specifications
"""
                for module in modules:
                    plan_content += f"\n### {module['name']}\n"
                    plan_content += (
                        f"**Purpose**: {module.get('purpose', 'Implementation')}\n\n"
                    )

                    if module.get("classes"):
                        plan_content += "**Classes**:\n"
                        for cls in module["classes"]:
                            plan_content += (
                                f"- `{cls['name']}`: {cls.get('purpose', '')}\n"
                            )
                            if cls.get("methods"):
                                plan_content += (
                                    f"  - Methods: {', '.join(cls['methods'])}\n"
                                )

                    if module.get("functions"):
                        plan_content += "\n**Functions**:\n"
                        for func in module["functions"]:
                            plan_content += f"- `{func.get('signature', func['name'])}`: {func.get('purpose', '')}\n"

                plan_content += "\n## Test Coverage\n"
                for test in test_modules:
                    plan_content += (
                        f"- **{test['name']}**: {test.get('coverage', 'Tests')}\n"
                    )

                plan_content += "\n## Implementation Order\n"
                plan_content += "1. Core modules and data structures\n"
                plan_content += "2. Business logic implementation\n"
                plan_content += "3. Integration and API layers\n"
                plan_content += "4. Comprehensive test suite\n"
                plan_content += "5. Documentation and examples\n"

                # Start streaming preview for PLAN.md (markdown file)
                self.console.start_file_preview(
                    "PLAN.md", max_lines=20, title_prefix="📋"
                )

                # Stream the content in chunks for visual effect
                plan_chunks = []
                chunk_size = 500  # Characters per chunk
                for i in range(0, len(plan_content), chunk_size):
                    chunk = plan_content[i : i + chunk_size]
                    plan_chunks.append(chunk)
                    self.console.update_file_preview(chunk)
                    # Small delay for visual streaming effect
                    import time

                    time.sleep(0.05)

                # End the preview
                self.console.stop_file_preview()

                # Write PLAN.md
                plan_path = os.path.join(project_name, "PLAN.md")
                with open(plan_path, "w", encoding="utf-8") as f:
                    f.write(plan_content)
                created_files.append(plan_path)

                plan_lines = plan_content.count("\n") + 1
                self.console.print_success(
                    f"✅ Created detailed PLAN.md with architecture ({plan_lines} lines)"
                )

                # Project setup phase complete - agent can now use update_plan_progress tool

                # Phase 2: Implement files one-by-one with validation
                self.console.print_info(
                    "\n📝 Phase 2: Implementing modules one-by-one..."
                )

                # Sort modules to implement core/config files first
                priority_order = ["config", "models", "database", "utils", "core"]
                modules_sorted = sorted(
                    modules,
                    key=lambda m: (
                        0
                        if "requirements.txt" in m["name"]
                        else (
                            1
                            if any(p in m["name"].lower() for p in priority_order)
                            else 2 if "main.py" in m["name"] else 3
                        )
                    ),
                )

                # Initial load of PLAN.md
                plan_path = os.path.join(project_name, "PLAN.md")

                for i, module in enumerate(modules_sorted, 1):
                    filename = module["name"]
                    progress_pct = int((i / len(modules)) * 100)
                    self.console.print_info(
                        f"  [{i}/{len(modules)}] ({progress_pct}%) Generating {filename}..."
                    )

                    file_path = os.path.join(project_name, filename)

                    # Create subdirectories if needed
                    dir_path = os.path.dirname(file_path)
                    if dir_path:
                        os.makedirs(dir_path, exist_ok=True)

                    # Re-read PLAN.md each time to get latest updates
                    if os.path.exists(plan_path):
                        with open(plan_path, "r", encoding="utf-8") as f:
                            plan_context = f.read()
                    else:
                        plan_context = plan_content

                    # Generate with architecture context including latest PLAN.md
                    context = (
                        f"{query}\n\nProject Plan (Current State):\n{plan_context}\n\n"
                        f"Current Module:\n{json.dumps(module, indent=2)}"
                    )
                    code = self._generate_code_for_file(
                        filename=filename,
                        purpose=module.get("purpose", ""),
                        context=context,
                    )

                    # Write initial version
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(code)

                    # Validate and fix iteratively (max 3 attempts)
                    if filename.endswith(".py"):
                        for attempt in range(3):
                            try:
                                # Syntax check

                                ast.parse(code)

                                # Anti-pattern check
                                antipattern_result = self._check_antipatterns(
                                    Path(file_path), code
                                )
                                if antipattern_result["errors"]:
                                    self.console.print_warning(
                                        f"    ⚠️  Anti-patterns detected: {len(antipattern_result['errors'])} issues"
                                    )
                                    implementation_issues.append(
                                        {
                                            "file": filename,
                                            "type": "antipattern",
                                            "issues": antipattern_result["errors"][:2],
                                        }
                                    )

                                # If syntax is valid, we're done
                                self.console.print_success(
                                    f"    ✅ {filename} validated"
                                )
                                break

                            except SyntaxError as e:
                                if attempt < 2:
                                    self.console.print_warning(
                                        f"    🔧 Fixing syntax error (attempt {attempt+1}/3)"
                                    )
                                    code = self._fix_code_with_llm(
                                        code, file_path, str(e)
                                    )
                                    with open(file_path, "w", encoding="utf-8") as f:
                                        f.write(code)
                                else:
                                    self.console.print_error(
                                        f"    ❌ Could not fix syntax in {filename}"
                                    )
                                    implementation_issues.append(
                                        {
                                            "file": filename,
                                            "type": "syntax",
                                            "error": str(e),
                                        }
                                    )

                    created_files.append(file_path)

                # Phase 3: Generate comprehensive tests
                self.console.print_info("\n🧪 Phase 3: Generating test suite...")

                for i, test in enumerate(test_modules, 1):
                    test_filename = test["name"]
                    progress_pct = int((i / len(test_modules)) * 100)
                    self.console.print_info(
                        f"  [{i}/{len(test_modules)}] ({progress_pct}%) Generating {test_filename}..."
                    )

                    test_path = os.path.join(project_name, test_filename)

                    # Re-read PLAN.md to get latest updates
                    if os.path.exists(plan_path):
                        with open(plan_path, "r", encoding="utf-8") as f:
                            plan_context = f.read()
                    else:
                        plan_context = plan_content

                    # Generate test with context about what to test and latest PLAN.md
                    test_context = f"{query}\n\nProject Plan (Current State):\n{plan_context}\n\nTest Coverage: {test.get('coverage', '')}\n\nModules to test:\n"
                    for module in modules:
                        if not module["name"].startswith("test_"):
                            test_context += (
                                f"- {module['name']}: {module.get('purpose', '')}\n"
                            )

                    # Add timeout handling for test generation
                    try:
                        import threading

                        test_code = None
                        generation_error = None

                        def generate_with_timeout(
                            test_filename_param, test_param, test_context_param
                        ):
                            nonlocal test_code, generation_error
                            try:
                                test_code = self._generate_code_for_file(
                                    filename=test_filename_param,
                                    purpose=f"Unit tests for {test_param.get('coverage', 'functionality')}",
                                    context=test_context_param,
                                )
                            except Exception as e:
                                generation_error = e

                        # Run generation in a thread with timeout
                        gen_thread = threading.Thread(
                            target=generate_with_timeout,
                            args=(test_filename, test, test_context),
                        )
                        gen_thread.daemon = True
                        gen_thread.start()
                        gen_thread.join(
                            timeout=180
                        )  # 180 second (3 min) timeout for test generation

                        if gen_thread.is_alive():
                            self.console.print_warning(
                                f"    ⚠️  Test generation timeout for {test_filename}, using placeholder..."
                            )
                            # Generate a simple placeholder test
                            test_code = f'''"""Unit tests for {test.get('coverage', 'functionality')}."""
import unittest

class TestPlaceholder(unittest.TestCase):
    """Placeholder tests - generation timed out."""

    def test_placeholder(self):
        """Placeholder test - needs implementation."""
        self.skipTest("Test generation timed out - needs manual implementation")

if __name__ == "__main__":
    unittest.main()
'''
                        elif generation_error is not None:
                            if isinstance(generation_error, Exception):
                                raise generation_error
                            else:
                                raise Exception(
                                    f"Test generation error: {generation_error}"
                                )
                        elif not test_code:
                            raise Exception("No test code generated")

                    except Exception as e:
                        self.console.print_warning(
                            f"    ⚠️  Failed to generate {test_filename}: {str(e)[:100]}"
                        )
                        # Generate a simple placeholder test
                        test_code = f'''"""Unit tests for {test.get('coverage', 'functionality')}."""
import unittest

class TestPlaceholder(unittest.TestCase):
    """Placeholder tests - generation failed."""

    def test_placeholder(self):
        """Placeholder test - needs implementation."""
        self.skipTest("Test generation failed - needs manual implementation")

if __name__ == "__main__":
    unittest.main()
'''

                    with open(test_path, "w", encoding="utf-8") as f:
                        f.write(test_code)

                    # Validate test file syntax
                    try:

                        ast.parse(test_code)
                        self.console.print_success(f"    ✅ {test_filename} validated")
                    except SyntaxError as e:
                        self.console.print_warning(
                            f"    ⚠️  Syntax issues in {test_filename}, attempting fix..."
                        )
                        test_code = self._fix_code_with_llm(
                            test_code, test_path, str(e)
                        )
                        with open(test_path, "w", encoding="utf-8") as f:
                            f.write(test_code)

                    created_files.append(test_path)

                # Phase 3.5: Apply Black formatting to all Python files
                self.console.print_info(
                    "\n🎨 Phase 3.5: Applying Black formatting to all Python files..."
                )

                # Format all Python files in the project
                python_files = []
                for f in created_files:
                    file_path = Path(f) if isinstance(f, str) else f
                    if file_path.suffix == ".py":
                        python_files.append(file_path)

                formatted_count = 0

                for py_file in python_files:
                    if py_file.exists():
                        format_result = self._execute_tool(
                            "format_with_black", {"file_path": str(py_file)}
                        )
                        if format_result.get("formatted"):
                            formatted_count += 1

                if formatted_count > 0:
                    self.console.print_success(
                        f"✅ Formatted {formatted_count} Python file(s) with Black"
                    )
                else:
                    self.console.print_info(
                        "✓ All Python files already properly formatted"
                    )

                # Phase 4: Run tests and fix issues
                self.console.print_info(
                    "\n🏃 Phase 4: Running tests and fixing issues..."
                )

                test_run_result = self._execute_tool(
                    "run_tests", {"project_path": project_name, "timeout": 30}
                )
                if test_run_result.get("status") == "success":
                    if test_run_result.get("tests_passed"):
                        self.console.print_success("✅ All tests passed!")
                        test_results["status"] = "passed"
                        test_results["details"] = "All tests executed successfully"
                    else:
                        # Show test failure details
                        failure_summary = test_run_result.get("failure_summary", "")
                        stdout = test_run_result.get("stdout", "")

                        self.console.print_warning(
                            f"⚠️  Some tests failed: {failure_summary}"
                            if failure_summary
                            else "⚠️  Some tests failed, attempting fixes..."
                        )

                        # Extract and show failed test names from pytest output
                        if stdout:
                            import re

                            # Get command that was run
                            test_command = test_run_result.get("command", "pytest")

                            # Look for FAILED lines in pytest output
                            failed_tests = re.findall(r"FAILED (.*?) -", stdout)
                            if failed_tests:
                                self.console.print_info(
                                    f"\n  Failed tests ({len(failed_tests)}):"
                                )
                                for test in failed_tests[:5]:  # Show first 5
                                    self.console.print_info(f"    • {test}")
                                if len(failed_tests) > 5:
                                    self.console.print_info(
                                        f"    ... and {len(failed_tests) - 5} more"
                                    )

                            # Show terminal output preview - just raw output in a panel
                            # Take first 20 lines of pytest output
                            lines = stdout.split("\n")[:20]
                            if lines:
                                if (
                                    hasattr(self.console, "console")
                                    and self.console.console
                                ):
                                    from rich.panel import Panel

                                    # Show command and raw output
                                    preview_text = f"$ {test_command}\n\n" + "\n".join(
                                        lines
                                    )
                                    self.console.console.print(
                                        Panel(
                                            preview_text,
                                            title="Test Output Preview",
                                            border_style="yellow",
                                            expand=False,
                                        )
                                    )
                                else:
                                    print("\n  Test Output Preview:")
                                    print("  " + "─" * 70)
                                    print(f"  $ {test_command}\n")
                                    for line in lines:
                                        print(f"  {line}")
                                    print("  " + "─" * 70)

                        test_results["status"] = "partial"
                        test_results["stderr"] = test_run_result.get("stderr", "")
                        test_results["stdout"] = stdout
                        test_results["failure_summary"] = failure_summary

                        # Try to fix test failures
                        for attempt in range(2):
                            self.console.print_info(
                                f"  🔧 Fix attempt {attempt+1}/2..."
                            )

                            # Run auto_fix_syntax_errors on the project
                            fix_result = self._execute_tool(
                                "auto_fix_syntax_errors", {"project_path": project_name}
                            )
                            if fix_result.get("files_fixed"):
                                fixed_files = fix_result["files_fixed"]
                                self.console.print_info(
                                    f"    Fixed {len(fixed_files)} files:"
                                )
                                for file in fixed_files[:3]:  # Show first 3
                                    self.console.print_info(f"      • {file}")
                                if len(fixed_files) > 3:
                                    self.console.print_info(
                                        f"      ... and {len(fixed_files) - 3} more"
                                    )

                                # Re-run tests
                                self.console.print_info("    Re-running tests...")
                                test_run_result = self._execute_tool(
                                    "run_tests",
                                    {"project_path": project_name, "timeout": 30},
                                )
                                if test_run_result.get("tests_passed"):
                                    self.console.print_success(
                                        "    ✅ Tests now passing!"
                                    )
                                    test_results["status"] = "passed"
                                    break
                                else:
                                    # Show what's still failing
                                    new_failure_summary = test_run_result.get(
                                        "failure_summary", ""
                                    )
                                    if new_failure_summary:
                                        self.console.print_warning(
                                            f"    Still failing: {new_failure_summary}"
                                        )
                            else:
                                self.console.print_info(
                                    "    No syntax errors found to fix"
                                )
                else:
                    self.console.print_warning(
                        f"⚠️  Could not run tests: {test_run_result.get('error', 'Unknown error')}"
                    )
                    test_results["status"] = "error"
                    test_results["error"] = test_run_result.get("error", "")

                # Phase 5: Final comprehensive validation
                self.console.print_info("\n🔍 Phase 5: Final project validation...")

                final_validation = self._execute_tool(
                    "validate_project", {"project_path": project_name, "fix": True}
                )

                # Try to fix any remaining issues
                if not final_validation.get("is_valid"):
                    self.console.print_info("  🔧 Attempting final fixes...")

                    for attempt in range(2):
                        if final_validation.get("total_errors", 0) == 0:
                            break

                        # Run auto-fix
                        auto_fix_result = self._execute_tool(
                            "auto_fix_syntax_errors", {"project_path": project_name}
                        )
                        if auto_fix_result.get("files_fixed"):
                            self.console.print_info(
                                f"    Fixed {len(auto_fix_result['files_fixed'])} files"
                            )

                        # Re-validate
                        final_validation = self._execute_tool(
                            "validate_project",
                            {"project_path": project_name, "fix": True},
                        )
                        if final_validation.get("is_valid"):
                            self.console.print_success(
                                "✅ All validation checks passed!"
                            )
                            break

                # Phase 6: Generate summary
                self.console.print_header("\n📊 Project Generation Complete!")

                # Build summary message
                summary = f"""
## Project: {project_name}

### 📋 Architecture
- **Overview**: {architecture.get('overview', query)[:100]}...
- **Technologies**: {', '.join(architecture.get('technologies', ['Python']))}
- **Patterns**: {', '.join(architecture.get('patterns', ['Modular']))}

### 📁 Generated Files ({len(created_files)} total)
- **Core Modules**: {len([f for f in created_files if f.endswith('.py') and 'test' not in f])}
- **Test Files**: {len([f for f in created_files if 'test' in f])}
- **Documentation**: {len([f for f in created_files if f.endswith('.md')])}
- **Configuration**: {len([f for f in created_files if f.endswith(('.txt', '.yml', '.yaml', '.json'))])}

### ✅ Quality Metrics
- **Syntax Validation**: {'✅ Passed' if not implementation_issues else f'⚠️  {len(implementation_issues)} issues'}
- **Test Results**: {test_results.get('status', 'Not run').title()}
- **Code Quality**: {final_validation.get('total_errors', 0)} errors, {final_validation.get('total_warnings', 0)} warnings
- **Anti-patterns**: {'None detected' if not any(i['type'] == 'antipattern' for i in implementation_issues) else 'Some detected'}

### 🎯 Ready to Use
The project is structured and ready for development. Key features:
{chr(10).join(f"- {module['name']}: {module.get('purpose', '')}" for module in modules[:5])}

### 🚀 Next Steps
1. Review PLAN.md for architecture details
2. Install dependencies: `pip install -r requirements.txt`
3. Run the application: `python main.py`
4. Run tests: `pytest`
"""

                if hasattr(self.console, "console") and self.console.console:
                    from rich.panel import Panel

                    self.console.console.print(
                        Panel(summary, title="Project Summary", expand=False)
                    )
                else:
                    print(summary)

                return {
                    "status": "success",
                    "project_name": project_name,
                    "files_created": created_files,
                    "validation": final_validation,
                    "test_results": test_results,
                    "implementation_issues": implementation_issues,
                    "summary": summary,
                    "message": f"✅ Successfully created {project_name} with {len(created_files)} files",
                }

            except Exception as e:
                return {"status": "error", "error": str(e)}

    def _validate_project_structure(
        self, _project_path: Path, files: List[Path]
    ) -> Dict[str, Any]:
        """Validate project structure for consistency issues.

        Args:
            _project_path: Path to the project directory (unused currently)
            files: List of Path objects for all files in the project

        Returns:
            Dictionary with validation results including errors and warnings
        """
        errors = []
        warnings = []

        filenames = [f.name for f in files if f.is_file()]

        # Check for multiple entry points (common issue)
        entry_points = ["main.py", "app.py", "run.py", "__main__.py", "wsgi.py"]
        found_entries = [ep for ep in entry_points if ep in filenames]

        if len(found_entries) > 1:
            errors.append(
                f"Multiple entry points found: {', '.join(found_entries)}. "
                "Choose ONE pattern: either monolithic or modular."
            )

        # Check for essential files
        if "README.md" not in filenames and "readme.md" not in filenames:
            errors.append("Missing README.md")

        if "requirements.txt" not in filenames and "pyproject.toml" not in filenames:
            warnings.append("Missing requirements.txt or pyproject.toml")

        # Check for PLAN.md
        if "PLAN.md" not in filenames and "plan.md" not in filenames:
            warnings.append(
                "No PLAN.md found (architectural plan should be created first)"
            )

        # Check for duplicate models (common issue)
        model_files = [
            f for f in files if "model" in f.name.lower() and f.suffix == ".py"
        ]
        if len(model_files) > 2:
            warnings.append(
                f"Multiple model files found ({len(model_files)}). "
                "Check for duplicate definitions."
            )

        return {"is_valid": len(errors) == 0, "errors": errors, "warnings": warnings}
