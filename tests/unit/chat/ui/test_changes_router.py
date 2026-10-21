# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The list / diff / undo endpoints behind "Undo this turn" (#5225)."""

import pytest
from fastapi.testclient import TestClient

from gaia.agents.base import file_snapshots as fs
from gaia.ui.server import create_app


@pytest.fixture
def home(tmp_path, monkeypatch):
    gaia_home = tmp_path / "gaia-home"
    monkeypatch.setenv("GAIA_HOME", str(gaia_home))
    monkeypatch.setattr("gaia.config.GAIA_CONFIG_FILE", gaia_home / "config.json")
    return gaia_home


@pytest.fixture
def client(home):
    return TestClient(create_app(db_path=":memory:"))


@pytest.fixture
def session_id(client):
    return client.post("/api/sessions", json={}).json()["id"]


def _edit_turn(session_id, target, before, after):
    target.write_bytes(before)
    with fs.recording_turn(session_id) as rec:
        fs.before_change(target, "edit_file")
        target.write_bytes(after)
    rec.link_message(7)
    return rec.turn_id


def test_list_diff_undo_round_trip(client, session_id, tmp_path, home):
    target = tmp_path / "doc.txt"
    turn = _edit_turn(session_id, target, b"old\n", b"new\n")
    assert (home / "snapshots" / session_id / turn).is_dir()

    turns = client.get(f"/api/sessions/{session_id}/changes").json()["turns"]
    assert [(t["turn_id"], t["message_id"]) for t in turns] == [(turn, 7)]
    assert turns[0]["files"][0]["change"] == "edited"

    diff = client.get(f"/api/sessions/{session_id}/changes/{turn}/diff").json()
    assert "-old" in diff["files"][0]["unified"]

    undo = client.post(f"/api/sessions/{session_id}/changes/{turn}/undo", json={})
    assert undo.status_code == 200, undo.text
    assert target.read_bytes() == b"old\n"

    again = client.post(f"/api/sessions/{session_id}/changes/{turn}/undo", json={})
    assert again.status_code == 409
    assert "already undone" in again.json()["detail"]


def test_conflict_is_409_with_the_files_named(client, session_id, tmp_path):
    target = tmp_path / "doc.txt"
    turn = _edit_turn(session_id, target, b"old", b"new")
    target.write_bytes(b"user edit")
    resp = client.post(f"/api/sessions/{session_id}/changes/{turn}/undo", json={})
    assert resp.status_code == 409
    assert resp.json()["detail"]["conflicts"][0]["path"] == str(target)
    assert target.read_bytes() == b"user edit"

    forced = client.post(
        f"/api/sessions/{session_id}/changes/{turn}/undo", json={"force": True}
    )
    assert forced.status_code == 200
    assert target.read_bytes() == b"old"


def test_unknown_session_and_turn(client, session_id):
    assert client.get("/api/sessions/missing/changes").status_code == 404
    resp = client.get(f"/api/sessions/{session_id}/changes/nope/diff")
    assert resp.status_code == 404


def test_undo_waits_for_a_running_turn(client, session_id, tmp_path):
    import asyncio

    turn = _edit_turn(session_id, tmp_path / "doc.txt", b"a", b"b")
    lock = client.app.state.session_locks.setdefault(session_id, asyncio.Lock())
    lock._locked = True  # held by a running turn
    try:
        resp = client.post(f"/api/sessions/{session_id}/changes/{turn}/undo", json={})
    finally:
        lock._locked = False
    assert resp.status_code == 409
    assert "still working" in resp.json()["detail"]


def test_settings_round_trip_and_prune(client, session_id, tmp_path, home):
    settings = client.get("/api/settings/undo").json()
    assert (settings["retention_days"], settings["max_mb"]) == (7, 500)

    _edit_turn(session_id, tmp_path / "doc.txt", b"a", b"b")
    used = client.get("/api/settings/undo").json()
    assert used["turns"] == 1 and used["used_bytes"] > 0

    resp = client.put("/api/settings/undo", json={"retention_days": 3, "max_mb": 50})
    assert resp.status_code == 200
    assert (resp.json()["retention_days"], resp.json()["max_mb"]) == (3, 50)
    assert '"undo_retention_days": 3' in (home / "config.json").read_text()

    bad = client.put("/api/settings/undo", json={"retention_days": 0, "max_mb": 50})
    assert bad.status_code == 422


def test_deleting_the_chat_removes_its_undo_history(client, session_id, tmp_path, home):
    _edit_turn(session_id, tmp_path / "doc.txt", b"a", b"b")
    assert client.delete(f"/api/sessions/{session_id}").status_code == 200
    assert not (home / "snapshots" / session_id).exists()


def test_whole_chat_total_and_undo_all(client, session_id, tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_bytes(b"a\n")
    with fs.recording_turn(session_id, turn_id="t1") as rec:
        fs.before_change(a, "edit_file")
        a.write_bytes(b"a\nmore\n")
        fs.before_change(b, "write_file")
        b.write_bytes(b"new\n")
    rec.link_message(3)

    total = client.get(f"/api/sessions/{session_id}/changes/total").json()
    assert total["totals"] == {
        "files": 2,
        "additions": 2,
        "deletions": 0,
        "uncounted": 0,
    }

    undo = client.post(f"/api/sessions/{session_id}/changes/undo", json={})
    assert undo.status_code == 200, undo.text
    assert a.read_bytes() == b"a\n" and not b.exists()
    after = client.get(f"/api/sessions/{session_id}/changes/total").json()
    assert after["files"] == []


def test_undo_all_conflict_is_409_and_changes_nothing(client, session_id, tmp_path):
    target = tmp_path / "doc.txt"
    _edit_turn(session_id, target, b"old", b"new")
    target.write_bytes(b"user edit")
    resp = client.post(f"/api/sessions/{session_id}/changes/undo", json={})
    assert resp.status_code == 409
    assert resp.json()["detail"]["conflicts"][0]["path"] == str(target)
    assert target.read_bytes() == b"user edit"


def test_undo_all_refused_while_a_turn_runs(client, session_id, tmp_path):
    import asyncio

    _edit_turn(session_id, tmp_path / "doc.txt", b"old", b"new")
    lock = client.app.state.session_locks.setdefault(session_id, asyncio.Lock())
    lock._locked = True  # held by a running turn
    try:
        resp = client.post(f"/api/sessions/{session_id}/changes/undo", json={})
    finally:
        lock._locked = False
    assert resp.status_code == 409
    assert "still working" in resp.json()["detail"]
    assert (tmp_path / "doc.txt").read_bytes() == b"new"


def test_undo_all_with_nothing_to_undo_is_409(client, session_id):
    resp = client.post(f"/api/sessions/{session_id}/changes/undo", json={})
    assert resp.status_code == 409
    assert "no file changes" in resp.json()["detail"]
