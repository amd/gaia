# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A refused chat attachment names the reason in a form the UI can translate."""

import io

import pytest
from fastapi.testclient import TestClient

from gaia.ui.routers import files
from gaia.ui.server import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    with TestClient(create_app(db_path=":memory:")) as test_client:
        yield test_client


def _upload(client, name, data):
    response = client.post(
        "/api/files/upload", files={"file": (name, io.BytesIO(data), "text/plain")}
    )
    return response.status_code, response.json()["detail"]


def test_a_file_with_no_extension_is_not_called_type_empty_quotes(client):
    status, detail = _upload(client, "noext", b"hello")
    assert status == 400
    assert detail["status"] == "no_extension"
    assert "''" not in detail["message"]
    assert "no extension" in detail["message"]


def test_an_unsupported_extension_is_named(client):
    status, detail = _upload(client, "a.xyz", b"hello")
    assert status == 400
    assert detail["status"] == "unsupported_type"
    assert detail["extension"] == ".xyz"
    assert "'.xyz' is not allowed" in detail["message"]


def test_an_empty_file_is_refused_with_a_code(client):
    status, detail = _upload(client, "blank.txt", b"")
    assert status == 400
    assert detail["status"] == "empty_file"


def test_an_oversized_file_reports_both_sizes(client, monkeypatch):
    monkeypatch.setattr(files, "MAX_UPLOAD_SIZE", 1024 * 1024)
    status, detail = _upload(client, "big.txt", b"a" * (2 * 1024 * 1024))
    assert status == 413
    assert detail["status"] == "too_large"
    assert (detail["size_mb"], detail["max_mb"]) == ("2.0", "1")
