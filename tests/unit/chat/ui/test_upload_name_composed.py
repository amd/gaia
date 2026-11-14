# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A dropped file's name is stored composed, so a macOS (decomposed) name
matches the one the model writes."""

import unicodedata

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("faiss", reason="RAGSDK requires faiss-cpu")

import gaia.ui.server as server  # noqa: E402
from gaia.ui.server import create_app  # noqa: E402


def test_upload_name_is_stored_composed(tmp_path, monkeypatch):
    async def fake_index(path, display_name=None, **_kwargs):
        return 1

    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    monkeypatch.setattr(server, "_index_document", fake_index)
    client = TestClient(create_app(db_path=":memory:"))
    decomposed = unicodedata.normalize("NFD", "café_مُحَمَّد.txt")
    assert decomposed != unicodedata.normalize("NFC", decomposed)

    resp = client.post(
        "/api/documents/upload",
        files={"file": (decomposed, b"some text")},
        headers={"x-gaia-ui": "1", "x-gaia-client": "agent-ui"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["filename"] == unicodedata.normalize("NFC", decomposed)
    assert unicodedata.is_normalized("NFC", body["filepath"])
