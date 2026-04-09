# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Document management endpoints for GAIA Agent UI.

Handles document listing, upload-by-path, indexing status, cancellation,
deletion, folder indexing, and the document file monitor status.

The ``_index_document`` function is accessed through ``gaia.ui.server``
so that test patches applied to ``gaia.ui.server._index_document`` take
effect correctly.
"""

import asyncio
import hashlib
import logging
import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from ..database import ChatDatabase
from ..dependencies import get_db, get_indexing_tasks, get_upload_locks
from ..models import (
    DocumentListResponse,
    DocumentResponse,
    DocumentUploadRequest,
    IndexFolderRequest,
    IndexFolderResponse,
)
from ..utils import (
    ALLOWED_EXTENSIONS,
    LARGE_FILE_THRESHOLD,
    compute_file_hash,
    compute_file_hash_from_fd,
    doc_to_response,
    ensure_within_home,
    safe_open_document,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["documents"])

# ── Blob upload configuration ────────────────────────────────────────────────

# Server-managed location for documents uploaded as blobs (drag-and-drop in
# browser mode, or any flow that can't provide a real filesystem path).
# Files stored here are owned by the server and cleaned up on doc deletion.
MANAGED_DOCS_DIR = Path.home() / ".gaia" / "documents"

# Maximum size for a blob-uploaded document. Matches /api/files/upload for
# consistency; path-based uploads (where the user already owns the file)
# have no server-enforced cap.
MAX_DOCUMENT_UPLOAD_SIZE = 20 * 1024 * 1024  # 20 MB

# Streaming chunk size for multipart blob upload.
UPLOAD_CHUNK_SIZE = 64 * 1024  # 64 KB


def _server_mod():
    """Lazily resolve ``gaia.ui.server`` for patchable function access."""
    return sys.modules["gaia.ui.server"]


def _copy_fd_to_temp(fd: int, suffix: str) -> Path:
    """Copy content from an open fd to a temp file. Returns temp file path."""
    os.lseek(fd, 0, os.SEEK_SET)
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="gaia_upload_")
    try:
        while True:
            block = os.read(fd, 65536)
            if not block:
                break
            os.write(tmp_fd, block)
    finally:
        os.close(tmp_fd)
    return Path(tmp_path)


def _cleanup_temp(path: Path) -> None:
    """Remove a temp file, logging but not raising on failure."""
    try:
        path.unlink(missing_ok=True)
    except Exception as e:
        logger.warning("Failed to clean up temp file %s: %s", path, e)


def _is_server_owned(filepath: str) -> bool:
    """Return True if *filepath* lives inside the server-managed docs dir.

    Used by ``delete_document`` to decide whether to unlink the on-disk file.
    User-provided paths (from ``upload-path``) are NOT server-owned and must
    be left untouched on delete.
    """
    try:
        resolved = Path(filepath).resolve()
        managed = MANAGED_DOCS_DIR.resolve()
        return resolved.is_relative_to(managed)
    except (OSError, ValueError):
        return False


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/api/documents", response_model=DocumentListResponse)
async def list_documents(db: ChatDatabase = Depends(get_db)):
    """List all documents in the library."""
    docs = db.list_documents()
    total_size = sum(d.get("file_size", 0) for d in docs)
    total_chunks = sum(d.get("chunk_count", 0) for d in docs)

    return DocumentListResponse(
        documents=[doc_to_response(d) for d in docs],
        total=len(docs),
        total_size_bytes=total_size,
        total_chunks=total_chunks,
    )


@router.post("/api/documents/upload-path", response_model=DocumentResponse)
async def upload_by_path(
    request: DocumentUploadRequest,
    db: ChatDatabase = Depends(get_db),
    indexing_tasks: dict = Depends(get_indexing_tasks),
    upload_locks: dict = Depends(get_upload_locks),
):
    """Index a document by file path (for Electron/local use).

    Uses O_NOFOLLOW + fstat to prevent TOCTOU races. Copies file to a temp
    location before indexing so downstream RAG operations work on a stable
    copy. Per-file locking serializes concurrent uploads of the same path.

    Small files (<5 MB) are indexed synchronously. Larger files are
    indexed in the background so the UI stays responsive.
    """
    # Resolve once — used for both the lock key and safe_open validation
    resolved = Path(request.filepath).resolve()
    lock = upload_locks.setdefault(str(resolved), asyncio.Lock())

    async with lock:
        # Atomic open with O_NOFOLLOW + fstat validation (no TOCTOU window)
        with safe_open_document(request.filepath) as (fd, file_stat, safe_filepath):
            file_size = file_stat.st_size
            file_mtime = file_stat.st_mtime
            file_hash = compute_file_hash_from_fd(fd)
            # Copy to temp so all downstream ops use a process-controlled file
            temp_path = _copy_fd_to_temp(fd, safe_filepath.suffix)
        # fd is auto-closed by context manager here

        _index_document = _server_mod()._index_document

        try:
            if file_size <= LARGE_FILE_THRESHOLD:
                # Small file: index synchronously
                chunk_count = await _index_document(temp_path)
                doc = db.add_document(
                    filename=safe_filepath.name,
                    filepath=str(safe_filepath),
                    file_hash=file_hash,
                    file_size=file_size,
                    chunk_count=chunk_count,
                    file_mtime=file_mtime,
                )
                return doc_to_response(doc)

            # Large file: create placeholder and index in background
            doc = db.add_document(
                filename=safe_filepath.name,
                filepath=str(safe_filepath),
                file_hash=file_hash,
                file_size=file_size,
                chunk_count=0,
                file_mtime=file_mtime,
            )
            doc_id = doc["id"]
            db.update_document_status(doc_id, "indexing")

            # Transfer temp_path ownership to the background task
            bg_temp = temp_path
            temp_path = None  # prevent cleanup in finally below

            async def _background_index(
                doc_id: str, temp_file: Path, original_name: str
            ):
                try:
                    logger.info(
                        "Background indexing started for %s (%s)",
                        original_name,
                        doc_id,
                    )
                    chunk_count = await _index_document(temp_file)
                    if doc_id in indexing_tasks:
                        db.update_document_status(
                            doc_id, "complete", chunk_count=chunk_count
                        )
                        logger.info(
                            "Background indexing complete for %s: %d chunks",
                            original_name,
                            chunk_count,
                        )
                except asyncio.CancelledError:
                    db.update_document_status(doc_id, "cancelled")
                    logger.info("Background indexing cancelled for %s", original_name)
                except Exception as e:
                    db.update_document_status(doc_id, "failed")
                    logger.error(
                        "Background indexing failed for %s: %s",
                        original_name,
                        e,
                        exc_info=True,
                    )
                finally:
                    indexing_tasks.pop(doc_id, None)
                    _cleanup_temp(temp_file)

            task = asyncio.create_task(
                _background_index(doc_id, bg_temp, safe_filepath.name)
            )
            indexing_tasks[doc_id] = task
            doc["indexing_status"] = "indexing"
            return doc_to_response(doc)

        finally:
            if temp_path is not None:
                _cleanup_temp(temp_path)

    # Lock entries are intentionally never removed: asyncio.Lock.release() sets
    # _locked=False before the next waiter resumes, so lock.locked() would
    # return False even with a pending waiter, causing a pop that breaks
    # serialization. Lock objects are cheap and the key space is bounded by
    # the number of distinct file paths ever uploaded.


@router.post("/api/documents/upload", response_model=DocumentResponse)
async def upload_document_blob(
    file: UploadFile = File(...),
    db: ChatDatabase = Depends(get_db),
):
    """Index a document from an uploaded blob.

    This is the drag-and-drop / browser-mode entry point. Unlike
    ``upload-path`` (which takes a server-side filesystem path), this
    endpoint accepts the file content directly as multipart form data —
    required because browser File objects do not expose an absolute
    filesystem path.

    The blob is streamed to ``MANAGED_DOCS_DIR`` with an abort-on-overflow
    size check, deduplicated by SHA-256 content hash, then indexed via the
    standard RAG pipeline. Files are capped at ``MAX_DOCUMENT_UPLOAD_SIZE``.

    On any failure, partial / orphaned files are cleaned up.
    """
    # 1. Validate filename
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")
    if "\x00" in file.filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Strip any path components the client might have sent (defense in depth;
    # we never use this value as a filesystem path, but it's stored in the
    # db for display and returned to the frontend).
    display_name = Path(file.filename).name
    ext = Path(display_name).suffix.lower()

    # 2. Validate extension against the strict document set (no images etc.)
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"File type '{ext}' is not supported. "
                f"Allowed types: {sorted(ALLOWED_EXTENSIONS)}"
            ),
        )

    # 3. Ensure the managed directory exists
    try:
        MANAGED_DOCS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.error("Failed to create managed docs dir %s: %s", MANAGED_DOCS_DIR, e)
        raise HTTPException(
            status_code=500, detail="Failed to prepare document storage"
        )

    # 4. Stream-write to a .partial file, hashing as we go, aborting on
    # oversize. try/finally ensures the partial is cleaned up on any exit
    # path (including ClientDisconnect and CancelledError).
    partial_path: Path | None = MANAGED_DOCS_DIR / f"{uuid.uuid4()}.partial"
    final_path: Path | None = None
    hasher = hashlib.sha256()
    bytes_written = 0

    try:
        try:
            with open(partial_path, "wb") as out:
                while True:
                    chunk = await file.read(UPLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    bytes_written += len(chunk)
                    if bytes_written > MAX_DOCUMENT_UPLOAD_SIZE:
                        raise HTTPException(
                            status_code=413,
                            detail=(
                                f"File too large. Maximum size is "
                                f"{MAX_DOCUMENT_UPLOAD_SIZE // (1024 * 1024)} MB."
                            ),
                        )
                    hasher.update(chunk)
                    out.write(chunk)
        except HTTPException:
            raise
        except OSError as e:
            logger.error("Failed to write blob upload to %s: %s", partial_path, e)
            raise HTTPException(status_code=500, detail="Failed to save uploaded file")

        if bytes_written == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")

        file_hash = hasher.hexdigest()

        # 5. Dedup by hash — if this content is already indexed, return the
        # existing doc and discard the partial. Fast path for "user dropped
        # the same file twice."
        existing = db.get_document_by_hash(file_hash)
        if existing:
            logger.info(
                "Blob upload dedup: %s matches existing doc %s",
                display_name,
                existing.get("id"),
            )
            return doc_to_response(existing)

        # 6. Promote partial -> final (atomic rename). After this, the
        # partial_path variable is cleared so the finally block won't
        # unlink our now-final file.
        final_path = MANAGED_DOCS_DIR / f"{uuid.uuid4()}{ext}"
        try:
            os.replace(partial_path, final_path)
        except OSError as e:
            logger.error("Failed to promote %s -> %s: %s", partial_path, final_path, e)
            raise HTTPException(status_code=500, detail="Failed to save uploaded file")
        partial_path = None  # ownership transferred
    finally:
        if partial_path is not None:
            _cleanup_temp(partial_path)

    # 7. Index and record. Wrap both so an indexing OR db failure unlinks
    # the final file (no orphan rows/files). The stat() call is also inside
    # the try so a rare filesystem hiccup post-rename doesn't leak the file.
    _index_document = _server_mod()._index_document

    try:
        file_mtime = final_path.stat().st_mtime
        chunk_count = await _index_document(final_path)
        doc = db.add_document(
            filename=display_name,
            filepath=str(final_path),
            file_hash=file_hash,
            file_size=bytes_written,
            chunk_count=chunk_count,
            file_mtime=file_mtime,
        )
    except HTTPException:
        _cleanup_temp(final_path)
        raise
    except Exception as e:
        _cleanup_temp(final_path)
        logger.error(
            "Failed to index uploaded document %s: %s",
            display_name,
            e,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to index document: {e}"
        ) from e

    # 8. Concurrent-drop race guard: if a simultaneous upload of the same
    # new file beat us to add_document, it will return the OTHER doc
    # (dedup by hash) whose filepath points at the winning file — leaving
    # ours as an orphan on disk. Detect that and unlink.
    try:
        returned_path = Path(doc["filepath"]).resolve()
        if returned_path != final_path.resolve():
            logger.info(
                "Blob upload race: %s lost to %s, unlinking orphan",
                final_path,
                returned_path,
            )
            _cleanup_temp(final_path)
    except (OSError, KeyError):
        pass

    logger.info(
        "Blob upload indexed: %s (%d bytes, %d chunks)",
        display_name,
        bytes_written,
        doc.get("chunk_count", 0),
    )
    return doc_to_response(doc)


@router.get("/api/documents/monitor/status")
async def monitor_status(request: Request):
    """Get status of the document file monitor."""
    monitor = getattr(request.app.state, "document_monitor", None)
    if not monitor:
        return {"running": False, "interval_seconds": 0, "reindexing": []}
    return {
        "running": monitor.is_running,
        "interval_seconds": monitor._interval,
        "reindexing": list(monitor.reindexing_docs),
    }


@router.get("/api/documents/{doc_id}/status")
async def get_document_status(
    doc_id: str,
    db: ChatDatabase = Depends(get_db),
    indexing_tasks: dict = Depends(get_indexing_tasks),
):
    """Get current indexing status for a document."""
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    is_active = doc_id in indexing_tasks
    return {
        "id": doc_id,
        "indexing_status": doc.get("indexing_status", "complete"),
        "chunk_count": doc.get("chunk_count", 0),
        "is_active": is_active,
    }


@router.post("/api/documents/{doc_id}/cancel")
async def cancel_indexing(
    doc_id: str,
    db: ChatDatabase = Depends(get_db),
    indexing_tasks: dict = Depends(get_indexing_tasks),
):
    """Cancel a running background indexing task."""
    task = indexing_tasks.get(doc_id)
    if not task:
        raise HTTPException(
            status_code=404, detail="No active indexing task for this document"
        )
    task.cancel()
    db.update_document_status(doc_id, "cancelled")
    indexing_tasks.pop(doc_id, None)
    logger.info("Indexing cancelled by user for document %s", doc_id)
    return {"cancelled": True, "id": doc_id}


@router.delete("/api/documents/{doc_id}")
async def delete_document(doc_id: str, db: ChatDatabase = Depends(get_db)):
    """Remove a document from the library.

    For blob-uploaded documents (files the server owns under
    ``MANAGED_DOCS_DIR``), the on-disk file is also unlinked as a
    best-effort cleanup. User-owned files from ``upload-path`` are left
    alone.
    """
    # Fetch first so we know the filepath even after deletion. This also
    # distinguishes "not found" from "found but db delete race lost."
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if not db.delete_document(doc_id):
        # Row vanished between fetch and delete (concurrent delete).
        # Treat as not found — idempotent for the caller.
        raise HTTPException(status_code=404, detail="Document not found")

    # Best-effort filesystem cleanup for server-owned files.
    filepath = doc.get("filepath")
    if filepath and _is_server_owned(filepath):
        _cleanup_temp(Path(filepath))

    return {"deleted": True}


@router.post("/api/documents/index-folder", response_model=IndexFolderResponse)
async def index_folder(request: IndexFolderRequest, db: ChatDatabase = Depends(get_db)):
    """Index all supported documents in a folder.

    Scans the given folder for files with extensions in
    ALLOWED_EXTENSIONS and indexes each one using the RAG pipeline.
    Indexing runs in a thread-pool executor to avoid blocking the
    event loop.

    Args:
        request: Contains folder_path and recursive flag.
    """
    folder_path = request.folder_path

    # Security: reject null bytes
    if "\x00" in folder_path:
        raise HTTPException(status_code=400, detail="Invalid folder path")

    raw_folder = Path(folder_path)

    resolved = raw_folder.resolve(strict=False)

    # Security: restrict folder indexing to user's home directory FIRST,
    # before ANY filesystem operations (is_symlink/exists/is_dir can throw
    # PermissionError on protected OS paths).
    ensure_within_home(resolved)

    # Check symlink after home restriction
    try:
        if raw_folder.is_symlink():
            raise HTTPException(
                status_code=400, detail="Symbolic links are not supported"
            )
    except PermissionError:
        raise HTTPException(status_code=403, detail="Access denied")

    if not resolved.exists():
        raise HTTPException(status_code=404, detail="Folder not found")

    if not resolved.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")

    # Collect all candidate files
    candidate_files: List[Path] = []
    try:
        pattern_iter = resolved.rglob("*") if request.recursive else resolved.iterdir()
        for item in pattern_iter:
            if item.is_symlink():
                continue
            if item.is_file() and item.suffix.lower() in ALLOWED_EXTENSIONS:
                candidate_files.append(item)
    except PermissionError:
        raise HTTPException(
            status_code=403,
            detail="Permission denied while scanning folder",
        )

    if not candidate_files:
        return IndexFolderResponse(indexed=0, failed=0, documents=[], errors=[])

    logger.info(
        "Indexing %d files from %s (recursive=%s)",
        len(candidate_files),
        resolved,
        request.recursive,
    )

    _index_document = _server_mod()._index_document
    indexed_docs: List[DocumentResponse] = []
    errors: List[str] = []

    for filepath in candidate_files:
        try:
            file_hash = await asyncio.get_running_loop().run_in_executor(
                None, compute_file_hash, filepath
            )
            file_stat = filepath.stat()
            file_size = file_stat.st_size
            file_mtime = file_stat.st_mtime

            chunk_count = await _index_document(filepath)

            doc = db.add_document(
                filename=filepath.name,
                filepath=str(filepath),
                file_hash=file_hash,
                file_size=file_size,
                chunk_count=chunk_count,
                file_mtime=file_mtime,
            )
            indexed_docs.append(doc_to_response(doc))
        except Exception as e:
            error_msg = f"{filepath.name}: {e}"
            logger.warning("Failed to index %s: %s", filepath, e)
            errors.append(error_msg)

    logger.info(
        "Folder indexing complete: %d indexed, %d failed",
        len(indexed_docs),
        len(errors),
    )

    return IndexFolderResponse(
        indexed=len(indexed_docs),
        failed=len(errors),
        documents=indexed_docs,
        errors=errors,
    )
