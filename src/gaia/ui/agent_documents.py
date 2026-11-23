# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Put the documents the agent indexed in a chat into the Document library.

When the user asks the agent to "add X to my documents", ``index_document``
indexes it inside the agent, which may be a separate sidecar process. This
module records each such file in the library and attaches it to the chat, as
the library's own Add does, so it shows in Documents and in the chat and the
next chat can attach it. Documents attached in the chat's composer go in the
same way, so the user can see and remove what the chat reads.
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
from gaia.upload_names import reserve_copy_path
from gaia.utils.image_formats import IMAGE_EXTENSIONS

from .database import ChatDatabase
from .routers.documents import _sanitize_stem
from .utils import (
    ALLOWED_EXTENSIONS,
    compute_file_hash,
    compute_file_hash_from_fd,
    document_copies_dir,
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

    copies = document_copies_dir()
    copies.mkdir(parents=True, exist_ok=True)
    copy = reserve_copy_path(
        copies,
        _sanitize_stem(path.stem),
        path.suffix.lower(),
        taken=lambda p: db.path_held_by_other_content(str(p), file_hash),
    )
    try:
        shutil.copyfile(path, copy)
    except OSError:
        copy.unlink(missing_ok=True)
        raise
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
    stored_real = Path(os.path.realpath(stored))
    return not any(
        Path(os.path.realpath(owned)) in stored_real.parents
        for owned in (managed_documents_dir(), document_copies_dir())
    )


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


def _listed_as_document(path: Path) -> bool:
    """Whether a composer attachment goes into the library.

    Images are read by vision and recordings transcribed, not indexed.
    """
    from gaia.audio.media import MEDIA_EXTENSIONS

    suffix = path.suffix.lower()
    return (
        suffix in ALLOWED_EXTENSIONS
        and suffix not in IMAGE_EXTENSIONS
        and suffix not in MEDIA_EXTENSIONS
    )


def add_chat_uploads(
    db: ChatDatabase,
    session_id: str,
    uploads: Iterable[str],
    reattached: Iterable[str] = (),
) -> List[Dict[str, Any]]:
    """Put the documents attached in the chat's composer in the library, attached to it.

    Each upload goes in once: one the user takes off stays off, and the chat
    stops reading it (``ChatDatabase.removed_session_uploads``), unless it is
    in *reattached*, the uploads the user attached again in this message.
    Returns the library rows newly attached. An upload that can't be recorded
    stays readable by the chat, as before it was listed; the error is logged.
    """
    # The same file attached again reuses its upload path.
    reopened = db.removed_session_uploads(session_id) & set(reattached)
    known = db.session_upload_paths(session_id) - reopened
    added: List[Dict[str, Any]] = []
    for upload in uploads:
        path = Path(os.path.realpath(upload))
        if upload in known or not _listed_as_document(path):
            continue
        try:
            # The upload is GAIA's own copy already, inside the document roots.
            doc = _record_in_place(db, path, 0)
            db.attach_document(session_id, doc["id"])
            db.record_session_upload(session_id, upload, doc["file_hash"])
        except (HTTPException, OSError, sqlite3.Error) as exc:
            reason = _detail(exc) if isinstance(exc, HTTPException) else exc
            logger.error(
                "Attachment %s stays readable in chat %s but is not listed in "
                "its documents: %s",
                path,
                session_id[:8],
                reason,
                exc_info=True,
            )
            continue
        added.append(db.get_document(doc["id"]))
    return added


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
