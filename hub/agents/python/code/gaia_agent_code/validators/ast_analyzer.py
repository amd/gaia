#!/usr/bin/env python
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""AST parsing and analysis for Python code."""

import ast
from typing import List, Optional

from ..models import CodeSymbol, ParsedCode


class ASTAnalyzer:
    """Analyzes Python code using Abstract Syntax Trees."""

    def parse_code(self, code: str) -> ParsedCode:
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
            # Parse the code into an AST
            tree = ast.parse(code)
            result.ast_tree = tree
            result.is_valid = True

            # Extract symbols from the AST
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    # Extract function information
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
                    # Extract class information
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
                    # Extract import statements
                    for alias in node.names:
                        import_name = alias.asname if alias.asname else alias.name
                        result.imports.append(f"import {alias.name}")
                        result.symbols.append(
                            CodeSymbol(
                                name=import_name, type="import", line=node.lineno
                            )
                        )

                elif isinstance(node, ast.ImportFrom):
                    # Extract from...import statements
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
                    # Extract module-level variable assignments
                    for target in node.targets:
                        if isinstance(target, ast.Name) and isinstance(
                            target.ctx, ast.Store
                        ):
                            # Check if this is at module level (col_offset == 0)
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

        # Regular arguments
        for arg in node.args.args:
            param = arg.arg
            if arg.annotation:
                param += f": {ast.unparse(arg.annotation)}"
            params.append(param)

        # *args
        if node.args.vararg:
            param = f"*{node.args.vararg.arg}"
            if node.args.vararg.annotation:
                param += f": {ast.unparse(node.args.vararg.annotation)}"
            params.append(param)

        # **kwargs
        if node.args.kwarg:
            param = f"**{node.args.kwarg.arg}"
            if node.args.kwarg.annotation:
                param += f": {ast.unparse(node.args.kwarg.annotation)}"
            params.append(param)

        signature = f"{node.name}({', '.join(params)})"

        # Add return type if present
        if node.returns:
            signature += f" -> {ast.unparse(node.returns)}"

        return signature

    def extract_functions(
        self, tree: ast.Module
    ) -> List[ast.FunctionDef | ast.AsyncFunctionDef]:
        """Extract all function definitions from an AST.

        Args:
            tree: AST Module to analyze

        Returns:
            List of FunctionDef and AsyncFunctionDef nodes
        """
        functions = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(node)
        return functions

    def extract_classes(self, tree: ast.Module) -> List[ast.ClassDef]:
        """Extract all class definitions from an AST.

        Args:
            tree: AST Module to analyze

        Returns:
            List of ClassDef nodes
        """
        classes = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                classes.append(node)
        return classes

    def get_docstring(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module,
    ) -> Optional[str]:
        """Extract docstring from an AST node.

        Args:
            node: AST node to extract docstring from

        Returns:
            Docstring text or None
        """
        return ast.get_docstring(node)
