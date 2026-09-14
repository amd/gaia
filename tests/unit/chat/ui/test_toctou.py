# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for TOCTOU security fix in document upload endpoint (Issue #448).

Tests safe_open_document() primitives and upload_by_path endpoint protection.
"""

import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.testclient import TestClient

from gaia.ui.utils import (
    DOCUMENT_ROOTS_ENV,
    compute_file_hash_from_fd,
    safe_open_document,
)


@pytest.fixture
def home_tmp_dir():
    """Create a temporary directory under $HOME for tests that need ensure_within_home."""
    d = Path.home() / ".gaia_test_toctou"
    d.mkdir(exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


class TestSafeOpenDocument:
    """Tests for safe_open_document() TOCTOU-safe context manager."""

    def test_safe_open_rejects_symlink(self, home_tmp_dir):
        """O_NOFOLLOW must reject symlinks at kernel level."""
        real_file = home_tmp_dir / "real.txt"
        real_file.write_text("content")
        symlink = home_tmp_dir / "link.txt"
        try:
            symlink.symlink_to(real_file)
        except OSError:
            pytest.skip("Symlink creation requires elevated privileges on Windows")
        with pytest.raises(HTTPException) as exc_info:
            with safe_open_document(str(symlink)):
                pass
        assert exc_info.value.status_code == 400
        assert (
            "symlink" in exc_info.value.detail.lower()
            or "symbolic" in exc_info.value.detail.lower()
        )

    def test_safe_open_rejects_symlinked_directory_escape(self, home_tmp_dir):
        """An intermediate symlinked dir inside home must not open files outside.

        The lexical (abspath) home check passes for
        ``~/.gaia_test_toctou/esc/secret.txt`` while the physical path lives
        outside home — the realpath containment check must reject it with 403.
        O_NOFOLLOW alone cannot catch this: it only guards the final component.
        """
        import tempfile

        outside_dir = Path(tempfile.mkdtemp(prefix="gaia_toctou_outside_"))
        try:
            secret = outside_dir / "secret.txt"
            secret.write_text("outside-home content")
            escape_link = home_tmp_dir / "esc"
            try:
                escape_link.symlink_to(outside_dir, target_is_directory=True)
            except OSError:
                pytest.skip("Symlink creation requires elevated privileges on Windows")
            attack_path = escape_link / "secret.txt"
            with pytest.raises(HTTPException) as exc_info:
                with safe_open_document(str(attack_path)):
                    pass
            assert exc_info.value.status_code == 403
        finally:
            shutil.rmtree(outside_dir, ignore_errors=True)

    def test_safe_open_rejects_missing_file(self, home_tmp_dir):
        """Non-existent file must return 404."""
        missing = home_tmp_dir / "nonexistent.txt"
        with pytest.raises(HTTPException) as exc_info:
            with safe_open_document(str(missing)):
                pass
        assert exc_info.value.status_code == 404

    def test_safe_open_rejects_outside_home(self):
        """Path outside home directory must return 403."""
        with pytest.raises(HTTPException) as exc_info:
            with safe_open_document("/etc/passwd"):
                pass
        assert exc_info.value.status_code == 403

    def test_safe_open_rejects_directory(self, home_tmp_dir):
        """Directory path must return 400 (not a regular file)."""
        d = home_tmp_dir / "mydir.txt"
        d.mkdir(exist_ok=True)
        with pytest.raises(HTTPException) as exc_info:
            with safe_open_document(str(d)):
                pass
        # Could be 400 (not a regular file) or 400 (O_NOFOLLOW on dir varies by OS)
        assert exc_info.value.status_code in (400, 404)

    def test_safe_open_rejects_disallowed_extension(self, home_tmp_dir):
        """Disallowed extension must return 400."""
        exe = home_tmp_dir / "evil.exe"
        exe.write_bytes(b"MZ")
        with pytest.raises(HTTPException) as exc_info:
            with safe_open_document(str(exe)):
                pass
        assert exc_info.value.status_code == 400

    def test_safe_open_returns_valid_fd_and_stat(self, home_tmp_dir):
        """Valid file must yield readable fd with correct stat."""
        f = home_tmp_dir / "doc.txt"
        content = b"hello world"
        f.write_bytes(content)
        with safe_open_document(str(f)) as (fd, st, resolved):
            data = os.read(fd, 1024)
            assert data == content
            assert st.st_size == len(content)
            assert resolved == f.resolve()

    def test_safe_open_allows_declared_root_outside_home(self, tmp_path, monkeypatch):
        """A root declared via GAIA_DOCUMENT_ROOTS is readable (#3585).

        This is the eval-corpus case: the checkout lives outside the service
        account's home, so without a declared root every indexed document 403s.
        """
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        doc = corpus / "acme_q3_report.md"
        doc.write_bytes(b"Q3 revenue was $14.2 million.")
        monkeypatch.setenv(DOCUMENT_ROOTS_ENV, str(corpus))
        with safe_open_document(str(doc)) as (fd, _st, resolved):
            assert os.read(fd, 1024) == b"Q3 revenue was $14.2 million."
            assert resolved == doc.resolve()

    def test_declared_root_does_not_open_its_parent(self, tmp_path, monkeypatch):
        """Declaring a root widens access to that root only, not above it."""
        # Home is always a root, and on Windows the temp dir sits INSIDE the
        # profile — so without pinning home elsewhere `outside` is allowed on
        # home's account and this test passes without testing anything.
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        outside = tmp_path / "secrets.txt"
        outside.write_bytes(b"do not read")
        monkeypatch.setenv(DOCUMENT_ROOTS_ENV, str(corpus))
        with pytest.raises(HTTPException) as exc_info:
            with safe_open_document(str(outside)):
                pass
        assert exc_info.value.status_code == 403

    def test_declared_root_still_rejects_symlink_escape(self, tmp_path, monkeypatch):
        """realpath containment applies to declared roots too, not just home."""
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        secret = outside_dir / "secret.txt"
        secret.write_text("outside-root content")
        escape_link = corpus / "esc"
        try:
            escape_link.symlink_to(outside_dir, target_is_directory=True)
        except OSError:
            pytest.skip("Symlink creation requires elevated privileges on Windows")
        monkeypatch.setenv(DOCUMENT_ROOTS_ENV, str(corpus))
        with pytest.raises(HTTPException) as exc_info:
            with safe_open_document(str(escape_link / "secret.txt")):
                pass
        assert exc_info.value.status_code == 403

    def test_misconfigured_root_fails_loudly(self, monkeypatch, home_tmp_dir):
        """A bogus GAIA_DOCUMENT_ROOTS is a 500, never a silent home-only run."""
        f = home_tmp_dir / "doc.txt"
        f.write_bytes(b"content")
        monkeypatch.setenv(DOCUMENT_ROOTS_ENV, "not/absolute")
        with pytest.raises(HTTPException) as exc_info:
            with safe_open_document(str(f)):
                pass
        assert exc_info.value.status_code == 500


class TestComputeFileHashFromFd:
    """Tests for compute_file_hash_from_fd()."""

    def test_compute_hash_from_fd_matches_path_hash(self, home_tmp_dir):
        """fd-based hash must equal path-based hash."""
        from gaia.ui.utils import compute_file_hash

        f = home_tmp_dir / "data.txt"
        f.write_bytes(b"test data for hashing " * 100)

        path_hash = compute_file_hash(f)

        with safe_open_document(str(f)) as (fd, st, resolved):
            fd_hash = compute_file_hash_from_fd(fd)

        assert fd_hash == path_hash


class TestUploadByPathTOCTOU:
    """Tests for TOCTOU protection in the upload_by_path endpoint."""

    @pytest.fixture
    def app(self):
        from gaia.ui.server import create_app

        return create_app(db_path=":memory:")

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_upload_copies_to_temp(self, home_tmp_dir, client):
        """_index_document must receive a temp path, not the original path."""
        doc_file = home_tmp_dir / "test.txt"
        doc_file.write_text("hello world")
        received_paths = []

        async def mock_index(path):
            received_paths.append(str(path))
            return 1

        with patch("gaia.ui.server._index_document", mock_index):
            resp = client.post(
                "/api/documents/upload-path",
                json={"filepath": str(doc_file)},
            )

        assert resp.status_code == 200
        assert len(received_paths) == 1
        # The path passed to _index_document should NOT be the original path
        assert received_paths[0] != str(doc_file)
        # It should be a temp file (gaia_upload_ prefix)
        assert "gaia_upload_" in received_paths[0]

    def test_upload_cleans_up_temp_on_success(self, home_tmp_dir, client):
        """Temp file must be removed after successful indexing."""
        doc_file = home_tmp_dir / "test.txt"
        doc_file.write_text("hello world")
        temp_paths = []

        async def mock_index(path):
            temp_paths.append(Path(str(path)))
            return 1

        with patch("gaia.ui.server._index_document", mock_index):
            resp = client.post(
                "/api/documents/upload-path",
                json={"filepath": str(doc_file)},
            )

        assert resp.status_code == 200
        assert len(temp_paths) == 1
        # Temp file should be cleaned up
        assert not temp_paths[0].exists()

    def test_upload_cleans_up_temp_on_failure(self, home_tmp_dir, client):
        """Temp file must be removed even when indexing fails."""
        doc_file = home_tmp_dir / "test.txt"
        doc_file.write_text("hello world")
        temp_paths = []

        async def mock_index_fail(path):
            temp_paths.append(Path(str(path)))
            raise RuntimeError("indexing failed")

        with patch("gaia.ui.server._index_document", mock_index_fail):
            resp = client.post(
                "/api/documents/upload-path",
                json={"filepath": str(doc_file)},
            )

        # Indexing failure now returns 200 with status="failed" (not 500)
        assert resp.status_code == 200
        assert resp.json()["indexing_status"] == "failed"
        # Temp file should still be cleaned up
        if temp_paths:
            assert not temp_paths[0].exists()

    def test_upload_outside_home_rejected(self, client):
        """Path outside home directory must be rejected with 403."""
        resp = client.post(
            "/api/documents/upload-path",
            json={"filepath": "/etc/passwd"},
        )
        assert resp.status_code == 403

    def test_upload_missing_file_returns_404(self, home_tmp_dir, client):
        """Missing file must return 404."""
        resp = client.post(
            "/api/documents/upload-path",
            json={"filepath": str(home_tmp_dir / "nonexistent.txt")},
        )
        assert resp.status_code == 404
