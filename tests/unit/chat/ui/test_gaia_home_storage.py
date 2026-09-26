# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Agent UI uploads and managed documents live under ``GAIA_HOME`` (#4347).

``GAIA_HOME`` points outside the (fake) home directory in every test here, so
a hard-coded ``Path.home() / ".gaia"`` shows up as a stray ``<home>/.gaia``.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from gaia.config import UnsafeGaiaHomeError
from gaia.ui._chat_helpers import _managed_documents_dir
from gaia.ui.server import create_app
from gaia.ui.server import main as server_main
from gaia.ui.utils import (
    DOCUMENT_ROOTS_ENV,
    document_roots,
    managed_documents_dir,
    uploads_dir,
)


@pytest.fixture
def homes(tmp_path, monkeypatch):
    """Fake user home plus a GAIA_HOME that is NOT inside it."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    gaia_home = tmp_path / "gaia-home"
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    monkeypatch.setenv("GAIA_HOME", str(gaia_home))
    monkeypatch.delenv(DOCUMENT_ROOTS_ENV, raising=False)
    yield fake_home, gaia_home
    assert not (
        fake_home / ".gaia"
    ).exists(), "wrote under <home>/.gaia while GAIA_HOME pointed elsewhere"


@pytest.fixture
def client(homes):
    return TestClient(create_app(db_path=":memory:"))


@pytest.fixture
def mock_index_document():
    with patch("gaia.ui.server._index_document", new=AsyncMock(return_value=3)) as m:
        yield m


def test_file_upload_is_stored_and_served_from_gaia_home(homes, client):
    _, gaia_home = homes
    resp = client.post(
        "/api/files/upload", files={"file": ("a.txt", b"hello", "text/plain")}
    )
    assert resp.status_code == 200, resp.text
    stored = gaia_home / "chat" / "uploads" / resp.json()["filename"]
    assert stored.read_bytes() == b"hello"
    assert client.get(resp.json()["url"]).content == b"hello"


def test_blob_document_lifecycle_uses_gaia_home(homes, client, mock_index_document):
    _, gaia_home = homes
    resp = client.post(
        "/api/documents/upload", files={"file": ("r.txt", b"report", "text/plain")}
    )
    assert resp.status_code == 200, resp.text
    doc = resp.json()
    stored = Path(doc["filepath"])
    assert stored.parent == gaia_home / "documents"

    reindex = client.post(f"/api/documents/{doc['id']}/reindex")
    assert reindex.status_code == 200, reindex.text

    assert client.delete(f"/api/documents/{doc['id']}").status_code == 200
    assert not stored.exists(), "server-owned file must be removed on delete"


def test_agent_scope_documents_dir_follows_gaia_home(homes):
    _, gaia_home = homes
    assert _managed_documents_dir() == (gaia_home / "documents").resolve()


def test_relocated_documents_dir_is_a_document_root(homes):
    _, gaia_home = homes
    assert (gaia_home / "documents").resolve() in document_roots()


@pytest.mark.parametrize("pick", [lambda h: h, lambda h: h.parent])
def test_gaia_home_at_or_above_user_home_is_refused(homes, monkeypatch, pick):
    fake_home, _ = homes
    monkeypatch.setenv("GAIA_HOME", str(pick(fake_home)))
    with pytest.raises(UnsafeGaiaHomeError) as exc_info:
        managed_documents_dir()
    assert "GAIA_HOME" in str(exc_info.value)
    with pytest.raises(UnsafeGaiaHomeError):
        uploads_dir()


def test_unsafe_gaia_home_is_an_actionable_startup_error_not_a_traceback(
    homes, monkeypatch, capsys
):
    """uploads_dir() runs in create_app(), so an HTTPException there would
    reach the user as a raw traceback instead of the remedy."""
    fake_home, _ = homes
    monkeypatch.setenv("GAIA_HOME", str(fake_home))
    monkeypatch.setattr(
        "sys.argv", ["gaia.ui.server", "--host", "127.0.0.1", "--port", "4200"]
    )
    # If the guard ever stops firing, fail instead of binding a real port.
    monkeypatch.setattr(
        "uvicorn.run",
        lambda *a, **k: pytest.fail("create_app() should have refused GAIA_HOME"),
    )

    with pytest.raises(SystemExit) as exc_info:
        server_main()

    assert exc_info.value.code == 64
    err = capsys.readouterr().err
    assert "GAIA_HOME" in err and str(fake_home / ".gaia") in err
    assert "Traceback" not in err
