# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""File browsing, search, preview, and open endpoints for GAIA Agent UI.

Provides filesystem access for the document picker UI:
- Browse directories with allowed-extension filtering
- Search files across user directories
- Preview text file contents
- Open files/folders in the system file explorer
"""

import asyncio
import datetime
import logging
import platform
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException

from ..models import (
    BrowseResponse,
    FileEntry,
    FilePreviewResponse,
    FileSearchResponse,
    FileSearchResult,
    OpenFileRequest,
)
from ..utils import (
    ALLOWED_EXTENSIONS,
    TEXT_EXTENSIONS,
    build_quick_links,
    ensure_within_home,
    format_size,
    list_windows_drives,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["files"])


# ── Browse ───────────────────────────────────────────────────────────────────


@router.get("/api/files/browse", response_model=BrowseResponse)
async def browse_files(path: Optional[str] = None):
    """Browse files and folders for the document picker.

    Lists folders (always shown) and files whose extension is in
    ALLOWED_EXTENSIONS.  Results are sorted folders-first, then
    alphabetically by name.

    Args:
        path: Directory to browse. Defaults to user home directory.
              On Windows, pass an empty string or "/" to list drive
              letters.
    """
    quick_links = build_quick_links()

    # On Windows, treat None / empty / "/" as "list drive letters"
    if platform.system() == "Windows" and (not path or path in ("/", "\\")):
        entries = list_windows_drives()
        return BrowseResponse(
            current_path="/",
            parent_path=None,
            entries=entries,
            quick_links=quick_links,
        )

    # Default to home directory when no path is given
    if not path:
        path = str(Path.home())

    # Security: reject null bytes
    if "\x00" in path:
        raise HTTPException(status_code=400, detail="Invalid path")

    raw_path = Path(path)

    # Do not follow symlinks (must check before resolve)
    if raw_path.is_symlink():
        raise HTTPException(
            status_code=400, detail="Symbolic links are not supported"
        )

    resolved = raw_path.resolve(strict=False)

    if not resolved.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    # Security: restrict browsing to user's home directory
    ensure_within_home(resolved)

    # Determine parent path (clamped to home directory)
    home = Path.home()
    parent_path: Optional[str] = None
    if resolved == home:
        # At home directory -- no parent to navigate to
        parent_path = None
    elif resolved.parent != resolved:
        # Check if parent is still within home; if not, clamp to home
        try:
            resolved.parent.relative_to(home)
            parent_path = str(resolved.parent)
        except ValueError:
            parent_path = str(home)
    elif platform.system() == "Windows":
        # At a drive root (e.g. C:\) -- go back to drive listing
        parent_path = "/"

    entries: List[FileEntry] = []
    try:
        for item in resolved.iterdir():
            # Skip symlinks for security
            if item.is_symlink():
                continue

            try:
                stat = item.stat()
            except (OSError, PermissionError):
                continue

            if item.is_dir():
                entries.append(
                    FileEntry(
                        name=item.name,
                        path=str(item),
                        type="folder",
                        size=0,
                        extension=None,
                        modified=datetime.datetime.fromtimestamp(
                            stat.st_mtime
                        ).isoformat(),
                    )
                )
            elif item.is_file():
                ext = item.suffix.lower()
                if ext in ALLOWED_EXTENSIONS:
                    entries.append(
                        FileEntry(
                            name=item.name,
                            path=str(item),
                            type="file",
                            size=stat.st_size,
                            extension=ext,
                            modified=datetime.datetime.fromtimestamp(
                                stat.st_mtime
                            ).isoformat(),
                        )
                    )
    except PermissionError:
        raise HTTPException(
            status_code=403, detail="Permission denied for this directory"
        )

    # Sort: folders first, then files, alphabetically within each group
    entries.sort(key=lambda e: (e.type != "folder", e.name.lower()))

    return BrowseResponse(
        current_path=str(resolved),
        parent_path=parent_path,
        entries=entries,
        quick_links=quick_links,
    )


# ── Open File/Folder ─────────────────────────────────────────────────────────


@router.post("/api/files/open")
async def open_file_or_folder(request: OpenFileRequest):
    """Open a file or its containing folder in the system file explorer.

    Args:
        request.path: Absolute path to the file or folder.
        request.reveal: If true, reveal the file in its parent folder
                       (default: true for files, ignored for folders).
    """
    import subprocess

    file_path = request.path
    if not file_path or "\x00" in file_path:
        raise HTTPException(status_code=400, detail="Invalid path")

    raw_path = Path(file_path)
    reveal = request.reveal

    # Security: reject symlinks BEFORE resolving so resolve() can't
    # silently redirect to an unintended target.
    if raw_path.is_symlink():
        raise HTTPException(
            status_code=400, detail="Symbolic links are not supported"
        )

    resolved = raw_path.resolve(strict=False)

    if not resolved.exists():
        raise HTTPException(status_code=404, detail="Path not found")

    # Security: restrict to user's home directory to prevent opening
    # arbitrary system files (especially important when tunnel is active)
    home = Path.home()
    try:
        resolved.relative_to(home)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail="Access restricted to files under user home directory",
        )

    try:
        if platform.system() == "Windows":
            if resolved.is_file() and reveal:
                # Reveal file in Explorer (selects it)
                subprocess.Popen(["explorer", "/select,", str(resolved)])
            else:
                # Open folder directly
                target = resolved if resolved.is_dir() else resolved.parent
                subprocess.Popen(["explorer", str(target)])
        elif platform.system() == "Darwin":
            if resolved.is_file() and reveal:
                subprocess.Popen(["open", "-R", str(resolved)])
            else:
                target = resolved if resolved.is_dir() else resolved.parent
                subprocess.Popen(["open", str(target)])
        else:
            target = resolved if resolved.is_dir() else resolved.parent
            subprocess.Popen(["xdg-open", str(target)])

        return {"status": "ok", "path": str(resolved)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── File Search ──────────────────────────────────────────────────────────────


@router.get("/api/files/search", response_model=FileSearchResponse)
async def search_files(
    query: str,
    file_types: Optional[str] = None,
    max_results: int = 20,
):
    """Search for files across the filesystem by name pattern.

    Searches common user directories (Documents, Downloads, Desktop)
    then expands to deeper search if needed. Results sorted by
    modification date (most recent first).

    Args:
        query: File name pattern to search for (partial matches supported).
        file_types: Comma-separated extensions to filter (e.g., 'csv,xlsx').
        max_results: Maximum results to return (1-100, default 20).
    """
    import time as _time

    if not query or not query.strip():
        raise HTTPException(status_code=400, detail="Search query is required")

    # Security: reject null bytes
    if "\x00" in query:
        raise HTTPException(status_code=400, detail="Invalid search query")

    query_lower = query.strip().lower()
    max_results = min(max(max_results, 1), 100)

    # Build extension filter
    extensions = None
    if file_types:
        extensions = {
            f".{ext.strip().lower()}"
            for ext in file_types.split(",")
            if ext.strip()
        }

    def _do_search() -> tuple:
        """Blocking filesystem scan -- runs in a thread."""
        matching_files: list = []
        seen_paths: set = set()
        searched_locations: list = []
        start_time = _time.monotonic()

        def _matches(file_path: Path) -> bool:
            name_match = query_lower in file_path.name.lower()
            if not name_match:
                return False
            if extensions:
                return file_path.suffix.lower() in extensions
            return True

        def _scan(
            directory: Path, max_depth: int = 5, depth: int = 0
        ):
            if depth > max_depth or len(matching_files) >= max_results:
                return
            if not directory.exists() or not directory.is_dir():
                return

            searched_locations.append(str(directory))

            try:
                for item in directory.iterdir():
                    if len(matching_files) >= max_results:
                        return
                    if item.name.startswith((".", "$", "__")):
                        continue
                    if item.name in (
                        "node_modules",
                        ".git",
                        "Windows",
                        "Program Files",
                        "Program Files (x86)",
                        "ProgramData",
                        "AppData",
                    ):
                        continue
                    try:
                        if item.is_symlink():
                            continue
                        if item.is_file() and _matches(item):
                            resolved_str = str(item.resolve())
                            if resolved_str in seen_paths:
                                continue
                            seen_paths.add(resolved_str)
                            st = item.stat()
                            size = st.st_size
                            matching_files.append(
                                {
                                    "name": item.name,
                                    "path": str(item),
                                    "size": size,
                                    "size_display": format_size(size),
                                    "extension": item.suffix.lower(),
                                    "modified": datetime.datetime.fromtimestamp(
                                        st.st_mtime
                                    ).isoformat(),
                                    "directory": str(item.parent),
                                }
                            )
                        elif item.is_dir() and depth < max_depth:
                            _scan(item, max_depth, depth + 1)
                    except (PermissionError, OSError):
                        continue
            except (PermissionError, OSError):
                pass

        home = Path.home()
        for loc in [
            home / "Documents",
            home / "Downloads",
            home / "Desktop",
            home / "OneDrive",
        ]:
            if len(matching_files) >= max_results:
                break
            _scan(loc, max_depth=4)

        if len(matching_files) < max_results:
            _scan(home, max_depth=3)

        matching_files.sort(key=lambda f: f["modified"], reverse=True)
        matching_files = matching_files[:max_results]

        elapsed_sec = _time.monotonic() - start_time
        logger.info(
            "File search for '%s': %d results in %.2fs (%d locations)",
            query,
            len(matching_files),
            elapsed_sec,
            len(searched_locations),
        )
        return matching_files, searched_locations

    # Run blocking scan in a thread to avoid blocking the event loop
    loop = asyncio.get_running_loop()
    matching_files, searched_locations = await loop.run_in_executor(
        None, _do_search
    )

    return FileSearchResponse(
        results=[FileSearchResult(**f) for f in matching_files],
        total=len(matching_files),
        query=query,
        searched_locations=searched_locations[:10],
    )


# ── File Preview ─────────────────────────────────────────────────────────────


@router.get("/api/files/preview", response_model=FilePreviewResponse)
async def preview_file(path: str, lines: int = 50):
    """Get a preview of a file's contents.

    For text files, returns the first N lines.
    For CSV/TSV, also returns column names and row count.
    For binary files, returns metadata only.

    Args:
        path: Absolute path to the file.
        lines: Number of lines to preview (default 50, max 200).
    """
    if not path:
        raise HTTPException(status_code=400, detail="File path is required")

    if "\x00" in path:
        raise HTTPException(status_code=400, detail="Invalid file path")

    raw_path = Path(path)

    # Check symlink before resolve (resolve follows symlinks)
    if raw_path.is_symlink():
        raise HTTPException(status_code=400, detail="Symbolic links not supported")

    resolved = raw_path.resolve(strict=False)

    if not resolved.exists():
        raise HTTPException(status_code=404, detail="File not found")

    if not resolved.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")

    # Security: restrict previews to user's home directory
    ensure_within_home(resolved)

    lines = min(max(lines, 1), 200)
    stat = resolved.stat()
    ext = resolved.suffix.lower()

    result = {
        "path": str(resolved),
        "name": resolved.name,
        "size": stat.st_size,
        "size_display": format_size(stat.st_size),
        "extension": ext,
        "modified": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "is_text": False,
        "preview_lines": [],
        "total_lines": None,
        "columns": None,
        "row_count": None,
        "encoding": None,
    }

    # Try to read as text
    if ext in TEXT_EXTENSIONS or stat.st_size < 1_000_000:  # Try text for < 1MB
        for encoding in ("utf-8", "latin-1", "cp1252"):
            try:
                import itertools

                preview = []
                total_lines = 0
                with open(resolved, "r", encoding=encoding) as f:
                    # Read only the first N lines for preview
                    for line in itertools.islice(f, lines):
                        preview.append(line.rstrip("\n\r")[:500])
                    # Count remaining lines without loading into memory
                    total_lines = len(preview)
                    for _ in f:
                        total_lines += 1
                result["is_text"] = True
                result["encoding"] = encoding
                result["total_lines"] = total_lines
                result["preview_lines"] = preview

                # CSV/TSV specific info
                if ext in (".csv", ".tsv"):
                    import csv as csv_mod

                    delimiter = "\t" if ext == ".tsv" else ","
                    try:
                        with open(resolved, "r", encoding=encoding) as cf:
                            reader = csv_mod.reader(cf, delimiter=delimiter)
                            header = next(reader, None)
                            if header:
                                result["columns"] = header
                                row_count = sum(1 for _ in reader)
                                result["row_count"] = row_count
                    except Exception:
                        pass
                break
            except (UnicodeDecodeError, UnicodeError):
                continue

    return FilePreviewResponse(**result)
