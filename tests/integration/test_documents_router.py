# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Integration tests for the GAIA Agent UI documents router.

Focused on the blob upload endpoint added to fix issue #728, plus the
related delete-cleanup logic.

The tests patch ``gaia.ui.server._index_document`` to return a fixed chunk
count rather than running real RAG indexing, so they run fast and do not
depend on Lemonade or any LLM backend.
"""

import logging
import shutil
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from gaia.ui.routers.documents import MANAGED_DOCS_DIR, MAX_DOCUMENT_UPLOAD_SIZE
from gaia.ui.server import create_app

logger = logging.getLogger(__name__)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def app():
    """Create FastAPI app with in-memory database."""
    return create_app(db_path=":memory:")


@pytest.fixture
def client(app):
    """Create test client for the app."""
    return TestClient(app)


@pytest.fixture
def mock_index_document():
    """Patch the RAG index function with an AsyncMock returning 7 chunks."""
    with patch("gaia.ui.server._index_document", new=AsyncMock(return_value=7)) as m:
        yield m


@pytest.fixture
def managed_docs_sandbox():
    """Isolate the managed docs dir for the duration of a test.

    The production ``MANAGED_DOCS_DIR`` is ``~/.gaia/documents``. We don't
    want tests dropping files into the user's real home dir, so we point
    the module-level constant at a throwaway subdirectory inside
    ``~/.gaia/test_documents_router/`` and clean it up afterwards.
    """
    sandbox = Path.home() / ".gaia" / "test_documents_router" / str(uuid.uuid4())[:8]
    sandbox.mkdir(parents=True, exist_ok=True)
    with patch("gaia.ui.routers.documents.MANAGED_DOCS_DIR", sandbox):
        yield sandbox
    try:
        shutil.rmtree(str(sandbox))
    except OSError as exc:
        logger.warning("Failed to clean up sandbox %s: %s", sandbox, exc)


@pytest.fixture
def home_tmp_dir():
    """Temp dir inside the user's home (needed for path-based upload tests)."""
    tmp_dir = Path.home() / ".gaia" / "test_documents_router" / str(uuid.uuid4())[:8]
    tmp_dir.mkdir(parents=True, exist_ok=True)
    yield tmp_dir
    try:
        shutil.rmtree(str(tmp_dir))
    except OSError as exc:
        logger.warning("Failed to clean up %s: %s", tmp_dir, exc)


# ── Blob upload happy paths ──────────────────────────────────────────────────


def test_upload_blob_happy_path(client, mock_index_document, managed_docs_sandbox):
    """Dropping a .txt file returns a DocumentResponse and writes a real file."""
    content = b"Hello, this is a test document.\n"
    response = client.post(
        "/api/documents/upload",
        files={"file": ("report.txt", content, "text/plain")},
    )
    assert response.status_code == 200, response.text

    doc = response.json()
    assert doc["filename"] == "report.txt"
    assert doc["file_size"] == len(content)
    assert doc["chunk_count"] == 7  # from the mocked _index_document

    # The on-disk file should exist inside the sandbox dir
    filepath = Path(doc["filepath"])
    assert filepath.exists()
    assert filepath.parent == managed_docs_sandbox
    assert filepath.read_bytes() == content

    # Indexing was called exactly once with the final path
    mock_index_document.assert_awaited_once()
    called_arg = mock_index_document.await_args.args[0]
    assert Path(called_arg) == filepath


def test_upload_blob_dedup_returns_existing(
    client, mock_index_document, managed_docs_sandbox
):
    """Uploading identical bytes twice returns the same doc, files on disk = 1."""
    content = b"Dedup me.\n"

    r1 = client.post(
        "/api/documents/upload",
        files={"file": ("first.txt", content, "text/plain")},
    )
    assert r1.status_code == 200
    doc1 = r1.json()

    r2 = client.post(
        "/api/documents/upload",
        files={"file": ("second.txt", content, "text/plain")},
    )
    assert r2.status_code == 200
    doc2 = r2.json()

    # Same doc returned (dedup by hash — the id is stable)
    assert doc1["id"] == doc2["id"]

    # Indexing only ran once — the second call short-circuited
    mock_index_document.assert_awaited_once()

    # Only the winning file should exist in the sandbox (no orphan partials
    # or duplicates)
    files_on_disk = [p for p in managed_docs_sandbox.iterdir() if p.is_file()]
    assert len(files_on_disk) == 1
    assert files_on_disk[0] == Path(doc1["filepath"])


# ── Validation / error paths ─────────────────────────────────────────────────


def test_upload_blob_empty_returns_400(
    client, mock_index_document, managed_docs_sandbox
):
    """Empty file → 400 and no file written."""
    response = client.post(
        "/api/documents/upload",
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()

    # No orphan files left behind
    assert not any(managed_docs_sandbox.iterdir())
    mock_index_document.assert_not_called()


def test_upload_blob_bad_extension_returns_400(
    client, mock_index_document, managed_docs_sandbox
):
    """Disallowed extension → 400 before any bytes are written."""
    response = client.post(
        "/api/documents/upload",
        files={"file": ("malware.exe", b"MZ\x00\x00", "application/octet-stream")},
    )
    assert response.status_code == 400
    assert ".exe" in response.json()["detail"]
    mock_index_document.assert_not_called()
    assert not any(managed_docs_sandbox.iterdir())


def test_upload_blob_oversized_returns_413(
    client, mock_index_document, managed_docs_sandbox
):
    """File > MAX_DOCUMENT_UPLOAD_SIZE → 413 and partial file cleaned up."""
    # 1 byte over the limit
    oversized = b"x" * (MAX_DOCUMENT_UPLOAD_SIZE + 1)
    response = client.post(
        "/api/documents/upload",
        files={"file": ("huge.txt", oversized, "text/plain")},
    )
    assert response.status_code == 413
    assert "too large" in response.json()["detail"].lower()

    # Critical: no .partial files left on disk after the abort
    leftover = list(managed_docs_sandbox.iterdir())
    assert leftover == [], f"Orphaned files after oversized upload: {leftover}"
    mock_index_document.assert_not_called()


def test_upload_blob_indexing_failure_cleans_up_file(client, managed_docs_sandbox):
    """If _index_document raises, the final file must be unlinked."""
    with patch(
        "gaia.ui.server._index_document",
        new=AsyncMock(side_effect=RuntimeError("RAG exploded")),
    ):
        response = client.post(
            "/api/documents/upload",
            files={"file": ("doomed.txt", b"will fail", "text/plain")},
        )
    assert response.status_code == 500

    # File should NOT be left on disk
    leftover = list(managed_docs_sandbox.iterdir())
    assert leftover == [], f"Orphaned files after indexing failure: {leftover}"


# ── Delete endpoint cleanup ──────────────────────────────────────────────────


def test_delete_blob_document_unlinks_file(
    client, mock_index_document, managed_docs_sandbox
):
    """DELETE on a server-owned doc unlinks the on-disk file."""
    # Upload
    r = client.post(
        "/api/documents/upload",
        files={"file": ("delete_me.txt", b"temporary content", "text/plain")},
    )
    assert r.status_code == 200
    doc = r.json()
    filepath = Path(doc["filepath"])
    assert filepath.exists()

    # Delete
    r = client.delete(f"/api/documents/{doc['id']}")
    assert r.status_code == 200
    assert r.json() == {"deleted": True}

    # File is gone
    assert not filepath.exists()


def test_delete_path_document_preserves_user_file(
    client, mock_index_document, managed_docs_sandbox, home_tmp_dir
):
    """DELETE on a user-owned (path-uploaded) doc leaves the file alone."""
    # Create a real file in the user's home temp dir and upload by path
    user_file = home_tmp_dir / "user_report.txt"
    user_file.write_text("User-owned document.\n", encoding="utf-8")

    r = client.post(
        "/api/documents/upload-path",
        json={"filepath": str(user_file)},
    )
    assert r.status_code == 200, r.text
    doc = r.json()
    assert Path(doc["filepath"]) == user_file

    # Delete via the API
    r = client.delete(f"/api/documents/{doc['id']}")
    assert r.status_code == 200

    # User's file must still exist — we do NOT own it
    assert user_file.exists(), "User-owned file was incorrectly deleted"


def test_delete_nonexistent_returns_404(client):
    r = client.delete("/api/documents/does-not-exist")
    assert r.status_code == 404
