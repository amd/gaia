# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A document is searchable from its text; its images are read in the background."""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from gaia.rag import image_pass
from gaia.rag.image_pass import VisionUnavailable
from gaia.rag.sdk import RAGSDK, RAGConfig
from gaia.utils.turn_activity import chat_turn

fitz = pytest.importorskip("pymupdf")


def _vector(text: str) -> np.ndarray:
    seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "big")
    vector = np.random.default_rng(seed).standard_normal(8).astype("float32")
    return vector / np.linalg.norm(vector)


def _sdk(cache: Path, read=None) -> RAGSDK:
    sdk = RAGSDK(
        RAGConfig(
            cache_dir=str(cache), show_stats=False, allowed_paths=[str(cache.parent)]
        )
    )
    sdk.encoded = []

    def encode(texts, *_args, **_kwargs):
        sdk.encoded.extend(texts)
        return np.vstack([_vector(text) for text in texts])

    sdk._encode_texts = encode
    sdk._encode_query = lambda query: _vector(query).reshape(1, -1)
    sdk._load_embedder = lambda: None
    sdk.vision_calls = []

    def read_images(_path, number, min_cover=0.0):
        sdk.vision_calls.append((number, min_cover))
        return (read or _reads_a_chart)(number)

    sdk._read_page_images = read_images
    return sdk


def _reads_a_chart(number):
    return [
        {
            "image_num": 1,
            "text": f"Chart on page {number}: revenue reached {number}40 million dirhams.",
            "dimensions": "1x1",
            "size_kb": 1,
        }
    ]


@pytest.fixture(autouse=True)
def no_background_thread(monkeypatch):
    monkeypatch.setattr(image_pass, "ensure_running", lambda *_args: None)


def _pdf(path: Path, *page_texts: str) -> Path:
    doc = fitz.open()
    picture = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 50, 50), False)
    picture.clear_with(120)
    for text in page_texts:
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(40, 40, 560, 300), text, fontsize=8)
        page.insert_image(fitz.Rect(40, 320, 440, 620), pixmap=picture)
    doc.save(path)
    doc.close()
    return path


TEXT = "Net revenue rose across the whole group this year. " * 12


def _drain(sdk: RAGSDK) -> None:
    image_pass._work(sdk.config.cache_dir, sdk._read_queued_page)


def test_text_is_searchable_before_any_image_is_read(tmp_path):
    sdk = _sdk(tmp_path / "cache")

    stats = sdk.index_document(str(_pdf(tmp_path / "doc.pdf", TEXT, TEXT)))

    assert stats["success"] and stats["num_chunks"] > 0
    assert sdk.vision_calls == []
    assert stats["images_reading"]["pages_total"] == 2
    assert sdk.images_still_reading()[0]["pages_done"] == 0


def test_a_chat_already_open_finds_image_text_when_it_arrives(tmp_path):
    pdf = _pdf(tmp_path / "doc.pdf", TEXT)
    library = _sdk(tmp_path / "cache")
    library.index_document(str(pdf))
    chat = _sdk(tmp_path / "cache")
    chat.index_document(str(pdf))
    before = len(chat.chunks)

    _drain(library)
    chat.refresh_image_text()

    assert len(chat.chunks) > before
    assert any("140 million" in chunk for chunk in chat.chunks)
    assert chat.images_still_reading() == []
    assert chat.file_embeddings[str(pdf.absolute())].shape[0] == len(chat.chunks)
    assert chat.index.ntotal == len(chat.chunks)


def test_a_finished_document_reloads_without_embedding_anything_again(tmp_path):
    pdf = _pdf(tmp_path / "doc.pdf", TEXT)
    library = _sdk(tmp_path / "cache")
    library.index_document(str(pdf))
    _drain(library)

    later = _sdk(tmp_path / "cache")
    stats = later.index_document(str(pdf))

    assert stats["from_cache"]
    assert later.encoded == []
    assert any("140 million" in chunk for chunk in later.chunks)


def test_a_scan_is_searchable_from_its_first_readable_page(tmp_path):
    sdk = _sdk(
        tmp_path / "cache",
        read=lambda number: [] if number == 1 else _reads_a_chart(number),
    )

    stats = sdk.index_document(str(_pdf(tmp_path / "scan.pdf", "", "", "", "")))

    assert stats["success"]
    assert [call[0] for call in sdk.vision_calls] == [1, 2]
    assert any("240 million" in chunk for chunk in sdk.chunks)
    assert stats["images_reading"]["pages_total"] == 2


def test_pages_short_of_text_are_read_first(tmp_path):
    sdk = _sdk(tmp_path / "cache")
    sdk.index_document(str(_pdf(tmp_path / "doc.pdf", TEXT, "Figure 1", TEXT)))

    _drain(sdk)

    assert [call[0] for call in sdk.vision_calls] == [2, 1, 3]


def test_logo_sized_images_are_left_out_only_on_pages_with_text(tmp_path):
    sdk = _sdk(tmp_path / "cache")
    sdk.index_document(str(_pdf(tmp_path / "doc.pdf", "Figure 1", TEXT)))

    _drain(sdk)

    assert sdk.vision_calls[0][1] == 0.0
    assert sdk.vision_calls[1][1] > 0.0


def test_a_new_process_carries_on_from_the_last_page(tmp_path):
    pdf = _pdf(tmp_path / "doc.pdf", TEXT, TEXT, TEXT)
    first = _sdk(tmp_path / "cache")
    first.index_document(str(pdf))
    key = first.file_metadata[str(pdf.absolute())]["image_pass_key"]
    job = image_pass._next_page(first.config.cache_dir)
    image_pass._read_one(
        first.config.cache_dir, job[0], job[1], first._read_queued_page
    )

    second = _sdk(tmp_path / "cache")
    _drain(second)

    assert [call[0] for call in second.vision_calls] == [2, 3]
    assert image_pass.progress(image_pass.status(first.config.cache_dir, key)) is None


def test_reading_survives_the_temporary_upload_being_deleted(tmp_path):
    upload = _pdf(tmp_path / "upload.pdf", TEXT)
    sdk = _sdk(tmp_path / "cache")
    sdk.index_document(str(upload))
    seen = []

    def read(path, number, min_cover=0.0):  # pylint: disable=unused-argument
        seen.append(Path(path).read_bytes()[:4])
        return _reads_a_chart(number)

    sdk._read_page_images = read
    upload.unlink()
    _drain(sdk)

    assert seen == [b"%PDF"]


def test_a_failed_page_is_recorded_and_the_rest_are_read(tmp_path):
    def read(number):
        if number == 1:
            raise RuntimeError("the model returned nothing")
        return _reads_a_chart(number)

    sdk = _sdk(tmp_path / "cache", read=read)
    sdk.index_document(str(_pdf(tmp_path / "doc.pdf", TEXT, TEXT)))

    _drain(sdk)

    key = next(iter(sdk.file_metadata.values()))["image_pass_key"]
    state = image_pass.status(sdk.config.cache_dir, key)
    assert state["failed"] == {"1": "the model returned nothing"}
    assert state["done"] == [2]


def test_a_missing_vision_model_says_so_and_loses_no_pages(tmp_path):
    def read(_number):
        raise VisionUnavailable("The vision model is not available")

    sdk = _sdk(tmp_path / "cache", read=read)
    sdk.index_document(str(_pdf(tmp_path / "doc.pdf", TEXT, TEXT)))

    _drain(sdk)

    reading = sdk.images_still_reading()[0]
    assert reading["state"] == image_pass.BLOCKED
    assert "vision model" in reading["reason"]
    assert reading["pages_done"] == 0
    assert any("Net revenue" in chunk for chunk in sdk.chunks)


def test_cancel_stops_reading_and_keeps_the_text(tmp_path):
    sdk = _sdk(tmp_path / "cache")
    sdk.index_document(str(_pdf(tmp_path / "doc.pdf", TEXT)))
    key = next(iter(sdk.file_metadata.values()))["image_pass_key"]

    assert image_pass.cancel(sdk.config.cache_dir, key)
    _drain(sdk)

    assert sdk.vision_calls == []
    assert sdk.images_still_reading() == []
    assert any("Net revenue" in chunk for chunk in sdk.chunks)


def test_reading_waits_for_a_chat_turn_to_finish(tmp_path):
    sdk = _sdk(tmp_path / "cache")
    sdk.index_document(str(_pdf(tmp_path / "doc.pdf", TEXT)))

    with chat_turn():
        worker = threading.Thread(target=_drain, args=(sdk,), daemon=True)
        worker.start()
        time.sleep(0.2)
        assert sdk.vision_calls == []
    worker.join(timeout=10)

    assert len(sdk.vision_calls) == 1


def test_clear_cache_takes_the_queue_with_it(tmp_path):
    sdk = _sdk(tmp_path / "cache")
    sdk.index_document(str(_pdf(tmp_path / "doc.pdf", TEXT)))

    sdk.clear_cache()

    assert not list(Path(sdk.config.cache_dir).glob("*_images*"))


def test_overview_counts_what_is_left(tmp_path):
    sdk = _sdk(tmp_path / "cache")
    sdk.index_document(str(_pdf(tmp_path / "a.pdf", TEXT, TEXT)))
    sdk.index_document(str(_pdf(tmp_path / "b.pdf", TEXT + "b")))

    overview = image_pass.overview(sdk.config.cache_dir)

    assert overview["documents"] == 2
    assert overview["pages_total"] == 3
    assert overview["names"] == ["a.pdf", "b.pdf"]


def test_a_search_says_it_ran_on_an_unfinished_index(tmp_path):
    from gaia.agents.tools.rag_tools import _unread_images_notice

    sdk = _sdk(tmp_path / "cache")
    sdk.index_document(str(_pdf(tmp_path / "report.pdf", TEXT, TEXT)))

    unread = _unread_images_notice(sdk.images_still_reading())

    assert unread["notice"] == {
        "kind": "partial_index",
        "names": ["report.pdf"],
        "pages_done": 0,
        "pages_total": 2,
    }
    assert "may be incomplete" in unread["message"]
    assert "0 of 2 pages" in unread["instruction"]


def test_the_notice_reaches_the_wire_once_a_turn():
    from gaia.ui.sse_handler import SSEOutputHandler
    from gaia.ui.sse_translation import CanonicalTranslator

    handler = SSEOutputHandler()
    notice = {
        "kind": "partial_index",
        "names": ["report.pdf"],
        "pages_done": 3,
        "pages_total": 9,
    }
    handler.print_index_notice("Still reading images", notice)
    handler.print_index_notice("Still reading images", notice)

    assert handler.event_queue.qsize() == 1
    event = handler.event_queue.get_nowait()
    wire = CanonicalTranslator("run").translate(event)

    assert wire == [
        {
            "type": "status",
            "message": "Still reading images",
            "partial_index": {
                "names": ["report.pdf"],
                "pages_done": 3,
                "pages_total": 9,
            },
        }
    ]

    handler.print_processing_start("next question", 5)
    handler.event_queue.get_nowait()
    handler.print_index_notice("Still reading images", notice)
    assert handler.event_queue.qsize() == 1
