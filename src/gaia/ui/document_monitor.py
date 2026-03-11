# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Document file monitor for automatic re-indexing.

Periodically checks indexed documents for file changes (modification time
and content hash) and triggers re-indexing when files are modified on disk.
"""

import asyncio
import hashlib
import logging
import os
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable, Awaitable, Dict, Optional, Set

from .database import ChatDatabase

logger = logging.getLogger(__name__)

# Default polling interval in seconds
DEFAULT_INTERVAL = 30.0


def _compute_file_hash(filepath: Path) -> str:
    """Compute SHA-256 hash of file contents."""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for block in iter(lambda: f.read(8192), b""):
            sha256.update(block)
    return sha256.hexdigest()


def _get_file_info(filepath: str) -> Optional[tuple]:
    """Get file mtime and size, or None if missing/inaccessible.

    Returns:
        Tuple of (mtime_float, size_int) or None.
    """
    try:
        st = os.stat(filepath)
        return (st.st_mtime, st.st_size)
    except OSError:
        return None


class DocumentMonitor:
    """Periodically checks indexed documents for file changes and re-indexes.

    Uses a lightweight polling approach: checks file modification times
    every `interval` seconds. Only computes the full SHA-256 hash when
    the mtime has changed, minimizing disk I/O.

    Handles:
    - File modified on disk → re-index with new hash
    - File touched without content change → update stored mtime only
    - File deleted → log warning (does not remove from library)
    - Concurrent re-indexing → skips docs already being re-indexed
    """

    def __init__(
        self,
        db: ChatDatabase,
        index_fn: Callable[[Path], Awaitable[int]],
        interval: float = DEFAULT_INTERVAL,
        active_tasks: Optional[Dict[str, Any]] = None,
    ):
        """Initialize the document monitor.

        Args:
            db: Database instance for reading/updating document records.
            index_fn: Async function that indexes a file and returns chunk count.
                      Signature: async def index_fn(filepath: Path) -> int
            interval: Polling interval in seconds (default: 30).
            active_tasks: Dict of currently active indexing tasks (doc_id → Task).
                          Used to avoid re-indexing docs that are being indexed
                          by user action.
        """
        self._db = db
        self._index_fn = index_fn
        self._interval = interval
        self._active_tasks = active_tasks or {}
        self._task: Optional[asyncio.Task] = None
        self._reindexing: Set[str] = set()  # doc IDs currently being re-indexed
        self._stop_event = asyncio.Event()
        self._check_count = 0
        self._reindex_count = 0

    async def start(self) -> None:
        """Start the monitor loop."""
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "Document monitor started (interval=%ds)", self._interval
        )

    async def stop(self) -> None:
        """Stop the monitor loop gracefully."""
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info(
            "Document monitor stopped (checked %d times, re-indexed %d docs)",
            self._check_count,
            self._reindex_count,
        )

    @property
    def is_running(self) -> bool:
        """True if the monitor loop is running."""
        return self._task is not None and not self._task.done()

    @property
    def reindexing_docs(self) -> Set[str]:
        """Set of document IDs currently being re-indexed."""
        return self._reindexing.copy()

    async def _run_loop(self) -> None:
        """Main polling loop: sleep, check documents, repeat."""
        # Initial delay to let the server finish starting up
        await asyncio.sleep(5.0)

        while not self._stop_event.is_set():
            try:
                await self._check_documents()
                self._check_count += 1
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Document monitor error during check")

            # Wait for interval or until stopped
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._interval
                )
                break  # stop_event was set
            except asyncio.TimeoutError:
                pass  # Normal timeout, continue loop

    async def _check_documents(self) -> None:
        """Check all indexed documents for file changes."""
        docs = self._db.list_documents()
        loop = asyncio.get_running_loop()

        for doc in docs:
            doc_id = doc["id"]
            filepath = doc.get("filepath")
            status = doc.get("indexing_status", "complete")

            # Skip docs that are not in a stable state
            if status not in ("complete", "missing"):
                continue

            if not filepath or doc_id in self._reindexing:
                continue

            # Skip docs currently being indexed by user action
            if doc_id in self._active_tasks:
                continue

            stored_mtime = doc.get("file_mtime")
            stored_hash = doc.get("file_hash")

            # Check file existence and mtime (non-blocking via executor)
            file_info = await loop.run_in_executor(
                None, _get_file_info, filepath
            )

            if file_info is None:
                # File deleted or inaccessible
                if status != "missing":
                    logger.warning(
                        "Indexed file no longer accessible: %s (doc_id=%s)",
                        filepath,
                        doc_id,
                    )
                continue

            current_mtime, current_size = file_info

            # Fast path: mtime unchanged → skip hash computation
            if stored_mtime is not None and current_mtime == stored_mtime:
                continue

            # Mtime changed: compute hash to confirm actual content change
            try:
                new_hash = await loop.run_in_executor(
                    None, _compute_file_hash, Path(filepath)
                )
            except Exception as e:
                logger.warning(
                    "Failed to hash file %s: %s", filepath, e
                )
                continue

            if new_hash == stored_hash:
                # Mtime changed but content identical (e.g., file was touched)
                # Update stored mtime to avoid re-checking next cycle
                logger.debug(
                    "File touched (mtime changed, hash same): %s", filepath
                )
                self._db.update_document_mtime(doc_id, current_mtime)
                continue

            # Content actually changed → re-index
            logger.info(
                "File content changed, re-indexing: %s (doc_id=%s)",
                filepath,
                doc_id,
            )
            await self._reindex_document(
                doc, new_hash, current_mtime, current_size
            )

    async def _reindex_document(
        self,
        doc: Dict[str, Any],
        new_hash: str,
        mtime: float,
        size: int,
    ) -> None:
        """Re-index a single document whose file content has changed."""
        doc_id = doc["id"]
        filepath = doc["filepath"]
        self._reindexing.add(doc_id)
        self._db.update_document_status(doc_id, "indexing")

        try:
            chunk_count = await self._index_fn(Path(filepath))
            self._db.reindex_document(
                doc_id, new_hash, mtime, chunk_count, size
            )
            self._reindex_count += 1
            logger.info(
                "Re-indexed %s: %d chunks (doc_id=%s)",
                filepath,
                chunk_count,
                doc_id,
            )
        except Exception:
            self._db.update_document_status(doc_id, "failed")
            logger.exception("Re-indexing failed for %s", filepath)
        finally:
            self._reindexing.discard(doc_id)
