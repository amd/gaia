# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Shared File Search and Management Tools.

Provides common file search and read operations that can be used across multiple agents.
These tools are agent-agnostic and don't depend on specific agent functionality.
"""

import ast
import csv
import fnmatch
import logging
import mimetypes
import os
import platform
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, List

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
            name = p.name
            parent = str(p.parent)
            # On Linux, Path won't split Windows backslash paths properly.
            # Fall back to PureWindowsPath when the name still has backslashes.
            if "\\" in name:
                wp = PureWindowsPath(fpath)
                name = wp.name
                parent = str(wp.parent)
            file_list.append(
                {
                    "number": i,
                    "name": name,
                    "path": str(fpath),
                    "directory": parent,
                }
            )
        return file_list

    def register_file_search_tools(self) -> None:
        """Register shared file search tools."""
        from gaia.agents.base.tools import tool

        @tool(
            atomic=True,
            name="search_file",
            description="Search for files by name/pattern. By default does a QUICK search of common locations (CWD, Documents, Downloads, Desktop). Only set deep_search=True if quick search found nothing AND user confirms they want a deeper search.",
            parameters={
                "file_pattern": {
                    "type": "str",
                    "description": "File name pattern to search for (e.g., 'oil', 'manual', '*.pdf'). Supports partial matches.",
                    "required": True,
                },
                "deep_search": {
                    "type": "bool",
                    "description": "If True, search ALL drives thoroughly (slow). Only use after quick search found nothing and user requests it. Default: False",
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
            file_pattern: str, deep_search: bool = False, file_types: str = None
        ) -> Dict[str, Any]:
            """
            Search for files with intelligent prioritization.

            Strategy:
            1. Quick search: CWD + common document locations (fast)
            2. Deep search: entire drive(s) (only when deep_search=True)
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

                # Detect if the pattern is a glob (contains * or ?)
                is_glob = "*" in file_pattern or "?" in file_pattern

                # For multi-word queries, split into individual words
                # so "operations manual" matches "Operations-Manual" in filenames
                query_words = pattern_lower.split() if not is_glob else []

                def matches_pattern_and_type(file_path: Path) -> bool:
                    """Check if file matches pattern and is a document type."""
                    name_lower = file_path.name.lower()
                    if is_glob:
                        # Use fnmatch for glob patterns like *.pdf, report*.docx
                        name_match = fnmatch.fnmatch(name_lower, pattern_lower)
                    elif len(query_words) > 1:
                        # Multi-word query: all words must appear in filename
                        # (handles hyphens, underscores, camelCase separators)
                        name_match = all(w in name_lower for w in query_words)
                    else:
                        # Single word: simple substring match
                        name_match = pattern_lower in name_lower
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

                # Phase 0+1: Search CWD AND common locations together
                # (always search both before returning, so Documents/Downloads
                # files aren't missed just because CWD had some matches)
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

                # Always also search common locations (Documents, Downloads, etc.)
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
                    if len(matching_files) >= 20:
                        break
                    # Skip if already searched as part of CWD
                    try:
                        if location.resolve() == cwd.resolve() or str(
                            location.resolve()
                        ).startswith(str(cwd.resolve())):
                            continue
                    except (OSError, ValueError):
                        pass
                    search_location(location, max_depth=5)

                # Deduplicate results (CWD and common locations may overlap)
                unique_files = []
                unique_set = set()
                for f in matching_files:
                    resolved = str(Path(f).resolve())
                    if resolved not in unique_set:
                        unique_set.add(resolved)
                        unique_files.append(f)
                matching_files = unique_files

                # Stop progress indicator after quick search
                if hasattr(self, "console") and hasattr(self.console, "stop_progress"):
                    self.console.stop_progress()

                # If found in CWD + common locations, return immediately
                if matching_files:
                    return {
                        "status": "success",
                        "files": matching_files[:10],
                        "file_list": self._format_file_list(matching_files[:10]),
                        "count": len(matching_files),
                        "total_locations_searched": len(searched_locations),
                        "search_context": "common_locations",
                        "display_message": f"✓ Found {len(matching_files)} file(s)",
                    }

                # Quick search found nothing
                if not deep_search:
                    # Return with hint that deep search is available
                    return {
                        "status": "success",
                        "files": [],
                        "count": 0,
                        "total_locations_searched": len(searched_locations),
                        "search_context": "common_locations",
                        "display_message": f"No files found matching '{file_pattern}' in common locations",
                        "deep_search_available": True,
                        "suggestion": "I can do a deep search across all drives if you'd like (this may take a minute).",
                    }

                # Phase 2: Deep drive search (only when explicitly requested)
                if hasattr(self, "console") and hasattr(self.console, "start_progress"):
                    self.console.start_progress(
                        "🔍 Deep search across all drives (this may take a minute)..."
                    )

                logger.debug("Phase 2: Deep search across drive(s)...")

                if platform.system() == "Windows":
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
            atomic=True,
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
            atomic=True,
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
                    # Check if parent directory exists to give a more helpful error
                    parent_dir = os.path.dirname(file_path)
                    parent_exists = os.path.exists(parent_dir) if parent_dir else False
                    file_name = os.path.basename(file_path)
                    hint = (
                        f" The parent directory '{parent_dir}' also does not exist."
                        if parent_dir and not parent_exists
                        else (
                            f" The directory '{parent_dir}' exists but the file is not in it."
                            if parent_dir
                            else ""
                        )
                    )
                    return {
                        "status": "error",
                        "error": (
                            f"File not found: {file_path}.{hint}"
                            f" Try using search_file with pattern '{file_name}'"
                            " to locate it elsewhere."
                        ),
                    }

                # Guard against reading very large files into memory
                file_size = os.path.getsize(file_path)
                if file_size > 10_000_000:  # 10 MB
                    return {
                        "status": "error",
                        "error": (
                            f"File too large ({file_size:,} bytes). "
                            "Use search_file_content for large files."
                        ),
                    }

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
            atomic=True,
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
            atomic=True,
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
            except PermissionError:
                logger.error(f"Permission denied writing to: {file_path}")
                return {
                    "status": "error",
                    "error": f"Permission denied: cannot write to '{file_path}'. "
                    "This folder may be protected by the operating system. "
                    "Try writing to a different location such as the Downloads folder.",
                    "operation": "write_file",
                }
            except FileNotFoundError:
                logger.error(f"Path not found for writing: {file_path}")
                return {
                    "status": "error",
                    "error": f"Path not found: '{file_path}'. "
                    "The parent directory does not exist and could not be created.",
                    "operation": "write_file",
                }
            except OSError as e:
                logger.error(f"OS error writing file: {e}")
                hint = ""
                if "read-only" in str(e).lower():
                    hint = " The file or folder may be read-only."
                elif "No space" in str(e) or "ENOSPC" in str(e):
                    hint = " The disk may be full."
                return {
                    "status": "error",
                    "error": f"Cannot write to '{file_path}': {e}.{hint}",
                    "operation": "write_file",
                }
            except Exception as e:
                logger.error(f"Error writing file: {e}")
                return {
                    "status": "error",
                    "error": str(e),
                    "operation": "write_file",
                }

        # --- Helper functions for new file browsing/analysis tools ---

        def _human_readable_size(size_bytes: int) -> str:
            """Convert bytes to human-readable string."""
            if size_bytes < 1024:
                return f"{size_bytes} B"
            elif size_bytes < 1024 * 1024:
                return f"{size_bytes / 1024:.1f} KB"
            elif size_bytes < 1024 * 1024 * 1024:
                return f"{size_bytes / (1024 * 1024):.1f} MB"
            else:
                return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

        def _relative_time(dt: datetime) -> str:
            """Convert a datetime to a human-readable relative time string."""
            now = datetime.now()
            diff = now - dt
            seconds = diff.total_seconds()

            if seconds < 60:
                return "just now"
            elif seconds < 3600:
                minutes = int(seconds / 60)
                return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
            elif seconds < 86400:
                hours = int(seconds / 3600)
                return f"{hours} hour{'s' if hours != 1 else ''} ago"
            elif seconds < 172800:
                return "yesterday"
            elif seconds < 604800:
                days = int(seconds / 86400)
                return f"{days} days ago"
            elif seconds < 2592000:
                weeks = int(seconds / 604800)
                return f"{weeks} week{'s' if weeks != 1 else ''} ago"
            else:
                return dt.strftime("%Y-%m-%d")

        def _read_tabular_file(file_path: str) -> tuple:
            """
            Read a tabular data file (CSV, TSV, or Excel) into a list of dicts.

            Returns:
                Tuple of (rows: List[Dict], columns: List[str], error: str or None)
            """
            ext = Path(file_path).suffix.lower()
            rows: List[Dict[str, Any]] = []
            columns: List[str] = []
            error = None

            if ext in (".xlsx", ".xls"):
                try:
                    import openpyxl

                    wb = openpyxl.load_workbook(
                        file_path, read_only=True, data_only=True
                    )
                    ws = wb.active
                    ws_rows = list(ws.iter_rows(values_only=True))
                    wb.close()
                    if not ws_rows:
                        return [], [], None
                    # First row is headers
                    columns = [
                        str(c) if c is not None else f"Column_{i}"
                        for i, c in enumerate(ws_rows[0])
                    ]
                    for row_vals in ws_rows[1:]:
                        row_dict = {}
                        for i, val in enumerate(row_vals):
                            col_name = columns[i] if i < len(columns) else f"Column_{i}"
                            row_dict[col_name] = val
                        rows.append(row_dict)
                except ImportError:
                    error = (
                        "Excel support requires openpyxl. "
                        "Install with: pip install openpyxl. "
                        "Alternatively, save the file as CSV and try again."
                    )
                except Exception as e:
                    error = f"Error reading Excel file: {e}"
            else:
                # CSV or TSV
                delimiter = "\t" if ext == ".tsv" else ","
                # Try multiple encodings
                content = None
                for encoding in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
                    try:
                        with open(file_path, "r", encoding=encoding, newline="") as f:
                            content = f.read()
                        break
                    except (UnicodeDecodeError, UnicodeError):
                        continue
                if content is None:
                    error = "Could not decode file with any supported encoding (utf-8, latin-1, cp1252)"
                    return [], [], error

                try:
                    # Use csv.Sniffer to detect delimiter if possible
                    try:
                        sample = content[:4096]
                        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
                        delimiter = dialect.delimiter
                    except csv.Error:
                        pass  # Use default delimiter

                    reader = csv.DictReader(content.splitlines(), delimiter=delimiter)
                    columns = reader.fieldnames or []
                    for row in reader:
                        rows.append(dict(row))
                except Exception as e:
                    error = f"Error parsing CSV/TSV file: {e}"

            return rows, columns, error

        def _infer_column_type(values: list) -> str:
            """Infer the data type of a column from its values."""
            numeric_count = 0
            date_count = 0
            total = 0

            for val in values:
                if val is None or (isinstance(val, str) and val.strip() == ""):
                    continue
                total += 1
                # Check numeric
                try:
                    cleaned = (
                        str(val)
                        .replace(",", "")
                        .replace("$", "")
                        .replace("£", "")
                        .replace("€", "")
                        .strip()
                    )
                    if cleaned.startswith("(") and cleaned.endswith(")"):
                        cleaned = cleaned[1:-1]  # Handle accounting negatives
                    float(cleaned)
                    numeric_count += 1
                    continue
                except (ValueError, TypeError):
                    pass
                # Check date-like
                val_str = str(val).strip()
                if any(sep in val_str for sep in ["/", "-"]) and len(val_str) >= 6:
                    date_count += 1

            if total == 0:
                return "empty"
            if numeric_count / total > 0.7:
                return "numeric"
            if date_count / total > 0.7:
                return "date"
            return "text"

        def _parse_numeric(val) -> float:
            """Parse a value as a float, handling currency symbols and accounting format."""
            if val is None:
                return 0.0
            cleaned = (
                str(val)
                .replace(",", "")
                .replace("$", "")
                .replace("£", "")
                .replace("€", "")
                .strip()
            )
            negative = False
            if cleaned.startswith("(") and cleaned.endswith(")"):
                cleaned = cleaned[1:-1]
                negative = True
            if cleaned.startswith("-"):
                cleaned = cleaned[1:]
                negative = True
            try:
                result = float(cleaned)
                return -result if negative else result
            except (ValueError, TypeError):
                return 0.0

        # --- New tool definitions ---

        @tool(
            atomic=True,
            name="browse_directory",
            description="List files and folders in a directory. Use for navigating the filesystem to help users find files.",
            parameters={
                "directory_path": {
                    "type": "str",
                    "description": "Directory path to browse (default: user's home directory)",
                    "required": False,
                },
                "show_hidden": {
                    "type": "bool",
                    "description": "Whether to show hidden files/folders (default: False)",
                    "required": False,
                },
                "sort_by": {
                    "type": "str",
                    "description": "Sort by: 'name', 'size', 'modified', 'type' (default: 'name')",
                    "required": False,
                },
            },
        )
        def browse_directory(
            directory_path: str = None, show_hidden: bool = False, sort_by: str = "name"
        ) -> Dict[str, Any]:
            """
            List files and folders in a directory for filesystem navigation.

            Provides detailed entry information including name, type, size, and
            modification date. Sorts folders first, then by the requested key.

            Args:
                directory_path: Directory path to browse (default: user's home directory)
                show_hidden: Whether to show hidden files/folders
                sort_by: Sort by 'name', 'size', 'modified', or 'type'

            Returns:
                Dictionary with entries list, path info, and counts
            """
            try:
                if directory_path is None:
                    directory_path = str(Path.home())

                dir_path = Path(directory_path).resolve()

                if not dir_path.exists():
                    return {
                        "status": "error",
                        "error": f"Directory not found: {directory_path}",
                        "has_errors": True,
                        "operation": "browse_directory",
                    }

                if not dir_path.is_dir():
                    return {
                        "status": "error",
                        "error": f"Path is not a directory: {directory_path}",
                        "has_errors": True,
                        "operation": "browse_directory",
                    }

                entries = []
                total_files = 0
                total_folders = 0

                try:
                    items = list(dir_path.iterdir())
                except PermissionError:
                    return {
                        "status": "error",
                        "error": f"Permission denied: {directory_path}",
                        "has_errors": True,
                        "operation": "browse_directory",
                    }

                for item in items:
                    try:
                        # Skip hidden files unless requested
                        if not show_hidden and item.name.startswith("."):
                            continue

                        stat_info = item.stat()
                        is_dir = item.is_dir()

                        if is_dir:
                            total_folders += 1
                        else:
                            total_files += 1

                        modified_dt = datetime.fromtimestamp(stat_info.st_mtime)

                        entry = {
                            "name": item.name,
                            "path": str(item),
                            "type": "folder" if is_dir else "file",
                            "size_bytes": stat_info.st_size if not is_dir else 0,
                            "size": (
                                _human_readable_size(stat_info.st_size)
                                if not is_dir
                                else "-"
                            ),
                            "modified": modified_dt.strftime("%Y-%m-%d %H:%M"),
                            "modified_ago": _relative_time(modified_dt),
                            "extension": item.suffix.lower() if not is_dir else "",
                        }
                        entries.append(entry)

                    except (PermissionError, OSError) as e:
                        logger.debug(f"Skipping {item.name}: {e}")
                        continue

                # Sort: folders first, then by requested key
                def sort_key(entry):
                    is_folder = 0 if entry["type"] == "folder" else 1
                    if sort_by == "size":
                        return (is_folder, -entry["size_bytes"])
                    elif sort_by == "modified":
                        return (
                            is_folder,
                            entry["modified"],
                        )  # Ascending so reverse later
                    elif sort_by == "type":
                        return (is_folder, entry["extension"], entry["name"].lower())
                    else:  # name
                        return (is_folder, entry["name"].lower())

                entries.sort(key=sort_key)

                # For modified sort, reverse within each group (folders/files)
                # so most recent comes first
                if sort_by == "modified":
                    folders = [e for e in entries if e["type"] == "folder"]
                    files = [e for e in entries if e["type"] == "file"]
                    folders.sort(key=lambda e: e["modified"], reverse=True)
                    files.sort(key=lambda e: e["modified"], reverse=True)
                    entries = folders + files

                # Limit to 200 entries
                truncated = len(entries) > 200
                entries = entries[:200]

                # Compute parent path
                parent_path = (
                    str(dir_path.parent) if dir_path.parent != dir_path else None
                )

                return {
                    "status": "success",
                    "entries": entries,
                    "current_path": str(dir_path),
                    "parent_path": parent_path,
                    "total_files": total_files,
                    "total_folders": total_folders,
                    "total_entries": total_files + total_folders,
                    "entries_shown": len(entries),
                    "truncated": truncated,
                    "display_message": (
                        f"Listing {len(entries)} items in {dir_path.name or str(dir_path)} "
                        f"({total_folders} folders, {total_files} files)"
                    ),
                }

            except Exception as e:
                logger.error(f"Error browsing directory: {e}")
                return {
                    "status": "error",
                    "error": str(e),
                    "has_errors": True,
                    "operation": "browse_directory",
                }

        @tool(
            atomic=True,
            name="get_file_info",
            description="Get detailed information about a file including size, type, dates, and content preview. Use before deciding how to process a file.",
            parameters={
                "file_path": {
                    "type": "str",
                    "description": "Path to the file",
                    "required": True,
                },
            },
        )
        def get_file_info(file_path: str) -> Dict[str, Any]:
            """
            Get detailed metadata and preview for a file.

            Returns file size, type, dates, and a content preview for text files.
            For CSV files, also returns column names and row count.

            Args:
                file_path: Path to the file

            Returns:
                Dictionary with file metadata and optional preview
            """
            try:
                fp = Path(file_path)

                if not fp.exists():
                    return {
                        "status": "error",
                        "error": f"File not found: {file_path}",
                        "has_errors": True,
                        "operation": "get_file_info",
                    }

                if not fp.is_file():
                    return {
                        "status": "error",
                        "error": f"Path is not a file: {file_path}",
                        "has_errors": True,
                        "operation": "get_file_info",
                    }

                stat_info = fp.stat()
                size_bytes = stat_info.st_size
                created_dt = datetime.fromtimestamp(stat_info.st_ctime)
                modified_dt = datetime.fromtimestamp(stat_info.st_mtime)

                # Determine MIME type
                mime_type, _ = mimetypes.guess_type(str(fp))
                if mime_type is None:
                    mime_type = "application/octet-stream"

                result = {
                    "status": "success",
                    "file_name": fp.name,
                    "file_path": str(fp.resolve()),
                    "file_size_bytes": size_bytes,
                    "file_size": _human_readable_size(size_bytes),
                    "extension": fp.suffix.lower(),
                    "mime_type": mime_type,
                    "created": created_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "modified": modified_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "modified_ago": _relative_time(modified_dt),
                }

                # Determine if text file
                text_extensions = {
                    ".txt",
                    ".md",
                    ".py",
                    ".js",
                    ".ts",
                    ".java",
                    ".c",
                    ".cpp",
                    ".h",
                    ".json",
                    ".xml",
                    ".yaml",
                    ".yml",
                    ".csv",
                    ".tsv",
                    ".log",
                    ".ini",
                    ".conf",
                    ".sh",
                    ".bat",
                    ".html",
                    ".css",
                    ".sql",
                    ".toml",
                    ".cfg",
                    ".rst",
                    ".tex",
                }
                is_text = fp.suffix.lower() in text_extensions or (
                    mime_type and mime_type.startswith("text/")
                )
                result["is_text"] = is_text

                if is_text:
                    # Read content for preview
                    file_content = None
                    used_encoding = None
                    for encoding in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
                        try:
                            with open(fp, "r", encoding=encoding) as f:
                                file_content = f.read()
                            used_encoding = encoding
                            break
                        except (UnicodeDecodeError, UnicodeError):
                            continue

                    if file_content is not None:
                        lines = file_content.splitlines()
                        result["encoding"] = used_encoding
                        result["line_count"] = len(lines)
                        result["preview"] = "\n".join(lines[:20])
                        if len(lines) > 20:
                            result["preview_note"] = (
                                f"Showing first 20 of {len(lines)} lines"
                            )

                        # CSV-specific info
                        if fp.suffix.lower() in (".csv", ".tsv"):
                            try:
                                delimiter = "\t" if fp.suffix.lower() == ".tsv" else ","
                                try:
                                    dialect = csv.Sniffer().sniff(
                                        file_content[:4096], delimiters=",\t;|"
                                    )
                                    delimiter = dialect.delimiter
                                except csv.Error:
                                    pass
                                reader = csv.DictReader(
                                    file_content.splitlines(), delimiter=delimiter
                                )
                                result["csv_columns"] = reader.fieldnames or []
                                # Count rows (subtract header)
                                result["csv_row_count"] = max(0, len(lines) - 1)
                            except Exception as e:
                                logger.debug(f"Could not parse CSV structure: {e}")
                    else:
                        result["encoding"] = "unknown"
                        result["preview"] = "[Could not decode file content]"
                else:
                    result["encoding"] = "binary"
                    result["preview"] = (
                        f"[Binary file, {_human_readable_size(size_bytes)}]"
                    )

                return result

            except Exception as e:
                logger.error(f"Error getting file info: {e}")
                return {
                    "status": "error",
                    "error": str(e),
                    "has_errors": True,
                    "operation": "get_file_info",
                }

        @tool(
            atomic=True,
            name="analyze_data_file",
            description="Parse and analyze CSV, Excel, or other tabular data files. Computes statistics, identifies categories, and summarizes data. Perfect for analyzing bank statements, expense reports, and financial data.",
            parameters={
                "file_path": {
                    "type": "str",
                    "description": "Path to the data file (CSV, XLSX, XLS, TSV)",
                    "required": True,
                },
                "analysis_type": {
                    "type": "str",
                    "description": "Type of analysis: 'summary' (overview), 'spending' (categorize expenses), 'trends' (time-based patterns), 'full' (all analyses). Default: 'summary'",
                    "required": False,
                },
                "columns": {
                    "type": "str",
                    "description": "Comma-separated column names to focus analysis on. If not specified, all columns are analyzed.",
                    "required": False,
                },
            },
        )
        def analyze_data_file(
            file_path: str, analysis_type: str = "summary", columns: str = None
        ) -> Dict[str, Any]:
            """
            Parse and analyze tabular data files with multiple analysis modes.

            Supports CSV, TSV, XLSX, and XLS files. Provides summary statistics,
            spending categorization, and trend analysis for financial data.

            Args:
                file_path: Path to the data file
                analysis_type: 'summary', 'spending', 'trends', or 'full'
                columns: Comma-separated column names to focus on (optional)

            Returns:
                Dictionary with analysis results based on the requested type
            """
            try:
                fp = Path(file_path)

                if not fp.exists():
                    return {
                        "status": "error",
                        "error": f"File not found: {file_path}",
                        "has_errors": True,
                        "operation": "analyze_data_file",
                    }

                supported_extensions = {".csv", ".tsv", ".xlsx", ".xls"}
                if fp.suffix.lower() not in supported_extensions:
                    return {
                        "status": "error",
                        "error": (
                            f"Unsupported file type: {fp.suffix}. "
                            f"Supported types: {', '.join(sorted(supported_extensions))}"
                        ),
                        "has_errors": True,
                        "operation": "analyze_data_file",
                    }

                # Read the file
                rows, all_columns, read_error = _read_tabular_file(file_path)

                if read_error:
                    return {
                        "status": "error",
                        "error": read_error,
                        "has_errors": True,
                        "operation": "analyze_data_file",
                    }

                if not rows:
                    return {
                        "status": "success",
                        "file": fp.name,
                        "row_count": 0,
                        "columns": all_columns,
                        "message": "File is empty or contains only headers.",
                    }

                # Filter columns if specified
                focus_columns = all_columns
                if columns:
                    requested = [c.strip() for c in columns.split(",")]
                    focus_columns = [c for c in requested if c in all_columns]
                    if not focus_columns:
                        return {
                            "status": "error",
                            "error": (
                                f"None of the requested columns found. "
                                f"Available columns: {', '.join(all_columns)}"
                            ),
                            "has_errors": True,
                            "operation": "analyze_data_file",
                        }

                result = {
                    "status": "success",
                    "file": fp.name,
                    "file_path": str(fp.resolve()),
                    "row_count": len(rows),
                    "columns": all_columns,
                    "column_count": len(all_columns),
                }

                # Infer column types
                column_types = {}
                for col in all_columns:
                    col_values = [row.get(col) for row in rows]
                    column_types[col] = _infer_column_type(col_values)
                result["column_types"] = column_types

                # --- Summary analysis ---
                if analysis_type in ("summary", "full"):
                    summary = {}
                    for col in focus_columns:
                        col_values = [row.get(col) for row in rows]
                        col_type = column_types.get(col, "text")

                        col_summary: Dict[str, Any] = {"type": col_type}

                        if col_type == "numeric":
                            numeric_vals = []
                            for v in col_values:
                                parsed = _parse_numeric(v)
                                if v is not None and str(v).strip() != "":
                                    numeric_vals.append(parsed)
                            if numeric_vals:
                                numeric_vals_sorted = sorted(numeric_vals)
                                col_summary["min"] = round(min(numeric_vals), 2)
                                col_summary["max"] = round(max(numeric_vals), 2)
                                col_summary["sum"] = round(sum(numeric_vals), 2)
                                col_summary["mean"] = round(
                                    sum(numeric_vals) / len(numeric_vals), 2
                                )
                                mid = len(numeric_vals_sorted) // 2
                                if (
                                    len(numeric_vals_sorted) % 2 == 0
                                    and len(numeric_vals_sorted) > 1
                                ):
                                    col_summary["median"] = round(
                                        (
                                            numeric_vals_sorted[mid - 1]
                                            + numeric_vals_sorted[mid]
                                        )
                                        / 2,
                                        2,
                                    )
                                else:
                                    col_summary["median"] = round(
                                        numeric_vals_sorted[mid], 2
                                    )
                                col_summary["count"] = len(numeric_vals)
                        else:
                            # Text or date column
                            non_empty = [
                                str(v).strip()
                                for v in col_values
                                if v is not None and str(v).strip()
                            ]
                            col_summary["unique_values"] = len(set(non_empty))
                            counter = Counter(non_empty)
                            col_summary["top_values"] = [
                                {"value": val, "count": cnt}
                                for val, cnt in counter.most_common(10)
                            ]
                            col_summary["total_non_empty"] = len(non_empty)

                        summary[col] = col_summary

                    result["summary"] = summary

                    # Sample rows (first 5)
                    result["sample_rows"] = rows[:5]

                # --- Spending analysis ---
                if analysis_type in ("spending", "full"):
                    spending = {}

                    # Auto-detect amount columns
                    amount_keywords = {
                        "amount",
                        "debit",
                        "credit",
                        "total",
                        "balance",
                        "price",
                        "cost",
                        "payment",
                        "charge",
                        "withdrawal",
                        "deposit",
                        "net",
                        "gross",
                        "fee",
                    }
                    date_keywords = {
                        "date",
                        "time",
                        "posted",
                        "transaction",
                        "effective",
                        "settlement",
                        "booking",
                    }
                    desc_keywords = {
                        "description",
                        "desc",
                        "memo",
                        "merchant",
                        "payee",
                        "category",
                        "name",
                        "vendor",
                        "details",
                        "narrative",
                        "reference",
                        "particulars",
                    }

                    def _find_columns(keywords: set) -> List[str]:
                        found = []
                        for col in all_columns:
                            col_lower = col.lower()
                            for kw in keywords:
                                if kw in col_lower:
                                    found.append(col)
                                    break
                        return found

                    amount_cols = _find_columns(amount_keywords)
                    date_cols = _find_columns(date_keywords)
                    desc_cols = _find_columns(desc_keywords)

                    # Also consider numeric columns as potential amount columns
                    if not amount_cols:
                        amount_cols = [
                            col
                            for col in all_columns
                            if column_types.get(col) == "numeric"
                        ]

                    spending["detected_amount_columns"] = amount_cols
                    spending["detected_date_columns"] = date_cols
                    spending["detected_description_columns"] = desc_cols

                    if amount_cols:
                        # Use the first amount column for primary analysis
                        primary_amount_col = amount_cols[0]
                        amounts = []
                        for row in rows:
                            val = row.get(primary_amount_col)
                            if val is not None and str(val).strip():
                                amounts.append(_parse_numeric(val))

                        debits = [a for a in amounts if a < 0]
                        credits = [a for a in amounts if a > 0]

                        spending["primary_amount_column"] = primary_amount_col
                        spending["total_transactions"] = len(amounts)
                        spending["total_spending"] = (
                            round(abs(sum(debits)), 2) if debits else 0
                        )
                        spending["total_income"] = (
                            round(sum(credits), 2) if credits else 0
                        )
                        spending["net"] = round(sum(amounts), 2)
                        spending["avg_transaction"] = (
                            round(sum(amounts) / len(amounts), 2) if amounts else 0
                        )
                        spending["largest_expense"] = (
                            round(min(debits), 2) if debits else 0
                        )
                        spending["largest_income"] = (
                            round(max(credits), 2) if credits else 0
                        )

                        # Check for separate debit/credit columns
                        debit_cols = [
                            c
                            for c in amount_cols
                            if "debit" in c.lower()
                            or "withdrawal" in c.lower()
                            or "charge" in c.lower()
                        ]
                        credit_cols = [
                            c
                            for c in amount_cols
                            if "credit" in c.lower() or "deposit" in c.lower()
                        ]

                        if debit_cols and credit_cols:
                            debit_col = debit_cols[0]
                            credit_col = credit_cols[0]
                            total_debits = 0.0
                            total_credits = 0.0
                            for row in rows:
                                dv = row.get(debit_col)
                                cv = row.get(credit_col)
                                if dv is not None and str(dv).strip():
                                    total_debits += abs(_parse_numeric(dv))
                                if cv is not None and str(cv).strip():
                                    total_credits += abs(_parse_numeric(cv))
                            spending["separate_columns_detected"] = True
                            spending["debit_column"] = debit_col
                            spending["credit_column"] = credit_col
                            spending["total_debits"] = round(total_debits, 2)
                            spending["total_credits"] = round(total_credits, 2)

                        # Group by category/merchant
                        if desc_cols:
                            primary_desc_col = desc_cols[0]
                            category_spending: Dict[str, float] = {}
                            for row in rows:
                                desc = str(row.get(primary_desc_col, "")).strip()
                                if not desc:
                                    desc = "Unknown"
                                amount_val = _parse_numeric(row.get(primary_amount_col))
                                if amount_val < 0:
                                    # Accumulate spending (as positive values)
                                    category_spending[desc] = category_spending.get(
                                        desc, 0
                                    ) + abs(amount_val)

                            # Sort by total spending, top 20
                            sorted_categories = sorted(
                                category_spending.items(),
                                key=lambda x: x[1],
                                reverse=True,
                            )[:20]
                            spending["spending_by_category"] = [
                                {"category": cat, "total": round(total, 2)}
                                for cat, total in sorted_categories
                            ]
                            spending["description_column"] = primary_desc_col

                        # Monthly breakdown if dates detected
                        if date_cols:
                            primary_date_col = date_cols[0]
                            monthly_totals: Dict[str, float] = {}
                            monthly_spending: Dict[str, float] = {}
                            monthly_income: Dict[str, float] = {}

                            from dateutil import parser as date_parser

                            for row in rows:
                                date_val = row.get(primary_date_col)
                                amount_val = _parse_numeric(row.get(primary_amount_col))
                                if date_val is None or str(date_val).strip() == "":
                                    continue

                                try:
                                    if isinstance(date_val, datetime):
                                        dt = date_val
                                    else:
                                        dt = date_parser.parse(
                                            str(date_val), fuzzy=True
                                        )
                                    month_key = dt.strftime("%Y-%m")
                                    monthly_totals[month_key] = (
                                        monthly_totals.get(month_key, 0) + amount_val
                                    )
                                    if amount_val < 0:
                                        monthly_spending[month_key] = (
                                            monthly_spending.get(month_key, 0)
                                            + abs(amount_val)
                                        )
                                    else:
                                        monthly_income[month_key] = (
                                            monthly_income.get(month_key, 0)
                                            + amount_val
                                        )
                                except (ValueError, TypeError, OverflowError):
                                    continue

                            if monthly_totals:
                                sorted_months = sorted(monthly_totals.keys())
                                spending["monthly_breakdown"] = [
                                    {
                                        "month": m,
                                        "net": round(monthly_totals.get(m, 0), 2),
                                        "spending": round(
                                            monthly_spending.get(m, 0), 2
                                        ),
                                        "income": round(monthly_income.get(m, 0), 2),
                                    }
                                    for m in sorted_months
                                ]
                                spending["date_column"] = primary_date_col
                    else:
                        spending["message"] = (
                            "Could not auto-detect amount columns. "
                            "Try specifying columns manually with the 'columns' parameter."
                        )

                    result["spending_analysis"] = spending

                # --- Trends analysis ---
                if analysis_type in ("trends", "full"):
                    trends: Dict[str, Any] = {}

                    # Find date and amount columns
                    date_keywords_t = {
                        "date",
                        "time",
                        "posted",
                        "transaction",
                        "effective",
                    }
                    amount_keywords_t = {
                        "amount",
                        "debit",
                        "credit",
                        "total",
                        "price",
                        "cost",
                        "payment",
                    }

                    def _find_cols(keywords: set) -> List[str]:
                        found = []
                        for col in all_columns:
                            cl = col.lower()
                            for kw in keywords:
                                if kw in cl:
                                    found.append(col)
                                    break
                        return found

                    trend_date_cols = _find_cols(date_keywords_t)
                    trend_amount_cols = _find_cols(amount_keywords_t)

                    if not trend_amount_cols:
                        trend_amount_cols = [
                            col
                            for col in all_columns
                            if column_types.get(col) == "numeric"
                        ]

                    if trend_date_cols and trend_amount_cols:
                        date_col = trend_date_cols[0]
                        amount_col = trend_amount_cols[0]

                        from dateutil import parser as date_parser

                        monthly_data: Dict[str, List[float]] = {}
                        weekly_data: Dict[str, List[float]] = {}

                        for row in rows:
                            date_val = row.get(date_col)
                            amount_val = _parse_numeric(row.get(amount_col))

                            if date_val is None or str(date_val).strip() == "":
                                continue

                            try:
                                if isinstance(date_val, datetime):
                                    dt = date_val
                                else:
                                    dt = date_parser.parse(str(date_val), fuzzy=True)
                                m_key = dt.strftime("%Y-%m")
                                w_key = dt.strftime("%Y-W%W")

                                monthly_data.setdefault(m_key, []).append(amount_val)
                                weekly_data.setdefault(w_key, []).append(amount_val)
                            except (ValueError, TypeError, OverflowError):
                                continue

                        if monthly_data:
                            monthly_summary = []
                            for month in sorted(monthly_data.keys()):
                                vals = monthly_data[month]
                                monthly_summary.append(
                                    {
                                        "period": month,
                                        "total": round(sum(vals), 2),
                                        "count": len(vals),
                                        "average": (
                                            round(sum(vals) / len(vals), 2)
                                            if vals
                                            else 0
                                        ),
                                    }
                                )
                            trends["monthly"] = monthly_summary

                            # Identify highest/lowest periods
                            if len(monthly_summary) > 1:
                                by_total = sorted(
                                    monthly_summary, key=lambda x: x["total"]
                                )
                                trends["lowest_period"] = by_total[0]
                                trends["highest_period"] = by_total[-1]

                        if weekly_data:
                            weekly_summary = []
                            for week in sorted(weekly_data.keys()):
                                vals = weekly_data[week]
                                weekly_summary.append(
                                    {
                                        "period": week,
                                        "total": round(sum(vals), 2),
                                        "count": len(vals),
                                    }
                                )
                            # Limit weekly to most recent 20 weeks
                            trends["weekly"] = weekly_summary[-20:]

                        trends["date_column"] = date_col
                        trends["amount_column"] = amount_col
                    else:
                        trends["message"] = (
                            "Could not detect both date and amount columns for trend analysis. "
                            f"Date columns found: {trend_date_cols}, "
                            f"Amount columns found: {trend_amount_cols}"
                        )

                    result["trends_analysis"] = trends

                # Limit output size for LLM context
                # Truncate sample_rows if too many columns
                if "sample_rows" in result and len(all_columns) > 20:
                    for i, row in enumerate(result["sample_rows"]):
                        truncated_row = {k: row[k] for k in list(row.keys())[:20]}
                        truncated_row["_note"] = (
                            f"Showing 20 of {len(all_columns)} columns"
                        )
                        result["sample_rows"][i] = truncated_row

                return result

            except ImportError as e:
                logger.error(f"Missing dependency for data analysis: {e}")
                return {
                    "status": "error",
                    "error": f"Missing dependency: {e}. Try: pip install python-dateutil openpyxl",
                    "has_errors": True,
                    "operation": "analyze_data_file",
                }
            except Exception as e:
                logger.error(f"Error analyzing data file: {e}")
                import traceback

                logger.error(traceback.format_exc())
                return {
                    "status": "error",
                    "error": str(e),
                    "has_errors": True,
                    "operation": "analyze_data_file",
                }

        @tool(
            atomic=True,
            name="list_recent_files",
            description="Find recently modified files in common locations (Documents, Downloads, Desktop). Useful for finding files the user recently worked with.",
            parameters={
                "location": {
                    "type": "str",
                    "description": "Where to search: 'all', 'documents', 'downloads', 'desktop' (default: 'all')",
                    "required": False,
                },
                "file_types": {
                    "type": "str",
                    "description": "Comma-separated extensions to filter (e.g., 'csv,xlsx,pdf'). Default: all common types",
                    "required": False,
                },
                "max_results": {
                    "type": "int",
                    "description": "Maximum number of results (default: 20)",
                    "required": False,
                },
                "days": {
                    "type": "int",
                    "description": "Only show files modified within this many days (default: 30)",
                    "required": False,
                },
            },
        )
        def list_recent_files(
            location: str = "all",
            file_types: str = None,
            max_results: int = 20,
            days: int = 30,
        ) -> Dict[str, Any]:
            """
            Find recently modified files in common user directories.

            Scans Documents, Downloads, Desktop, and other common locations
            for files modified within the specified time range.

            Args:
                location: 'all', 'documents', 'downloads', or 'desktop'
                file_types: Comma-separated extensions to filter
                max_results: Maximum number of results to return
                days: Only show files modified within this many days

            Returns:
                Dictionary with list of recent files sorted by modification time
            """
            try:
                home = Path.home()

                # Determine directories to scan
                location_map = {
                    "documents": [home / "Documents"],
                    "downloads": [home / "Downloads"],
                    "desktop": [home / "Desktop"],
                    "all": [
                        home / "Documents",
                        home / "Downloads",
                        home / "Desktop",
                        home / "OneDrive",
                    ],
                }

                dirs_to_scan = location_map.get(location.lower(), location_map["all"])

                # Filter extensions
                if file_types:
                    allowed_extensions = {
                        f".{ext.strip().lower()}" for ext in file_types.split(",")
                    }
                else:
                    allowed_extensions = {
                        ".pdf",
                        ".doc",
                        ".docx",
                        ".txt",
                        ".md",
                        ".csv",
                        ".json",
                        ".xlsx",
                        ".xls",
                        ".pptx",
                        ".ppt",
                        ".odt",
                        ".rtf",
                        ".html",
                        ".xml",
                        ".yaml",
                        ".yml",
                        ".py",
                        ".js",
                        ".ts",
                        ".jpg",
                        ".jpeg",
                        ".png",
                        ".gif",
                        ".bmp",
                        ".svg",
                        ".zip",
                        ".rar",
                        ".7z",
                        ".mp3",
                        ".mp4",
                        ".wav",
                    }

                cutoff = datetime.now() - timedelta(days=days)
                recent_files = []

                for scan_dir in dirs_to_scan:
                    if not scan_dir.exists():
                        continue

                    try:
                        for item in scan_dir.rglob("*"):
                            if not item.is_file():
                                continue

                            # Skip hidden files
                            if item.name.startswith("."):
                                continue

                            # Filter by extension
                            if item.suffix.lower() not in allowed_extensions:
                                continue

                            try:
                                stat_info = item.stat()
                                modified_dt = datetime.fromtimestamp(stat_info.st_mtime)

                                # Check if within date range
                                if modified_dt < cutoff:
                                    continue

                                recent_files.append(
                                    {
                                        "file_name": item.name,
                                        "file_path": str(item),
                                        "size_bytes": stat_info.st_size,
                                        "size": _human_readable_size(stat_info.st_size),
                                        "modified": modified_dt.strftime(
                                            "%Y-%m-%d %H:%M"
                                        ),
                                        "modified_ago": _relative_time(modified_dt),
                                        "extension": item.suffix.lower(),
                                        "directory": str(item.parent),
                                    }
                                )
                            except (PermissionError, OSError):
                                continue

                    except (PermissionError, OSError) as e:
                        logger.debug(f"Could not scan {scan_dir}: {e}")
                        continue

                # Sort by modification time (most recent first)
                recent_files.sort(key=lambda x: x["modified"], reverse=True)

                total_found = len(recent_files)
                locations_searched = [d.name for d in dirs_to_scan if d.exists()]

                # Return all files — first batch shown directly, rest in a
                # collapsible section so the LLM doesn't truncate them.
                shown = recent_files[:max_results]
                extra = recent_files[max_results:]

                # Build display_message with collapsible extra files
                loc_str = ", ".join(locations_searched)
                display_parts = [
                    f"Found {total_found} recent file(s) in {loc_str} (last {days} days)"
                ]
                for f in shown:
                    display_parts.append(f"  {f['file_name']} ({f['directory']})")
                if extra:
                    display_parts.append(
                        f"\n<details><summary>+{len(extra)} more files</summary>\n"
                    )
                    for f in extra:
                        display_parts.append(f"  {f['file_name']} ({f['directory']})")
                    display_parts.append("</details>")

                return {
                    "status": "success",
                    "files": recent_files[:max_results],
                    "all_files": recent_files,
                    "count": len(shown),
                    "total_found": total_found,
                    "locations_searched": locations_searched,
                    "days_range": days,
                    "display_message": "\n".join(display_parts),
                }

            except Exception as e:
                logger.error(f"Error listing recent files: {e}")
                return {
                    "status": "error",
                    "error": str(e),
                    "has_errors": True,
                    "operation": "list_recent_files",
                }
