#!/usr/bin/env python
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Anti-pattern detection for Python code."""

import ast
from pathlib import Path
from typing import Any, Dict, List


class AntipatternChecker:
    """Checks for combinatorial anti-patterns and code smells."""

    def check(self, _file_path: Path, content: str) -> Dict[str, Any]:
        """Check for combinatorial anti-patterns like in HRMS services.py.

        Args:
            _file_path: Path to the file being checked (unused currently)
            content: File content to analyze

        Returns:
            Dictionary with errors and warnings found
        """
        errors = []
        warnings = []

        try:
            tree = ast.parse(content)

            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue

                func_name = node.name
                params = [
                    arg.arg for arg in node.args.args if arg.arg not in ("self", "cls")
                ]

                # Check for excessive function name length (HRMS had 311 chars!)
                if len(func_name) > 80:
                    errors.append(
                        f"Line {node.lineno}: Function name {len(func_name)} chars: {func_name[:60]}..."
                    )

                # Check for combinatorial naming
                and_count = func_name.count("_and_")
                by_count = func_name.count("_by_")
                if and_count >= 3 or by_count >= 3:
                    errors.append(
                        f"Line {node.lineno}: Combinatorial function with {and_count} 'and' and {by_count} 'by'"
                    )

                # Check parameter count (HRMS had 12+ params)
                if len(params) > 6:
                    warnings.append(
                        f"Line {node.lineno}: Function has {len(params)} parameters"
                    )

                # Check function complexity (simple heuristic based on body size)
                function_lines = 0
                if hasattr(node, "end_lineno") and node.end_lineno is not None:
                    function_lines = node.end_lineno - node.lineno
                if function_lines > 50:
                    warnings.append(
                        f"Line {node.lineno}: Function '{func_name}' is {function_lines} lines long (consider breaking it up)"
                    )

            # Check for duplicate class definitions
            class_names = [
                node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
            ]
            duplicates = [name for name in class_names if class_names.count(name) > 1]
            if duplicates:
                errors.append(
                    f"Duplicate class definitions: {', '.join(set(duplicates))}"
                )

            # Check for excessive file length
            lines = content.split("\n")
            if len(lines) > 1000:
                warnings.append(
                    f"File has {len(lines)} lines (consider splitting into multiple modules)"
                )

        except SyntaxError:
            pass  # Let pylint handle syntax errors

        return {"errors": errors, "warnings": warnings}

    def check_dict(self, content: str) -> Dict[str, Any]:
        """Check for anti-patterns in code content (without file path).

        Args:
            content: Python code content to check

        Returns:
            Dictionary with errors and warnings found
        """
        # Use a dummy path for checking content directly
        return self.check(Path("dummy.py"), content)

    def check_naming_patterns(self, tree: ast.Module) -> List[str]:
        """Check for problematic naming patterns.

        Args:
            tree: AST tree to analyze

        Returns:
            List of naming issues found
        """
        issues = []

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                # Check for overly long names
                if len(node.name) > 40:
                    issues.append(
                        f"Function '{node.name[:40]}...' has excessively long name ({len(node.name)} chars)"
                    )

                # Check for too many underscores (indicates poor design)
                if node.name.count("_") > 5:
                    issues.append(
                        f"Function '{node.name}' has too many underscores ({node.name.count('_')})"
                    )

            elif isinstance(node, ast.ClassDef):
                # Check class naming conventions
                if not node.name[0].isupper():
                    issues.append(
                        f"Class '{node.name}' should start with uppercase letter"
                    )

                # Check for overly long class names
                if len(node.name) > 30:
                    issues.append(
                        f"Class '{node.name[:30]}...' has excessively long name ({len(node.name)} chars)"
                    )

        return issues

    def check_function_complexity(self, node: ast.FunctionDef) -> List[str]:
        """Check function complexity metrics.

        Args:
            node: Function AST node to analyze

        Returns:
            List of complexity issues
        """
        issues = []

        # Count nested levels
        max_depth = self._get_max_nesting_depth(node)
        if max_depth > 4:
            issues.append(f"Function has excessive nesting depth: {max_depth}")

        # Count number of branches
        branches = self._count_branches(node)
        if branches > 10:
            issues.append(f"Function has too many branches: {branches}")

        # Count number of loops
        loops = self._count_loops(node)
        if loops > 3:
            issues.append(f"Function has too many loops: {loops}")

        return issues

    def _get_max_nesting_depth(self, node: ast.AST, current_depth: int = 0) -> int:
        """Calculate maximum nesting depth in a function.

        Args:
            node: AST node to analyze
            current_depth: Current nesting level

        Returns:
            Maximum nesting depth found
        """
        max_depth = current_depth

        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.If, ast.For, ast.While, ast.With, ast.Try)):
                child_depth = self._get_max_nesting_depth(child, current_depth + 1)
                max_depth = max(max_depth, child_depth)
            else:
                child_depth = self._get_max_nesting_depth(child, current_depth)
                max_depth = max(max_depth, child_depth)

        return max_depth

    def _count_branches(self, node: ast.AST) -> int:
        """Count number of branches in a function.

        Args:
            node: AST node to analyze

        Returns:
            Number of branches (if/elif/else)
        """
        count = 0
        for child in ast.walk(node):
            if isinstance(child, (ast.If, ast.IfExp)):
                count += 1
                # Count elif branches
                if isinstance(child, ast.If):
                    count += len(child.orelse) if isinstance(child.orelse, list) else 0

        return count

    def _count_loops(self, node: ast.AST) -> int:
        """Count number of loops in a function.

        Args:
            node: AST node to analyze

        Returns:
            Number of loops (for/while)
        """
        count = 0
        for child in ast.walk(node):
            if isinstance(child, (ast.For, ast.While, ast.AsyncFor)):
                count += 1

        return count
