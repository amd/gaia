# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Adding documents returns at once, indexing runs alone, chats leave it alone."""

import asyncio
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from gaia.agents.tools import rag_tools
from gaia.ui import _chat_helpers
from gaia.ui.models import ChatRequest  # noqa: F401
from gaia.ui.restart_recovery import (
    INTERRUPTED_INDEXING_ERROR,
    recover_interrupted_work,
)
from gaia.ui.server import create_app
from gaia.utils.index_tracker import index_tracker


@pytest.fixture(autouse=True)
def clean_tracker():
    index_tracker.reset()
    yield
    index_tracker.reset()


class Gate:
    """An index function held open until the test lets it finish."""

    def __init__(self, chunks=5):
        self.chunks = chunks
        self.release = threading.Event()
        self.calls = []

    async def __call__(self, path, display_name=None, progress_key=None):
        self.calls.append((path, display_name, progress_key))
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        if isinstance(self.chunks, Exception):
            raise self.chunks
        return self.chunks


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr("gaia.ui.utils.Path.home", lambda: tmp_path)
    return create_app(db_path=":memory:")


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def gate(monkeypatch):
    held = Gate()
    monkeypatch.setattr("gaia.ui.server._index_document", held)
    yield held
    held.release.set()


def wait_for(check, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.01)
    raise AssertionError("condition not reached")


def document_named(client, doc_id):
    return client.get(f"/api/documents/{doc_id}/status").json()["indexing_status"]


def test_the_overview_starts_empty_from_a_cold_app(client):
    body = client.get("/api/documents/indexing").json()
    assert body["active"] is False
    assert body["total"] == 0
    assert body["documents"] == []


def test_a_small_file_added_in_the_background_returns_before_indexing(
    client, gate, tmp_path
):
    small = tmp_path / "notes.txt"
    small.write_text("hello")

    response = client.post(
        "/api/documents/upload-path",
        json={"filepath": str(small), "background": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["indexing_status"] == "indexing"
    assert gate.release.is_set() is False

    overview = client.get("/api/documents/indexing").json()
    assert overview["active"] is True
    assert overview["total"] == 1
    assert overview["documents"][0]["filename"] == "notes.txt"

    gate.release.set()
    wait_for(lambda: document_named(client, body["id"]) == "complete")
    overview = client.get("/api/documents/indexing").json()
    assert overview["active"] is False
    assert overview["done"] == 1


def test_without_the_flag_a_small_file_still_answers_when_indexed(
    client, gate, tmp_path
):
    small = tmp_path / "notes.txt"
    small.write_text("hello")
    gate.release.set()

    body = client.post(
        "/api/documents/upload-path", json={"filepath": str(small)}
    ).json()

    assert body["indexing_status"] == "complete"
    assert body["chunk_count"] == 5


def test_adding_a_file_twice_starts_one_index(client, gate, tmp_path):
    small = tmp_path / "notes.txt"
    small.write_text("hello")
    payload = {"filepath": str(small), "background": True}

    first = client.post("/api/documents/upload-path", json=payload).json()
    second = client.post("/api/documents/upload-path", json=payload).json()

    assert first["id"] == second["id"]
    wait_for(lambda: gate.calls)
    time.sleep(0.05)
    assert len(gate.calls) == 1


def test_a_finished_file_added_again_is_not_read_again(client, gate, tmp_path):
    small = tmp_path / "notes.txt"
    small.write_text("hello")
    payload = {"filepath": str(small), "background": True}
    doc = client.post("/api/documents/upload-path", json=payload).json()
    gate.release.set()
    wait_for(lambda: document_named(client, doc["id"]) == "complete")

    again = client.post("/api/documents/upload-path", json=payload).json()

    assert again["indexing_status"] == "complete"
    assert len(gate.calls) == 1


def test_a_dropped_file_returns_before_indexing_and_a_failure_keeps_its_row(
    client, gate
):
    gate.chunks = RuntimeError("the embedder is gone")

    body = client.post(
        "/api/documents/upload?background=true",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    ).json()

    assert body["indexing_status"] == "indexing"
    gate.release.set()
    wait_for(lambda: document_named(client, body["id"]) == "failed")
    listed = client.get("/api/documents").json()["documents"]
    assert "embedder" in listed[0]["last_error"]
    overview = client.get("/api/documents/indexing").json()
    assert overview["failed"] == 1
    assert "embedder" in overview["documents"][0]["error"]


def test_a_folder_gets_every_row_at_once(client, gate, tmp_path):
    folder = tmp_path / "reports"
    folder.mkdir()
    for name in ("a.txt", "b.txt", "c.txt"):
        (folder / name).write_text(name)

    body = client.post(
        "/api/documents/index-folder",
        json={"folder_path": str(folder), "background": True},
    ).json()

    assert body["queued"] == 3
    assert body["indexed"] == 0
    assert {d["indexing_status"] for d in body["documents"]} == {"indexing"}
    overview = client.get("/api/documents/indexing").json()
    assert overview["total"] == 3
    assert overview["done"] == 0


def test_retry_in_the_background_can_be_cancelled(client, gate, tmp_path):
    small = tmp_path / "notes.txt"
    small.write_text("hello")
    gate.chunks = 0
    gate.release.set()
    doc = client.post(
        "/api/documents/upload-path", json={"filepath": str(small)}
    ).json()
    assert doc["indexing_status"] == "failed"

    gate.chunks = 4
    gate.release.clear()
    retried = client.post(f"/api/documents/{doc['id']}/reindex?background=true").json()
    assert retried["indexing_status"] == "indexing"

    assert client.post(f"/api/documents/{doc['id']}/cancel").status_code == 200
    wait_for(lambda: document_named(client, doc["id"]) == "cancelled")
    overview = client.get("/api/documents/indexing").json()
    assert overview["active"] is False
    assert overview["cancelled"] == 1


def test_cancelling_a_queued_document_keeps_the_others_going(client, gate, tmp_path):
    ids = []
    for name in ("a.txt", "b.txt"):
        path = tmp_path / name
        path.write_text(name)
        ids.append(
            client.post(
                "/api/documents/upload-path",
                json={"filepath": str(path), "background": True},
            ).json()["id"]
        )

    client.post(f"/api/documents/{ids[0]}/cancel")

    overview = client.get("/api/documents/indexing").json()
    assert overview["active"] is True
    assert overview["cancelled"] == 1
    assert document_named(client, ids[1]) == "indexing"


def test_dismiss_clears_a_finished_batch(client, gate, tmp_path):
    small = tmp_path / "notes.txt"
    small.write_text("hello")
    doc = client.post(
        "/api/documents/upload-path",
        json={"filepath": str(small), "background": True},
    ).json()
    gate.release.set()
    wait_for(lambda: document_named(client, doc["id"]) == "complete")
    batch = client.get("/api/documents/indexing").json()["batch_id"]

    assert client.post(f"/api/documents/indexing/{batch}/dismiss").status_code == 200

    assert client.get("/api/documents/indexing").json()["total"] == 0


def test_status_carries_the_estimate_fields(client, gate, tmp_path):
    small = tmp_path / "notes.txt"
    small.write_text("hello")
    doc = client.post(
        "/api/documents/upload-path",
        json={"filepath": str(small), "background": True},
    ).json()

    progress = client.get(f"/api/documents/{doc['id']}/status").json()["progress"]

    assert progress["phase"] == "queued"
    assert progress["seconds_left"] is None
    assert progress["estimate"] == "estimating"


def test_a_restart_mid_index_stays_visible():
    db = MagicMock()
    db.turns_in_progress.return_value = []
    db.fail_unfinished_indexing.return_value = ["doc-1"]
    db.get_document.return_value = {"filename": "report.pdf"}

    recover_interrupted_work(db)

    overview = index_tracker.overview()
    assert overview["active"] is False
    assert overview["failed"] == 1
    assert overview["documents"][0]["error"] == INTERRUPTED_INDEXING_ERROR


# ── Chats ─────────────────────────────────────────────────────────────────


def _db_with(*docs):
    by_id = {d["id"]: d for d in docs}
    db = MagicMock()
    db.get_document.side_effect = by_id.get
    return db


INDEXING = {
    "id": "a",
    "filename": "a.pdf",
    "filepath": "/docs/a.pdf",
    "indexing_status": "indexing",
}
DONE = {**INDEXING, "id": "b", "filepath": "/docs/b.pdf", "indexing_status": "complete"}
CANCELLED = {
    **INDEXING,
    "id": "c",
    "filename": "c.pdf",
    "filepath": "/docs/c.pdf",
    "indexing_status": "cancelled",
}


def test_a_chat_never_gets_a_document_still_indexing_to_index_itself():
    db = _db_with(INDEXING, DONE, CANCELLED)

    paths, _ = _chat_helpers._resolve_rag_paths(db, ["a", "b", "c"])
    still_indexing, not_indexed = _chat_helpers._documents_not_ready(
        db, ["a", "b", "c"]
    )

    assert paths == ["/docs/b.pdf"]
    assert [d["id"] for d in still_indexing] == ["a"]
    assert [d["id"] for d in not_indexed] == ["c"]


def test_the_turn_says_what_is_happening():
    handler = MagicMock()

    _chat_helpers._say_documents_not_ready(handler, [INDEXING], [CANCELLED])

    events = [call.args[0] for call in handler._emit.call_args_list]
    assert events[0]["notice"]["kind"] == "still_indexing"
    assert events[0]["notice"]["names"] == ["a.pdf"]
    assert "a.pdf" in events[0]["message"]
    assert events[1]["notice"]["kind"] == "not_indexed"
    assert events[1]["status"] == "warning"


def test_the_turn_says_nothing_when_every_document_is_ready():
    handler = MagicMock()
    _chat_helpers._say_documents_not_ready(handler, [], [])
    handler._emit.assert_not_called()


def test_the_agents_tools_report_a_file_the_app_is_indexing(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_text("x")
    index_tracker.enqueue("a", "a.pdf", str(pdf))
    index_tracker.report("a", "embedding", 40, 100)
    rag = MagicMock()

    result, job = rag_tools._index_within_budget(rag, str(pdf))

    assert result is None
    rag.index_document.assert_not_called()
    message = rag_tools._indexing_in_progress(job)
    assert message["status"] == "in_progress"
    assert message["chunks_embedded"] == 40
    assert message["total_chunks"] == 100


def test_the_prompt_line_sees_watched_documents_until_they_finish(tmp_path):
    class Rag:
        pass

    rag = Rag()
    pdf = tmp_path / "a.pdf"
    pdf.write_text("x")
    index_tracker.enqueue("a", "a.pdf", str(pdf))

    rag_tools.watch_documents_indexing(rag, [str(pdf)])
    assert rag_tools.documents_still_indexing(rag) == [str(pdf)]

    index_tracker.finish("a", "complete")
    assert rag_tools.documents_still_indexing(rag) == []


class _Sse:
    def __init__(self):
        self.events = []

    def _emit(self, event):
        self.events.append(event)


class _Agent:
    def __init__(self, index):
        self.rag = MagicMock()
        self.rag.index_document.side_effect = index
        self.indexed_files = set()

    def rebuild_system_prompt(self):
        pass


def test_a_chat_index_of_a_file_already_running_starts_nothing(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_text("x")
    index_tracker.enqueue("a", "a.pdf", str(pdf))
    agent = _Agent(lambda *a, **k: {"success": True})

    _chat_helpers._index_rag_with_progress(agent, [str(pdf)], _Sse())

    agent.rag.index_document.assert_not_called()


def test_a_cached_document_never_flashes_through_the_overview(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_text("x")

    def cached(path, progress_callback=None, heartbeat=None):
        progress_callback(10, 10)
        return {"success": True, "num_chunks": 10, "from_cache": True}

    _chat_helpers._index_rag_with_progress(_Agent(cached), [str(pdf)], _Sse())

    assert index_tracker.overview()["total"] == 0


def test_an_index_inside_a_turn_shows_its_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(_chat_helpers, "_CHAT_INDEX_TICK_S", 0)
    pdf = tmp_path / "a.txt"
    pdf.write_text("x")
    seen = []

    def real_work(path, progress_callback=None, heartbeat=None):
        heartbeat()
        progress_callback(0, 4)
        seen.append(index_tracker.overview()["active"])
        progress_callback(4, 4)
        return {"success": True, "num_chunks": 4}

    sse = _Sse()
    _chat_helpers._index_rag_with_progress(_Agent(real_work), [str(pdf)], sse)

    assert seen == [True]
    ticks = [e["index"] for e in sse.events if "index" in e]
    assert ticks[0]["phase"] == "reading"
    assert ticks[-1]["phase"] == "embedding"
    assert ticks[-1]["name"] == Path(pdf).name
    assert index_tracker.overview()["done"] == 1
