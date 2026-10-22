# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""What the Document library's path box tells a user when indexing goes wrong.

A folder path, a missing file, a file outside the readable folders and a file
that can't be parsed each get their own plain answer; none shows a Python repr.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from gaia.ui._chat_helpers import UnreadableDocumentError
from gaia.ui.utils import DOCUMENT_ROOTS_ENV


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fake home as the only readable root, with GAIA's state in tmp too."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    monkeypatch.delenv(DOCUMENT_ROOTS_ENV, raising=False)
    return fake_home


@pytest.fixture
def client(home):
    from gaia.ui.server import create_app

    return TestClient(create_app(db_path=":memory:"))


def _upload(client, path):
    return client.post("/api/documents/upload-path", json={"filepath": str(path)})


class TestFolderPath:
    def test_folder_is_named_as_a_folder_not_a_file_type(self, home, client):
        folder = home / "documents"
        folder.mkdir()

        resp = _upload(client, folder)

        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["status"] == "is_folder"
        assert "is a folder" in detail["message"]
        assert "File type not allowed" not in detail["message"]

    def test_folder_indexing_counts_added_and_skipped_files(self, home, client):
        folder = home / "documents"
        folder.mkdir()
        (folder / "notes.md").write_text("# Notes")
        (folder / "setup.exe").write_bytes(b"MZ")
        (folder / "photo.heic").write_bytes(b"\x00")
        (folder / "nested").mkdir()
        (folder / "nested" / "deep.md").write_text("below the top level")

        async def fake_index(_path):
            return 2

        with patch("gaia.ui.server._index_document", fake_index):
            resp = client.post(
                "/api/documents/index-folder",
                json={"folder_path": str(folder), "recursive": False},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["indexed"] == 1
        assert body["skipped"] == 2
        assert body["failed"] == 0
        assert [d["filename"] for d in body["documents"]] == ["notes.md"]

    def test_tilde_folder_path_reaches_folder_indexing(self, home, client, monkeypatch):
        """``~`` is the user's home, not a folder named ``~`` in the server's cwd."""
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))
        folder = home / "documents"
        folder.mkdir()
        (folder / "notes.md").write_text("# Notes")

        resp = _upload(client, "~/documents")
        assert resp.status_code == 400
        assert resp.json()["detail"]["status"] == "is_folder"

        async def fake_index(_path):
            return 1

        with patch("gaia.ui.server._index_document", fake_index):
            resp = client.post(
                "/api/documents/index-folder",
                json={"folder_path": "~/documents", "recursive": False},
            )
        assert resp.status_code == 200
        assert resp.json()["indexed"] == 1


class TestMissingAndOutOfScope:
    def test_missing_file_inside_the_roots_says_it_does_not_exist(self, home, client):
        resp = _upload(client, home / "nope" / "missing.pdf")

        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert "there is no file at" in detail
        assert "Check the path" in detail
        assert DOCUMENT_ROOTS_ENV not in detail

    def test_existing_file_outside_the_roots_explains_the_roots(
        self, tmp_path, home, client
    ):
        outside = tmp_path / "elsewhere" / "report.pdf"
        outside.parent.mkdir()
        outside.write_bytes(b"%PDF-1.4")

        resp = _upload(client, outside)

        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert str(home) in detail
        assert DOCUMENT_ROOTS_ENV in detail

    def test_outside_the_roots_answers_the_same_whether_or_not_it_exists(
        self, tmp_path, home, client
    ):
        """No existence oracle outside the roots: a missing file looks the same."""
        present = tmp_path / "elsewhere" / "report.pdf"
        present.parent.mkdir()
        present.write_bytes(b"%PDF-1.4")
        missing = tmp_path / "elsewhere" / "missing.pdf"

        got_present = _upload(client, present)
        got_missing = _upload(client, missing)

        assert got_present.status_code == got_missing.status_code == 403
        assert got_missing.json()["detail"] == got_present.json()["detail"].replace(
            "report.pdf", "missing.pdf"
        )
        assert "not found" not in got_missing.json()["detail"].lower()


class TestUnsupportedType:
    def test_no_python_list_in_the_message(self, home, client):
        exe = home / "tool.exe"
        exe.write_bytes(b"MZ")

        detail = _upload(client, exe).json()["detail"]

        assert "File type not allowed" in detail
        assert ".exe" in detail
        assert "[" not in detail and "'.bat'" not in detail

    def test_file_without_extension_says_so(self, home, client):
        bare = home / "README"
        bare.write_text("hello")

        detail = _upload(client, bare).json()["detail"]

        assert "has no file extension" in detail
        assert "File type not allowed: ." not in detail

    def test_blob_upload_uses_the_same_plain_message(self, home, client):
        resp = client.post(
            "/api/documents/upload",
            files={"file": ("tool.exe", b"MZ", "application/octet-stream")},
        )

        assert resp.status_code == 400
        assert "[" not in resp.json()["detail"]


class TestFailureReason:
    def test_unreadable_file_reason_and_kind_are_stored_and_listed(self, home, client):
        pdf = home / "broken.pdf"
        pdf.write_bytes(b"%PDF-1.4 truncated")
        reason = (
            "RAG indexing failed for broken.pdf: Could not read PDF: broken.pdf\n"
            "The file appears to be corrupted, truncated, or not a valid PDF."
        )

        async def corrupt(_path):
            raise UnreadableDocumentError(reason, "corrupted")

        with patch("gaia.ui.server._index_document", corrupt):
            resp = _upload(client, pdf)

        assert resp.status_code == 200
        assert resp.json()["indexing_status"] == "failed"
        assert resp.json()["last_error"] == reason
        assert resp.json()["error_kind"] == "corrupted"

        listed = client.get("/api/documents").json()["documents"]
        assert listed[0]["last_error"] == reason
        assert listed[0]["error_kind"] == "corrupted"

    def test_transient_failure_has_no_kind_and_success_clears_both(self, home, client):
        doc_file = home / "notes.md"
        doc_file.write_text("# Notes")

        async def boom(_path):
            raise RuntimeError("embedder not loaded")

        with patch("gaia.ui.server._index_document", boom):
            failed = _upload(client, doc_file).json()
        assert failed["last_error"] == "embedder not loaded"
        assert failed["error_kind"] is None

        async def ok(_path):
            return 3

        with patch("gaia.ui.server._index_document", ok):
            resp = client.post(f"/api/documents/{failed['id']}/reindex")

        assert resp.status_code == 200
        assert resp.json()["last_error"] is None
        assert resp.json()["error_kind"] is None


class TestIndexDocumentKinds:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["corrupted", "encrypted", "empty"])
    async def test_permanent_pdf_failures_carry_their_kind(self, kind):
        from gaia.ui._chat_helpers import _index_document

        rag = MagicMock()
        rag.index_document.return_value = {
            "success": False,
            "error": "Could not read PDF: x.pdf",
            "pdf_status": kind,
        }
        with (
            patch("gaia.rag.sdk.RAGSDK", return_value=rag),
            patch("gaia.rag.sdk.RAGConfig"),
        ):
            with pytest.raises(UnreadableDocumentError) as exc_info:
                await _index_document(Path("x.pdf"))

        assert exc_info.value.kind == kind
        assert "Could not read PDF" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_other_failures_stay_plain_runtime_errors(self):
        from gaia.ui._chat_helpers import _index_document

        rag = MagicMock()
        rag.index_document.return_value = {
            "success": False,
            "error": "embedder not loaded",
            "pdf_status": "unreadable",
        }
        with (
            patch("gaia.rag.sdk.RAGSDK", return_value=rag),
            patch("gaia.rag.sdk.RAGConfig"),
        ):
            with pytest.raises(RuntimeError) as exc_info:
                await _index_document(Path("x.pdf"))

        assert not isinstance(exc_info.value, UnreadableDocumentError)
