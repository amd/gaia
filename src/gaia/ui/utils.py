# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Shared utility functions and constants for GAIA Agent UI.

Contains helper functions and data shared across multiple router modules:
- File-related constants (allowed extensions, text extensions)
- Path sanitization and validation
- Data-conversion helpers (session, message, document -> response models)
- Filesystem helpers (format_size, quick links, Windows drives)
"""

import hashlib
import json
import logging
import os
import stat
import string
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional, Tuple

from fastapi import HTTPException

from .models import (
    DocumentResponse,
    FileEntry,
    MessageResponse,
    QuickLink,
    SessionResponse,
    SourceInfo,
)

logger = logging.getLogger(__name__)


# ── Constants ──────────────────────────────────────────────────────────────────

# Allowed document extensions for upload
ALLOWED_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".txt",
        ".md",
        ".csv",
        ".json",
        ".doc",
        ".docx",
        ".ppt",
        ".pptx",
        ".xls",
        ".xlsx",
        ".html",
        ".htm",
        ".xml",
        ".svg",
        ".yaml",
        ".yml",
        ".py",
        ".js",
        ".ts",
        ".java",
        ".c",
        ".cpp",
        ".h",
        ".rs",
        ".go",
        ".rb",
        ".sh",
        ".bat",
        ".ps1",
        ".log",
        ".cfg",
        ".ini",
        ".toml",
    }
)

# Text file extensions for preview endpoint
TEXT_EXTENSIONS = frozenset(
    {
        ".txt",
        ".md",
        ".csv",
        ".tsv",
        ".json",
        ".xml",
        ".yaml",
        ".yml",
        ".py",
        ".js",
        ".ts",
        ".html",
        ".css",
        ".log",
        ".ini",
        ".cfg",
        ".toml",
        ".sql",
        ".sh",
        ".bat",
        ".ps1",
        ".java",
        ".c",
        ".cpp",
        ".h",
        ".rs",
        ".go",
        ".rb",
    }
)

# Threshold for switching to background indexing
LARGE_FILE_THRESHOLD = 5 * 1024 * 1024  # 5 MB


# ── Data Conversion Helpers ────────────────────────────────────────────────────


def session_to_response(session: dict) -> SessionResponse:
    """Convert database session dict to response model."""
    return SessionResponse(
        id=session["id"],
        title=session["title"],
        created_at=session["created_at"],
        updated_at=session["updated_at"],
        model=session["model"],
        system_prompt=session.get("system_prompt"),
        message_count=session.get("message_count", 0),
        document_ids=session.get("document_ids", []),
        private=bool(session.get("private", 0)),
    )


def message_to_response(msg: dict) -> MessageResponse:
    """Convert database message dict to response model."""
    from .models import AgentStepResponse

    sources = None
    if msg.get("rag_sources"):
        try:
            raw_sources = msg["rag_sources"]
            if isinstance(raw_sources, str):
                raw_sources = json.loads(raw_sources)
            sources = [SourceInfo(**s) for s in raw_sources]
        except Exception:
            sources = None

    agent_steps = None
    if msg.get("agent_steps"):
        try:
            raw_steps = msg["agent_steps"]
            if isinstance(raw_steps, str):
                raw_steps = json.loads(raw_steps)
            agent_steps = [AgentStepResponse(**s) for s in raw_steps]
        except Exception:
            agent_steps = None

    # Map inference_stats column to stats response field
    stats = None
    if msg.get("inference_stats"):
        try:
            from .models import InferenceStatsResponse

            raw_stats = msg["inference_stats"]
            if isinstance(raw_stats, str):
                raw_stats = json.loads(raw_stats)
            stats = InferenceStatsResponse(**raw_stats)
        except Exception:
            stats = None

    return MessageResponse(
        id=msg["id"],
        session_id=msg["session_id"],
        role=msg["role"],
        content=msg["content"],
        created_at=msg["created_at"],
        rag_sources=sources,
        agent_steps=agent_steps,
        stats=stats,
    )


def doc_to_response(doc: dict) -> DocumentResponse:
    """Convert database document dict to response model."""
    return DocumentResponse(
        id=doc["id"],
        filename=doc["filename"],
        filepath=doc["filepath"],
        file_size=doc.get("file_size", 0),
        chunk_count=doc.get("chunk_count", 0),
        indexed_at=doc["indexed_at"],
        last_accessed_at=doc.get("last_accessed_at"),
        sessions_using=doc.get("sessions_using", 0),
        indexing_status=doc.get("indexing_status", "complete"),
    )


# ── Path Sanitization / Validation ─────────────────────────────────────────────


def sanitize_document_path(user_path: str) -> Path:
    """Sanitize a user-provided file path for document upload.

    Resolves the path, validates it is absolute, checks for null bytes,
    and enforces an extension allowlist. Returns a safe Path object
    that has been fully validated.

    Args:
        user_path: Raw file path string from user input.

    Returns:
        A resolved, validated Path object safe for filesystem operations.

    Raises:
        HTTPException: If the path is invalid, contains traversal, or
            has a disallowed extension.
    """
    # Reject null bytes early (before any path operations)
    if "\x00" in user_path:
        raise HTTPException(status_code=400, detail="Invalid file path")

    # Check symlink before resolve (resolve follows symlinks silently)
    if Path(user_path).is_symlink():
        raise HTTPException(status_code=400, detail="Symbolic links are not supported")

    # Resolve to absolute canonical path (eliminates .., etc.)
    resolved = Path(user_path).resolve()

    # Verify the path is absolute
    if not resolved.is_absolute():
        raise HTTPException(status_code=400, detail="Invalid file path")

    # Check file extension against allowlist
    ext = resolved.suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        # Provide categorized feedback for common unsupported types
        _UNSUPPORTED_CATEGORIES = {
            "image": (
                {
                    ".jpg",
                    ".jpeg",
                    ".png",
                    ".gif",
                    ".bmp",
                    ".tiff",
                    ".webp",
                    ".ico",
                    ".heic",
                    ".heif",
                },
                "Image files cannot be indexed for text search. "
                "Tip: If your images contain text, convert them to PDF first — GAIA can extract text from PDFs.",
            ),
            "video": (
                {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v"},
                "Video files are not supported for indexing.",
            ),
            "audio": (
                {".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a", ".opus"},
                "Audio files are not supported for indexing. "
                "Tip: GAIA has a separate voice/talk mode — try `gaia talk` from the CLI.",
            ),
            "archive": (
                {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz"},
                "Archive files must be extracted first. "
                "Extract the archive and then index the individual files inside.",
            ),
            "executable": (
                {".exe", ".msi", ".dll", ".so", ".app", ".dmg", ".bin", ".com"},
                "Executable and binary files cannot be indexed.",
            ),
            "database": (
                {".sqlite", ".db", ".mdb", ".accdb", ".dbf"},
                "Database files are not supported for direct indexing. "
                "Tip: Export your data to CSV or JSON format, then index those files.",
            ),
        }

        hint = ""
        category = ""
        for cat, (exts, msg) in _UNSUPPORTED_CATEGORIES.items():
            if ext in exts:
                hint = msg
                category = cat
                break

        if not hint:
            hint = f"The file type '{ext}' is not supported for indexing."

        detail = (
            f"{hint} "
            f"Supported formats: PDF, TXT, MD, CSV, JSON, Office docs (DOC/DOCX, PPT/PPTX, XLS/XLSX), "
            f"HTML, XML, YAML, and 30+ code file formats. "
            f"Want support for {category + ' files' if category else 'this file type'}? "
            f"Request it at https://github.com/amd/gaia/issues/new?title=[Feature]%20Support%20{ext}%20file%20indexing"
        )
        raise HTTPException(status_code=400, detail=detail)

    return resolved


def sanitize_static_path(base_dir: Path, user_path: str) -> Optional[Path]:
    """Sanitize a URL path for static file serving.

    Ensures the resolved path stays within the base directory.
    Returns None if the path would escape the base directory.

    Args:
        base_dir: The root directory for static files (must be resolved).
        user_path: The URL path component from the request.

    Returns:
        A safe resolved Path within base_dir, or None if invalid.
    """
    if not user_path:
        return None

    # Reject null bytes and obvious traversal patterns
    if "\x00" in user_path or ".." in user_path:
        return None

    # Build and resolve the candidate path
    resolved_base = base_dir.resolve()
    candidate = (resolved_base / user_path).resolve()

    # Verify the candidate is within the base directory
    try:
        candidate.relative_to(resolved_base)
    except ValueError:
        return None

    return candidate


def validate_file_path(filepath: Path) -> None:
    """Validate that a file path is safe to access.

    Checks:
    - Path is absolute (after resolve)
    - Path does not contain null bytes
    - File extension is in allowed set

    Raises:
        HTTPException: If the path is invalid or unsafe.
    """
    # Check for null bytes (path injection)
    if "\x00" in str(filepath):
        raise HTTPException(status_code=400, detail="Invalid file path")

    # Verify the path is absolute (resolve() makes it absolute)
    if not filepath.is_absolute():
        raise HTTPException(status_code=400, detail="Invalid file path")

    # Check file extension
    ext = filepath.suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ext}",
        )


def ensure_within_home(resolved: Path) -> None:
    """Raise HTTP 403 if *resolved* is not inside the user's home directory.

    This helper is used by file-browsing, preview, and search endpoints to
    prevent access to arbitrary filesystem locations.
    """
    home = Path.home()
    try:
        resolved.relative_to(home)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail="Access restricted to files under user home directory",
        )


# ── Filesystem Helpers ─────────────────────────────────────────────────────────


def format_size(size_bytes: int) -> str:
    """Format bytes as human-readable string."""
    if size_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    size = float(size_bytes)
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.1f} {units[i]}"


def compute_file_hash(filepath: Path) -> str:
    """Compute SHA-256 hash of file contents."""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for block in iter(lambda: f.read(8192), b""):
            sha256.update(block)
    return sha256.hexdigest()


def compute_file_hash_from_fd(fd: int) -> str:
    """Compute SHA-256 hash of a file from an open file descriptor.

    The fd is read from its current position; callers should seek(0) first
    if they need the full file hash.
    """
    sha256 = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    while True:
        block = os.read(fd, 8192)
        if not block:
            break
        sha256.update(block)
    return sha256.hexdigest()


@contextmanager
def safe_open_document(
    path: str,
) -> Generator[Tuple[int, os.stat_result, Path], None, None]:
    """TOCTOU-safe context manager for opening document files.

    Validates that the path:
    - Is not a symlink (400 if symlink — checked before resolving)
    - Is within the user home directory (403 if not)
    - Has an allowed extension (400 if not)
    - Exists and is a regular file (404 / 400 if not)

    Yields a tuple of (fd, stat_result, resolved_path).
    The fd is a raw OS file descriptor open for reading; callers must NOT
    close it — the context manager owns its lifetime.
    """
    raw = Path(path)
    home = Path.home().resolve()

    # 1. Must be within home directory — check before lstat so that
    # non-existent paths outside home return 403 (not 404).
    # Use os.path.abspath (doesn't follow symlinks) for this check.
    abs_raw = Path(os.path.abspath(str(raw)))
    try:
        abs_raw.relative_to(home)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: path must be within home directory ({home})",
        )

    # 2. Reject symlinks before resolving — lstat doesn't follow symlinks
    try:
        lst = os.lstat(str(raw))
        if stat.S_ISLNK(lst.st_mode):
            raise HTTPException(
                status_code=400,
                detail="Symbolic link documents are not allowed for security reasons",
            )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {raw}")
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Cannot stat file: {exc}")

    # 3. Now resolve (safe: we already confirmed it's not a symlink)
    resolved = raw.resolve()

    # 4. Extension must be allowed
    if resolved.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type not allowed: {resolved.suffix}. Allowed: {sorted(ALLOWED_EXTENSIONS)}",
        )

    # 5. Open safely — use O_NOFOLLOW on POSIX to reject symlinks at kernel level
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW  # POSIX only; raises OSError(ELOOP) on symlinks
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY  # Ensure binary mode on Windows

    try:
        fd = os.open(str(resolved), flags)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {resolved}")
    except OSError as exc:
        import errno

        if exc.errno in (getattr(errno, "ELOOP", None), getattr(errno, "EMLINK", None)):
            raise HTTPException(
                status_code=400,
                detail="Symlink documents are not allowed for security reasons",
            )
        raise HTTPException(status_code=400, detail=f"Cannot open file: {exc}")

    try:
        st = os.fstat(fd)
        # 6. Must be a regular file (not dir, device, etc.)
        if not stat.S_ISREG(st.st_mode):
            raise HTTPException(
                status_code=400,
                detail="Path is not a regular file",
            )
        yield fd, st, resolved
    finally:
        os.close(fd)


def build_quick_links() -> list:
    """Build a list of common quick-access filesystem locations.

    Returns platform-appropriate links to Desktop, Documents, Downloads,
    and the user home directory.
    """
    home = Path.home()
    links = [
        QuickLink(name="Home", path=str(home), icon="home"),
    ]

    candidates = [
        ("Desktop", home / "Desktop", "desktop"),
        ("Documents", home / "Documents", "documents"),
        ("Downloads", home / "Downloads", "download"),
    ]

    for name, candidate_path, icon in candidates:
        if candidate_path.is_dir():
            links.append(QuickLink(name=name, path=str(candidate_path), icon=icon))

    return links


def list_windows_drives() -> list:
    """List available Windows drive letters as FileEntry items.

    Iterates A-Z and returns an entry for each drive letter whose
    root directory exists on the system.
    """
    entries = []
    for letter in string.ascii_uppercase:
        drive = f"{letter}:\\"
        if Path(drive).exists():
            entries.append(
                FileEntry(
                    name=f"{letter}:",
                    path=drive,
                    type="folder",
                    size=0,
                    extension=None,
                    modified=None,
                )
            )
    return entries
