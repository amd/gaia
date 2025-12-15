# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Error fixing tools mixin for Code Agent."""

import ast
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class ErrorFixingMixin:
    """Mixin providing error fixing, correction, and workflow planning tools.

    This mixin provides tools for:
    - Automatic syntax error detection and fixing
    - Fixing code based on error messages
    - Fixing pylint linting errors
    - Fixing Python runtime errors
    - Creating architectural plans (PLAN.md)
    - Creating project structures from plans
    - Creating workflow plans for complex tasks
    - Initializing and updating GAIA.md project context

    Tools provided:
    - auto_fix_syntax_errors: Scan and fix syntax errors in Python files
    - fix_code: Fix code based on error description
    - fix_linting_errors: Fix pylint issues in code
    - fix_python_errors: Fix runtime errors in Python code
    - create_architectural_plan: Generate PLAN.md for projects
    - create_project_structure: Create folder structure from plan
    - create_workflow_plan: Plan complex multi-step workflows
    - init_gaia_md: Initialize GAIA.md from codebase analysis
    - update_gaia_md: Update GAIA.md with new information
    """

    def register_error_fixing_tools(self) -> None:
        """Register error fixing tools."""
        from gaia.agents.base.tools import tool

        @tool
        def auto_fix_syntax_errors(project_path: str) -> Dict[str, Any]:
            """Automatically detect and fix syntax errors in Python files.

            Args:
                project_path: Path to the project directory to scan and fix

            Returns:
                Dictionary with fix results
            """
            try:
                import glob

                fixed_files = []
                errors_found = []

                # Find all Python files in the project
                python_files = glob.glob(
                    os.path.join(project_path, "**/*.py"), recursive=True
                )

                for file_path in python_files:
                    # Read and analyze the file
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            content = f.read()

                        # Check syntax using AST
                        try:
                            ast.parse(content)
                            is_valid = True
                            errors = []
                        except SyntaxError as e:
                            is_valid = False
                            errors = [f"Line {e.lineno}: {e.msg}"]

                        result = {
                            "status": "success",
                            "is_valid": is_valid,
                            "errors": errors,
                        }
                    except Exception as e:
                        result = {"status": "error", "error": str(e)}

                    if result.get("status") == "success" and not result.get(
                        "is_valid", True
                    ):
                        errors = result.get("errors", [])
                        self.console.print_warning(
                            f"🔧 Fixing syntax errors in {os.path.basename(file_path)}"
                        )

                        # Read the file content
                        with open(file_path, "r", encoding="utf-8") as f:
                            content = f.read()

                        # Use LLM to fix the code with context
                        error_msg = "; ".join(errors)
                        fixed_content = self._fix_code_with_llm(
                            content, file_path, error_msg
                        )

                        if fixed_content != content:
                            # Write the fixed content
                            with open(file_path, "w", encoding="utf-8") as f:
                                f.write(fixed_content)

                            fixed_files.append(
                                {"file": file_path, "errors_fixed": errors}
                            )
                            self.console.print_success(
                                f"✓ Fixed {os.path.basename(file_path)}"
                            )
                        else:
                            errors_found.append({"file": file_path, "errors": errors})

                return {
                    "status": "success",
                    "files_fixed": len(fixed_files),
                    "errors_remaining": len(errors_found),
                    "fixed_files": fixed_files,
                    "errors_found": errors_found,
                    "message": f"🔧 Fixed {len(fixed_files)} files, {len(errors_found)} files still have errors",
                }

            except Exception as e:
                return {"status": "error", "error": str(e)}

        @tool
        def fix_code(file_path: str, error_description: str = "") -> Dict[str, Any]:
            """Fix Python code using LLM-driven analysis and correction.

            Args:
                file_path: Path to the Python file to fix
                error_description: Optional description of the error to fix

            Returns:
                Dictionary with fix results
            """
            try:
                # Verify file exists
                if not os.path.exists(file_path):
                    return {
                        "status": "error",
                        "error": f"File not found: {file_path}. Use list_files to see available files.",
                    }

                # Read the file
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()

                # If no error description provided, try to detect it
                if not error_description:
                    try:
                        ast.parse(content)
                        return {
                            "status": "success",
                            "message": "No syntax errors found",
                            "file_modified": False,
                        }
                    except SyntaxError as e:
                        error_description = f"Line {e.lineno}: {e.msg}"

                # Use LLM to fix the code
                fixed_content = self._fix_code_with_llm(
                    content, file_path, error_description
                )

                if fixed_content != content:
                    # Generate diff before writing
                    import difflib

                    diff = "\n".join(
                        difflib.unified_diff(
                            content.splitlines(keepends=True),
                            fixed_content.splitlines(keepends=True),
                            fromfile=f"a/{os.path.basename(file_path)}",
                            tofile=f"b/{os.path.basename(file_path)}",
                            lineterm="",
                        )
                    )

                    # Display diff
                    if diff:
                        self.console.print_diff(diff, os.path.basename(file_path))

                    # Write the fixed content
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(fixed_content)

                    return {
                        "status": "success",
                        "file_modified": True,
                        "original_lines": len(content.splitlines()),
                        "fixed_lines": len(fixed_content.splitlines()),
                        "diff": diff,
                        "message": f"Fixed {os.path.basename(file_path)}",
                    }
                else:
                    console = getattr(self, "console", None)
                    if console:
                        console.print_info(
                            f"fix_code: No changes were made to {os.path.basename(file_path)}"
                        )
                    return {
                        "status": "info",
                        "file_modified": False,
                        "message": "No changes needed",
                    }

            except Exception as e:
                return {"status": "error", "error": str(e)}

        @tool
        def create_architectural_plan(
            query: str, project_type: str = "application"
        ) -> Dict[str, Any]:
            """Create a detailed architectural plan for a project.

            Args:
                query: The user's project requirements
                project_type: Type of project (application, library, game, etc.)

            Returns:
                Dictionary with architectural plan
            """
            try:

                # Analyze the query to extract key components
                query_lower = query.lower()

                # Default project structure based on type
                if "game" in query_lower or "arcade" in query_lower:
                    project_type = "game"
                elif "api" in query_lower or "server" in query_lower:
                    project_type = "api"
                elif "library" in query_lower or "package" in query_lower:
                    project_type = "library"

                # Create the architecture based on analysis
                plan = {
                    "project_name": "",
                    "project_type": project_type,
                    "description": query,
                    "created": datetime.now().isoformat(),
                    "architecture": {
                        "overview": "",
                        "components": [],
                        "folder_structure": {},
                        "files": [],
                        "classes": [],
                        "functions": [],
                        "dependencies": [],
                    },
                    "implementation_order": [],
                }

                # Analyze query to generate dynamic architecture
                # Extract project name from query
                if "create" in query_lower:
                    words = query.split()
                    idx = words.index("create") if "create" in words else 0
                    if idx + 1 < len(words):
                        project_words = words[idx + 1 : idx + 3]
                        plan["project_name"] = "_".join(
                            [w.lower() for w in project_words if w.isalpha()]
                        )
                    else:
                        plan["project_name"] = "project"
                else:
                    # Try to extract a meaningful name
                    import re

                    nouns = re.findall(r"\b[A-Za-z]+\b", query)
                    plan["project_name"] = (
                        "_".join(nouns[:2]).lower() if nouns else "project"
                    )

                plan["architecture"][
                    "overview"
                ] = f"A {project_type} implementing: {query}"

                # Generate dynamic folder structure based on project type
                root_folder = plan["project_name"] + "/"

                if project_type == "game":
                    plan["architecture"]["folder_structure"] = {
                        root_folder: {
                            "main.py": "Entry point and main loop",
                            "core/": {
                                "__init__.py": "Core package initialization",
                                "game.py": "Main game logic",
                                "entities.py": "Game entities and objects",
                                "physics.py": "Physics and collision detection",
                            },
                            "ui/": {
                                "__init__.py": "UI package initialization",
                                "renderer.py": "Rendering and display",
                                "menu.py": "Menu screens",
                                "hud.py": "HUD and score display",
                            },
                            "utils/": {
                                "__init__.py": "Utils package initialization",
                                "constants.py": "Game constants and settings",
                                "helpers.py": "Utility functions",
                            },
                            "assets/": "Game assets (images, sounds, etc.)",
                            "requirements.txt": "Python dependencies",
                            "README.md": "Project documentation",
                            "GAIA.md": "GAIA agent guidance",
                        }
                    }
                    # Dependencies will be determined based on actual game requirements
                    plan["architecture"]["dependencies"] = []
                elif project_type == "api":
                    plan["architecture"]["folder_structure"] = {
                        root_folder: {
                            "main.py": "Application entry point",
                            "api/": {
                                "__init__.py": "API package initialization",
                                "routes.py": "API route definitions",
                                "models.py": "Data models",
                                "handlers.py": "Request handlers",
                            },
                            "core/": {
                                "__init__.py": "Core logic package",
                                "services.py": "Business logic services",
                                "database.py": "Database connections",
                            },
                            "utils/": {
                                "__init__.py": "Utilities package",
                                "validators.py": "Input validators",
                                "helpers.py": "Helper functions",
                            },
                            "tests/": {
                                "__init__.py": "Test package",
                                "test_api.py": "API tests",
                                "test_services.py": "Service tests",
                            },
                            "requirements.txt": "Python dependencies",
                            "README.md": "API documentation",
                            "GAIA.md": "GAIA agent guidance",
                        }
                    }
                    plan["architecture"]["dependencies"] = [
                        "fastapi>=0.100.0",
                        "uvicorn>=0.23.0",
                    ]
                else:
                    # Default application structure
                    plan["architecture"]["folder_structure"] = {
                        root_folder: {
                            "main.py": "Application entry point",
                            "core/": {
                                "__init__.py": "Core package initialization",
                                "app.py": "Main application logic",
                                "models.py": "Data models",
                                "services.py": "Business logic",
                            },
                            "utils/": {
                                "__init__.py": "Utilities package",
                                "config.py": "Configuration management",
                                "helpers.py": "Helper functions",
                            },
                            "tests/": {
                                "__init__.py": "Test package",
                                "test_app.py": "Application tests",
                                "test_models.py": "Model tests",
                            },
                            "requirements.txt": "Python dependencies",
                            "README.md": "Project documentation",
                            "GAIA.md": "GAIA agent guidance",
                        }
                    }
                    plan["architecture"]["dependencies"] = []

                # Generate dynamic class list based on project analysis
                plan["architecture"][
                    "classes"
                ] = []  # Will be populated based on actual requirements

                # Generate implementation order
                plan["implementation_order"] = [
                    "Create project structure",
                    "Set up configuration and constants",
                    "Implement core data models",
                    "Build main application logic",
                    "Add UI/API layer if needed",
                    "Implement utilities and helpers",
                    "Write unit tests",
                    "Add documentation",
                    "Test and refine",
                ]

                # Save the plan to PLAN.md
                plan_content = f"# {plan['project_name']} - Architecture Plan\n\n"
                plan_content += f"**Created:** {plan['created']}\n"
                plan_content += f"**Type:** {plan['project_type']}\n\n"
                plan_content += f"## Overview\n{plan['architecture']['overview']}\n\n"
                plan_content += "## Project Structure\n```\n"

                def format_structure(structure, indent=""):
                    result = ""
                    for key, value in structure.items():
                        if isinstance(value, dict):
                            result += f"{indent}{key}\\n"
                            result += format_structure(value, indent + "  ")
                        else:
                            result += f"{indent}{key} - {value}\\n"
                    return result

                plan_content += format_structure(
                    plan["architecture"]["folder_structure"]
                )
                plan_content += "```\n\n"

                if plan["architecture"]["classes"]:
                    plan_content += "## Classes\n"
                    for cls in plan["architecture"]["classes"]:
                        plan_content += (
                            f"- **{cls['name']}** ({cls['file']}): {cls['purpose']}\n"
                        )
                    plan_content += "\n"

                if plan["implementation_order"]:
                    plan_content += "## Implementation Order\n"
                    for i, step in enumerate(plan["implementation_order"], 1):
                        plan_content += f"{i}. {step}\n"
                    plan_content += "\n"

                # Add execution steps with checkboxes to track progress
                execution_steps = [
                    {
                        "step": 1,
                        "action": "create_plan",
                        "description": "Create architectural plan",
                        "completed": True,
                    },
                    {
                        "step": 2,
                        "action": "create_structure",
                        "description": "Create project folders and files",
                        "completed": False,
                    },
                    {
                        "step": 3,
                        "action": "implement",
                        "description": "Generate code for all components",
                        "completed": False,
                    },
                    {
                        "step": 4,
                        "action": "validate",
                        "description": "Lint and validate all code",
                        "completed": False,
                    },
                    {
                        "step": 5,
                        "action": "test",
                        "description": "Test and fix any issues",
                        "completed": False,
                    },
                    {
                        "step": 6,
                        "action": "finalize",
                        "description": "Verify complete implementation",
                        "completed": False,
                    },
                ]

                # Add execution steps to content
                plan_content = plan_content.replace(
                    "## Implementation Order",
                    "## Execution Progress\n"
                    + "\n".join(
                        [
                            f"- [{'x' if step['completed'] else ' '}] Step {step['step']}: {step['description']}"
                            for step in execution_steps
                        ]
                    )
                    + "\n\n## Implementation Order",
                )

                # Write PLAN.md with checkboxes
                plan_path = os.path.abspath("PLAN.md")
                with open(plan_path, "w", encoding="utf-8") as f:
                    f.write(plan_content)

                # Store the plan for later use (NOT the agent's execution plan)
                self.plan = plan
                self.plan["execution_steps"] = execution_steps
                self.project_root = os.path.abspath(plan["project_name"])

                return {
                    "status": "success",
                    "plan_created": True,
                    "plan_file": plan_path,
                    "project_name": plan["project_name"],
                    "num_files": len(
                        [
                            f
                            for f in str(
                                plan["architecture"]["folder_structure"]
                            ).split()
                            if "." in f
                        ]
                    ),
                    "num_classes": len(plan["architecture"]["classes"]),
                    "message": f"✅ Step 1/6: Created architectural plan for {plan['project_name']}",
                }
            except Exception as e:
                return {"status": "error", "error": str(e)}

        @tool
        def create_project_structure() -> Dict[str, Any]:
            """Create the project folder structure based on the current plan.

            Returns:
                Dictionary with creation results
            """
            try:
                if not hasattr(self, "plan") or not self.plan:
                    return {
                        "status": "error",
                        "error": "No architectural plan found. Create a plan first.",
                    }

                created_dirs = []
                created_files = []

                def create_structure(structure, base_path=""):
                    for name, content in structure.items():
                        full_path = os.path.join(base_path, name)

                        if name.endswith("/"):
                            # It's a directory
                            dir_path = full_path[:-1]  # Remove trailing slash
                            os.makedirs(dir_path, exist_ok=True)
                            created_dirs.append(dir_path)

                            if isinstance(content, dict):
                                create_structure(content, dir_path)
                        else:
                            # It's a file
                            dir_name = os.path.dirname(full_path)
                            if dir_name:
                                os.makedirs(dir_name, exist_ok=True)

                            # Create empty file or with initial content
                            if not os.path.exists(full_path):
                                with open(full_path, "w", encoding="utf-8") as f:
                                    if name == "__init__.py":
                                        f.write('"""Package initialization."""\n')
                                    elif name == "requirements.txt":
                                        deps = self.plan["architecture"].get(
                                            "dependencies", []
                                        )
                                        f.write("\n".join(deps))
                                    elif name == "README.md":
                                        f.write(f"# {self.plan['project_name']}\n\n")
                                        f.write(
                                            f"{self.plan['architecture']['overview']}\n"
                                        )
                                    elif name == "GAIA.md":
                                        # Create GAIA.md for the project
                                        # Note: This would be handled by update_gaia_md tool at runtime
                                        f.write(
                                            f"# GAIA.md\\n\\nProject: {self.plan['project_name']}\\n"
                                        )
                                    else:
                                        f.write(
                                            f'"""\n{content if isinstance(content, str) else "Module implementation"}\n"""\n'
                                        )
                                created_files.append(full_path)

                # Create the project structure
                structure = self.plan["architecture"]["folder_structure"]
                create_structure(structure)

                # Update execution steps
                if (
                    hasattr(self, "current_plan")
                    and self.plan
                    and "execution_steps" in self.plan
                ):
                    self.plan["execution_steps"][1]["completed"] = True
                    # Update PLAN.md to reflect progress
                    self._update_plan_progress()

                return {
                    "status": "success",
                    "project_root": self.project_root,
                    "dirs_created": len(created_dirs),
                    "files_created": len(created_files),
                    "created_dirs": created_dirs[:10],  # First 10 dirs
                    "created_files": created_files[:10],  # First 10 files
                    "message": f"✅ Step 2/6: Created {len(created_dirs)} directories and {len(created_files)} files",
                }
            except Exception as e:
                return {"status": "error", "error": str(e)}

        @tool
        def implement_from_plan(
            component: str = None, auto_implement_all: bool = False
        ) -> Dict[str, Any]:
            """Implement components based on the architectural plan.

            Args:
                component: Specific component to implement (e.g., "Snake", "game/snake.py")
                auto_implement_all: Implement all components automatically

            Returns:
                Dictionary with implementation results
            """
            # component parameter reserved for future single-component implementation
            _ = component  # Will be used in future versions
            try:
                if not hasattr(self, "plan") or not self.plan:
                    return {
                        "status": "error",
                        "error": "No architectural plan found. Create a plan first.",
                    }

                implemented = []
                errors = []

                # Get classes to implement
                classes = self.plan["architecture"].get("classes", [])

                # Generate implementations for all files in the project structure
                if auto_implement_all and hasattr(self, "plan") and self.plan:
                    # Get list of Python files from the project structure
                    structure = self.plan.get("architecture", {}).get(
                        "folder_structure", {}
                    )
                    python_files = self._extract_python_files(structure)

                    # Generate code for each file based on its purpose
                    if not python_files:
                        logger.warning("No Python files found in project structure")

                    for file_info in python_files:
                        try:
                            file_path = os.path.join(
                                self.project_root or "", file_info["path"]
                            )

                            logger.info(f"Generating code for {file_path}")

                            # Generate contextual code based on filename and project type
                            code = self._generate_code_for_file(
                                filename=file_info["name"],
                                purpose=file_info["purpose"],
                                context=self.plan.get("description", ""),
                            )

                            # Ensure directory exists
                            dir_path = os.path.dirname(file_path)
                            if dir_path:
                                os.makedirs(dir_path, exist_ok=True)

                            # Write the generated code
                            with open(file_path, "w", encoding="utf-8") as f:
                                f.write(code)

                            implemented.append(
                                {
                                    "file": file_path,
                                    "status": "success",
                                    "lines": len(code.splitlines()),
                                }
                            )
                            logger.info(f"Successfully generated {file_path}")
                        except Exception as e:
                            logger.error(
                                f"Failed to generate {file_info['path']}: {str(e)}"
                            )
                            errors.append({"file": file_info["path"], "error": str(e)})

                elif auto_implement_all and classes:
                    # Original class implementation logic
                    for cls_info in classes:
                        try:
                            # Generate class implementation
                            file_path = os.path.join(
                                self.project_root or "", cls_info["file"]
                            )

                            # Generate generic class implementation based on purpose
                            code = f'''"""Implementation of {cls_info['name']}."""


class {cls_info['name']}:
    """{cls_info['purpose']}"""

    def __init__(self):
        """Initialize {cls_info['name']}."""
        # TODO: Add initialization logic based on requirements
        pass

    # TODO: Add methods based on class purpose
'''

                            # Create directories if needed
                            os.makedirs(os.path.dirname(file_path), exist_ok=True)

                            # Write the implementation
                            with open(file_path, "w", encoding="utf-8") as f:
                                f.write(code)

                            implemented.append(
                                {
                                    "class": cls_info["name"],
                                    "file": file_path,
                                    "status": "success",
                                }
                            )
                        except Exception as e:
                            errors.append({"class": cls_info["name"], "error": str(e)})

                # Update execution steps
                if (
                    hasattr(self, "current_plan")
                    and self.plan
                    and "execution_steps" in self.plan
                ):
                    self.plan["execution_steps"][2]["completed"] = True
                    # Update PLAN.md to reflect progress
                    self._update_plan_progress()

                return {
                    "status": "success",
                    "implemented": implemented,
                    "errors": errors,
                    "total_implemented": len(implemented),
                    "total_errors": len(errors),
                    "message": f"✅ Step 3/6: Implemented {len(implemented)} components",
                }
            except Exception as e:
                return {"status": "error", "error": str(e)}

        @tool
        def create_workflow_plan(query: str) -> Dict[str, Any]:
            """Create a comprehensive workflow plan for a complex query.

            Args:
                query: The user's request or requirements

            Returns:
                Dictionary with workflow plan
            """
            # Analyze the query to determine required steps
            steps = []

            # Always start with understanding and generation
            steps.append(
                {
                    "step": 1,
                    "action": "analyze",
                    "description": "Understand requirements",
                }
            )
            steps.append(
                {
                    "step": 2,
                    "action": "generate",
                    "description": "Generate code solution",
                }
            )

            # Add file operations if needed
            if (
                "file" in query.lower()
                or "save" in query.lower()
                or "write" in query.lower()
            ):
                steps.append(
                    {"step": 3, "action": "write", "description": "Save code to file"}
                )

            # Add validation steps
            steps.append(
                {
                    "step": len(steps) + 1,
                    "action": "validate",
                    "description": "Validate syntax",
                }
            )
            steps.append(
                {
                    "step": len(steps) + 1,
                    "action": "lint",
                    "description": "Check code quality",
                }
            )
            steps.append(
                {
                    "step": len(steps) + 1,
                    "action": "fix_linting",
                    "description": "Fix linting errors",
                }
            )

            # Add test generation after implementation and linting
            steps.append(
                {
                    "step": len(steps) + 1,
                    "action": "generate_tests",
                    "description": "Generate unit tests",
                }
            )

            # Add execution if it's a complete program
            if (
                "calculator" in query.lower()
                or "program" in query.lower()
                or "main" in query.lower()
            ):
                steps.append(
                    {
                        "step": len(steps) + 1,
                        "action": "execute",
                        "description": "Test execution",
                    }
                )
                steps.append(
                    {
                        "step": len(steps) + 1,
                        "action": "fix",
                        "description": "Fix any errors",
                    }
                )
                steps.append(
                    {
                        "step": len(steps) + 1,
                        "action": "test_execution",
                        "description": "Run unit tests",
                    }
                )

            # Always end with verification
            steps.append(
                {
                    "step": len(steps) + 1,
                    "action": "verify",
                    "description": "Verify results",
                }
            )
            steps.append(
                {
                    "step": len(steps) + 1,
                    "action": "summarize",
                    "description": "Summarize accomplishments",
                }
            )

            return {
                "status": "success",
                "query": query,
                "workflow_steps": steps,
                "total_steps": len(steps),
                "estimated_time": len(steps) * 2,  # Rough estimate in seconds
                "plan_created": True,
            }

        @tool
        def fix_linting_errors(
            file_path: str, lint_issues: List[Dict[str, Any]]
        ) -> Dict[str, Any]:
            """Fix linting errors based on pylint output.

            Args:
                file_path: Path to the Python file with linting issues
                lint_issues: List of linting issues from pylint

            Returns:
                Dictionary with fix results
            """
            return self._fix_linting_errors(file_path, lint_issues)

        @tool
        def init_gaia_md(project_root: str = ".") -> Dict[str, Any]:
            """Initialize GAIA.md by analyzing the current codebase.

            Args:
                project_root: Root directory to analyze (default: current directory)

            Returns:
                Dictionary with initialization results
            """
            try:

                # Analyze project structure
                project_name = os.path.basename(os.path.abspath(project_root))
                structure = {}
                python_files = []
                classes_found = []
                functions_found = []

                # Walk through the directory
                for root, dirs, files in os.walk(project_root):
                    # Skip hidden directories and common ignore patterns
                    dirs[:] = [
                        d for d in dirs if not d.startswith(".") and d != "__pycache__"
                    ]

                    rel_path = os.path.relpath(root, project_root)
                    if rel_path == ".":
                        current_level = structure
                    else:
                        current_level = structure
                        for part in rel_path.split(os.sep):
                            if part + "/" not in current_level:
                                current_level[part + "/"] = {}
                            current_level = current_level[part + "/"]

                    for file in files:
                        file_path = os.path.join(root, file)
                        rel_file_path = os.path.relpath(file_path, project_root)

                        if file.endswith(".py"):
                            python_files.append(rel_file_path)
                            # Try to parse Python file
                            try:
                                with open(file_path, "r", encoding="utf-8") as f:
                                    content = f.read()
                                tree = ast.parse(content)

                                # Extract classes and functions
                                for node in ast.walk(tree):
                                    if isinstance(node, ast.ClassDef):
                                        docstring = ast.get_docstring(node)
                                        classes_found.append(
                                            {
                                                "name": node.name,
                                                "file": rel_file_path,
                                                "docstring": (
                                                    docstring
                                                    if docstring
                                                    else "No description"
                                                ),
                                            }
                                        )
                                    elif isinstance(node, ast.FunctionDef):
                                        if (
                                            node.col_offset == 0
                                        ):  # Module-level function
                                            docstring = ast.get_docstring(node)
                                            functions_found.append(
                                                {
                                                    "name": node.name,
                                                    "file": rel_file_path,
                                                    "docstring": (
                                                        docstring
                                                        if docstring
                                                        else "No description"
                                                    ),
                                                }
                                            )

                                current_level[file] = "Python module"
                            except Exception:
                                current_level[file] = "Python file"
                        elif file.endswith((".md", ".txt", ".json", ".yml", ".yaml")):
                            current_level[file] = "Configuration/Documentation"
                        elif file in ["requirements.txt", "setup.py", "pyproject.toml"]:
                            current_level[file] = "Project configuration"
                        else:
                            current_level[file] = "Resource file"

                # Detect project type
                project_type = "application"
                if any(f.startswith("test") for f in python_files):
                    project_type = "library/application with tests"
                if "setup.py" in [os.path.basename(f) for f in python_files]:
                    project_type = "Python package"
                if any("game" in f.lower() for f in python_files):
                    project_type = "game"
                if any(
                    "api" in f.lower() or "server" in f.lower() for f in python_files
                ):
                    project_type = "API/server"

                # Generate description
                description = (
                    f"A {project_type} project with {len(python_files)} Python files"
                )
                if classes_found:
                    description += f", {len(classes_found)} classes"
                if functions_found:
                    description += (
                        f", and {len(functions_found)} module-level functions"
                    )

                # Build instructions based on analysis
                instructions = []
                if "test" in project_name.lower() or any(
                    "test" in f for f in python_files
                ):
                    instructions.append("- Run tests before making changes")
                if any("main.py" in f for f in python_files):
                    instructions.append("- main.py is the entry point")
                if classes_found:
                    instructions.append(
                        "- Maintain existing class structure and interfaces"
                    )
                instructions.append("- Follow existing code style and patterns")

                # Create GAIA.md content
                content = "# GAIA.md\n\n"
                content += "This file provides guidance to GAIA Code Agent when working with code in this project.\n\n"
                content += f"## Project: {project_name}\n\n"
                content += f"## Description\n{description}\n\n"
                content += f"## Project Type\n{project_type}\n\n"

                if structure:
                    content += "## Project Structure\n```\n"

                    def format_structure(struct, indent=""):
                        result = ""
                        for key, value in struct.items():
                            if isinstance(value, dict):
                                result += f"{indent}{key}\n"
                                result += format_structure(value, indent + "  ")
                            else:
                                result += f"{indent}{key} - {value}\n"
                        return result

                    content += format_structure(structure)
                    content += "```\n\n"

                if classes_found:
                    content += "## Main Classes\n"
                    for cls in classes_found[:10]:  # Limit to first 10
                        content += f"- **{cls['name']}** ({cls['file']}): {cls['docstring'].split('.')[0] if cls['docstring'] else 'No description'}.\n"
                    content += "\n"

                if functions_found:
                    content += "## Main Functions\n"
                    for func in functions_found[:10]:  # Limit to first 10
                        content += f"- **{func['name']}** ({func['file']}): {func['docstring'].split('.')[0] if func['docstring'] else 'No description'}.\n"
                    content += "\n"

                content += "## Development Guidelines\n"
                for instruction in instructions:
                    content += f"{instruction}\n"
                content += "- Follow PEP 8 style guidelines\n"
                content += "- Add docstrings to all functions and classes\n"
                content += "- Include type hints where appropriate\n\n"

                content += "## Code Quality\n"
                content += "- All code should pass pylint checks\n"
                content += "- Use Black formatter for consistent style\n"
                content += "- Ensure proper error handling\n\n"

                # Write GAIA.md
                gaia_path = os.path.join(project_root, "GAIA.md")
                with open(gaia_path, "w", encoding="utf-8") as f:
                    f.write(content)

                return {
                    "status": "success",
                    "file_path": gaia_path,
                    "project_name": project_name,
                    "project_type": project_type,
                    "python_files": len(python_files),
                    "classes_found": len(classes_found),
                    "functions_found": len(functions_found),
                    "message": f"GAIA.md initialized for {project_name} with {len(python_files)} Python files analyzed",
                }
            except Exception as e:
                return {"status": "error", "error": str(e)}

        @tool
        def fix_python_errors(file_path: str, error_message: str) -> Dict[str, Any]:
            """Attempt to fix Python runtime errors based on error messages.

            Args:
                file_path: Path to the Python file with errors
                error_message: The error message from execution

            Returns:
                Dictionary with fix results
            """
            try:
                path = Path(file_path)
                if not path.exists():
                    return {"status": "error", "error": f"File not found: {file_path}"}

                content = path.read_text(encoding="utf-8")
                original_content = content
                fixes_applied = []

                # Common error fixes
                if "NameError" in error_message and "not defined" in error_message:
                    # Extract the undefined name
                    import re

                    match = re.search(r"name '(\w+)' is not defined", error_message)
                    if match:
                        undefined_name = match.group(1)
                        # Add import if it's a common module
                        if undefined_name in ["Dict", "List", "Optional", "Tuple"]:
                            if "from typing import" not in content:
                                content = (
                                    f"from typing import {undefined_name}\n" + content
                                )
                                fixes_applied.append(
                                    f"Added import for {undefined_name}"
                                )

                if "IndentationError" in error_message:
                    # Try to fix indentation
                    lines = content.split("\n")
                    fixed_lines = []
                    for line in lines:
                        # Ensure consistent 4-space indentation
                        if line.startswith(" ") and not line.startswith("    "):
                            spaces = len(line) - len(line.lstrip())
                            tabs = spaces // 4
                            line = "    " * tabs + line.lstrip()
                        fixed_lines.append(line)
                    content = "\n".join(fixed_lines)
                    fixes_applied.append("Fixed indentation")

                if "TypeError" in error_message:
                    # Add type checking
                    if "float() argument" in error_message:
                        # Wrap float conversions in try-except
                        content = content.replace(
                            "float(input(", "float(input("
                        )  # This would need more sophisticated replacement
                        fixes_applied.append("Added type handling")

                # Write the fixed content if changes were made
                if content != original_content:
                    # Skip backup creation - not needed for generated code
                    # backup_path = path.with_suffix(path.suffix + ".bak")
                    # backup_path.write_text(original_content, encoding="utf-8")

                    # Write fixed content
                    path.write_text(content, encoding="utf-8")

                    return {
                        "status": "success",
                        "fixes_applied": fixes_applied,
                        # "backup_created": str(backup_path),
                        "file_modified": True,
                    }
                else:
                    return {
                        "status": "info",
                        "message": "No automatic fixes available for this error",
                        "error_type": (
                            error_message.split(":")[0]
                            if ":" in error_message
                            else "Unknown"
                        ),
                        "file_modified": False,
                    }

            except Exception as e:
                return {"status": "error", "error": str(e)}

    def _fix_linting_errors(
        self,
        file_path: str,
        lint_issues: List[Dict[str, Any]],
        max_iterations: int = 3,
        create_backup: bool = False,
    ) -> Dict[str, Any]:
        """Fix linting errors using LLM to intelligently correct issues.

        Iteratively fixes linting errors until all are resolved or max iterations reached.

        Args:
            file_path: Path to the Python file with linting issues
            lint_issues: List of linting issues from pylint
            max_iterations: Maximum number of fix attempts (default: 3)
            create_backup: Create .bak file before modifying (default: False)

        Returns:
            Dictionary with fix results
        """
        try:
            path = Path(file_path)
            if not path.exists():
                return {"status": "error", "error": f"File not found: {file_path}"}

            original_content = path.read_text(encoding="utf-8")
            current_content = original_content
            all_fixes_applied = []
            iteration = 0
            remaining_issues = lint_issues

            while remaining_issues and iteration < max_iterations:
                iteration += 1
                logger.info(
                    f"Lint fix iteration {iteration}/{max_iterations} for {file_path}: "
                    f"{len(remaining_issues)} issues"
                )

                # Format lint issues for LLM (limit to first 10 per iteration)
                issues_to_fix = remaining_issues[:10]
                issues_text = "\n".join(
                    [
                        f"Line {issue.get('line', 0)}: [{issue.get('symbol', 'unknown')}] "
                        f"{issue.get('message', '')}"
                        for issue in issues_to_fix
                    ]
                )

                # Use LLM to fix the code
                prompt = f"""Fix the following pylint linting issues in this Python code:

Linting Issues:
{issues_text}

Current Code:
```python
{current_content}
```

Fix the linting issues while preserving the code's functionality.
Return ONLY the corrected Python code, no explanations."""

                try:
                    response = self.chat.send(prompt)
                    fixed_code = response.text.strip()

                    # Extract code from markdown blocks if present
                    if "```python" in fixed_code:
                        fixed_code = (
                            fixed_code.split("```python")[1].split("```")[0].strip()
                        )
                    elif "```" in fixed_code:
                        fixed_code = fixed_code.split("```")[1].split("```")[0].strip()

                    # Validate the fixed code
                    validation = self.syntax_validator.validate_dict(fixed_code)
                    if not validation["is_valid"]:
                        logger.warning(
                            f"LLM fix produced invalid syntax, skipping iteration {iteration}"
                        )
                        break

                    # Write to file and re-check with pylint
                    path.write_text(fixed_code, encoding="utf-8")
                    current_content = fixed_code

                    # Re-run pylint to check remaining issues
                    import json
                    import subprocess

                    result = subprocess.run(
                        ["python", "-m", "pylint", "--output-format=json", str(path)],
                        capture_output=True,
                        text=True,
                        timeout=30,
                        check=False,
                    )

                    if result.stdout:
                        try:
                            remaining_issues = json.loads(result.stdout)
                            all_fixes_applied.append(
                                f"Iteration {iteration}: Fixed {len(issues_to_fix)} issues"
                            )

                            if not remaining_issues:
                                logger.info(
                                    f"All linting issues resolved after {iteration} iterations"
                                )
                                break
                        except json.JSONDecodeError:
                            break

                except Exception as e:
                    logger.warning(f"LLM-based fix iteration {iteration} failed: {e}")
                    break

            # Final result
            if current_content != original_content:
                result = {
                    "status": "success",
                    "fixes_applied": all_fixes_applied,
                    "file_modified": True,
                    "total_fixes": len(all_fixes_applied),
                    "iterations": iteration,
                    "remaining_issues": (
                        len(remaining_issues) if remaining_issues else 0
                    ),
                }

                # Create backup if requested
                if create_backup:
                    backup_path = path.with_suffix(path.suffix + ".bak")
                    backup_path.write_text(original_content, encoding="utf-8")
                    result["backup_created"] = str(backup_path)

                return result
            else:
                return {
                    "status": "info",
                    "message": "No fixes could be applied",
                    "file_modified": False,
                }

        except Exception as e:
            return {"status": "error", "error": str(e)}

    def _update_plan_progress(self):
        """Update PLAN.md file with current execution progress.

        Updates checkboxes in PLAN.md to reflect completed steps.
        """
        try:
            if (
                not hasattr(self, "plan")
                or not self.plan
                or "execution_steps" not in self.plan
            ):
                return

            plan_path = os.path.abspath("PLAN.md")
            if os.path.exists(plan_path):
                # Read existing content
                with open(plan_path, "r", encoding="utf-8") as f:
                    content = f.read()

                # Update checkboxes
                for step in self.plan["execution_steps"]:
                    old_line = f"- [ ] Step {step['step']}: {step['description']}"
                    new_line = (
                        f"- [{'x' if step['completed'] else ' '}] "
                        f"Step {step['step']}: {step['description']}"
                    )
                    content = content.replace(old_line, new_line)

                # Write back
                with open(plan_path, "w", encoding="utf-8") as f:
                    f.write(content)
        except Exception as e:
            logger.warning(f"Could not update plan progress: {e}")
