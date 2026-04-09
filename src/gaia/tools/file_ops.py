# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""File-system tools for pipeline agents.

Provides sandboxed file read, write, and list operations.  Every path is
resolved against *workspace_dir* and checked to prevent traversal outside the
workspace boundary.
"""

from pathlib import Path

from gaia.agents.base.tools import tool


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


def _resolve_safe_path(path: str, workspace_dir: str) -> Path:
    """Resolve *path* relative to *workspace_dir* and reject traversal.

    Args:
        path: Relative (or absolute) path supplied by the caller.
        workspace_dir: The sandbox root directory.

    Returns:
        The resolved :class:`~pathlib.Path`.

    Raises:
        PermissionError: If the resolved path escapes *workspace_dir*.
    """
    workspace_root = Path(workspace_dir).resolve()
    resolved_path = Path(workspace_dir, path).resolve()
    if not resolved_path.is_relative_to(workspace_root):
        raise PermissionError(
            f"Path '{path}' resolves outside workspace: {resolved_path}"
        )
    return resolved_path


# ---------------------------------------------------------------------------
# Public tools
# ---------------------------------------------------------------------------


@tool
def file_read(path: str, workspace_dir: str = ".") -> dict:
    """Read the contents of a file inside the workspace.

    Args:
        path: Relative path to the file (resolved against *workspace_dir*).
        workspace_dir: Root directory that acts as the sandbox boundary.

    Returns:
        A dict with ``status``, ``content``, ``file_path``, ``line_count``,
        and ``size_bytes`` on success, or ``status`` and ``error`` on failure.
    """
    try:
        resolved = _resolve_safe_path(path, workspace_dir)
        content = resolved.read_text(encoding="utf-8")
        return {
            "status": "success",
            "content": content,
            "file_path": str(resolved),
            "line_count": content.count("\n") + (1 if content else 0),
            "size_bytes": resolved.stat().st_size,
        }
    except PermissionError:
        raise  # Let path-traversal errors propagate
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@tool
def file_write(path: str, content: str, workspace_dir: str = ".") -> dict:
    """Write *content* to a file inside the workspace.

    Parent directories are created automatically if they do not exist.

    Args:
        path: Relative path to the target file.
        content: The text content to write.
        workspace_dir: Root directory that acts as the sandbox boundary.

    Returns:
        A dict with ``status``, ``file_path``, and ``bytes_written`` on
        success, or ``status`` and ``error`` on failure.
    """
    try:
        resolved = _resolve_safe_path(path, workspace_dir)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        return {
            "status": "success",
            "file_path": str(resolved),
            "bytes_written": resolved.stat().st_size,
        }
    except PermissionError:
        raise
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@tool
def file_list(directory: str = ".", workspace_dir: str = ".") -> dict:
    """List files and sub-directories inside the workspace.

    Args:
        directory: Relative path to the directory to list.
        workspace_dir: Root directory that acts as the sandbox boundary.

    Returns:
        A dict with ``status``, ``path``, ``files``, ``directories``, and
        ``total`` on success, or ``status`` and ``error`` on failure.
    """
    try:
        resolved = _resolve_safe_path(directory, workspace_dir)
        files = []
        directories = []
        for entry in sorted(resolved.iterdir()):
            if entry.is_dir():
                directories.append(entry.name)
            else:
                files.append(entry.name)
        return {
            "status": "success",
            "path": str(resolved),
            "files": files,
            "directories": directories,
            "total": len(files) + len(directories),
        }
    except PermissionError:
        raise
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
