# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A citation opens the cited document: GET /api/documents/{id}/file serves it inline."""

from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from gaia.ui.utils import DOCUMENT_ROOTS_ENV


@pytest.fixture
def home(tmp_path, monkeypatch):
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


async def _three_chunks(_path, display_name=None, progress_key=None):
    return 3


def _add(client, path):
    with patch("gaia.ui.server._index_document", _three_chunks):
        resp = client.post("/api/documents/upload-path", json={"filepath": str(path)})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_a_library_pdf_is_served_inline_as_pdf(home, client):
    pdf = home / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%fake\n")
    doc_id = _add(client, pdf)

    resp = client.get(f"/api/documents/{doc_id}/file")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["content-disposition"].startswith("inline")
    assert resp.content == pdf.read_bytes()


def test_unknown_document_is_404(client):
    assert client.get("/api/documents/nope/file").status_code == 404


def test_a_file_removed_from_disk_says_where_it_was(home, client):
    notes = home / "notes.md"
    notes.write_text("# Notes")
    doc_id = _add(client, notes)
    notes.unlink()

    resp = client.get(f"/api/documents/{doc_id}/file")

    assert resp.status_code == 404
    assert "notes.md" in resp.json()["detail"]


def test_a_document_outside_the_document_roots_is_not_served(
    home, client, tmp_path, monkeypatch
):
    notes = home / "notes.md"
    notes.write_text("# Notes")
    doc_id = _add(client, notes)
    moved = tmp_path / "elsewhere"
    moved.mkdir()
    monkeypatch.setattr(Path, "home", lambda: moved)

    assert client.get(f"/api/documents/{doc_id}/file").status_code == 403


def test_a_symlinked_library_file_is_not_served(home, client, tmp_path):
    notes = home / "notes.md"
    notes.write_text("# Notes")
    doc_id = _add(client, notes)
    secret = tmp_path / "secret.txt"
    secret.write_text("private")
    notes.unlink()
    try:
        notes.symlink_to(secret)
    except OSError:
        pytest.skip("creating symlinks needs privileges on this machine")

    assert client.get(f"/api/documents/{doc_id}/file").status_code == 403
