# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A running index reports pages read, then chunks embedded, and clears when done."""

from unittest.mock import patch

import pytest

pypdf = pytest.importorskip("pypdf")

from gaia.ui import _chat_helpers  # noqa: E402


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
            self.seen.append(dict(_chat_helpers.INDEX_PROGRESS["doc-1"]))
        for done in (0, 4, 8):
            progress_callback(done, 8)
            self.seen.append(dict(_chat_helpers.INDEX_PROGRESS["doc-1"]))
        return {"success": True, "num_chunks": 8}


@pytest.mark.asyncio
async def test_progress_counts_pages_then_chunks_and_clears(tmp_path):
    pdf = tmp_path / "report.pdf"
    _blank_pdf(pdf, 3)
    _FakeRAG.seen = []

    with patch("gaia.rag.sdk.RAGSDK", _FakeRAG):
        chunks = await _chat_helpers._index_document(pdf, progress_key="doc-1")

    assert chunks == 8
    assert _FakeRAG.seen == [
        {"phase": "reading", "done": 1, "total": 3},
        {"phase": "reading", "done": 2, "total": 3},
        {"phase": "reading", "done": 3, "total": 3},
        {"phase": "embedding", "done": 0, "total": 8},
        {"phase": "embedding", "done": 4, "total": 8},
        {"phase": "embedding", "done": 8, "total": 8},
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

    assert _chat_helpers.INDEX_PROGRESS == {}


def test_page_total_is_unknown_for_non_pdf_and_unreadable_files(tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text("# hi")
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf")

    assert _chat_helpers._page_total(notes) is None
    assert _chat_helpers._page_total(broken) is None
