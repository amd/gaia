# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Everything a chat produced, for the session panel's Outputs (#5277).

Each artifact appears once with the turn that made it, survives a reload
(it is read back from the database and snapshots), and shows as undone once
Undo restores its turn.
"""

import pytest
from fastapi.testclient import TestClient

from gaia.agents.base import file_snapshots as fs
from gaia.ui.server import create_app
from gaia.ui.turn_summary import TurnLedger


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


def _ledger(*calls):
    ledger = TurnLedger()
    for tool, args, data, success in calls:
        ledger.tool_started(tool)
        ledger.tool_args(args)
        ledger.tool_result(data, success)
    return ledger


class TestLedgerOutputs:
    def test_records_images_documents_memories_and_schedules(self, tmp_path):
        img = tmp_path / "cat.png"
        img.write_bytes(b"png")
        ledger = _ledger(
            (
                "generate_image",
                {"prompt": "a cat"},
                {"status": "success", "image_path": str(img)},
                True,
            ),
            (
                "index_document",
                {"file_path": str(tmp_path / "r.pdf")},
                {"status": "success"},
                True,
            ),
            (
                "remember",
                {"fact": "Prefers metric units", "category": "preference"},
                {"status": "stored", "knowledge_id": "k1"},
                True,
            ),
            (
                "run_shell_command",
                {
                    "command": 'gaia schedule add --name "Morning brief" --cron "0 8 * * *" --prompt hi'
                },
                "ok",
                True,
            ),
        )
        outs = ledger.summary(location=None)["outputs"]
        assert [(o["kind"], o["name"]) for o in outs] == [
            ("image", "cat.png"),
            ("document", "r.pdf"),
            ("memory", "Prefers metric units"),
            ("schedule", "Morning brief"),
        ]
        assert outs[2]["ref"] == "k1"

    def test_failed_and_skipped_calls_produce_nothing(self, tmp_path):
        ledger = _ledger(
            ("generate_image", {"prompt": "x"}, {"status": "error"}, False),
            ("remember", {"fact": "a secret key"}, {"status": "skipped"}, True),
            (
                "index_document",
                {"file_path": "x.pdf"},
                {"status": "error", "error": "no"},
                True,
            ),
        )
        assert ledger.summary(location=None)["outputs"] == []


def _answer(db, session_id, summary, text="ok"):
    db.add_message(session_id, "user", "do it")
    return db.add_message(session_id, "assistant", text, turn_summary=summary)


def _summary(files=(), outputs=()):
    return {
        "pages": [],
        "documents": [],
        "files": [
            {"path": str(p), "name": p.name, "openable": True, "mtime": 0}
            for p in files
        ],
        "sites": [],
        "outputs": list(outputs),
        "live_data": {"attempted": 0, "failed": 0},
        "cost": {"kind": "local"},
    }


def test_every_artifact_appears_once_newest_first(client, session_id, tmp_path):
    db = client.app.state.db
    report = tmp_path / "report.md"
    report.write_text("v1", encoding="utf-8")
    first = _answer(db, session_id, _summary(files=[report]))
    script = tmp_path / "build.py"
    script.write_text("print(1)", encoding="utf-8")
    page = tmp_path / "page.html"
    page.write_text("<p>", encoding="utf-8")
    second = _answer(
        db,
        session_id,
        _summary(
            files=[report, script, page],
            outputs=[{"kind": "memory", "name": "Likes tea", "ref": "k9"}],
        ),
    )

    items = client.get(f"/api/sessions/{session_id}/outputs").json()["items"]
    names = [i["name"] for i in items]
    assert names.count("report.md") == 1, "a file edited twice is listed once"
    by = {i["name"]: i for i in items}
    assert by["report.md"]["message_id"] == second
    assert by["Likes tea"]["kind"] == "memory"
    # Open runs nothing: scripts and web pages only show in their folder.
    assert by["report.md"]["openable"] is True
    assert by["build.py"]["openable"] is False and by["build.py"]["revealable"] is True
    assert (
        by["page.html"]["openable"] is False and by["page.html"]["revealable"] is True
    )
    assert first < second


def test_undone_turn_shows_its_files_as_undone(client, session_id, tmp_path):
    db = client.app.state.db
    target = tmp_path / "notes.md"
    with fs.recording_turn(session_id) as rec:
        fs.before_change(target, "write_file")
        target.write_text("new", encoding="utf-8")
    mid = _answer(db, session_id, _summary(files=[target]))
    rec.link_message(mid)

    item = client.get(f"/api/sessions/{session_id}/outputs").json()["items"][0]
    assert (item["change"], item["state"]) == ("created", "present")

    client.post(f"/api/sessions/{session_id}/changes/{rec.turn_id}/undo", json={})
    item = client.get(f"/api/sessions/{session_id}/outputs").json()["items"][0]
    assert item["state"] == "undone"
    assert item["openable"] is False and item["revealable"] is False


def test_a_deleted_file_is_listed_from_the_snapshots(client, session_id, tmp_path):
    db = client.app.state.db
    target = tmp_path / "old.txt"
    target.write_text("bye", encoding="utf-8")
    with fs.recording_turn(session_id) as rec:
        fs.before_change(target, "delete_file")
        target.unlink()
    mid = _answer(db, session_id, _summary())
    rec.link_message(mid)
    (item,) = client.get(f"/api/sessions/{session_id}/outputs").json()["items"]
    assert (item["name"], item["change"], item["message_id"]) == (
        "old.txt",
        "deleted",
        mid,
    )
    assert item["state"] == "present"


def test_a_file_deleted_outside_gaia_is_gone(client, session_id, tmp_path):
    db = client.app.state.db
    target = tmp_path / "gone.md"
    target.write_text("x", encoding="utf-8")
    _answer(db, session_id, _summary(files=[target]))
    target.unlink()
    (item,) = client.get(f"/api/sessions/{session_id}/outputs").json()["items"]
    assert item["state"] == "gone" and item["openable"] is False


def test_documents_added_to_the_chat_are_listed(client, session_id, tmp_path):
    db = client.app.state.db
    doc = tmp_path / "manual.pdf"
    doc.write_bytes(b"%PDF")
    record = db.add_document("manual.pdf", str(doc), "hash1", 4, 1)
    db.attach_document(session_id, record["id"])
    (item,) = client.get(f"/api/sessions/{session_id}/outputs").json()["items"]
    assert (item["kind"], item["name"], item["message_id"]) == (
        "document",
        "manual.pdf",
        None,
    )


def test_survives_a_reload(home, tmp_path):
    db_path = tmp_path / "chat.db"
    target = tmp_path / "keep.md"
    target.write_text("x", encoding="utf-8")
    first = TestClient(create_app(db_path=str(db_path)))
    sid = first.post("/api/sessions", json={}).json()["id"]
    _answer(first.app.state.db, sid, _summary(files=[target]))
    first.app.state.db.close()

    second = TestClient(create_app(db_path=str(db_path)))
    items = second.get(f"/api/sessions/{sid}/outputs").json()["items"]
    assert [i["name"] for i in items] == ["keep.md"]


def test_empty_chat_has_no_outputs(client, session_id):
    assert client.get(f"/api/sessions/{session_id}/outputs").json()["items"] == []


def test_a_running_turn_has_no_change_kind_yet(client, session_id, tmp_path):
    target = tmp_path / "draft.md"
    rec = fs.TurnRecorder(session_id, "t-running", fs.snapshots_root())
    rec.capture(target, "write_file")
    target.write_text("x", encoding="utf-8")
    (item,) = client.get(f"/api/sessions/{session_id}/outputs").json()["items"]
    assert item["name"] == "draft.md"
    assert item["change"] is None
