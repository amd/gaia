# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Code formatting tools mixin for Code Agent."""

import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict


class CodeFormattingMixin:
    """Mixin providing code formatting and linting tools for Python code.

    This mixin provides tools for:
    - Formatting Python code with Black
    - Combined linting and formatting analysis
    - Automatic code quality improvements

    Tools provided:
    - format_with_black: Format code using Black formatter
    - lint_and_format: Combined pylint analysis and Black formatting
    """

    def register_code_formatting_tools(self) -> None:
        """Register code formatting tools."""
        from gaia.agents.base.tools import tool

        @tool
        def format_with_black(
            file_path: str = None,
            code: str = None,
            line_length: int = 88,
            check_only: bool = False,
        ) -> Dict[str, Any]:
            """Format Python code with Black.

            Args:
                file_path: Path to Python file to format (optional)
                code: Python code string to format (optional)
                line_length: Maximum line length (default: 88)
                check_only: Only check if formatting is needed, don't modify (default: False)

            Returns:
                Dictionary with formatting result
            """
            try:
                # Determine source
                if file_path:
                    path = Path(file_path)
                    if not path.exists():
                        return {
                            "status": "error",
                            "error": f"File not found: {file_path}",
                        }
                    target_file = str(path)
                    original_content = path.read_text(encoding="utf-8")
                elif code:
                    # Write code to temporary file
                    with tempfile.NamedTemporaryFile(
                        mode="w", suffix=".py", delete=False
                    ) as f:
                        f.write(code)
                        target_file = f.name
                    original_content = code
                else:
                    return {
                        "status": "error",
                        "error": "Either file_path or code must be provided",
                    }

                # Run black
                cmd = [
                    "python",
                    "-m",
                    "black",
                    f"--line-length={line_length}",
                    "--quiet",
                ]

                if check_only:
                    cmd.append("--check")
                    cmd.append("--diff")

                cmd.append(target_file)

                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=30, check=False
                )

                # Read the formatted content
                formatted_content = original_content
                if not check_only and result.returncode == 0:
                    if file_path:
                        formatted_content = Path(target_file).read_text(
                            encoding="utf-8"
                        )
                    else:
                        with open(target_file, "r", encoding="utf-8") as f:
                            formatted_content = f.read()

                # Clean up temp file if created
                if code and os.path.exists(target_file):
                    os.unlink(target_file)

                # Check if formatting was needed
                needs_formatting = (
                    result.returncode != 0
                    if check_only
                    else original_content != formatted_content
                )

                # Generate diff if content changed
                diff = None
                if needs_formatting and not check_only:
                    diff = self._generate_unified_diff(
                        original_content, formatted_content, "original", "formatted"
                    )
                elif check_only and result.stdout:
                    diff = result.stdout

                return {
                    "status": "success",
                    "formatted": not check_only and needs_formatting,
                    "needs_formatting": needs_formatting,
                    "formatted_code": formatted_content if not check_only else None,
                    "diff": diff,
                    "command": " ".join(shlex.quote(str(c)) for c in cmd),
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "return_code": result.returncode,
                    "message": (
                        "Code formatted successfully"
                        if needs_formatting and not check_only
                        else (
                            "Code needs formatting"
                            if needs_formatting
                            else "Code is already properly formatted"
                        )
                    ),
                }

            except subprocess.TimeoutExpired:
                return {"status": "error", "error": "Black formatting timed out"}
            except subprocess.CalledProcessError as e:
                return {"status": "error", "error": f"Black failed: {e.stderr}"}
            except ImportError:
                return {
                    "status": "error",
                    "error": "black is not installed. Install with: pip install black",
                }
            except Exception as e:
                return {"status": "error", "error": str(e)}

        @tool
        def lint_and_format(
            file_path: str = None,
            code: str = None,
            fix: bool = False,
            line_length: int = 88,
        ) -> Dict[str, Any]:
            """Combined linting and formatting analysis.

            Args:
                file_path: Path to Python file (optional)
                code: Python code string (optional)
                fix: Apply black formatting if needed (default: False)
                line_length: Maximum line length for black (default: 88)

            Returns:
                Dictionary with combined linting and formatting results
            """
            try:
                results = {"status": "success", "file_path": file_path}

                # First, check syntax
                if code:
                    syntax_check = self._validate_python_syntax(code)
                elif file_path:
                    content = Path(file_path).read_text(encoding="utf-8")
                    syntax_check = self._validate_python_syntax(content)
                else:
                    return {
                        "status": "error",
                        "error": "Either file_path or code must be provided",
                    }

                results["syntax_valid"] = syntax_check["is_valid"]

                if not syntax_check["is_valid"]:
                    results["syntax_errors"] = syntax_check.get("errors", [])
                    results["message"] = (
                        "Code has syntax errors - fix these before linting"
                    )
                    return results

                # Run pylint analysis
                pylint_result = self._execute_tool(
                    "analyze_with_pylint", {"file_path": file_path, "code": code}
                )
                results["pylint"] = {
                    "total_issues": pylint_result.get("total_issues", 0),
                    "errors": pylint_result.get("errors", 0),
                    "warnings": pylint_result.get("warnings", 0),
                    "conventions": pylint_result.get("conventions", 0),
                    "issues": pylint_result.get("issues", [])[:10],  # First 10 issues
                }

                # Check/apply black formatting
                black_result = format_with_black(
                    file_path=file_path,
                    code=code,
                    line_length=line_length,
                    check_only=not fix,
                )

                results["formatting"] = {
                    "needs_formatting": black_result.get("needs_formatting", False),
                    "formatted": black_result.get("formatted", False),
                }

                if fix and black_result.get("formatted_code"):
                    results["formatted_code"] = black_result["formatted_code"]
                    if file_path:
                        # Write the formatted code back
                        Path(file_path).write_text(
                            black_result["formatted_code"], encoding="utf-8"
                        )
                        results["file_updated"] = True

                # If there are linting issues, try to fix them
                if results["pylint"]["total_issues"] > 0 and fix:
                    # Call the tool via the execution framework
                    fix_lint_result = self._execute_tool(
                        "fix_linting_errors",
                        {
                            "file_path": file_path,
                            "lint_issues": results["pylint"]["issues"],
                        },
                    )
                    if fix_lint_result.get("file_modified"):
                        results["lint_fixes_applied"] = fix_lint_result.get(
                            "fixes_applied", []
                        )
                        results["file_updated"] = True

                        # Re-run pylint to check remaining issues
                        pylint_recheck = self._execute_tool(
                            "analyze_with_pylint", {"file_path": file_path}
                        )
                        results["pylint_after_fixes"] = {
                            "total_issues": pylint_recheck.get("total_issues", 0),
                            "errors": pylint_recheck.get("errors", 0),
                            "warnings": pylint_recheck.get("warnings", 0),
                        }

                if black_result.get("diff"):
                    results["formatting_diff"] = black_result["diff"]

                # Overall assessment
                is_clean = (
                    results["pylint"]["total_issues"] == 0
                    and not results["formatting"]["needs_formatting"]
                )

                results["clean"] = is_clean
                results["message"] = (
                    "Code is clean and properly formatted"
                    if is_clean
                    else f"Found {results['pylint']['total_issues']} linting issues"
                    + (
                        " and formatting issues"
                        if results["formatting"]["needs_formatting"]
                        else ""
                    )
                )

                return results

            except Exception as e:
                return {"status": "error", "error": str(e)}

    def _generate_unified_diff(
        self,
        original: str,
        modified: str,
        original_name: str = "original",
        modified_name: str = "modified",
        context_lines: int = 3,
    ) -> str:
        """Generate a unified diff between two strings.

        Args:
            original: Original content
            modified: Modified content
            original_name: Name for original in diff header
            modified_name: Name for modified in diff header
            context_lines: Number of context lines

        Returns:
            Unified diff as string
        """
        import difflib

        original_lines = original.splitlines(keepends=True)
        modified_lines = modified.splitlines(keepends=True)

        # Generate the diff
        diff = difflib.unified_diff(
            original_lines,
            modified_lines,
            fromfile=original_name,
            tofile=modified_name,
            n=context_lines,
            lineterm="",
        )

        return "".join(diff)
