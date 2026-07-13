#!/usr/bin/env python
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Python syntax validation module."""

import ast
from typing import Any, Dict, List

from ..models import ValidationResult


class SyntaxValidator:
    """Validates Python code syntax."""

    def validate(self, code: str) -> ValidationResult:
        """Validate Python code syntax.

        Args:
            code: Python code to validate

        Returns:
            ValidationResult with validation details
        """
        result = ValidationResult(is_valid=True)

        try:
            # Try to compile the code
            compile(code, "<string>", "exec")

            # Also try to parse with AST for more detailed checking
            ast.parse(code)

            return result

        except SyntaxError as e:
            result.is_valid = False
            result.errors.append(f"Line {e.lineno}: {e.msg}")
            if e.text:
                result.errors.append(f"  {e.text.rstrip()}")
                if e.offset:
                    result.errors.append(f"  {' ' * (e.offset - 1)}^")
            return result

        except Exception as e:
            result.is_valid = False
            result.errors.append(f"Parse error: {str(e)}")
            return result

    def validate_dict(self, code: str) -> Dict[str, Any]:
        """Validate Python code and return as dictionary (legacy format).

        Args:
            code: Python code to validate

        Returns:
            Dictionary with validation results
        """
        result = self.validate(code)

        return {
            "status": "success" if result.is_valid else "error",
            "is_valid": result.is_valid,
            "errors": result.errors,
            "message": "Syntax is valid" if result.is_valid else "Syntax errors found",
        }

    def get_syntax_errors(self, code: str) -> List[SyntaxError]:
        """Get all syntax errors from code.

        Args:
            code: Python code to check

        Returns:
            List of SyntaxError objects
        """
        errors = []
        try:
            compile(code, "<string>", "exec")
        except SyntaxError as e:
            errors.append(e)

        return errors

    def check_indentation(self, code: str) -> List[str]:
        """Check for indentation issues in code.

        Args:
            code: Python code to check

        Returns:
            List of indentation warnings
        """
        warnings = []
        lines = code.split("\n")

        for i, line in enumerate(lines, 1):
            if line and line[0] in " \t":
                # Check for mixed tabs and spaces
                if " " in line and "\t" in line:
                    warnings.append(f"Line {i}: Mixed tabs and spaces in indentation")

                # Check for non-standard indentation (not multiples of 4)
                if line.startswith(" "):
                    spaces = len(line) - len(line.lstrip())
                    if spaces % 4 != 0:
                        warnings.append(
                            f"Line {i}: Non-standard indentation ({spaces} spaces)"
                        )

        return warnings

    def validate_imports(self, code: str) -> List[str]:
        """Validate import statements in code.

        Args:
            code: Python code to check

        Returns:
            List of import-related warnings
        """
        warnings = []

        try:
            tree = ast.parse(code)

            # Check for wildcard imports
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name == "*":
                            warnings.append(
                                f"Line {node.lineno}: Wildcard import 'from {node.module} import *' is discouraged"
                            )

            # Check for duplicate imports
            imports_seen = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in imports_seen:
                            warnings.append(
                                f"Line {node.lineno}: Duplicate import '{alias.name}'"
                            )
                        imports_seen.add(alias.name)

        except SyntaxError:
            # Syntax errors will be caught by main validation
            pass

        return warnings

    def check_line_length(self, code: str, max_length: int = 88) -> List[str]:
        """Check for lines exceeding maximum length.

        Args:
            code: Python code to check
            max_length: Maximum allowed line length (default: 88 for Black)

        Returns:
            List of line length warnings
        """
        warnings = []
        lines = code.split("\n")

        for i, line in enumerate(lines, 1):
            if len(line) > max_length:
                warnings.append(
                    f"Line {i}: Line too long ({len(line)} > {max_length} characters)"
                )

        return warnings
