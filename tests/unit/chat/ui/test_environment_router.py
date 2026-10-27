# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Environment endpoints behind the session panel (#5276).

Changes persist, show up in the listing, and reach the chat's live agent at
once, so the next step is checked against them without a restart.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from gaia.security import PathValidator
from gaia.ui import session_environment
from gaia.ui.database import ChatDatabase
from gaia.ui.server import create_app


@pytest.fixture
def home(tmp_path, monkeypatch):
    gaia_home = tmp_path / "gaia-home"
    monkeypatch.setenv("GAIA_HOME", str(gaia_home))
    monkeypatch.setattr("gaia.config.GAIA_CONFIG_FILE", gaia_home / "config.json")
    return gaia_home


@pytest.fixture(autouse=True)
def in_process_agent(monkeypatch):
    """No agent registry in unit tests; chats run in-process, as with a source install."""
    monkeypatch.setattr(session_environment, "relayed", lambda _session: False)


@pytest.fixture
def client(home):
    return TestClient(create_app(db_path=":memory:"))


@pytest.fixture
def session_id(client):
    return client.post("/api/sessions", json={}).json()["id"]


@pytest.fixture
def folder(tmp_path):
    path = tmp_path / "project"
    path.mkdir()
    return path


@pytest.fixture
def live_agent(monkeypatch, tmp_path):
    agent = SimpleNamespace(
        path_validator=PathValidator(allowed_paths=[str(tmp_path / "base")]),
        internet_off=False,
    )
    (tmp_path / "base").mkdir()
    monkeypatch.setattr(session_environment, "_live_agent", lambda _sid: agent)
    return agent


def _paths(body):
    return [f["path"] for f in body["folders"]]


def test_default_is_internet_on_and_the_computed_scope(client, session_id):
    body = client.get(f"/api/sessions/{session_id}/environment").json()
    assert body["internet"] is True
    assert body["removed"] == []
    assert body["enforced"] is True
    sources = {f["source"] for f in body["folders"]}
    assert "gaia_documents" in sources


def test_add_then_remove_folder_persists(client, session_id, folder):
    added = client.post(
        f"/api/sessions/{session_id}/environment/folders", json={"path": str(folder)}
    )
    assert added.status_code == 200, added.text
    entry = next(f for f in added.json()["folders"] if f["path"] == str(folder))
    assert entry["source"] == "added" and entry["kind"] == "folder"

    removed = client.post(
        f"/api/sessions/{session_id}/environment/folders/remove",
        json={"path": str(folder)},
    ).json()
    assert str(folder) not in _paths(removed)
    assert removed["removed"] == [str(folder.resolve())]

    again = client.get(f"/api/sessions/{session_id}/environment").json()
    assert str(folder) not in _paths(again)


def test_changes_reach_the_live_agent_immediately(
    client, session_id, folder, live_agent
):
    v = live_agent.path_validator
    client.post(
        f"/api/sessions/{session_id}/environment/folders", json={"path": str(folder)}
    )
    assert v.is_path_allowed(str(folder / "a.txt"), prompt_user=False)

    client.post(
        f"/api/sessions/{session_id}/environment/folders/remove",
        json={"path": str(folder)},
    )
    assert not v.is_path_allowed(str(folder / "a.txt"), prompt_user=False)

    client.put(f"/api/sessions/{session_id}/environment/internet", json={"on": False})
    assert live_agent.internet_off is True
    client.put(f"/api/sessions/{session_id}/environment/internet", json={"on": True})
    assert live_agent.internet_off is False


def test_removing_a_computed_folder_is_enforced_on_a_new_agent(
    client, session_id, tmp_path
):
    base = tmp_path / "base"
    base.mkdir()
    client.post(
        f"/api/sessions/{session_id}/environment/folders/remove",
        json={"path": str(base)},
    )
    # The next turn builds a fresh agent and applies the chat's environment.
    agent = SimpleNamespace(
        path_validator=PathValidator(allowed_paths=[str(base)]), internet_off=False
    )
    db = client.app.state.db
    session_environment.apply_environment(agent, session_id, db)
    assert not agent.path_validator.is_path_allowed(
        str(base / "x.txt"), prompt_user=False
    )


def test_internet_off_persists_and_applies_to_the_next_turn(client, session_id):
    body = client.put(
        f"/api/sessions/{session_id}/environment/internet", json={"on": False}
    ).json()
    assert body["internet"] is False
    agent = SimpleNamespace(path_validator=None, internet_off=False)
    session_environment.apply_environment(agent, session_id, client.app.state.db)
    assert agent.internet_off is True


@pytest.mark.parametrize("bad", ["not-a-folder", "home", "root"])
def test_unsafe_or_missing_folders_are_refused(client, session_id, tmp_path, bad):
    from pathlib import Path

    path = {
        "not-a-folder": str(tmp_path / "missing"),
        "home": str(Path.home()),
        "root": Path.home().anchor,
    }[bad]
    resp = client.post(
        f"/api/sessions/{session_id}/environment/folders", json={"path": path}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]


def test_a_folder_inside_a_removed_one_is_refused(client, session_id, folder):
    inner = folder / "inner"
    inner.mkdir()
    client.post(
        f"/api/sessions/{session_id}/environment/folders/remove",
        json={"path": str(folder)},
    )
    resp = client.post(
        f"/api/sessions/{session_id}/environment/folders", json={"path": str(inner)}
    )
    assert resp.status_code == 400
    assert "Add that folder back" in resp.json()["detail"]


def test_adding_a_removed_folder_back_releases_it(
    client, session_id, folder, live_agent
):
    url = f"/api/sessions/{session_id}/environment/folders"
    client.post(f"{url}/remove", json={"path": str(folder)})
    body = client.post(url, json={"path": str(folder)}).json()
    assert body["removed"] == []
    assert live_agent.path_validator.is_path_allowed(
        str(folder / "a.txt"), prompt_user=False
    )


def test_unknown_session_is_404(client):
    assert client.get("/api/sessions/nope/environment").status_code == 404


def test_a_relayed_agent_is_reported_unenforced_and_changes_are_refused(
    client, session_id, folder, monkeypatch
):
    # A Hub binary or the email sidecar runs as its own process.
    monkeypatch.setattr(session_environment, "relayed", lambda _session: True)
    body = client.get(f"/api/sessions/{session_id}/environment").json()
    assert body["enforced"] is False
    off = client.put(
        f"/api/sessions/{session_id}/environment/internet", json={"on": False}
    )
    assert off.status_code == 409
    assert "its own process" in off.json()["detail"]
    removed = client.post(
        f"/api/sessions/{session_id}/environment/folders/remove",
        json={"path": str(folder)},
    )
    assert removed.status_code == 409
    added = client.post(
        f"/api/sessions/{session_id}/environment/folders", json={"path": str(folder)}
    )
    assert added.status_code == 400
    assert (
        client.get(f"/api/sessions/{session_id}/environment").json()["internet"] is True
    )


def test_an_invalid_allowed_paths_setting_is_named_not_an_empty_list(
    client, session_id, monkeypatch
):
    # Set after start-up, as when a folder it names is deleted mid-run.
    monkeypatch.setenv("GAIA_ALLOWED_PATHS", "relative/folder")

    res = client.get(f"/api/sessions/{session_id}/environment")

    assert res.status_code == 500
    assert "GAIA_ALLOWED_PATHS" in res.json()["detail"]


def _chat_count(client):
    return client.get("/api/sessions").json()["total"]


def test_a_new_chat_can_start_with_a_folder(client, folder):
    created = client.post("/api/sessions", json={"folders": [str(folder)]})
    assert created.status_code == 200, created.text
    body = client.get(f"/api/sessions/{created.json()['id']}/environment").json()
    entry = next(f for f in body["folders"] if f["path"] == str(folder.resolve()))
    assert entry["source"] == "added"


@pytest.mark.parametrize("bad", ["home", "missing"])
def test_a_refused_folder_leaves_no_chat_behind(client, tmp_path, bad):
    path = str(Path.home()) if bad == "home" else str(tmp_path / "nope")
    refused = client.post("/api/sessions", json={"folders": [path]})
    assert refused.status_code == 400
    assert "GAIA can't be given" in refused.text or "is not a folder" in refused.text
    assert _chat_count(client) == 0


def test_a_relayed_agent_cannot_start_with_a_folder(client, folder, monkeypatch):
    monkeypatch.setattr(session_environment, "relayed", lambda _session: True)
    refused = client.post("/api/sessions", json={"folders": [str(folder)]})
    assert refused.status_code == 400
    assert "runs as its own process" in refused.text
    assert _chat_count(client) == 0


def test_a_folder_removed_after_the_check_leaves_no_chat_behind(
    client, folder, monkeypatch
):
    checked = session_environment.check_new_chat_folders

    def check_then_remove(agent_type, folders):
        checked(agent_type, folders)
        folder.rmdir()

    monkeypatch.setattr(
        session_environment, "check_new_chat_folders", check_then_remove
    )
    refused = client.post("/api/sessions", json={"folders": [str(folder)]})
    assert refused.status_code == 400
    assert "is not a folder" in refused.text
    assert _chat_count(client) == 0


def test_a_failed_folder_save_leaves_no_chat_behind(client, folder, monkeypatch):
    def broken_save(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(ChatDatabase, "set_session_environment", broken_save)
    failed = client.post("/api/sessions", json={"folders": [str(folder)]})
    assert failed.status_code == 500
    assert _chat_count(client) == 0


def _break_rollback(monkeypatch):
    def broken_delete(*_args, **_kwargs):
        raise OSError("database is locked")

    monkeypatch.setattr(ChatDatabase, "delete_session", broken_delete)


def test_a_failed_rollback_keeps_the_folder_refusal_as_the_answer(
    client, folder, monkeypatch, caplog
):
    checked = session_environment.check_new_chat_folders

    def check_then_remove(agent_type, folders):
        checked(agent_type, folders)
        folder.rmdir()

    monkeypatch.setattr(
        session_environment, "check_new_chat_folders", check_then_remove
    )
    _break_rollback(monkeypatch)
    refused = client.post("/api/sessions", json={"folders": [str(folder)]})
    assert refused.status_code == 400
    assert "is not a folder" in refused.text
    assert "Could not remove half-made chat" in caplog.text


def test_a_failed_rollback_keeps_the_original_500_and_logs_both(
    client, folder, monkeypatch, caplog
):
    def broken_save(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(ChatDatabase, "set_session_environment", broken_save)
    _break_rollback(monkeypatch)
    failed = client.post("/api/sessions", json={"folders": [str(folder)]})
    assert failed.status_code == 500
    assert "Check server logs" in failed.json()["detail"]
    assert "database is locked" in caplog.text
    assert "disk full" in caplog.text
