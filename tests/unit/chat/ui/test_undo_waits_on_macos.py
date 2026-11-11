# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Undo says macOS is asking instead of hanging on "Undoing…".

The agent that writes a file in ``~/Documents`` has the user's permission for
it; the Agent UI server, which runs the undo, may not. Its first read there then
blocks behind the macOS dialog for as long as nobody answers. The platform is
patched and the home folder is a temp dir, so these run anywhere.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gaia.agents.base import file_snapshots as fs
from gaia.agents.tools import protected_folders
from gaia.agents.tools.protected_folders import DENIED, macos_guarded_folder
from gaia.ui.server import create_app


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = (tmp_path / "home").resolve()
    (home / "Documents").mkdir(parents=True)
    gaia_home = tmp_path / "gaia-home"
    monkeypatch.setenv("GAIA_HOME", str(gaia_home))
    monkeypatch.setattr("gaia.config.GAIA_CONFIG_FILE", gaia_home / "config.json")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(protected_folders, "_probes", {})
    return home


@pytest.fixture
def dialog(monkeypatch):
    """Reads of folders in ``held`` block like TCC; ``refused`` ones are denied."""
    state = {"held": set(), "refused": set(), "answer": threading.Event()}
    real = protected_folders._read_first_entry

    def read(folder):
        if folder.name in state["held"]:
            state["answer"].wait(10)
        if folder.name in state["refused"]:
            return DENIED
        return real(folder)

    monkeypatch.setattr(protected_folders, "_read_first_entry", read)
    monkeypatch.setattr(protected_folders, "MACOS_PROMPT_WAIT_S", 0.05)
    yield state
    state["answer"].set()


@pytest.fixture
def client(home):
    return TestClient(create_app(db_path=":memory:"))


@pytest.fixture
def created(client, home):
    """A chat whose one turn created ~/Documents/shopping.txt."""
    session_id = client.post("/api/sessions", json={}).json()["id"]
    target = home / "Documents" / "shopping.txt"
    with fs.recording_turn(session_id) as rec:
        fs.before_change(target, "write_file")
        target.write_text("Eggs\n")
    return session_id, rec.turn_id, target


def _undo(client, session_id, turn_id):
    return client.post(f"/api/sessions/{session_id}/changes/{turn_id}/undo", json={})


def test_undo_reports_the_open_dialog_and_changes_nothing(client, created, dialog):
    session_id, turn_id, target = created
    dialog["held"].add("Documents")

    resp = _undo(client, session_id, turn_id)

    assert resp.status_code == 409
    assert "macOS is asking whether GAIA may use ~/Documents" in resp.json()["detail"]
    assert target.exists()


def test_undo_all_reports_the_open_dialog(client, created, dialog):
    session_id, _turn_id, target = created
    dialog["held"].add("Documents")

    resp = client.post(f"/api/sessions/{session_id}/changes/undo", json={})

    assert resp.status_code == 409
    assert "Answer that dialog" in resp.json()["detail"]
    assert target.exists()


def test_undo_all_ignores_a_turn_already_undone(client, home, dialog):
    session_id = client.post("/api/sessions", json={}).json()["id"]
    docs_file = home / "Documents" / "old.txt"
    with fs.recording_turn(session_id) as rec:
        fs.before_change(docs_file, "write_file")
        docs_file.write_text("x\n")
    assert _undo(client, session_id, rec.turn_id).status_code == 200
    elsewhere = home / "notes.txt"
    with fs.recording_turn(session_id):
        fs.before_change(elsewhere, "write_file")
        elsewhere.write_text("y\n")
    dialog["refused"].add("Documents")

    resp = client.post(f"/api/sessions/{session_id}/changes/undo", json={})

    assert resp.status_code == 200, resp.text
    assert not elsewhere.exists()


def test_undo_says_where_to_allow_a_denied_folder(client, created, dialog):
    session_id, turn_id, target = created
    dialog["refused"].add("Documents")

    resp = _undo(client, session_id, turn_id)

    assert resp.status_code == 409
    assert "Privacy & Security > Files and Folders" in resp.json()["detail"]
    assert target.exists()


def test_undo_runs_once_the_folder_is_granted(client, created, dialog):
    session_id, turn_id, target = created

    resp = _undo(client, session_id, turn_id)

    assert resp.status_code == 200, resp.text
    assert not target.exists()


def test_the_guarded_folder_is_found_without_touching_the_file(home, monkeypatch):
    inside = f"{home / 'Documents'}{os.sep}"

    def guarded(real):
        def stat(path, *args, **kwargs):
            assert not os.fspath(path).startswith(inside), f"stat-ed {path}"
            return real(path, *args, **kwargs)

        return stat

    monkeypatch.setattr(os, "stat", guarded(os.stat))
    monkeypatch.setattr(os, "lstat", guarded(os.lstat))
    folder = macos_guarded_folder(str(home / "Documents" / "notes" / "a.txt"))
    assert folder == home / "Documents"


def test_nothing_is_guarded_off_macos(home, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert macos_guarded_folder(str(home / "Documents" / "a.txt")) is None
