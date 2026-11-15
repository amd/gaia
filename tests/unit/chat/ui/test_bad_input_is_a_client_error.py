# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Impossible input gets a 4xx that says what's wrong, never a 500.

Found by fuzzing the Agent UI backend: a null byte or a path longer than
Windows allows raised ValueError from resolve()/stat(); an offset past
SQLite's integer range raised OverflowError; an unknown provider name on
/v1/connections raised KeyError or reached the OS credential store.
"""

import errno
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from gaia.ui.server import create_app

UI = {"X-Gaia-UI": "1"}
NULL_PATH = "C:\\gaia-test\\a\x00b"
HUGE_PATH = "C:\\gaia-test\\" + "a" * 200_000
TOO_BIG = "99999999999999999999"


@pytest.fixture
def home(tmp_path, monkeypatch):
    gaia_home = tmp_path / "gaia-home"
    monkeypatch.setenv("GAIA_HOME", str(gaia_home))
    monkeypatch.setattr("gaia.config.GAIA_CONFIG_FILE", gaia_home / "config.json")
    return gaia_home


@pytest.fixture
def client(home):
    return TestClient(
        create_app(db_path=":memory:"),
        base_url="http://127.0.0.1",
        headers=UI,
        raise_server_exceptions=True,
    )


@pytest.fixture
def session_id(client):
    return client.post("/api/sessions", json={}).json()["id"]


@pytest.mark.parametrize("path", [NULL_PATH, HUGE_PATH], ids=["null-byte", "huge"])
def test_checking_an_impossible_folder_explains_it(client, path):
    r = client.post("/api/environment/folders/check", json={"path": path})
    assert r.status_code == 200
    assert "is not a path this PC can open" in r.json()["refusal"]
    assert len(r.json()["refusal"]) < 400


def test_adding_an_impossible_folder_is_refused(client, session_id, monkeypatch):
    # Asking the agent whether it takes folders would start a real daemon.
    monkeypatch.setattr("gaia.ui.session_environment.takes_folders", lambda *_a: True)
    r = client.post(
        f"/api/sessions/{session_id}/environment/folders", json={"path": NULL_PATH}
    )
    assert r.status_code == 400


def test_a_document_path_longer_than_windows_allows_is_refused(client):
    r = client.post("/api/documents/upload-path", json={"filepath": HUGE_PATH})
    assert r.status_code == 400


def test_opening_a_path_longer_than_windows_allows_is_refused(client):
    r = client.post("/api/files/open", json={"path": HUGE_PATH})
    assert r.status_code == 400


# Linux and macOS raise OSError(ENAMETOOLONG) where Windows raises ValueError.
def _posix_too_long(*_args, **_kwargs):
    raise OSError(errno.ENAMETOOLONG, "File name too long")


@pytest.mark.parametrize(
    "method, url, body",
    [
        ("post", "/api/environment/folders/check", {"path": "C:\gaia-test\long"}),
        ("post", "/api/files/open", {"path": "C:\gaia-test\long"}),
        ("post", "/api/documents/upload-path", {"filepath": "C:\gaia-test\long.txt"}),
    ],
    ids=["folder-check", "open-file", "document-by-path"],
)
def test_a_path_too_long_for_posix_is_refused_too(
    client, monkeypatch, method, url, body
):
    monkeypatch.setattr("pathlib.Path.resolve", _posix_too_long)
    r = getattr(client, method)(url, json=body)
    assert r.status_code in (200, 400)
    if r.status_code == 200:
        assert "is not a path this PC can open" in r.json()["refusal"]


@pytest.mark.parametrize(
    "url",
    [
        "/api/sessions?offset=" + TOO_BIG,
        "/api/sessions/{sid}/messages?offset=" + TOO_BIG,
        "/api/memory/knowledge?offset=" + TOO_BIG,
    ],
    ids=["sessions", "messages", "memory"],
)
def test_an_offset_past_sqlite_range_is_a_validation_error(client, session_id, url):
    r = client.get(url.format(sid=session_id))
    assert r.status_code == 422
    assert "offset" in r.text


def test_a_large_but_valid_offset_is_just_an_empty_page(client):
    r = client.get("/api/sessions?offset=999999999999")
    assert r.status_code == 200
    assert r.json()["sessions"] == []


FORWARD_BODY = {"client_id": "id", "refresh_token": "token", "grant_agents": []}


@pytest.mark.parametrize("provider", ["nonexistent-id", "a" * 5000])
def test_an_unknown_provider_is_refused_before_the_keyring(client, provider):
    with patch("keyring.get_password") as read, patch("keyring.set_password") as write:
        posted = client.post(f"/v1/connections/{provider}", json=FORWARD_BODY)
        revoked = client.delete(f"/v1/connections/{provider}")
    assert posted.status_code == 404
    assert revoked.status_code == 404
    assert "Unknown OAuth provider" in posted.json()["detail"]
    read.assert_not_called()
    write.assert_not_called()
