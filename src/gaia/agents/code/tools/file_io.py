#!/usr/bin/env python
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
File I/O tools mixin for code agents.

This module provides a mixin class with file I/O operations that can be
inherited by agents that need file manipulation capabilities.
"""

import ast
import difflib
import os
from typing import Any, Dict, Optional

from gaia.agents.base.tools import tool


class FileIOToolsMixin:
    """Mixin class providing file I/O tools for code agents.

    This class provides a collection of file I/O operations as tools that can be
    registered and used by agents. It includes reading, writing, editing, searching,
    and diffing capabilities for Python files.

    Attributes (provided by CodeAgent via ValidationAndParsingMixin):
        _validate_python_syntax: Method to validate Python syntax
        _parse_python_code: Method to parse Python code and extract structure

    NOTE: This mixin expects the agent to also have ValidationAndParsingMixin
    for _validate_python_syntax() and _parse_python_code() methods.
    """

    def register_file_io_tools(self) -> None:
        """Register all file I/O tools."""

        @tool
        def read_file(file_path: str) -> Dict[str, Any]:
            """Read any file and intelligently analyze based on file type.

            Automatically detects file type and provides appropriate analysis:
            - Python files (.py): Syntax validation + symbol extraction (functions/classes)
            - Markdown files (.md): Headers + code blocks + links
            - Other text files: Raw content

            Args:
                file_path: Path to the file to read

            Returns:
                Dictionary with file content and type-specific metadata
            """
            try:
                # Security check
                if not self.path_validator.is_path_allowed(file_path):
                    return {
                        "status": "error",
                        "error": f"Access denied: {file_path} is not in allowed paths",
                    }

                if not os.path.exists(file_path):
                    return {"status": "error", "error": f"File not found: {file_path}"}

                # Read file content
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                except UnicodeDecodeError:
                    # Binary file
                    with open(file_path, "rb") as f:
                        content_bytes = f.read()
                    return {
                        "status": "success",
                        "file_path": file_path,
                        "file_type": "binary",
                        "content": f"[Binary file, {len(content_bytes)} bytes]",
                        "is_binary": True,
                        "size_bytes": len(content_bytes),
                    }

                # Detect file type by extension
                ext = os.path.splitext(file_path)[1].lower()

                # Base result with common fields
                result = {
                    "status": "success",
                    "file_path": file_path,
                    "content": content,
                    "line_count": len(content.splitlines()),
                    "size_bytes": len(content.encode("utf-8")),
                }

                # Python file - add syntax validation and symbol extraction
                if ext == ".py":
                    import re

                    result["file_type"] = "python"

                    # Validate syntax using mixin method
                    validation = self._validate_python_syntax(content)
                    result["is_valid"] = validation["is_valid"]
                    result["errors"] = validation.get("errors", [])

                    # Extract symbols using mixin method
                    if validation["is_valid"]:
                        parsed = self._parse_python_code(content)
                        # Handle both ParsedCode object and dict (for backward compat)
                        if hasattr(parsed, "symbols"):
                            result["symbols"] = [
                                {"name": s.name, "type": s.type, "line": s.line}
                                for s in parsed.symbols
                            ]
                        elif hasattr(parsed, "ast_tree"):
                            # ParsedCode object
                            tree = parsed.ast_tree
                            symbols = []
                            for node in ast.walk(tree):
                                if isinstance(
                                    node, (ast.FunctionDef, ast.AsyncFunctionDef)
                                ):
                                    symbols.append(
                                        {
                                            "name": node.name,
                                            "type": "function",
                                            "line": node.lineno,
                                        }
                                    )
                                elif isinstance(node, ast.ClassDef):
                                    symbols.append(
                                        {
                                            "name": node.name,
                                            "type": "class",
                                            "line": node.lineno,
                                        }
                                    )
                            result["symbols"] = symbols

                # Markdown file - extract structure
                elif ext == ".md":
                    import re

                    result["file_type"] = "markdown"

                    # Extract headers
                    headers = re.findall(r"^#{1,6}\s+(.+)$", content, re.MULTILINE)
                    result["headers"] = headers

                    # Extract code blocks
                    code_blocks = re.findall(r"```(\w*)\n(.*?)```", content, re.DOTALL)
                    result["code_blocks"] = [
                        {"language": lang, "code": code} for lang, code in code_blocks
                    ]

                    # Extract links
                    links = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", content)
                    result["links"] = [
                        {"text": text, "url": url} for text, url in links
                    ]

                # Other text files
                else:
                    result["file_type"] = ext[1:] if ext else "text"

                return result

            except Exception as e:
                return {"status": "error", "error": str(e)}

        @tool
        def write_python_file(
            file_path: str,
            content: str,
            validate: bool = True,
            create_dirs: bool = True,
        ) -> Dict[str, Any]:
            """Write Python code to a file.

            Args:
                file_path: Path where to write the file
                content: Python code content
                validate: Whether to validate syntax before writing
                create_dirs: Whether to create parent directories

            Returns:
                Dictionary with write operation results
            """
            try:
                # Validate syntax if requested (using mixin method)
                if validate:
                    validation = self._validate_python_syntax(content)
                    if not validation["is_valid"]:
                        return {
                            "status": "error",
                            "error": "Invalid Python syntax",
                            "syntax_errors": validation.get("errors", []),
                        }

                # Security check
                if not self.path_validator.is_path_allowed(file_path):
                    return {
                        "status": "error",
                        "error": f"Access denied: {file_path} is not in allowed paths",
                    }

                # Create parent directories if needed
                if create_dirs and os.path.dirname(file_path):
                    os.makedirs(os.path.dirname(file_path), exist_ok=True)

                # Write the file
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)

                return {
                    "status": "success",
                    "file_path": file_path,
                    "bytes_written": len(content.encode("utf-8")),
                    "line_count": len(content.splitlines()),
                }
            except Exception as e:
                return {"status": "error", "error": str(e)}

        @tool
        def edit_python_file(
            file_path: str,
            old_content: str,
            new_content: str,
            backup: bool = True,
            dry_run: bool = False,
        ) -> Dict[str, Any]:
            """Edit a Python file by replacing content.

            Args:
                file_path: Path to the file to edit
                old_content: Content to find and replace
                new_content: New content to insert
                backup: Whether to create a backup
                dry_run: Whether to only simulate the edit

            Returns:
                Dictionary with edit operation results
            """
            try:
                # Security check
                if not self.path_validator.is_path_allowed(file_path):
                    return {
                        "status": "error",
                        "error": f"Access denied: {file_path} is not in allowed paths",
                    }

                # Read current content
                if not os.path.exists(file_path):
                    return {"status": "error", "error": f"File not found: {file_path}"}

                with open(file_path, "r", encoding="utf-8") as f:
                    current_content = f.read()

                # Check if old content exists
                if old_content not in current_content:
                    return {
                        "status": "error",
                        "error": "Content to replace not found in file",
                    }

                # Create new content
                modified_content = current_content.replace(old_content, new_content, 1)

                # Validate new content (using mixin method)
                validation = self._validate_python_syntax(modified_content)
                if not validation["is_valid"]:
                    return {
                        "status": "error",
                        "error": "Edit would result in invalid Python syntax",
                        "syntax_errors": validation.get("errors", []),
                    }

                # Generate diff
                diff = "\n".join(
                    difflib.unified_diff(
                        current_content.splitlines(keepends=True),
                        modified_content.splitlines(keepends=True),
                        fromfile=file_path,
                        tofile=file_path,
                    )
                )

                if dry_run:
                    return {
                        "status": "success",
                        "dry_run": True,
                        "diff": diff,
                        "would_change": current_content != modified_content,
                    }

                # Create backup if requested
                if backup:
                    backup_path = f"{file_path}.bak"
                    with open(backup_path, "w", encoding="utf-8") as f:
                        f.write(current_content)

                # Write the modified content
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(modified_content)

                return {
                    "status": "success",
                    "file_path": file_path,
                    "diff": diff,
                    "backup_created": backup,
                }
            except Exception as e:
                return {"status": "error", "error": str(e)}

        @tool
        def search_code(
            directory: str = ".",
            pattern: str = "",
            file_extension: str = ".py",
            max_results: int = 100,
        ) -> Dict[str, Any]:
            """Search for patterns in code files.

            Args:
                directory: Directory to search in
                pattern: Pattern to search for
                file_extension: File extension to filter
                max_results: Maximum number of results

            Returns:
                Dictionary with search results
            """
            try:
                # Security check
                if not self.path_validator.is_path_allowed(directory):
                    return {
                        "status": "error",
                        "error": f"Access denied: {directory} is not in allowed paths",
                    }

                results = []
                files_searched = 0
                files_with_matches = 0

                for root, _, files in os.walk(directory):
                    for file in files:
                        if not file.endswith(file_extension):
                            continue

                        file_path = os.path.join(root, file)
                        files_searched += 1

                        try:
                            with open(file_path, "r", encoding="utf-8") as f:
                                content = f.read()

                            if pattern in content:
                                files_with_matches += 1
                                # Find line numbers with matches
                                matches = []
                                for i, line in enumerate(content.splitlines(), 1):
                                    if pattern in line:
                                        matches.append(
                                            {"line": i, "content": line.strip()}
                                        )

                                results.append(
                                    {
                                        "file": os.path.relpath(file_path, directory),
                                        "matches": matches[
                                            :10
                                        ],  # Limit matches per file
                                    }
                                )

                                if len(results) >= max_results:
                                    break
                        except Exception:
                            continue

                    if len(results) >= max_results:
                        break

                return {
                    "status": "success",
                    "pattern": pattern,
                    "directory": directory,
                    "files_searched": files_searched,
                    "files_with_matches": files_with_matches,
                    "results": results,
                }
            except Exception as e:
                return {"status": "error", "error": str(e)}

        @tool
        def generate_diff(
            file_path: str, new_content: str, context_lines: int = 3
        ) -> Dict[str, Any]:
            """Generate a unified diff for a file.

            Args:
                file_path: Path to the original file
                new_content: New content to compare
                context_lines: Number of context lines in diff

            Returns:
                Dictionary with diff information
            """
            try:
                # Security check
                if not self.path_validator.is_path_allowed(file_path):
                    return {
                        "status": "error",
                        "error": f"Access denied: {file_path} is not in allowed paths",
                    }

                # Read original content
                if os.path.exists(file_path):
                    with open(file_path, "r", encoding="utf-8") as f:
                        original_content = f.read()
                else:
                    original_content = ""

                # Generate unified diff
                diff = list(
                    difflib.unified_diff(
                        original_content.splitlines(keepends=True),
                        new_content.splitlines(keepends=True),
                        fromfile=file_path,
                        tofile=file_path,
                        n=context_lines,
                    )
                )

                # Count changes
                additions = sum(
                    1
                    for line in diff
                    if line.startswith("+") and not line.startswith("+++")
                )
                deletions = sum(
                    1
                    for line in diff
                    if line.startswith("-") and not line.startswith("---")
                )

                return {
                    "status": "success",
                    "file_path": file_path,
                    "diff": "".join(diff),
                    "additions": additions,
                    "deletions": deletions,
                    "has_changes": bool(diff),
                }
            except Exception as e:
                return {"status": "error", "error": str(e)}

        @tool
        def write_markdown_file(
            file_path: str, content: str, create_dirs: bool = True
        ) -> Dict[str, Any]:
            """Write content to a markdown file.

            Args:
                file_path: Path where to write the file
                content: Markdown content
                create_dirs: Whether to create parent directories

            Returns:
                Dictionary with write operation results
            """
            try:
                # Security check
                if not self.path_validator.is_path_allowed(file_path):
                    return {
                        "status": "error",
                        "error": f"Access denied: {file_path} is not in allowed paths",
                    }

                # Create parent directories if needed
                if create_dirs:
                    os.makedirs(os.path.dirname(file_path), exist_ok=True)

                # Write the file
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)

                return {
                    "status": "success",
                    "file_path": file_path,
                    "bytes_written": len(content.encode("utf-8")),
                    "line_count": len(content.splitlines()),
                }
            except Exception as e:
                return {"status": "error", "error": str(e)}

        @tool
        def write_file(
            file_path: str,
            content: str,
            create_dirs: bool = True,
            project_dir: Optional[str] = None,
        ) -> Dict[str, Any]:
            """Write content to any file (TypeScript, JavaScript, JSON, etc.) without syntax validation.

            Use this tool for non-Python files like .tsx, .ts, .js, .json, etc.

            Args:
                file_path: Path where to write the file
                content: Content to write to the file
                create_dirs: Whether to create parent directories if they don't exist
                project_dir: Project root directory for resolving relative paths

            Returns:
                dict: Status and file information
            """
            try:
                from pathlib import Path

                path = Path(file_path)
                if project_dir:
                    base = Path(project_dir).resolve()
                    if not path.is_absolute():
                        path = base / path
                path = path.resolve()

                # Create parent directories if requested
                if create_dirs and not path.parent.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)

                # Write content to file
                path.write_text(content, encoding="utf-8")

                console = getattr(self, "console", None)
                if console:
                    if content.strip():
                        console.print_prompt(
                            content,
                            title=f"✏️ write_file → {path}",
                        )
                    else:
                        console.print_info(
                            f"write_file: {path} was created but no content was written."
                        )

                return {
                    "status": "success",
                    "file_path": str(path),
                    "size_bytes": len(content),
                    "file_type": path.suffix[1:] if path.suffix else "unknown",
                }
            except Exception as e:
                return {"status": "error", "error": str(e)}

        @tool
        def edit_file(
            file_path: str,
            old_content: str,
            new_content: str,
            project_dir: Optional[str] = None,
        ) -> Dict[str, Any]:
            """Edit any file by replacing old content with new content (no syntax validation).

            Use this tool for non-Python files like .tsx, .ts, .js, .json, etc.

            Args:
                file_path: Path to the file to edit
                old_content: Exact content to find and replace
                new_content: New content to replace with
                project_dir: Project root directory for resolving relative paths

            Returns:
                dict: Status and edit information
            """
            try:
                from pathlib import Path

                path = Path(file_path)
                if project_dir:
                    base = Path(project_dir).resolve()
                    if not path.is_absolute():
                        path = base / path
                path = path.resolve()

                if not path.exists():
                    return {"status": "error", "error": f"File not found: {file_path}"}

                # Read current content
                current_content = path.read_text(encoding="utf-8")

                # Check if old_content exists in file
                if old_content not in current_content:
                    return {
                        "status": "error",
                        "error": f"Content to replace not found in {file_path}",
                    }

                # Replace content
                updated_content = current_content.replace(old_content, new_content, 1)

                # Generate diff before writing
                diff = "\n".join(
                    difflib.unified_diff(
                        current_content.splitlines(keepends=True),
                        updated_content.splitlines(keepends=True),
                        fromfile=f"a/{os.path.basename(str(path))}",
                        tofile=f"b/{os.path.basename(str(path))}",
                        lineterm="",
                    )
                )

                # Write updated content
                path.write_text(updated_content, encoding="utf-8")

                console = getattr(self, "console", None)
                if console:
                    if diff.strip():
                        console.print_diff(diff, os.path.basename(str(path)))
                    else:
                        console.print_info(f"edit_file: No changes were made to {path}")

                return {
                    "status": "success",
                    "file_path": str(path),
                    "old_size": len(current_content),
                    "new_size": len(updated_content),
                    "file_type": path.suffix[1:] if path.suffix else "unknown",
                    "diff": diff,
                }
            except Exception as e:
                return {"status": "error", "error": str(e)}

        @tool
        def update_gaia_md(
            project_root: str = ".",
            project_name: str = None,
            description: str = None,
            structure: Dict[str, Any] = None,
            instructions: str = None,
        ) -> Dict[str, Any]:
            """Create or update GAIA.md file for project context.

            Args:
                project_root: Root directory of the project
                project_name: Name of the project
                description: Project description
                structure: Project structure dictionary
                instructions: Special instructions for GAIA

            Returns:
                Dictionary with update results
            """
            try:
                from datetime import datetime

                gaia_path = os.path.join(project_root, "GAIA.md")

                # Security check
                if not self.path_validator.is_path_allowed(gaia_path):
                    return {
                        "status": "error",
                        "error": f"Access denied: {gaia_path} is not in allowed paths",
                    }

                # Start building content
                content = "# GAIA.md\n\n"
                content += "This file provides guidance to GAIA Code Agent when working with code in this project.\n\n"

                if project_name:
                    content += f"## Project: {project_name}\n\n"

                if description:
                    content += f"## Description\n{description}\n\n"

                content += f"**Last Updated:** {datetime.now().isoformat()}\n\n"

                if structure:
                    content += "## Project Structure\n```\n"

                    def format_structure(struct, indent=""):
                        result = ""
                        if isinstance(struct, dict):
                            for key, value in struct.items():
                                if isinstance(value, dict):
                                    result += f"{indent}{key}\n"
                                    result += format_structure(value, indent + "  ")
                                else:
                                    result += f"{indent}{key} - {value}\n"
                        return result

                    content += format_structure(structure)
                    content += "```\n\n"

                if instructions:
                    content += f"## Special Instructions\n{instructions}\n\n"

                # Add default sections
                content += "## Development Guidelines\n"
                content += "- Follow PEP 8 style guidelines\n"
                content += "- Add docstrings to all functions and classes\n"
                content += "- Include type hints where appropriate\n"
                content += "- Write unit tests for new functionality\n\n"

                content += "## Code Quality\n"
                content += "- All code should pass pylint checks\n"
                content += "- Use Black formatter for consistent style\n"
                content += "- Ensure proper error handling\n\n"

                # Write the file
                with open(gaia_path, "w", encoding="utf-8") as f:
                    f.write(content)

                return {
                    "status": "success",
                    "file_path": gaia_path,
                    "created": not os.path.exists(gaia_path),
                    "message": f"GAIA.md {'created' if not os.path.exists(gaia_path) else 'updated'} at {gaia_path}",
                }
            except Exception as e:
                return {"status": "error", "error": str(e)}

        @tool
        def replace_function(
            file_path: str,
            function_name: str,
            new_implementation: str,
            backup: bool = True,
        ) -> Dict[str, Any]:
            """Replace a specific function in a Python file.

            Args:
                file_path: Path to the Python file
                function_name: Name of the function to replace
                new_implementation: New function implementation
                backup: Whether to create backup

            Returns:
                Dictionary with replacement result
            """
            try:
                # Security check
                if not self.path_validator.is_path_allowed(file_path):
                    return {
                        "status": "error",
                        "error": f"Access denied: {file_path} is not in allowed paths",
                    }

                if not os.path.exists(file_path):
                    return {"status": "error", "error": f"File not found: {file_path}"}

                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()

                # Parse the file to find the function
                try:
                    tree = ast.parse(content)
                except SyntaxError as e:
                    return {"status": "error", "error": f"File has syntax errors: {e}"}

                # Find the function node
                function_node = None
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if node.name == function_name:
                            function_node = node
                            break

                if not function_node:
                    return {
                        "status": "error",
                        "error": f"Function '{function_name}' not found in file",
                    }

                # Get line range of the function
                lines = content.splitlines(keepends=True)
                start_line = function_node.lineno - 1

                # Find end of function (simplified - finds next def or class at same indent)
                end_line = len(lines)
                indent_level = len(lines[start_line]) - len(lines[start_line].lstrip())

                for i in range(start_line + 1, len(lines)):
                    line = lines[i]
                    if line.strip() and not line.lstrip().startswith("#"):
                        current_indent = len(line) - len(line.lstrip())
                        if current_indent <= indent_level and line.strip():
                            if line.lstrip().startswith(
                                ("def ", "class ", "async def ")
                            ):
                                end_line = i
                                break

                # Create backup if requested
                if backup:
                    backup_path = f"{file_path}.bak"
                    with open(backup_path, "w", encoding="utf-8") as f:
                        f.write(content)

                # Replace the function
                new_lines = (
                    lines[:start_line] + [new_implementation + "\n"] + lines[end_line:]
                )
                modified_content = "".join(new_lines)

                # Validate new content (using mixin method)
                validation = self._validate_python_syntax(modified_content)
                if not validation["is_valid"]:
                    return {
                        "status": "error",
                        "error": "Replacement would result in invalid syntax",
                        "syntax_errors": validation.get("errors", []),
                    }

                # Write the modified content
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(modified_content)

                # Generate diff
                diff = "\n".join(
                    difflib.unified_diff(
                        content.splitlines(keepends=True),
                        modified_content.splitlines(keepends=True),
                        fromfile=file_path,
                        tofile=file_path,
                    )
                )

                return {
                    "status": "success",
                    "file_path": file_path,
                    "function_replaced": function_name,
                    "backup_path": backup_path if backup else None,
                    "diff": diff,
                }
            except Exception as e:
                return {"status": "error", "error": str(e)}

        # Return the list of registered tools for tracking
        return [
            "read_file",
            "write_python_file",
            "edit_python_file",
            "search_code",
            "generate_diff",
            "write_markdown_file",
            "update_gaia_md",
            "replace_function",
        ]
