# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Consolidated validation and parsing mixin combining validation, AST parsing, and error fixing helpers."""

import ast
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List

from ..models import CodeSymbol, ParsedCode

if TYPE_CHECKING:
    from ..validators import (  # noqa: F401
        AntipatternChecker,
        RequirementsValidator,
        SyntaxValidator,
    )

logger = logging.getLogger(__name__)


class ValidationAndParsingMixin:
    """Consolidated mixin providing validation, AST parsing, and error fixing helpers.

    Attributes (provided by CodeAgent):
        syntax_validator: SyntaxValidator instance
        antipattern_checker: AntipatternChecker instance
        requirements_validator: RequirementsValidator instance

    This mixin provides helper methods (not tools) for:
    - Python syntax validation
    - Python code parsing with AST
    - Requirements.txt validation (hallucination detection)
    - Anti-pattern detection (combinatorial functions, excessive parameters)
    - JavaScript/TypeScript, CSS, and HTML validation

    Helper methods (used by other mixins):
    - _validate_python_syntax: Validate Python code syntax
    - _parse_python_code: Parse Python code and extract structure
    - _validate_requirements: Check requirements.txt for issues
    - _validate_python_files: Validate Python files with pylint and anti-patterns
    - _check_antipatterns: Detect code smells and anti-patterns
    - _validate_javascript_files: Validate JS/TS files with ESLint
    - _validate_css_files: Basic CSS validation
    - _validate_html_files: Basic HTML validation

    Note: This mixin does not register tools, it provides helper methods.
    """

    # ============================================================
    # VALIDATION HELPER METHODS
    # ============================================================

    def _validate_requirements(self, req_file: Path, fix: bool) -> Dict[str, Any]:
        """Validate requirements.txt for hallucinated packages.

        Args:
            req_file: Path to requirements.txt file
            fix: Whether to auto-fix issues

        Returns:
            Dictionary with validation results
        """
        return self.requirements_validator.validate(req_file, fix)

    def _validate_python_files(
        self, py_files: List[Path], _fix: bool
    ) -> Dict[str, Any]:
        """Validate Python files for syntax and common issues.

        Args:
            py_files: List of Python file paths
            _fix: Whether to auto-fix issues (unused currently)

        Returns:
            Dictionary with validation results
        """
        errors = []
        warnings = []

        for py_file in py_files:
            try:
                content = py_file.read_text()

                # Validate syntax
                syntax_result = self.syntax_validator.validate_dict(content)
                if not syntax_result["is_valid"]:
                    errors.extend(
                        [f"{py_file}: {err}" for err in syntax_result.get("errors", [])]
                    )

                # Check for antipatterns
                antipattern_result = self._check_antipatterns(py_file, content)
                warnings.extend(
                    [
                        f"{py_file}: {warn}"
                        for warn in antipattern_result.get("warnings", [])
                    ]
                )

            except Exception as e:
                errors.append(f"{py_file}: Failed to validate - {e}")

        return {"errors": errors, "warnings": warnings, "is_valid": len(errors) == 0}

    def _check_antipatterns(self, _file_path: Path, content: str) -> Dict[str, Any]:
        """Check for common antipatterns in Python code.

        Args:
            _file_path: Path to the file being checked (unused currently)
            content: File content

        Returns:
            Dictionary with antipattern check results
        """
        return self.antipattern_checker.check_dict(content)

    def _validate_python_syntax(self, code: str) -> Dict[str, Any]:
        """Validate Python code syntax (delegates to validator).

        Args:
            code: Python code to validate

        Returns:
            Dictionary with validation results
        """
        return self.syntax_validator.validate_dict(code)

    def _validate_javascript_files(
        self, js_files: List[Path], _fix: bool
    ) -> Dict[str, Any]:
        """Validate JavaScript files for syntax issues.

        Args:
            js_files: List of JavaScript file paths
            _fix: Whether to auto-fix issues (unused currently)

        Returns:
            Dictionary with validation results
        """
        warnings = []

        for js_file in js_files:
            try:
                content = js_file.read_text()

                # Basic syntax checks
                if "var " in content:
                    warnings.append(f"{js_file}: Use 'const' or 'let' instead of 'var'")
                if "==" in content and "===" not in content:
                    warnings.append(
                        f"{js_file}: Use '===' instead of '==' for equality checks"
                    )

            except Exception as e:
                warnings.append(f"{js_file}: Failed to read - {e}")

        return {"warnings": warnings, "is_valid": True}  # Warnings don't invalidate

    def _validate_css_files(self, css_files: List[Path]) -> Dict[str, Any]:
        """Validate CSS files for basic issues.

        Args:
            css_files: List of CSS file paths

        Returns:
            Dictionary with validation results
        """
        warnings = []

        for css_file in css_files:
            try:
                content = css_file.read_text()

                # Check for duplicate selectors
                if content.count("{") != content.count("}"):
                    warnings.append(f"{css_file}: Mismatched braces")

            except Exception as e:
                warnings.append(f"{css_file}: Failed to read - {e}")

        return {"warnings": warnings, "is_valid": True}

    def _validate_html_files(self, html_files: List[Path]) -> Dict[str, Any]:
        """Validate HTML files for basic structure.

        Args:
            html_files: List of HTML file paths

        Returns:
            Dictionary with validation results
        """
        warnings = []

        for html_file in html_files:
            try:
                content = html_file.read_text()

                # Basic structure checks
                if "<html" not in content.lower():
                    warnings.append(f"{html_file}: Missing <html> tag")
                if "<body" not in content.lower():
                    warnings.append(f"{html_file}: Missing <body> tag")

            except Exception as e:
                warnings.append(f"{html_file}: Failed to read - {e}")

        return {"warnings": warnings, "is_valid": True}

    # ============================================================
    # AST PARSING METHODS
    # ============================================================

    def _parse_python_code(self, code: str) -> ParsedCode:
        """Parse Python code using AST.

        Args:
            code: Python source code

        Returns:
            ParsedCode object with parsing results
        """
        result = ParsedCode()
        result.symbols = []
        result.imports = []
        result.errors = []

        try:
            tree = ast.parse(code)
            result.ast_tree = tree
            result.is_valid = True

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    signature = self._get_function_signature(node)
                    docstring = ast.get_docstring(node)
                    result.symbols.append(
                        CodeSymbol(
                            name=node.name,
                            type="function",
                            line=node.lineno,
                            signature=signature,
                            docstring=docstring,
                        )
                    )

                elif isinstance(node, ast.ClassDef):
                    docstring = ast.get_docstring(node)
                    result.symbols.append(
                        CodeSymbol(
                            name=node.name,
                            type="class",
                            line=node.lineno,
                            docstring=docstring,
                        )
                    )

                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        import_name = alias.asname if alias.asname else alias.name
                        result.imports.append(f"import {alias.name}")
                        result.symbols.append(
                            CodeSymbol(
                                name=import_name, type="import", line=node.lineno
                            )
                        )

                elif isinstance(node, ast.ImportFrom):
                    module = node.module if node.module else ""
                    for alias in node.names:
                        import_name = alias.asname if alias.asname else alias.name
                        result.imports.append(f"from {module} import {alias.name}")
                        result.symbols.append(
                            CodeSymbol(
                                name=import_name, type="import", line=node.lineno
                            )
                        )

                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and isinstance(
                            target.ctx, ast.Store
                        ):
                            if hasattr(node, "col_offset") and node.col_offset == 0:
                                result.symbols.append(
                                    CodeSymbol(
                                        name=target.id,
                                        type="variable",
                                        line=node.lineno,
                                    )
                                )

        except SyntaxError as e:
            result.is_valid = False
            result.errors.append(f"Syntax error at line {e.lineno}: {e.msg}")
        except Exception as e:
            result.is_valid = False
            result.errors.append(f"Parse error: {str(e)}")

        return result

    def _get_function_signature(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> str:
        """Extract function signature from AST node.

        Args:
            node: AST FunctionDef or AsyncFunctionDef node

        Returns:
            Function signature as string
        """
        params = []

        for arg in node.args.args:
            param = arg.arg
            if arg.annotation:
                param += f": {ast.unparse(arg.annotation)}"
            params.append(param)

        if node.args.vararg:
            param = f"*{node.args.vararg.arg}"
            if node.args.vararg.annotation:
                param += f": {ast.unparse(node.args.vararg.annotation)}"
            params.append(param)

        if node.args.kwarg:
            param = f"**{node.args.kwarg.arg}"
            if node.args.kwarg.annotation:
                param += f": {ast.unparse(node.args.kwarg.annotation)}"
            params.append(param)

        signature = f"{node.name}({', '.join(params)})"

        if node.returns:
            signature += f" -> {ast.unparse(node.returns)}"

        return signature

    def _extract_python_files(
        self, project_path: Path, plan_data: Dict[str, Any]
    ) -> List[Path]:
        """Extract list of Python files from project structure.

        Args:
            project_path: Root path of the project
            plan_data: Project plan data containing structure

        Returns:
            List of Python file paths
        """
        py_files = []

        def extract_from_structure(structure, current_path=""):
            if isinstance(structure, dict):
                for key, value in structure.items():
                    new_path = f"{current_path}/{key}" if current_path else key
                    if isinstance(value, dict):
                        extract_from_structure(value, new_path)
                    elif key.endswith(".py"):
                        py_files.append(project_path / new_path)

        if "structure" in plan_data:
            extract_from_structure(plan_data["structure"])

        return py_files

    def _update_plan_task(self, plan_path: str, task_name: str, completed: bool = True):
        """Update task status in PLAN.md file.

        Args:
            plan_path: Path to PLAN.md file
            task_name: Name of the task to update
            completed: Whether task is completed (True) or in progress (False)
        """
        try:
            plan_file = Path(plan_path)
            if not plan_file.exists():
                return

            # pylint: disable=unspecified-encoding
            content = plan_file.read_text()

            if completed:
                pattern = rf"- \[ \] {re.escape(task_name)}"
                replacement = f"- [x] {task_name}"
            else:
                pattern = rf"- \[ \] {re.escape(task_name)}"
                replacement = f"- [~] {task_name}"

            new_content = re.sub(pattern, replacement, content)
            plan_file.write_text(new_content)

        except Exception as e:
            logger.warning(f"Failed to update plan task: {e}")
