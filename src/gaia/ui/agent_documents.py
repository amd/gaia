# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Put the documents the agent indexed in a chat into the Document library.

When the user asks the agent to "add X to my documents", ``index_document``
indexes it inside the agent, which may be a separate sidecar process. This
module records each such file in the library and attaches it to the chat, as
the library's own Add does, so it shows in Documents and in the chat and the
next chat can attach it.
"""

from __future__ import annotations

import functools
import hashlib
import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Collection, Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException

from gaia.logger import get_logger

from .database import ChatDatabase
from .routers.documents import _sanitize_stem, _short_id
from .utils import (
    ALLOWED_EXTENSIONS,
    compute_file_hash,
    compute_file_hash_from_fd,
    is_within_document_roots,
    managed_documents_dir,
    safe_open_document,
    unsupported_type_message,
)

logger = get_logger(__name__)


@dataclass
class LibraryAdditions:
    """What recording a turn's indexed documents did."""

    #: Library rows now attached to the chat.
    documents: List[Dict[str, Any]] = field(default_factory=list)
    #: Sentences for the user: a file that went in as a copy, or did not go in.
    notices: List[str] = field(default_factory=list)


def _detail(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict):
        return str(detail.get("message") or detail)
    return str(detail)


def _record_in_place(db: ChatDatabase, path: Path, chunks: int) -> Dict[str, Any]:
    """Record a file under a document root at its own path."""
    with safe_open_document(str(path)) as (fd, file_stat, safe_path):
        file_hash = compute_file_hash_from_fd(fd)
    return db.add_document(
        filename=safe_path.name,
        filepath=str(safe_path),
        file_hash=file_hash,
        file_size=file_stat.st_size,
        chunk_count=chunks,
        file_mtime=file_stat.st_mtime,
    )


def _record_copy(
    db: ChatDatabase, path: Path, chunks: int
) -> Tuple[Dict[str, Any], bool]:
    """Record a file outside every document root as a copy the library owns.

    The library only opens files under its roots, so the original could not be
    previewed or re-indexed from there; the agent was allowed to read it.
    Returns the library row and whether a copy was made: none is when the
    library already holds the same content.
    """
    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=unsupported_type_message(path.name))
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"There is no file at {path}.")

    hasher = hashlib.sha256()
    with open(path, "rb") as src:
        for block in iter(lambda: src.read(65536), b""):
            hasher.update(block)
    file_hash = hasher.hexdigest()
    existing = db.get_document_by_hash(file_hash)
    if existing:
        return existing, False

    managed = managed_documents_dir()
    managed.mkdir(parents=True, exist_ok=True)
    copy = managed / f"{_short_id()}_{_sanitize_stem(path.stem)}{path.suffix.lower()}"
    shutil.copyfile(path, copy)
    file_stat = copy.stat()
    doc = db.add_document(
        filename=path.name,
        filepath=str(copy),
        file_hash=file_hash,
        file_size=file_stat.st_size,
        chunk_count=chunks,
        file_mtime=file_stat.st_mtime,
    )
    return doc, True


def _is_other_file(doc: Dict[str, Any], path: Path) -> bool:
    """Whether the library row *doc* matched *path* by content but is another file.

    GAIA's own copies are skipped: one is usually this file's copy from an
    earlier turn, and the copy notice already named it.
    """
    stored = doc.get("filepath") or ""
    if not stored or Path(os.path.realpath(stored)) == path:
        return False
    managed = Path(os.path.realpath(managed_documents_dir()))
    return managed not in Path(os.path.realpath(stored)).parents


def add_indexed_documents(
    db: ChatDatabase, session_id: str, indexed: Iterable[Tuple[str, int]]
) -> LibraryAdditions:
    """Record each ``(path, chunks)`` the agent indexed and attach it to the chat.

    A file that can't be recorded is named in a notice with the reason; the
    rest still go in.
    """
    result = LibraryAdditions()
    for raw_path, chunks in indexed:
        path = Path(os.path.realpath(raw_path))
        try:
            if is_within_document_roots(path):
                doc, copied = _record_in_place(db, path, chunks), False
            else:
                doc, copied = _record_copy(db, path, chunks)
            db.attach_document(session_id, doc["id"])
            result.documents.append(db.get_document(doc["id"]))
        except HTTPException as exc:
            reason = _detail(exc)
        except (OSError, sqlite3.Error) as exc:
            logger.error(
                "Could not add agent-indexed %s to the Document library: %s",
                path,
                exc,
                exc_info=True,
            )
            reason = str(exc)
        else:
            if copied:
                result.notices.append(
                    f"{path.name} is outside the folders your documents are read "
                    f"from, so your documents hold a copy of it. Changes to "
                    f"{path} won't reach the copy."
                )
            elif _is_other_file(doc, path):
                result.notices.append(
                    f"{path.name} has the same content as {doc['filepath']}, "
                    f"which is already in your documents, so this chat uses "
                    f"that file."
                )
            continue
        logger.warning("Agent-indexed %s not added to the library: %s", path, reason)
        result.notices.append(
            f"{path.name} was indexed for this answer, but couldn't be added to "
            f"your documents, so later chats won't find it: {reason}"
        )
    return result


@functools.lru_cache(maxsize=512)
def _hash_at(
    path: str, mtime_ns: int, size: int  # pylint: disable=unused-argument
) -> str:
    """Hash *path*; ``mtime_ns`` and ``size`` only key the cache, so an edit re-reads."""
    return compute_file_hash(Path(path))


def _content_hash(path: str) -> Optional[str]:
    """SHA-256 of *path*'s content, the library's ``file_hash``; None if unreadable."""
    try:
        st = os.stat(path)
        return _hash_at(path, st.st_mtime_ns, st.st_size)
    except OSError as exc:
        logger.debug("Could not hash %s: %s", path, exc)
        return None


def not_yet_indexed(paths: Iterable[str], indexed: Collection[str]) -> List[str]:
    """The *paths* whose content the agent has not indexed yet.

    The library holds a copy of a file from outside its folders, at a new
    path; the agent indexed the original, so the copy has nothing new for it.
    An unreadable path is kept, so indexing it reports why.
    """
    fresh = [p for p in paths if p not in indexed]
    if not fresh:
        return fresh
    held = {h for h in map(_content_hash, indexed) if h}
    return [p for p in fresh if _content_hash(p) not in held]
