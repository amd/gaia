# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Recordings attached in the Agent UI reach the agent, at any size."""

import os
import re
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.audio.media import AUDIO_EXTENSIONS, MEDIA_EXTENSIONS, VIDEO_EXTENSIONS
from gaia.ui import utils
from gaia.ui.routers import files

UI_LIST = (
    Path(__file__).resolve().parents[4] / "src/gaia/apps/webui/src/utils/mediaFiles.ts"
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "uploads_dir", lambda: tmp_path)
    app = FastAPI()
    app.include_router(files.router)
    return TestClient(app)


def _upload(client, name, body=b"media bytes"):
    return client.post("/api/files/upload-media", params={"name": name}, content=body)


@pytest.mark.parametrize("ext", sorted(MEDIA_EXTENSIONS))
def test_every_format_is_accepted(client, tmp_path, ext):
    response = _upload(client, f"talk{ext}")

    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["is_image"] is False
    assert (tmp_path / saved["filename"]).read_bytes() == b"media bytes"


def test_a_recording_past_the_document_limit_is_accepted(client, monkeypatch):
    monkeypatch.setattr(files, "MAX_UPLOAD_SIZE", 4)

    assert _upload(client, "long.mp4").status_code == 200


def test_a_document_is_not_accepted_as_a_recording(client, tmp_path):
    assert _upload(client, "notes.txt").status_code == 400
    assert list(tmp_path.iterdir()) == []


def test_typescript_is_never_a_recording():
    """.ts is also MPEG transport stream; a code file must stay a document."""
    assert not MEDIA_EXTENSIONS & utils.ALLOWED_EXTENSIONS


def test_no_room_is_refused_before_a_byte_lands(client, tmp_path, monkeypatch):
    usage = type("Usage", (), {"free": 1})()
    monkeypatch.setattr(files.shutil, "disk_usage", lambda _path: usage)

    assert _upload(client, "long.mp4").status_code == 507
    assert list(tmp_path.iterdir()) == []


def test_old_recordings_are_cleared_and_other_files_are_not(client, tmp_path):
    old = tmp_path / "old.mp4"
    note = tmp_path / "old.txt"
    fresh = tmp_path / "fresh.mp4"
    for path in (old, note, fresh):
        path.write_bytes(b"x")
    long_ago = time.time() - (files.MEDIA_RETENTION_DAYS + 1) * 86400
    os.utime(old, (long_ago, long_ago))
    os.utime(note, (long_ago, long_ago))

    _upload(client, "new.mp4")

    assert not old.exists()
    assert note.exists()
    assert fresh.exists()


def test_a_recording_over_its_own_limit_leaves_nothing_behind(
    client, tmp_path, monkeypatch
):
    monkeypatch.setattr(files, "MAX_MEDIA_UPLOAD_SIZE", 4)

    response = _upload(client, "huge.mp4")

    assert response.status_code == 413
    assert list(tmp_path.iterdir()) == []


def test_an_empty_recording_is_refused(client, tmp_path):
    assert _upload(client, "empty.mp4", b"").status_code == 400
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "name",
    [
        "../../evil.mp4",
        "..\\..\\evil.mp4",
        "a b; rm -rf $(x).mp4",
        "تسجيل الاجتماع.mp4",
        "[link](http://x).mp4",
        ".mp4.mp4",
    ],
)
def test_a_hostile_name_stays_a_plain_file_in_the_folder(client, tmp_path, name):
    response = _upload(client, name)

    assert response.status_code == 200
    saved = response.json()["filename"]
    assert re.fullmatch(r"[A-Za-z0-9._-]+\.mp4", saved)
    assert (tmp_path / saved).parent == tmp_path


def test_the_transcript_keeps_a_recognizable_name(client):
    saved = _upload(client, "Quarterly review.mp4").json()["filename"]

    assert saved.startswith("Quarterly_review-")


def test_an_attached_recording_reaches_the_agent_by_path(client, tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "uploads_dir", lambda: tmp_path)
    saved = _upload(client, "talk.mp4").json()

    message = utils.resolve_upload_links(f"[talk.mp4]({saved['url']})")

    assert str(tmp_path / saved["filename"]) in message
    assert utils.attached_upload_paths(message) == [str(tmp_path / saved["filename"])]


@pytest.mark.parametrize("ext", [".mp4", ".mkv", ".mp3", ".m4a"])
def test_the_document_library_points_recordings_at_the_chat(ext):
    assert ext not in utils.ALLOWED_EXTENSIONS

    message = utils.unsupported_type_message(f"talk{ext}")

    assert "Attach" in message
    assert "gaia talk" not in message


def test_the_ui_list_matches_the_backend():
    source = UI_LIST.read_text(encoding="utf-8")
    video, audio = re.findall(r"new Set\(\[(.*?)\]\)", source, re.S)

    assert set(re.findall(r"'(\.\w+)'", video)) == VIDEO_EXTENSIONS
    assert set(re.findall(r"'(\.\w+)'", audio)) == AUDIO_EXTENSIONS
