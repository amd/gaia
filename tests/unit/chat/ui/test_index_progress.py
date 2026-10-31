# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A running index reports pages read, then chunks embedded, and clears when done."""

import asyncio
import threading
import time
from unittest.mock import patch

import pytest

pypdf = pytest.importorskip("pypdf")

from gaia.ui import _chat_helpers  # noqa: E402
from gaia.utils.index_tracker import index_tracker  # noqa: E402


@pytest.fixture(autouse=True)
def clean_tracker():
    index_tracker.reset()
    yield
    index_tracker.reset()


def _blank_pdf(path, pages):
    writer = pypdf.PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    with open(path, "wb") as fh:
        writer.write(fh)


class _FakeRAG:
    """Calls the hooks the way RAGSDK.index_document does, recording progress."""

    seen = []

    def __init__(self, config):
        pass

    def index_document(self, path, progress_callback=None, heartbeat=None, **_):
        for _page in range(3):
            heartbeat()
            self.seen.append(_chat_helpers.index_progress("doc-1"))
        for done in (0, 4, 8):
            progress_callback(done, 8)
            self.seen.append(_chat_helpers.index_progress("doc-1"))
        return {"success": True, "num_chunks": 8}


@pytest.mark.asyncio
async def test_progress_counts_pages_then_chunks_and_clears(tmp_path):
    pdf = tmp_path / "report.pdf"
    _blank_pdf(pdf, 3)
    _FakeRAG.seen = []

    with patch("gaia.rag.sdk.RAGSDK", _FakeRAG):
        chunks = await _chat_helpers._index_document(pdf, progress_key="doc-1")

    assert chunks == 8
    assert [(p["phase"], p["done"], p["total"]) for p in _FakeRAG.seen] == [
        ("reading", 1, 3),
        ("reading", 2, 3),
        ("reading", 3, 3),
        ("embedding", 0, 8),
        ("embedding", 4, 8),
        ("embedding", 8, 8),
    ]
    assert _chat_helpers.index_progress("doc-1") is None


@pytest.mark.asyncio
async def test_no_progress_is_recorded_without_a_key(tmp_path):
    pdf = tmp_path / "report.pdf"
    _blank_pdf(pdf, 1)

    class _Quiet(_FakeRAG):
        def index_document(self, path, progress_callback=None, heartbeat=None, **_):
            heartbeat()
            progress_callback(1, 1)
            return {"success": True, "num_chunks": 1}

    with patch("gaia.rag.sdk.RAGSDK", _Quiet):
        await _chat_helpers._index_document(pdf)

    assert index_tracker.overview()["total"] == 0


@pytest.mark.asyncio
async def test_a_document_joins_the_estimate_with_its_pages_before_a_slot_is_free(
    tmp_path,
):
    pdf = tmp_path / "report.pdf"
    _blank_pdf(pdf, 3)
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    class _Held(_FakeRAG):
        def index_document(self, path, progress_callback=None, heartbeat=None, **_):
            loop.call_soon_threadsafe(started.set)
            release.wait(5)
            return {"success": True, "num_chunks": 1}

    with patch("gaia.rag.sdk.RAGSDK", _Held):
        task = asyncio.create_task(
            _chat_helpers._index_document(pdf, progress_key="doc-1")
        )
        await asyncio.wait_for(started.wait(), 5)
        assert index_tracker._docs["doc-1"].pages == 3
        release.set()
        assert await task == 1

    assert index_tracker.overview()["done"] == 1


@pytest.mark.asyncio
async def test_cancelling_records_the_outcome_and_stops_the_index(tmp_path):
    pdf = tmp_path / "report.pdf"
    _blank_pdf(pdf, 3)
    started = asyncio.Event()
    stopped = threading.Event()
    loop = asyncio.get_running_loop()

    class _Pages(_FakeRAG):
        def index_document(self, path, progress_callback=None, heartbeat=None, **_):
            loop.call_soon_threadsafe(started.set)
            try:
                while True:
                    heartbeat()
                    time.sleep(0.01)
            finally:
                stopped.set()

    with patch("gaia.rag.sdk.RAGSDK", _Pages):
        task = asyncio.create_task(
            _chat_helpers._index_document(pdf, progress_key="doc-1")
        )
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert stopped.wait(5)
    assert index_tracker.overview()["cancelled"] == 1


@pytest.mark.asyncio
async def test_a_failed_index_keeps_its_reason_in_the_overview(tmp_path):
    pdf = tmp_path / "report.pdf"
    _blank_pdf(pdf, 1)

    class _Broken(_FakeRAG):
        def index_document(self, path, progress_callback=None, heartbeat=None, **_):
            return {"success": False, "error": "the embedder stopped"}

    with patch("gaia.rag.sdk.RAGSDK", _Broken):
        with pytest.raises(RuntimeError):
            await _chat_helpers._index_document(pdf, progress_key="doc-1")

    overview = index_tracker.overview()
    assert overview["failed"] == 1
    assert "the embedder stopped" in overview["documents"][0]["error"]


def test_page_total_is_unknown_for_non_pdf_and_unreadable_files(tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text("# hi")
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf")

    assert _chat_helpers._page_total(notes) is None
    assert _chat_helpers._page_total(broken) is None
