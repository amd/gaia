# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""An edited library file added again keeps one row, with its new content.

In v0.26.0-rc18, re-adding three files edited during a folder index left two
rows for each path: the re-add inserted a row for the new content, then the
change monitor tried to move the old row onto the same hash, hit
``UNIQUE constraint failed: documents.file_hash`` and marked it "failed"
with no reason.
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from gaia.ui.database import ChatDatabase
from gaia.ui.document_monitor import DocumentMonitor, _compute_file_hash


@pytest.fixture
def db():
    database = ChatDatabase(":memory:")
    yield database
    database.close()


def _add(db, path):
    stat = os.stat(path)
    return db.add_document(
        filename=os.path.basename(path),
        filepath=str(path),
        file_hash=_compute_file_hash(path),
        file_size=stat.st_size,
        chunk_count=1,
        file_mtime=stat.st_mtime,
    )


def _edit(path, text):
    time.sleep(0.05)
    path.write_text(text, encoding="utf-8")


def test_an_edited_file_added_again_updates_its_row(db, tmp_path):
    path = tmp_path / "station_010.txt"
    path.write_text("Station 10 logs 70 litres.", encoding="utf-8")
    first = _add(db, path)
    _edit(path, "Station 10 logs 71 litres.")

    again = _add(db, path)

    assert again["id"] == first["id"]
    assert again["file_hash"] == _compute_file_hash(path)
    assert again["indexing_status"] == "complete"
    assert [d["id"] for d in db.list_documents()] == [first["id"]]


def test_a_different_file_with_the_old_content_still_gets_its_own_row(db, tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("alpha", encoding="utf-8")
    b.write_text("beta", encoding="utf-8")

    first, second = _add(db, a), _add(db, b)

    assert first["id"] != second["id"]
    assert len(db.list_documents()) == 2


@pytest.mark.asyncio
async def test_the_monitor_and_a_re_add_racing_leave_one_complete_row(db, tmp_path):
    path = tmp_path / "station_011.md"
    path.write_text("# Station 11\nlogs 77 litres", encoding="utf-8")
    doc = _add(db, path)
    _edit(path, "# Station 11\nlogs 78 litres")
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_index(_filepath) -> int:
        started.set()
        await release.wait()
        return 2

    monitor = DocumentMonitor(db=db, index_fn=slow_index, interval=0.1)
    await monitor.start()
    try:
        await asyncio.wait_for(started.wait(), timeout=10)
        _add(db, path)  # the user drops the edited file in again
        release.set()
        for _ in range(100):
            if not monitor.reindexing_docs:
                break
            await asyncio.sleep(0.05)
    finally:
        await monitor.stop()

    rows = db.list_documents()
    assert [(r["id"], r["indexing_status"]) for r in rows] == [(doc["id"], "complete")]
    assert rows[0]["file_hash"] == _compute_file_hash(path)


@pytest.mark.asyncio
async def test_a_re_index_that_cannot_be_saved_says_why(db, tmp_path):
    """A file edited to match another library file can't take its hash."""
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("alpha", encoding="utf-8")
    b.write_text("beta", encoding="utf-8")
    doc_a = _add(db, a)
    _add(db, b)
    _edit(a, "beta")
    done = asyncio.Event()

    async def index(_filepath) -> int:
        done.set()
        return 1

    monitor = DocumentMonitor(db=db, index_fn=index, interval=0.1)
    await monitor.start()
    try:
        await asyncio.wait_for(done.wait(), timeout=10)
        for _ in range(100):
            if db.get_document(doc_a["id"])["indexing_status"] == "failed":
                break
            await asyncio.sleep(0.05)
    finally:
        await monitor.stop()

    failed = db.get_document(doc_a["id"])
    assert failed["indexing_status"] == "failed"
    assert failed["last_error"] and "b.txt" in failed["last_error"]


def test_a_chat_keeps_the_edited_file_and_a_stale_failure_is_cleared(db, tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("v1", encoding="utf-8")
    doc = _add(db, path)
    session = db.create_session(title="t")
    db.attach_document(session["id"], doc["id"])
    db.update_document_status(doc["id"], "failed", last_error="stale")
    _edit(path, "v2")

    again = _add(db, path)

    assert again["id"] == doc["id"]
    assert again["indexing_status"] == "complete" and not again["last_error"]
    assert [d["id"] for d in db.get_session_documents(session["id"])] == [doc["id"]]


@pytest.mark.asyncio
async def test_a_re_index_that_raises_says_why(db, tmp_path):
    path = tmp_path / "broken.md"
    path.write_text("v1", encoding="utf-8")
    doc = _add(db, path)
    _edit(path, "v2")

    async def index(_filepath) -> int:
        raise RuntimeError("embedder unreachable")

    monitor = DocumentMonitor(db=db, index_fn=index, interval=0.1)
    await monitor.start()
    try:
        for _ in range(100):
            if db.get_document(doc["id"])["indexing_status"] == "failed":
                break
            await asyncio.sleep(0.05)
    finally:
        await monitor.stop()

    failed = db.get_document(doc["id"])
    assert failed["indexing_status"] == "failed"
    assert "embedder unreachable" in failed["last_error"]
