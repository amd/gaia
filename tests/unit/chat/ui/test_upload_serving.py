# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An uploaded page opened from a chat link can never run as the app."""

import pytest
from fastapi.testclient import TestClient

from gaia.ui.server import create_app
from gaia.ui.utils import uploads_dir

_PAGE = b"<html><script>fetch('/api/sessions')</script></html>"
_SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    up = uploads_dir()
    up.mkdir(parents=True)
    files = {
        "page.html": _PAGE,
        "PAGE.HTM": _PAGE,
        "pic.svg": _SVG,
        "data.xml": b"<x/>",
        "notes.txt": _PAGE,
        "shot.png": b"\x89PNG\r\n\x1a\n",
        "report.pdf": b"%PDF-1.4",
        "REPORT.PDF.html": _PAGE,
    }
    for name, body in files.items():
        (up / name).write_bytes(body)
    return TestClient(create_app(db_path=":memory:"))


@pytest.mark.parametrize(
    "name",
    [
        "page.html",
        "PAGE.HTM",
        "pic.svg",
        "data.xml",
        "notes.txt",
        "shot.png",
        "REPORT.PDF.html",
    ],
)
def test_upload_is_sandboxed(client, name):
    r = client.get(f"/api/files/uploads/{name}")
    assert r.status_code == 200
    assert r.headers["content-security-policy"] == "sandbox"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_pdf_still_opens_in_the_viewer(client):
    r = client.get("/api/files/uploads/report.pdf")
    assert r.status_code == 200
    assert "content-security-policy" not in r.headers
    assert r.headers["x-content-type-options"] == "nosniff"


def test_head_request_is_sandboxed_too(client):
    r = client.head("/api/files/uploads/page.html")
    assert r.headers["content-security-policy"] == "sandbox"


@pytest.mark.parametrize(
    "path", ["/api/files/uploads/missing.html", "/api/files/uploads/..%2Fx.html"]
)
def test_missing_or_traversal_is_404(client, path):
    assert client.get(path).status_code == 404


def test_page_renamed_to_pdf_is_not_served_as_a_page(client, tmp_path):
    (uploads_dir() / "fake.pdf").write_bytes(_PAGE)
    r = client.get("/api/files/uploads/fake.pdf")
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["x-content-type-options"] == "nosniff"
