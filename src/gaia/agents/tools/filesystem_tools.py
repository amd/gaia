# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# pylint: disable=protected-access

"""
File System Navigation and Management Tools.

Provides file system browsing, search, tree visualization, file info,
bookmarks, and enhanced file reading for GAIA agents.
"""

import datetime
import json
import logging
import mimetypes
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _format_size(size_bytes: int) -> str:
    """Format bytes to human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def _format_date(timestamp: float) -> str:
    """Format timestamp to readable date string."""
    dt = datetime.datetime.fromtimestamp(timestamp)
    return dt.strftime("%Y-%m-%d %H:%M")


class FileSystemToolsMixin:
    """File system navigation, search, and management tools.

    Provides browse, tree, search, file info, bookmarks, and read capabilities.
    All path parameters are validated through PathValidator before access.

    Available to: ChatAgent, CodeAgent, or any agent needing file system access.

    Tool registration follows GAIA pattern: register_filesystem_tools() method
    with @tool decorator using docstrings for descriptions.
    """

    _fs_index = None  # Optional FileSystemIndexService instance
    _path_validator = None  # Optional PathValidator instance
    _bookmarks: dict = {}  # In-memory bookmarks (persisted in Phase 2 via index)

    def _validate_path(self, path: str) -> Path:
        """Validate and resolve a path. Raises ValueError if blocked."""
        resolved = Path(path).expanduser().resolve()
        if self._path_validator and not self._path_validator.is_path_allowed(
            str(resolved)
        ):
            raise ValueError(f"Access denied: {resolved}")
        return resolved

    def _get_default_excludes(self) -> set:
        """Get platform-specific default directory exclusion patterns."""
        import sys

        excludes = {
            "__pycache__",
            ".git",
            ".svn",
            ".hg",
            "node_modules",
            ".venv",
            "venv",
            ".env",
            ".tox",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            "__MACOSX",
        }
        if sys.platform == "win32":
            excludes.update(
                {
                    "$Recycle.Bin",
                    "$RECYCLE.BIN",
                    "System Volume Information",
                    "Recovery",
                    "PerfLogs",
                }
            )
        else:
            excludes.update(
                {
                    "proc",
                    "sys",
                    "dev",
                    "run",
                    "snap",
                }
            )
        return excludes

    def register_filesystem_tools(self) -> None:
        """Register all file system navigation and management tools."""
        from gaia.agents.base.tools import tool

        mixin = self  # Capture self for nested functions

        @tool(atomic=True)
        def browse_directory(
            path: str = "~",
            show_hidden: bool = False,
            sort_by: str = "name",
            filter_type: str = None,
            max_items: int = 50,
        ) -> str:
            """Browse a directory and list its contents with metadata.

            Returns files and subdirectories with size, modification date, and type info.
            Use this to explore what's inside a folder. Default path is user's home directory.

            Args:
                path: Directory to browse (default: home directory ~)
                show_hidden: Include hidden files/directories (default: False)
                sort_by: Sort order - name, size, modified, or type (default: name)
                filter_type: Filter by extension without dot, e.g. 'pdf', 'py' (default: all)
                max_items: Maximum items to return (default: 50)
            """
            try:
                resolved = mixin._validate_path(path)

                if not resolved.is_dir():
                    return f"Error: '{resolved}' is not a directory."

                items = []
                total_size = 0

                try:
                    entries = list(os.scandir(str(resolved)))
                except PermissionError:
                    return f"Error: Permission denied accessing '{resolved}'."
                except OSError as e:
                    return f"Error accessing '{resolved}': {e}"

                for entry in entries:
                    try:
                        name = entry.name

                        # Skip hidden files unless requested
                        if not show_hidden and name.startswith("."):
                            continue

                        # Filter by type
                        if filter_type and entry.is_file():
                            ext = Path(name).suffix.lstrip(".").lower()
                            if ext != filter_type.lower():
                                continue

                        st = entry.stat(follow_symlinks=False)
                        is_dir = entry.is_dir(follow_symlinks=False)

                        if is_dir:
                            # For directories, try to get total size (quick estimate)
                            size = 0
                            try:
                                size = sum(
                                    f.stat().st_size
                                    for f in os.scandir(entry.path)
                                    if f.is_file(follow_symlinks=False)
                                )
                            except (PermissionError, OSError):
                                size = 0
                        else:
                            size = st.st_size

                        total_size += size

                        items.append(
                            {
                                "name": name,
                                "is_dir": is_dir,
                                "size": size,
                                "modified": st.st_mtime,
                                "extension": (
                                    Path(name).suffix.lstrip(".").lower()
                                    if not is_dir
                                    else ""
                                ),
                            }
                        )
                    except (PermissionError, OSError):
                        continue

                # Sort
                if sort_by == "size":
                    items.sort(key=lambda x: x["size"], reverse=True)
                elif sort_by == "modified":
                    items.sort(key=lambda x: x["modified"], reverse=True)
                elif sort_by == "type":
                    items.sort(
                        key=lambda x: (not x["is_dir"], x["extension"], x["name"])
                    )
                else:  # name (default)
                    items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))

                # Truncate
                items = items[:max_items]

                # Format output
                lines = [
                    f"{resolved} ({len(entries)} items, {_format_size(total_size)} total)\n"
                ]
                lines.append(f"  {'Type':<6} {'Name':<35} {'Size':<12} {'Modified'}")
                lines.append(f"  {'----':<6} {'----':<35} {'----':<12} {'--------'}")

                for item in items:
                    type_str = "[DIR]" if item["is_dir"] else "[FIL]"
                    name_str = item["name"] + ("/" if item["is_dir"] else "")
                    size_str = _format_size(item["size"])
                    mod_str = _format_date(item["modified"])
                    lines.append(
                        f"  {type_str:<6} {name_str:<35} {size_str:<12} {mod_str}"
                    )

                if len(entries) > max_items:
                    lines.append(f"\n  ... and {len(entries) - max_items} more items")

                return "\n".join(lines)

            except ValueError as e:
                return str(e)
            except Exception as e:
                logger.error(f"Error browsing directory: {e}")
                return f"Error browsing directory: {e}"

        @tool(atomic=True)
        def tree(
            path: str = ".",
            max_depth: int = 3,
            show_sizes: bool = False,
            include_pattern: str = None,
            exclude_pattern: str = None,
            dirs_only: bool = False,
        ) -> str:
            """Show a tree visualization of a directory structure.

            Useful for understanding project layouts and folder hierarchies.
            Shows nested directories and files with optional size info.

            Args:
                path: Root directory for tree (default: current directory)
                max_depth: Maximum depth to display (default: 3)
                show_sizes: Show file sizes next to names (default: False)
                include_pattern: Only show files matching this glob pattern, e.g. '*.py'
                exclude_pattern: Hide files/dirs matching this pattern, e.g. 'node_modules'
                dirs_only: Only show directories, no files (default: False)
            """
            try:
                import fnmatch

                resolved = mixin._validate_path(path)

                if not resolved.is_dir():
                    return f"Error: '{resolved}' is not a directory."

                default_excludes = mixin._get_default_excludes()
                lines = [str(resolved)]
                dir_count = 0
                file_count = 0
                total_size = 0

                def _build_tree(current: Path, prefix: str, depth: int):
                    nonlocal dir_count, file_count, total_size

                    if depth > max_depth:
                        return

                    try:
                        entries = sorted(
                            os.scandir(str(current)),
                            key=lambda e: (not e.is_dir(), e.name.lower()),
                        )
                    except (PermissionError, OSError):
                        return

                    # Filter entries
                    filtered = []
                    for entry in entries:
                        name = entry.name

                        # Skip hidden
                        if name.startswith("."):
                            continue

                        # Default excludes
                        if name in default_excludes:
                            continue

                        # User exclude pattern
                        if exclude_pattern and fnmatch.fnmatch(name, exclude_pattern):
                            continue

                        is_dir = entry.is_dir(follow_symlinks=False)

                        # Include pattern (only applies to files)
                        if include_pattern and not is_dir:
                            if not fnmatch.fnmatch(name, include_pattern):
                                continue

                        # dirs_only filter
                        if dirs_only and not is_dir:
                            continue

                        filtered.append(entry)

                    for i, entry in enumerate(filtered):
                        is_last = i == len(filtered) - 1
                        connector = "+-- " if is_last else "+-- "
                        extension = "    " if is_last else "|   "

                        is_dir = entry.is_dir(follow_symlinks=False)

                        if is_dir:
                            dir_count += 1
                            suffix = "/"
                            size_str = ""
                        else:
                            file_count += 1
                            try:
                                size = entry.stat(follow_symlinks=False).st_size
                                total_size += size
                                size_str = (
                                    f" ({_format_size(size)})" if show_sizes else ""
                                )
                            except (PermissionError, OSError):
                                size_str = ""
                            suffix = ""

                        lines.append(
                            f"{prefix}{connector}{entry.name}{suffix}{size_str}"
                        )

                        if is_dir:
                            _build_tree(Path(entry.path), prefix + extension, depth + 1)

                _build_tree(resolved, "", 1)

                # Summary
                summary_parts = []
                if dir_count > 0:
                    summary_parts.append(
                        f"{dir_count} director{'ies' if dir_count != 1 else 'y'}"
                    )
                if file_count > 0:
                    summary_parts.append(
                        f"{file_count} file{'s' if file_count != 1 else ''}"
                    )
                if show_sizes and total_size > 0:
                    summary_parts.append(f"{_format_size(total_size)} total")

                if summary_parts:
                    lines.append(f"\n{', '.join(summary_parts)}")

                return "\n".join(lines)

            except ValueError as e:
                return str(e)
            except Exception as e:
                logger.error(f"Error generating tree: {e}")
                return f"Error generating tree: {e}"

        @tool(atomic=True)
        def file_info(path: str) -> str:
            """Get comprehensive information about a file or directory.

            Returns size, dates, type, MIME type, encoding, and format-specific
            metadata (line count for text, dimensions for images, page count for PDFs).
            For directories: item count, total size, file type breakdown.
            """
            try:
                resolved = mixin._validate_path(path)

                if not resolved.exists():
                    return f"Error: '{resolved}' does not exist."

                st = resolved.stat()
                lines = []

                if resolved.is_dir():
                    # Directory info
                    lines.append(f"Directory: {resolved}")
                    lines.append(f"  Modified:  {_format_date(st.st_mtime)}")

                    # Count items and sizes
                    file_count = 0
                    dir_count = 0
                    total_size = 0
                    ext_counts = {}

                    try:
                        for entry in os.scandir(str(resolved)):
                            try:
                                if entry.is_dir(follow_symlinks=False):
                                    dir_count += 1
                                elif entry.is_file(follow_symlinks=False):
                                    file_count += 1
                                    fsize = entry.stat(follow_symlinks=False).st_size
                                    total_size += fsize
                                    ext = Path(entry.name).suffix.lower()
                                    ext_counts[ext] = ext_counts.get(ext, 0) + 1
                            except (PermissionError, OSError):
                                continue
                    except (PermissionError, OSError):
                        lines.append("  Contents: Permission denied")
                        return "\n".join(lines)

                    lines.append(
                        f"  Contents:  {file_count} files, {dir_count} subdirectories"
                    )
                    lines.append(
                        f"  Total Size (direct children): {_format_size(total_size)}"
                    )

                    if ext_counts:
                        sorted_exts = sorted(
                            ext_counts.items(),
                            key=lambda x: x[1],
                            reverse=True,
                        )[:10]
                        ext_str = ", ".join(
                            f"{ext or '(none)'}: {cnt}" for ext, cnt in sorted_exts
                        )
                        lines.append(f"  File Types: {ext_str}")

                else:
                    # File info
                    lines.append(f"File: {resolved}")
                    lines.append(f"  Name:      {resolved.name}")
                    lines.append(f"  Size:      {_format_size(st.st_size)}")
                    lines.append(f"  Modified:  {_format_date(st.st_mtime)}")
                    lines.append(f"  Created:   {_format_date(st.st_ctime)}")

                    # MIME type
                    mime, encoding = mimetypes.guess_type(str(resolved))
                    lines.append(f"  MIME Type: {mime or 'unknown'}")
                    if encoding:
                        lines.append(f"  Encoding:  {encoding}")

                    # Extension
                    ext = resolved.suffix.lower()
                    lines.append(f"  Extension: {ext or '(none)'}")

                    # Format-specific metadata
                    if (
                        mime
                        and mime.startswith("text/")
                        or ext
                        in {
                            ".py",
                            ".js",
                            ".ts",
                            ".md",
                            ".txt",
                            ".csv",
                            ".json",
                            ".xml",
                            ".yaml",
                            ".yml",
                            ".toml",
                            ".ini",
                            ".cfg",
                            ".html",
                            ".css",
                        }
                    ):
                        try:
                            with open(
                                resolved,
                                "r",
                                encoding="utf-8",
                                errors="ignore",
                            ) as f:
                                content = f.read()
                            line_count = content.count("\n") + (
                                1 if content and not content.endswith("\n") else 0
                            )
                            lines.append(f"  Lines:     {line_count}")
                            # Character count
                            lines.append(f"  Chars:     {len(content)}")
                        except Exception:
                            pass

                    elif ext == ".pdf":
                        try:
                            import PyPDF2

                            with open(resolved, "rb") as f:
                                reader = PyPDF2.PdfReader(f)
                                lines.append(f"  Pages:     {len(reader.pages)}")
                                if reader.metadata:
                                    if reader.metadata.title:
                                        lines.append(
                                            f"  Title:     {reader.metadata.title}"
                                        )
                                    if reader.metadata.author:
                                        lines.append(
                                            f"  Author:    {reader.metadata.author}"
                                        )
                        except ImportError:
                            lines.append("  Pages:     (install PyPDF2 for PDF info)")
                        except Exception:
                            pass

                    elif ext in {
                        ".jpg",
                        ".jpeg",
                        ".png",
                        ".gif",
                        ".bmp",
                        ".webp",
                        ".tiff",
                    }:
                        try:
                            from PIL import Image

                            with Image.open(resolved) as img:
                                lines.append(f"  Dimensions: {img.width}x{img.height}")
                                lines.append(f"  Mode:      {img.mode}")
                        except ImportError:
                            lines.append(
                                "  Dimensions: (install Pillow for image info)"
                            )
                        except Exception:
                            pass

                return "\n".join(lines)

            except ValueError as e:
                return str(e)
            except Exception as e:
                logger.error(f"Error getting file info: {e}")
                return f"Error getting file info: {e}"

        @tool(atomic=True)
        def find_files(
            query: str,
            search_type: str = "auto",
            scope: str = "smart",
            file_types: str = None,
            size_range: str = None,
            date_range: str = None,
            max_results: int = 25,
            sort_by: str = "relevance",
        ) -> str:
            """Search for files by name, content, or metadata.

            This is the primary file search tool. When the file system index is available,
            searches the index first (instant). Falls back to filesystem glob when index
            is unavailable.

            Search types:
            - auto: intelligently picks the best strategy based on query
            - name: search by file/directory name pattern (glob)
            - content: search inside file contents (grep-like)
            - metadata: filter by size, date, type only

            Scope 'smart' searches: current directory first, then home common locations,
            then indexed directories. Use 'everywhere' for full drive search (slow).

            Args:
                query: Search query - file name, pattern (e.g. '*.pdf'), or content text
                search_type: auto, name, content, or metadata (default: auto)
                scope: smart, home, cwd, everywhere, or a specific path (default: smart)
                file_types: Comma-separated extensions to filter, e.g. 'pdf,docx,txt'
                size_range: Size filter, e.g. '>10MB', '<1KB', '1MB-100MB'
                date_range: Date filter, e.g. 'today', 'this-week', '2026-01', '>2026-01-01'
                max_results: Maximum results to return (default: 25)
                sort_by: Sort order - relevance, name, size, modified (default: relevance)
            """
            try:
                results = []

                # Parse file type filters
                type_filters = None
                if file_types:
                    type_filters = {
                        f".{t.strip().lower().lstrip('.')}"
                        for t in file_types.split(",")
                    }

                # Parse size range
                min_size, max_size = _parse_size_range(size_range)

                # Parse date range
                min_date, max_date = _parse_date_range(date_range)

                # Determine search type
                effective_type = search_type
                if effective_type == "auto":
                    if "*" in query or "?" in query:
                        effective_type = "name"
                    elif size_range or date_range:
                        effective_type = "metadata"
                    elif len(query.split()) > 3 or any(
                        c in query
                        for c in [
                            "=",
                            "(",
                            ")",
                            "def ",
                            "class ",
                            "import ",
                        ]
                    ):
                        effective_type = "content"
                    else:
                        effective_type = "name"

                # Try index first if available
                if mixin._fs_index and effective_type in (
                    "name",
                    "auto",
                    "metadata",
                ):
                    try:
                        index_results = mixin._fs_index.query_files(
                            name=query if effective_type != "metadata" else None,
                            extension=(
                                list(type_filters)[0].lstrip(".")
                                if type_filters and len(type_filters) == 1
                                else None
                            ),
                            min_size=min_size,
                            max_size=max_size,
                            modified_after=min_date,
                            modified_before=max_date,
                            limit=max_results,
                        )
                        if index_results:
                            lines = [
                                f"Found {len(index_results)} result(s) from index:\n"
                            ]
                            for i, r in enumerate(index_results, 1):
                                size_str = _format_size(r.get("size", 0))
                                mod_str = r.get("modified_at", "")
                                lines.append(
                                    f"  {i}. {r['path']} ({size_str}, {mod_str})"
                                )
                            return "\n".join(lines)
                    except Exception as e:
                        logger.debug(
                            f"Index search failed, falling back to filesystem: {e}"
                        )

                # Filesystem search
                # Determine search roots based on scope
                search_roots = _get_search_roots(scope)

                query_lower = query.lower()
                is_glob = "*" in query or "?" in query

                for root_path in search_roots:
                    if len(results) >= max_results:
                        break

                    root = Path(root_path).expanduser().resolve()
                    if not root.exists() or not root.is_dir():
                        continue

                    if effective_type == "content":
                        # Content search (grep-like)
                        _search_content(
                            root,
                            query,
                            results,
                            max_results,
                            type_filters,
                            min_size,
                            max_size,
                            min_date,
                            max_date,
                        )
                    else:
                        # Name/metadata search
                        _search_names(
                            root,
                            query,
                            query_lower,
                            is_glob,
                            results,
                            max_results,
                            type_filters,
                            min_size,
                            max_size,
                            min_date,
                            max_date,
                        )

                # Sort results
                if sort_by == "size":
                    results.sort(key=lambda x: x.get("size", 0), reverse=True)
                elif sort_by == "modified":
                    results.sort(key=lambda x: x.get("modified", 0), reverse=True)
                elif sort_by == "name":
                    results.sort(key=lambda x: x.get("name", "").lower())
                # relevance = default order (already by search priority)

                if not results:
                    return f"No files found matching '{query}'."

                lines = [f"Found {len(results)} result(s):\n"]
                for i, r in enumerate(results, 1):
                    size_str = _format_size(r.get("size", 0))
                    mod_str = (
                        _format_date(r.get("modified", 0)) if r.get("modified") else ""
                    )
                    path_str = r.get("path", "")

                    if effective_type == "content" and r.get("match_line"):
                        lines.append(f"  {i}. {path_str} ({size_str})")
                        lines.append(
                            f"     Line {r['match_line_num']}: {r['match_line'][:120]}"
                        )
                    else:
                        lines.append(f"  {i}. {path_str} ({size_str}, {mod_str})")

                return "\n".join(lines)

            except ValueError as e:
                return str(e)
            except Exception as e:
                logger.error(f"Error searching files: {e}")
                return f"Error searching files: {e}"

        @tool(atomic=True)
        def read_file(
            file_path: str,
            lines: int = 100,
            encoding: str = "auto",
            mode: str = "full",
        ) -> str:
            """Read and display a file's contents with intelligent type-based analysis.

            For text/code: shows content with line numbers.
            For CSV/TSV: shows tabular format with column headers.
            For JSON/YAML: pretty-printed with truncation for large objects.
            For images: dimensions, format, EXIF metadata.
            For PDF: page count, title, text preview.
            For DOCX/XLSX: structure overview and text content.
            For binary: hex dump header and file type detection.
            Use mode='preview' for a quick summary, mode='metadata' for info only.

            Args:
                file_path: Path to the file to read
                lines: Number of lines to show, 0 for all (default: 100)
                encoding: File encoding, 'auto' for auto-detect (default: auto)
                mode: Reading mode - full, preview, or metadata (default: full)
            """
            try:
                resolved = mixin._validate_path(file_path)

                if not resolved.exists():
                    return f"Error: File not found: {resolved}"

                if resolved.is_dir():
                    return f"Error: '{resolved}' is a directory. Use browse_directory or tree instead."

                ext = resolved.suffix.lower()
                file_size = resolved.stat().st_size

                # Metadata-only mode
                if mode == "metadata":
                    return file_info(str(resolved))

                # Handle specific file types

                # CSV/TSV
                if ext in (".csv", ".tsv"):
                    return _read_tabular(resolved, ext, lines, mode)

                # JSON
                if ext == ".json":
                    return _read_json(resolved, lines, mode)

                # PDF
                if ext == ".pdf":
                    return _read_pdf(resolved, mode)

                # Images
                if ext in {
                    ".jpg",
                    ".jpeg",
                    ".png",
                    ".gif",
                    ".bmp",
                    ".webp",
                    ".tiff",
                    ".svg",
                }:
                    info = file_info(str(resolved))
                    return f"[Image file]\n{info}"

                # Binary detection
                if file_size > 0:
                    try:
                        with open(resolved, "rb") as f:
                            sample = f.read(1024)
                        # Check for binary content
                        text_chars = bytearray(
                            {7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x100))
                        )
                        nontext = sum(1 for byte in sample if byte not in text_chars)
                        if nontext / len(sample) > 0.30:
                            mime, _ = mimetypes.guess_type(str(resolved))
                            hex_preview = sample[:64].hex(" ")
                            return (
                                f"[Binary file: {_format_size(file_size)}]\n"
                                f"MIME: {mime or 'unknown'}\n"
                                f"Hex preview: {hex_preview}..."
                            )
                    except Exception:
                        pass

                # Text file reading
                detected_encoding = encoding
                if detected_encoding == "auto":
                    detected_encoding = "utf-8"
                    # Try charset detection if available
                    try:
                        from charset_normalizer import from_path

                        result = from_path(str(resolved))
                        best = result.best()
                        if best:
                            detected_encoding = best.encoding
                    except ImportError:
                        pass

                try:
                    with open(
                        resolved,
                        "r",
                        encoding=detected_encoding,
                        errors="replace",
                    ) as f:
                        all_lines = f.readlines()
                except UnicodeDecodeError:
                    with open(
                        resolved,
                        "r",
                        encoding="utf-8",
                        errors="replace",
                    ) as f:
                        all_lines = f.readlines()

                total_lines = len(all_lines)

                if mode == "preview":
                    display_lines = all_lines[:20]
                    truncated = total_lines > 20
                elif lines > 0:
                    display_lines = all_lines[:lines]
                    truncated = total_lines > lines
                else:
                    display_lines = all_lines
                    truncated = False

                # Format with line numbers
                output_lines = [
                    f"File: {resolved} ({total_lines} lines, {_format_size(file_size)})"
                ]
                if detected_encoding != "utf-8":
                    output_lines.append(f"Encoding: {detected_encoding}")
                output_lines.append("")

                for i, line in enumerate(display_lines, 1):
                    output_lines.append(f"  {i:>5} | {line.rstrip()}")

                if truncated:
                    output_lines.append(
                        f"\n  ... ({total_lines - len(display_lines)} more lines)"
                    )

                return "\n".join(output_lines)

            except ValueError as e:
                return str(e)
            except Exception as e:
                logger.error(f"Error reading file: {e}")
                return f"Error reading file: {e}"

        @tool(atomic=True)
        def bookmark(
            action: str = "list",
            path: str = None,
            label: str = None,
        ) -> str:
            """Save, list, or remove bookmarks for frequently accessed files and directories.

            Bookmarks persist across sessions in the file system index.
            Use 'add' with a path and optional label to save a bookmark.
            Use 'remove' with a path to delete a bookmark.
            Use 'list' to see all saved bookmarks.

            Args:
                action: add, remove, or list (default: list)
                path: File or directory path to bookmark (required for add/remove)
                label: Human-friendly name for the bookmark (optional, for add)
            """
            try:
                if action == "list":
                    # Try index first, fall back to in-memory
                    if mixin._fs_index:
                        bookmarks = mixin._fs_index.list_bookmarks()
                    else:
                        bookmarks = [
                            {
                                "path": p,
                                "label": info.get("label", ""),
                                "category": info.get("category", ""),
                            }
                            for p, info in mixin._bookmarks.items()
                        ]

                    if not bookmarks:
                        return "No bookmarks saved yet. Use bookmark(action='add', path='...', label='...') to add one."

                    lines = ["Bookmarks:\n"]
                    for i, bm in enumerate(bookmarks, 1):
                        label_str = (
                            f' "{bm.get("label", "")}"' if bm.get("label") else ""
                        )
                        cat_str = (
                            f' [{bm.get("category", "")}]' if bm.get("category") else ""
                        )
                        lines.append(f"  {i}.{label_str} -> {bm['path']}{cat_str}")
                    return "\n".join(lines)

                elif action == "add":
                    if not path:
                        return "Error: 'path' is required when adding a bookmark."

                    resolved = mixin._validate_path(path)
                    if not resolved.exists():
                        return f"Error: Path does not exist: {resolved}"

                    path_str = str(resolved)

                    if mixin._fs_index:
                        # Auto-categorize
                        category = "directory" if resolved.is_dir() else "file"
                        mixin._fs_index.add_bookmark(
                            path_str, label=label, category=category
                        )
                    else:
                        mixin._bookmarks[path_str] = {
                            "label": label or "",
                            "category": "",
                        }

                    label_msg = f' as "{label}"' if label else ""
                    return f"Bookmarked{label_msg}: {path_str}"

                elif action == "remove":
                    if not path:
                        return "Error: 'path' is required when removing a bookmark."

                    resolved = mixin._validate_path(path)
                    path_str = str(resolved)

                    if mixin._fs_index:
                        removed = mixin._fs_index.remove_bookmark(path_str)
                    else:
                        removed = path_str in mixin._bookmarks
                        mixin._bookmarks.pop(path_str, None)

                    if removed:
                        return f"Bookmark removed: {path_str}"
                    else:
                        return f"No bookmark found for: {path_str}"

                else:
                    return f"Error: Unknown action '{action}'. Use 'add', 'remove', or 'list'."

            except ValueError as e:
                return str(e)
            except Exception as e:
                logger.error(f"Error managing bookmarks: {e}")
                return f"Error managing bookmarks: {e}"

        # --- Helper functions (not tools, not decorated) ---

        def _parse_size_range(size_range: str) -> tuple:
            """Parse size range string like '>10MB', '<1KB', '1MB-100MB'."""
            if not size_range:
                return None, None

            def _parse_size_value(s: str) -> int:
                s = s.strip().upper()
                multipliers = {
                    "B": 1,
                    "KB": 1024,
                    "MB": 1024**2,
                    "GB": 1024**3,
                    "TB": 1024**4,
                }
                for suffix, mult in sorted(
                    multipliers.items(), key=lambda x: -len(x[0])
                ):
                    if s.endswith(suffix):
                        num = float(s[: -len(suffix)])
                        return int(num * mult)
                return int(s)

            s = size_range.strip()
            if s.startswith(">"):
                return _parse_size_value(s[1:]), None
            elif s.startswith("<"):
                return None, _parse_size_value(s[1:])
            elif "-" in s:
                parts = s.split("-", 1)
                return _parse_size_value(parts[0]), _parse_size_value(parts[1])
            return None, None

        def _parse_date_range(date_range: str) -> tuple:
            """Parse date range string like 'today', 'this-week', '>2026-01-01'."""
            if not date_range:
                return None, None

            now = datetime.datetime.now()
            s = date_range.strip().lower()

            if s == "today":
                start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                return start.isoformat(), None
            elif s == "this-week":
                start = now - datetime.timedelta(days=now.weekday())
                start = start.replace(hour=0, minute=0, second=0, microsecond=0)
                return start.isoformat(), None
            elif s == "this-month":
                start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                return start.isoformat(), None
            elif s.startswith(">"):
                return s[1:].strip(), None
            elif s.startswith("<"):
                return None, s[1:].strip()
            elif len(s) == 7:  # YYYY-MM format
                return f"{s}-01", f"{s}-31"
            return None, None

        def _get_search_roots(scope: str) -> list:
            """Get search root directories based on scope."""
            home = str(Path.home())
            cwd = str(Path.cwd())

            if scope == "cwd":
                return [cwd]
            elif scope == "home":
                return [home]
            elif scope == "everywhere":
                import sys

                if sys.platform == "win32":
                    import string

                    return [
                        f"{d}:\\"
                        for d in string.ascii_uppercase
                        if Path(f"{d}:\\").exists()
                    ]
                return ["/"]
            elif scope == "smart":
                roots = [cwd]
                common = [
                    "Documents",
                    "Downloads",
                    "Desktop",
                    "Projects",
                    "Work",
                    "OneDrive",
                ]
                for folder in common:
                    p = Path(home) / folder
                    if p.exists() and str(p) != cwd:
                        roots.append(str(p))
                return roots
            else:
                # Treat as a specific path
                return [scope]

        def _search_names(
            root,
            _query,
            query_lower,
            is_glob,
            results,
            max_results,
            type_filters,
            min_size,
            max_size,
            min_date,
            max_date,
        ):
            """Search for files by name."""
            import fnmatch

            default_excludes = mixin._get_default_excludes()

            def _walk(current, depth):
                if depth > 10 or len(results) >= max_results:
                    return
                try:
                    for entry in os.scandir(str(current)):
                        if len(results) >= max_results:
                            return
                        try:
                            name = entry.name
                            if name.startswith(".") or name in default_excludes:
                                continue

                            is_dir = entry.is_dir(follow_symlinks=False)

                            # Check name match
                            if is_glob:
                                match = fnmatch.fnmatch(name.lower(), query_lower)
                            else:
                                match = query_lower in name.lower()

                            if match:
                                st = entry.stat(follow_symlinks=False)

                                # Type filter
                                if type_filters and not is_dir:
                                    ext = Path(name).suffix.lower()
                                    if ext not in type_filters:
                                        continue

                                # Size filter
                                if not is_dir:
                                    if min_size and st.st_size < min_size:
                                        continue
                                    if max_size and st.st_size > max_size:
                                        continue

                                # Date filter
                                if min_date:
                                    mod_str = datetime.datetime.fromtimestamp(
                                        st.st_mtime
                                    ).isoformat()
                                    if mod_str < min_date:
                                        continue
                                if max_date:
                                    mod_str = datetime.datetime.fromtimestamp(
                                        st.st_mtime
                                    ).isoformat()
                                    if mod_str > max_date:
                                        continue

                                results.append(
                                    {
                                        "path": str(Path(entry.path).resolve()),
                                        "name": name,
                                        "size": st.st_size if not is_dir else 0,
                                        "modified": st.st_mtime,
                                        "is_dir": is_dir,
                                    }
                                )

                            if is_dir and name not in default_excludes:
                                _walk(Path(entry.path), depth + 1)

                        except (PermissionError, OSError):
                            continue
                except (PermissionError, OSError):
                    return

            _walk(root, 0)

        def _search_content(
            root,
            query,
            results,
            max_results,
            type_filters,
            min_size,
            max_size,
            _min_date,
            _max_date,
        ):
            """Search inside file contents."""
            default_excludes = mixin._get_default_excludes()
            text_exts = {
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
                ".log",
                ".ini",
                ".html",
                ".css",
                ".sql",
                ".sh",
                ".bat",
                ".toml",
                ".cfg",
                ".conf",
                ".rs",
                ".go",
                ".rb",
            }

            query_lower = query.lower()

            def _walk(current, depth):
                if depth > 8 or len(results) >= max_results:
                    return
                try:
                    for entry in os.scandir(str(current)):
                        if len(results) >= max_results:
                            return
                        try:
                            name = entry.name
                            if name.startswith(".") or name in default_excludes:
                                continue

                            if entry.is_dir(follow_symlinks=False):
                                _walk(Path(entry.path), depth + 1)
                            elif entry.is_file(follow_symlinks=False):
                                ext = Path(name).suffix.lower()

                                # Type filter
                                if type_filters:
                                    if ext not in type_filters:
                                        continue
                                elif ext not in text_exts:
                                    continue

                                st = entry.stat(follow_symlinks=False)

                                # Size filters
                                if min_size and st.st_size < min_size:
                                    continue
                                if max_size and st.st_size > max_size:
                                    continue

                                # Skip large files
                                if st.st_size > 10 * 1024 * 1024:  # 10MB
                                    continue

                                try:
                                    with open(
                                        entry.path,
                                        "r",
                                        encoding="utf-8",
                                        errors="ignore",
                                    ) as f:
                                        for line_num, line in enumerate(f, 1):
                                            if query_lower in line.lower():
                                                results.append(
                                                    {
                                                        "path": str(
                                                            Path(entry.path).resolve()
                                                        ),
                                                        "name": name,
                                                        "size": st.st_size,
                                                        "modified": st.st_mtime,
                                                        "is_dir": False,
                                                        "match_line": line.strip(),
                                                        "match_line_num": line_num,
                                                    }
                                                )
                                                break  # One match per file
                                except Exception:
                                    pass
                        except (PermissionError, OSError):
                            continue
                except (PermissionError, OSError):
                    return

            _walk(root, 0)

        def _read_tabular(path, ext, max_lines, mode):
            """Read CSV/TSV file with tabular formatting."""
            import csv

            delimiter = "\t" if ext == ".tsv" else ","

            try:
                with open(
                    path,
                    "r",
                    encoding="utf-8",
                    errors="replace",
                    newline="",
                ) as f:
                    reader = csv.reader(f, delimiter=delimiter)
                    rows = []
                    for i, row in enumerate(reader):
                        rows.append(row)
                        if mode == "preview" and i >= 10:
                            break
                        if max_lines > 0 and i >= max_lines:
                            break

                if not rows:
                    return f"Empty {ext} file: {path}"

                # Calculate column widths
                max_cols = max(len(r) for r in rows)
                col_widths = [0] * max_cols
                for row in rows[:50]:  # Use first 50 rows for width calc
                    for j, cell in enumerate(row):
                        col_widths[j] = max(col_widths[j], min(len(str(cell)), 30))

                lines = [f"File: {path} ({len(rows)} rows, {max_cols} columns)\n"]

                # Header row
                if rows:
                    header = rows[0]
                    header_str = " | ".join(
                        str(h)[:30].ljust(col_widths[j]) for j, h in enumerate(header)
                    )
                    lines.append(f"  {header_str}")
                    lines.append(
                        f"  {'-+-'.join('-' * w for w in col_widths[:len(header)])}"
                    )

                # Data rows
                for row in rows[1:]:
                    row_str = " | ".join(
                        str(c)[:30].ljust(col_widths[j]) for j, c in enumerate(row)
                    )
                    lines.append(f"  {row_str}")

                return "\n".join(lines)
            except Exception as e:
                return f"Error reading {ext} file: {e}"

        def _read_json(path, max_lines, mode):
            """Read JSON file with pretty printing."""
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                formatted = json.dumps(data, indent=2, ensure_ascii=False)
                json_lines = formatted.split("\n")

                total = len(json_lines)
                if mode == "preview":
                    json_lines = json_lines[:30]
                elif max_lines > 0:
                    json_lines = json_lines[:max_lines]

                output = [f"File: {path} (JSON, {total} lines)\n"]
                for i, line in enumerate(json_lines, 1):
                    output.append(f"  {i:>5} | {line}")

                if len(json_lines) < total:
                    output.append(f"\n  ... ({total - len(json_lines)} more lines)")

                return "\n".join(output)
            except json.JSONDecodeError as e:
                return f"Invalid JSON file: {e}"
            except Exception as e:
                return f"Error reading JSON file: {e}"

        def _read_pdf(path, mode):
            """Read PDF file."""
            try:
                import PyPDF2
            except ImportError:
                return "PDF reading requires PyPDF2. Install with: pip install PyPDF2"

            try:
                with open(path, "rb") as f:
                    reader = PyPDF2.PdfReader(f)
                    num_pages = len(reader.pages)

                    lines = [f"File: {path} (PDF, {num_pages} pages)"]

                    # Metadata
                    if reader.metadata:
                        if reader.metadata.title:
                            lines.append(f"  Title: {reader.metadata.title}")
                        if reader.metadata.author:
                            lines.append(f"  Author: {reader.metadata.author}")

                    lines.append("")

                    if mode == "preview":
                        # First page only
                        text = reader.pages[0].extract_text()
                        if text:
                            preview_lines = text.strip().split("\n")[:30]
                            lines.append("Page 1 preview:")
                            for pl in preview_lines:
                                lines.append(f"  {pl}")
                    else:
                        # All pages (up to reasonable limit)
                        max_pages = min(num_pages, 20)
                        for page_num in range(max_pages):
                            text = reader.pages[page_num].extract_text()
                            if text:
                                lines.append(f"--- Page {page_num + 1} ---")
                                for pl in text.strip().split("\n"):
                                    lines.append(f"  {pl}")
                                lines.append("")

                        if num_pages > max_pages:
                            lines.append(f"\n... ({num_pages - max_pages} more pages)")

                    return "\n".join(lines)
            except Exception as e:
                return f"Error reading PDF: {e}"
