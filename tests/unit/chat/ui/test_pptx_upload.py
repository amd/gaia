# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What the Agent UI's upload route returns for a damaged PowerPoint file."""

from __future__ import annotations

import io
import zipfile

import pytest

pytest.importorskip("pptx")
pytest.importorskip("faiss", reason="RAGSDK requires faiss-cpu")


@pytest.fixture
def client(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from gaia.ui.server import create_app

    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    return TestClient(create_app(db_path=":memory:"))


def _upload(client, name, content):
    return client.post(
        "/api/documents/upload",
        files={"file": (name, content)},
        headers={"x-gaia-ui": "1", "x-gaia-client": "agent-ui"},
    )


def _zip_without_slides() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "not a presentation")
    return buf.getvalue()


@pytest.mark.parametrize(
    "content",
    [b"this is not a real presentation", _zip_without_slides()],
    ids=["not-a-zip", "zip-without-slides"],
)
def test_a_damaged_presentation_is_reported_as_damaged(client, content):
    resp = _upload(client, "deck.pptx", content)

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["status"] == "corrupted"
    assert "deck.pptx" in detail["message"]
