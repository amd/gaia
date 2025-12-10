# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Shared File Search and Management Tools.

Provides common file search and read operations that can be used across multiple agents.
These tools are agent-agnostic and don't depend on specific agent functionality.
"""

import ast
import logging
import os
import platform
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)


class FileSearchToolsMixin:
    """
    Mixin providing shared file search and read operations.

    Tools provided:
    - search_file: Search filesystem for files by name/pattern
    - search_directory: Search filesystem for directories by name
    - read_file: Read any file with intelligent type-based analysis
    """

    def _format_file_list(self, file_paths: list) -> list:
        """Format file paths for numbered display to user."""
        file_list = []
        for i, fpath in enumerate(file_paths, 1):
            p = Path(fpath)
            file_list.append(
                {
                    "number": i,
                    "name": p.name,
                    "path": str(fpath),
                    "directory": str(p.parent),
                }
            )
        return file_list

    def register_file_search_tools(self) -> None:
        """Register shared file search tools."""
        from gaia.agents.base.tools import tool

        @tool(
            name="search_file",
            description="Search for files by name/pattern across entire drive(s). Searches common locations first, then does deep search. Use when user asks 'find X on my drive'.",
            parameters={
                "file_pattern": {
                    "type": "str",
                    "description": "File name pattern to search for (e.g., 'oil', 'manual', '*.pdf'). Supports partial matches.",
                    "required": True,
                },
                "search_all_drives": {
                    "type": "bool",
                    "description": "Search all available drives (default: True on Windows)",
                    "required": False,
                },
                "file_types": {
                    "type": "str",
                    "description": "Comma-separated file extensions to filter (e.g., 'pdf,docx,txt'). Default: all document types",
                    "required": False,
                },
            },
        )
        def search_file(
            file_pattern: str, search_all_drives: bool = True, file_types: str = None
        ) -> Dict[str, Any]:
            """
            Search for files with intelligent prioritization.

            Strategy:
            1. Search common document locations first (fast)
            2. If not found, search entire drive(s) (thorough)
            3. Filter by document file types for speed
            """
            try:
                # Document file extensions to search
                if file_types:
                    doc_extensions = {
                        f".{ext.strip().lower()}" for ext in file_types.split(",")
                    }
                else:
                    doc_extensions = {
                        ".pdf",
                        ".doc",
                        ".docx",
                        ".txt",
                        ".md",
                        ".csv",
                        ".json",
                        ".xlsx",
                        ".xls",
                    }

                matching_files = []
                pattern_lower = file_pattern.lower()
                searched_locations = []

                def matches_pattern_and_type(file_path: Path) -> bool:
                    """Check if file matches pattern and is a document type."""
                    name_match = pattern_lower in file_path.name.lower()
                    type_match = file_path.suffix.lower() in doc_extensions
                    return name_match and type_match

                def search_location(location: Path, max_depth: int = 999):
                    """Search a specific location up to max_depth."""
                    if not location.exists():
                        return

                    searched_locations.append(str(location))
                    logger.debug(f"Searching {location}...")

                    def search_recursive(current_path: Path, depth: int):
                        if depth > max_depth or len(matching_files) >= 20:
                            return

                        try:
                            for item in current_path.iterdir():
                                # Skip system/hidden directories
                                if item.name.startswith(
                                    (".", "$", "Windows", "Program Files")
                                ):
                                    continue

                                if item.is_file():
                                    if matches_pattern_and_type(item):
                                        matching_files.append(str(item.resolve()))
                                        logger.debug(f"Found: {item.name}")
                                elif item.is_dir() and depth < max_depth:
                                    search_recursive(item, depth + 1)
                        except (PermissionError, OSError) as e:
                            logger.debug(f"Skipping {current_path}: {e}")

                    search_recursive(location, 0)

                # Phase 0: Search CURRENT WORKING DIRECTORY first and thoroughly
                cwd = Path.cwd()
                home = Path.home()

                # Show progress to user
                if hasattr(self, "console") and hasattr(self.console, "start_progress"):
                    self.console.start_progress(
                        f"🔍 Searching current directory ({cwd.name}) for '{file_pattern}'..."
                    )

                logger.debug(
                    f"Phase 0: Deep search of current directory for '{file_pattern}'..."
                )
                logger.debug(f"Current directory: {cwd}")

                # Search current directory thoroughly (unlimited depth)
                search_location(cwd, max_depth=999)

                # If found in CWD, return immediately
                if matching_files:
                    if hasattr(self, "console") and hasattr(
                        self.console, "stop_progress"
                    ):
                        self.console.stop_progress()

                    # Add helpful context about where it was found
                    return {
                        "status": "success",
                        "files": matching_files[:10],
                        "file_list": self._format_file_list(matching_files[:10]),
                        "count": len(matching_files),
                        "search_context": "current_directory",
                        "display_message": f"✓ Found {len(matching_files)} file(s) in current directory ({cwd.name})",
                    }

                # Phase 1: Search common locations
                if hasattr(self, "console") and hasattr(self.console, "start_progress"):
                    self.console.start_progress(
                        "🔍 Searching common folders (Documents, Downloads, Desktop)..."
                    )

                logger.debug("Phase 1: Searching common document locations...")

                common_locations = [
                    home / "Documents",
                    home / "Downloads",
                    home / "Desktop",
                    home / "OneDrive",
                    home / "Google Drive",
                    home / "Dropbox",
                ]

                for location in common_locations:
                    if len(matching_files) >= 10:
                        break
                    search_location(location, max_depth=5)

                # If found in common locations, return
                if matching_files:
                    if hasattr(self, "console") and hasattr(
                        self.console, "stop_progress"
                    ):
                        self.console.stop_progress()

                    return {
                        "status": "success",
                        "files": matching_files[:10],
                        "file_list": self._format_file_list(matching_files[:10]),
                        "count": len(matching_files),
                        "total_locations_searched": len(searched_locations),
                        "search_context": "common_locations",
                        "display_message": f"✓ Found {len(matching_files)} file(s) in common locations",
                    }

                # Phase 2: Deep drive search if still not found
                if hasattr(self, "console") and hasattr(self.console, "start_progress"):
                    self.console.start_progress(
                        "🔍 Deep search across all drives (this may take a minute)..."
                    )

                logger.debug("Phase 2: Deep search across drive(s)...")

                if platform.system() == "Windows" and search_all_drives:
                    # Search all available drives on Windows
                    import string

                    for drive_letter in string.ascii_uppercase:
                        drive = Path(f"{drive_letter}:/")
                        if drive.exists():
                            logger.debug(f"Searching drive {drive_letter}:...")
                            search_location(drive, max_depth=999)
                            if len(matching_files) >= 10:
                                break
                else:
                    # On Linux/Mac, search from root
                    search_location(Path("/"), max_depth=999)

                # Stop progress indicator
                if hasattr(self, "console") and hasattr(self.console, "stop_progress"):
                    self.console.stop_progress()

                # Return final results
                if matching_files:
                    return {
                        "status": "success",
                        "files": matching_files[:10],
                        "file_list": self._format_file_list(matching_files[:10]),
                        "count": len(matching_files),
                        "total_locations_searched": len(searched_locations),
                        "display_message": f"✓ Found {len(matching_files)} file(s) after deep search",
                        "user_instruction": "If multiple files found, display numbered list and ask user to select one.",
                    }
                else:
                    # Build helpful message about what was searched
                    search_summary = []
                    if str(cwd) in searched_locations:
                        search_summary.append(f"current directory ({cwd.name})")
                    if len(searched_locations) > 1:
                        search_summary.append(
                            f"{len(searched_locations)} total locations"
                        )

                    searched_str = (
                        ", ".join(search_summary)
                        if search_summary
                        else f"{len(searched_locations)} locations"
                    )

                    return {
                        "status": "success",
                        "files": [],
                        "count": 0,
                        "total_locations_searched": len(searched_locations),
                        "search_summary": searched_str,
                        "display_message": f"❌ No files found matching '{file_pattern}'",
                        "searched": f"Searched {searched_str}",
                        "suggestion": "Try a different search term, check spelling, or provide the full file path if you know it.",
                    }

            except Exception as e:
                logger.error(f"Error searching for files: {e}")
                import traceback

                logger.error(traceback.format_exc())
                return {
                    "status": "error",
                    "error": str(e),
                    "has_errors": True,
                    "operation": "search_file",
                }

        @tool(
            name="search_directory",
            description="Search for a directory by name starting from a root path. Use when user asks to find or index 'my data folder' or similar.",
            parameters={
                "directory_name": {
                    "type": "str",
                    "description": "Name of directory to search for (e.g., 'data', 'documents')",
                    "required": True,
                },
                "search_root": {
                    "type": "str",
                    "description": "Root path to start search from (default: user's home directory)",
                    "required": False,
                },
                "max_depth": {
                    "type": "int",
                    "description": "Maximum depth to search (default: 4)",
                    "required": False,
                },
            },
        )
        def search_directory(
            directory_name: str, search_root: str = None, max_depth: int = 4
        ) -> Dict[str, Any]:
            """
            Search for directories by name.

            Returns list of matching directory paths.
            """
            try:
                # Default to home directory if no root specified
                if search_root is None:
                    search_root = str(Path.home())

                search_root = Path(search_root).resolve()

                if not search_root.exists():
                    return {
                        "status": "error",
                        "error": f"Search root does not exist: {search_root}",
                        "has_errors": True,
                    }

                logger.debug(
                    f"Searching for directory '{directory_name}' from {search_root}"
                )

                matching_dirs = []

                def search_recursive(current_path: Path, depth: int):
                    """Recursively search for matching directories."""
                    if depth > max_depth:
                        return

                    try:
                        for item in current_path.iterdir():
                            if item.is_dir():
                                # Check if name matches (case-insensitive)
                                if directory_name.lower() in item.name.lower():
                                    matching_dirs.append(str(item.resolve()))
                                    logger.debug(f"Found matching directory: {item}")

                                # Continue searching subdirectories
                                if depth < max_depth:
                                    search_recursive(item, depth + 1)
                    except (PermissionError, OSError) as e:
                        # Skip directories we can't access
                        logger.debug(f"Skipping {current_path}: {e}")

                search_recursive(search_root, 0)

                if matching_dirs:
                    return {
                        "status": "success",
                        "directories": matching_dirs[:10],  # Limit to 10 results
                        "count": len(matching_dirs),
                        "message": f"Found {len(matching_dirs)} matching directories",
                    }
                else:
                    return {
                        "status": "success",
                        "directories": [],
                        "count": 0,
                        "message": f"No directories matching '{directory_name}' found",
                    }

            except Exception as e:
                logger.error(f"Error searching for directory: {e}")
                return {
                    "status": "error",
                    "error": str(e),
                    "has_errors": True,
                    "operation": "search_directory",
                }

        @tool(
            name="read_file",
            description="Read any file and intelligently analyze based on file type. Supports Python, Markdown, and other text files.",
            parameters={
                "file_path": {
                    "type": "str",
                    "description": "Path to the file to read",
                    "required": True,
                }
            },
        )
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

                # Python file - add symbol extraction
                if ext == ".py":
                    result["file_type"] = "python"

                    try:
                        tree = ast.parse(content)
                        result["is_valid"] = True
                        result["errors"] = []

                        # Extract symbols
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
                    except SyntaxError as e:
                        result["is_valid"] = False
                        result["errors"] = [str(e)]

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

        @tool(
            name="search_file_content",
            description="Search for text patterns within files on disk (like grep). Searches actual file contents, not indexed documents.",
            parameters={
                "pattern": {
                    "type": "str",
                    "description": "Text pattern or keyword to search for",
                    "required": True,
                },
                "directory": {
                    "type": "str",
                    "description": "Directory to search in (default: current directory)",
                    "required": False,
                },
                "file_pattern": {
                    "type": "str",
                    "description": "File pattern to filter (e.g., '*.py', '*.txt'). Default: all text files",
                    "required": False,
                },
                "case_sensitive": {
                    "type": "bool",
                    "description": "Whether search should be case-sensitive (default: False)",
                    "required": False,
                },
            },
        )
        def search_file_content(
            pattern: str,
            directory: str = ".",
            file_pattern: str = None,
            case_sensitive: bool = False,
        ) -> Dict[str, Any]:
            """
            Search for text patterns within files (grep-like functionality).

            Searches actual file contents on disk, not RAG indexed documents.
            """
            try:
                import fnmatch

                directory = Path(directory).resolve()

                if not directory.exists():
                    return {
                        "status": "error",
                        "error": f"Directory not found: {directory}",
                    }

                # Text file extensions to search
                text_extensions = {
                    ".txt",
                    ".md",
                    ".py",
                    ".js",
                    ".java",
                    ".c",
                    ".cpp",
                    ".h",
                    ".json",
                    ".xml",
                    ".yaml",
                    ".yml",
                    ".csv",
                    ".log",
                    ".ini",
                    ".conf",
                    ".sh",
                    ".bat",
                    ".html",
                    ".css",
                    ".sql",
                }

                matches = []
                files_searched = 0
                search_pattern = pattern if case_sensitive else pattern.lower()

                def search_file(file_path: Path):
                    """Search within a single file."""
                    try:
                        with open(
                            file_path, "r", encoding="utf-8", errors="ignore"
                        ) as f:
                            for line_num, line in enumerate(f, 1):
                                search_line = line if case_sensitive else line.lower()
                                if search_pattern in search_line:
                                    matches.append(
                                        {
                                            "file": str(file_path),
                                            "line": line_num,
                                            "content": line.strip()[
                                                :200
                                            ],  # Limit line length
                                        }
                                    )
                                    if len(matches) >= 100:  # Limit total matches
                                        return False
                        return True
                    except Exception:
                        return True  # Continue searching

                # Search files
                for file_path in directory.rglob("*"):
                    if not file_path.is_file():
                        continue

                    # Filter by file pattern if provided
                    if file_pattern:
                        if not fnmatch.fnmatch(file_path.name, file_pattern):
                            continue
                    else:
                        # Only search text files
                        if file_path.suffix.lower() not in text_extensions:
                            continue

                    files_searched += 1
                    if not search_file(file_path):
                        break  # Hit match limit

                if matches:
                    return {
                        "status": "success",
                        "pattern": pattern,
                        "matches": matches[:50],  # Return first 50
                        "total_matches": len(matches),
                        "files_searched": files_searched,
                        "message": f"Found {len(matches)} matches in {files_searched} files",
                    }
                else:
                    return {
                        "status": "success",
                        "pattern": pattern,
                        "matches": [],
                        "total_matches": 0,
                        "files_searched": files_searched,
                        "message": f"No matches found for '{pattern}' in {files_searched} files",
                    }

            except Exception as e:
                logger.error(f"Error searching file content: {e}")
                return {
                    "status": "error",
                    "error": str(e),
                    "has_errors": True,
                    "operation": "search_file_content",
                }

        @tool(
            name="write_file",
            description="Write content to any file. Creates parent directories if needed.",
            parameters={
                "file_path": {
                    "type": "str",
                    "description": "Path where to write the file",
                    "required": True,
                },
                "content": {
                    "type": "str",
                    "description": "Content to write to the file",
                    "required": True,
                },
                "create_dirs": {
                    "type": "bool",
                    "description": "Whether to create parent directories (default: True)",
                    "required": False,
                },
            },
        )
        def write_file(
            file_path: str, content: str, create_dirs: bool = True
        ) -> Dict[str, Any]:
            """
            Write content to a file.

            Generic file writer for any file type.
            """
            try:
                file_path = Path(file_path)

                # Create parent directories if needed
                if create_dirs and file_path.parent:
                    file_path.parent.mkdir(parents=True, exist_ok=True)

                # Write the file
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)

                return {
                    "status": "success",
                    "file_path": str(file_path),
                    "bytes_written": len(content.encode("utf-8")),
                    "line_count": len(content.splitlines()),
                }
            except Exception as e:
                logger.error(f"Error writing file: {e}")
                return {
                    "status": "error",
                    "error": str(e),
                    "operation": "write_file",
                }
