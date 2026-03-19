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
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request

from ..database import ChatDatabase
from ..dependencies import get_db, get_indexing_tasks
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


def _server_mod():
    """Lazily resolve ``gaia.ui.server`` for patchable function access."""
    return sys.modules["gaia.ui.server"]


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
):
    """Index a document by file path (for Electron/local use).

    Small files (<5 MB) are indexed synchronously. Larger files are
    indexed in the background so the UI stays responsive; the returned
    document will have ``indexing_status='indexing'`` and the frontend
    can poll ``GET /api/documents/{id}/status`` for progress.
    """
    # Use safe_open_document for TOCTOU-safe validation (rejects symlinks,
    # enforces home-directory confinement, checks extension).
    # Then copy to a temp file so indexing reads a stable snapshot.
    with safe_open_document(request.filepath) as (fd, file_stat, safe_filepath):
        file_hash = compute_file_hash_from_fd(fd)
        file_size = file_stat.st_size
        file_mtime = file_stat.st_mtime

        suffix = safe_filepath.suffix
        tmp_fd, tmp_path_str = tempfile.mkstemp(prefix="gaia_upload_", suffix=suffix)
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            with os.fdopen(tmp_fd, "wb") as tmp_f:
                while True:
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        break
                    tmp_f.write(chunk)
        except Exception:
            os.unlink(tmp_path_str)
            raise

    tmp_path = Path(tmp_path_str)

    _index_document = _server_mod()._index_document

    if file_size <= LARGE_FILE_THRESHOLD:
        # Small file: index synchronously (fast)
        try:
            chunk_count = await _index_document(tmp_path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
        doc = db.add_document(
            filename=safe_filepath.name,
            filepath=str(safe_filepath),
            file_hash=file_hash,
            file_size=file_size,
            chunk_count=chunk_count,
            file_mtime=file_mtime,
        )
        return doc_to_response(doc)

    # Large file: create a placeholder record and index in background
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

    async def _background_index(doc_id: str, filepath: Path, tmp: Path):
        """Run indexing in background, updating DB status on completion."""
        try:
            logger.info(
                "Background indexing started for %s (%s)", filepath.name, doc_id
            )
            chunk_count = await _index_document(tmp)
            # Check if task was cancelled while we were indexing
            if doc_id in indexing_tasks:
                db.update_document_status(doc_id, "complete", chunk_count=chunk_count)
                logger.info(
                    "Background indexing complete for %s: %d chunks",
                    filepath.name,
                    chunk_count,
                )
        except asyncio.CancelledError:
            db.update_document_status(doc_id, "cancelled")
            logger.info("Background indexing cancelled for %s", filepath.name)
        except Exception as e:
            db.update_document_status(doc_id, "failed")
            logger.error(
                "Background indexing failed for %s: %s",
                filepath.name,
                e,
                exc_info=True,
            )
        finally:
            indexing_tasks.pop(doc_id, None)
            tmp.unlink(missing_ok=True)

    task = asyncio.create_task(_background_index(doc_id, safe_filepath, tmp_path))
    indexing_tasks[doc_id] = task

    # Return immediately with indexing_status='indexing'
    doc["indexing_status"] = "indexing"
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
    """Remove a document from the library."""
    if not db.delete_document(doc_id):
        raise HTTPException(status_code=404, detail="Document not found")
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
