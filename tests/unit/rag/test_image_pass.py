# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A document is searchable from its text; its images are read in the background."""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path
from types import SimpleNamespace

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
            cache_dir=str(cache),
            show_stats=False,
            allowed_paths=[str(cache.parent)],
            read_images_later=True,
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
    """Read everything readable now, on this thread."""
    cache_dir = sdk.config.cache_dir
    while (job := image_pass._next_page(cache_dir)) is not None:
        image_pass._read_one(cache_dir, job[0], job[1], sdk._read_queued_page)


def _key(sdk: RAGSDK) -> str:
    return next(iter(sdk.file_metadata.values()))["image_pass_key"]


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
    key = _key(sdk)
    cache_dir = sdk.config.cache_dir

    for _ in range(image_pass._PAGE_ATTEMPTS):
        _drain(sdk)
        state = image_pass.status(cache_dir, key)
        state["retry_at"] = 0
        image_pass._save(image_pass._state_path(cache_dir, key), state)
    _drain(sdk)

    state = image_pass.status(cache_dir, key)
    assert state["failed"] == {"1": "the model returned nothing"}
    assert state["done"] == [2]
    assert sdk.vision_calls.count((1, sdk.vision_calls[0][1])) == 3
    assert image_pass.overview(cache_dir)["gave_up"] == {key: 1}


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
    assert image_pass.cancel(sdk.config.cache_dir, _key(sdk))
    _drain(sdk)

    assert sdk.vision_calls == []
    assert sdk.images_still_reading() == []
    assert any("Net revenue" in chunk for chunk in sdk.chunks)


def test_opening_a_stopped_document_does_not_start_reading_again(tmp_path):
    pdf = _pdf(tmp_path / "doc.pdf", TEXT)
    sdk = _sdk(tmp_path / "cache")
    sdk.index_document(str(pdf))
    image_pass.cancel(sdk.config.cache_dir, _key(sdk))

    later = _sdk(tmp_path / "cache")
    later.index_document(str(pdf))

    assert later.images_still_reading() == []


def test_a_document_whose_queue_was_lost_is_queued_again(tmp_path):
    pdf = _pdf(tmp_path / "doc.pdf", TEXT)
    sdk = _sdk(tmp_path / "cache")
    sdk.index_document(str(pdf))
    for leftover in Path(sdk.config.cache_dir).glob("*_images*"):
        leftover.unlink()

    later = _sdk(tmp_path / "cache")
    later.index_document(str(pdf))

    assert later.images_still_reading()[0]["pages_total"] == 1


def test_indexing_the_same_bytes_again_reads_the_images_again(tmp_path):
    pdf = _pdf(tmp_path / "doc.pdf", TEXT)
    sdk = _sdk(tmp_path / "cache")
    sdk.index_document(str(pdf))
    _drain(sdk)
    for cache in Path(sdk.config.cache_dir).glob("*.json*"):
        if "_images" not in cache.name:
            cache.unlink()

    later = _sdk(tmp_path / "cache")
    stats = later.index_document(str(pdf))

    assert stats["success"] and stats["images_reading"]["pages_total"] == 1


def test_changing_the_chunking_keeps_its_own_queue(tmp_path):
    pdf = _pdf(tmp_path / "doc.pdf", TEXT)
    one = _sdk(tmp_path / "cache")
    one.index_document(str(pdf))
    two = _sdk(tmp_path / "cache")
    two.config.chunk_size = 300
    two.index_document(str(pdf))

    assert _key(one) != _key(two)


def test_a_page_that_can_not_be_found_is_not_counted_as_read(tmp_path):
    from gaia.rag.pdf_utils import extract_images_from_page_pymupdf

    assert extract_images_from_page_pymupdf(str(tmp_path / "gone.pdf"), 1) == []
    with pytest.raises(Exception):
        extract_images_from_page_pymupdf(str(tmp_path / "gone.pdf"), 1, strict=True)


def test_one_unreadable_page_does_not_fail_a_scan(tmp_path):
    def read(number):
        if number == 1:
            raise RuntimeError("the model returned nothing")
        return _reads_a_chart(number)

    sdk = _sdk(tmp_path / "cache", read=read)

    stats = sdk.index_document(str(_pdf(tmp_path / "scan.pdf", "", "")))

    assert stats["success"]
    assert any("240 million" in chunk for chunk in sdk.chunks)
    assert stats["images_reading"]["pages_total"] == 1


def test_a_scan_with_no_vision_model_says_why(tmp_path):
    def read(_number):
        raise VisionUnavailable("The vision model is not available")

    sdk = _sdk(tmp_path / "cache", read=read)

    stats = sdk.index_document(str(_pdf(tmp_path / "scan.pdf", "", "")))

    assert not stats["success"]
    assert "vision model" in stats["error"]


def test_progress_stays_quick_while_a_page_is_being_read(tmp_path):
    sdk = _sdk(tmp_path / "cache")
    sdk.index_document(str(_pdf(tmp_path / "doc.pdf", TEXT, TEXT)))
    cache_dir = sdk.config.cache_dir
    entered, release = threading.Event(), threading.Event()

    def slow(_state, _number):
        entered.set()
        release.wait(5)
        return 0

    worker = threading.Thread(
        target=image_pass._work, args=(cache_dir, slow), daemon=True
    )
    worker.start()
    assert entered.wait(5)

    started = time.monotonic()
    image_pass.rename(cache_dir, _key(sdk), "Report.pdf")
    stopped = image_pass.cancel(cache_dir, _key(sdk))
    elapsed = time.monotonic() - started
    release.set()
    worker.join(timeout=10)

    assert elapsed < 1.0
    assert stopped


def test_a_reading_that_was_stopped_midway_is_not_brought_back(tmp_path):
    sdk = _sdk(tmp_path / "cache")
    sdk.index_document(str(_pdf(tmp_path / "doc.pdf", TEXT, TEXT)))
    cache_dir = sdk.config.cache_dir
    key = _key(sdk)

    def stop_while_reading(_state, _number):
        image_pass.cancel(cache_dir, key)
        return 1

    job = image_pass._next_page(cache_dir)
    image_pass._read_one(cache_dir, job[0], job[1], stop_while_reading)

    assert image_pass.progress(image_pass.status(cache_dir, key)) is None
    assert image_pass._next_page(cache_dir) is None


def test_the_worker_waits_for_a_blocked_document_instead_of_leaving(tmp_path):
    def read(_number):
        raise VisionUnavailable("The vision model is not available")

    sdk = _sdk(tmp_path / "cache", read=read)
    sdk.index_document(str(_pdf(tmp_path / "doc.pdf", TEXT)))
    cache_dir = sdk.config.cache_dir
    _drain(sdk)

    worker = threading.Thread(
        target=image_pass._work, args=(cache_dir, sdk._read_queued_page), daemon=True
    )
    worker.start()
    worker.join(timeout=1.5)
    alive = worker.is_alive()
    image_pass.cancel(cache_dir, _key(sdk))
    worker.join(timeout=5)

    assert alive
    assert not worker.is_alive()


def test_a_state_file_can_not_point_outside_the_cache(tmp_path):
    with pytest.raises(ValueError):
        image_pass.cache_file(str(tmp_path), {"cache_name": "../elsewhere.json"})
    with pytest.raises(ValueError):
        image_pass.source_file(str(tmp_path), {"key": "x", "suffix": ".pdf"})


def test_a_chat_scanned_label_mentioning_text_is_kept():
    from gaia.rag.sdk import _useful_image_texts

    kept = "Allergy notice: this product does not contain nuts or dairy at all."
    empty = "No visible text is clearly legible in this image, sorry."

    assert _useful_image_texts([{"text": kept}, {"text": empty}]) == [{"text": kept}]


def test_a_single_file_search_warns_only_about_that_file(tmp_path):
    from gaia.agents.tools.rag_tools import _reports_unread_images

    first = _pdf(tmp_path / "a.pdf", TEXT)
    second = _pdf(tmp_path / "b.pdf", TEXT + "b")
    sdk = _sdk(tmp_path / "cache")
    sdk.index_document(str(first))
    sdk.index_document(str(second))
    mixin = SimpleNamespace(rag=sdk, console=None)
    search = _reports_unread_images(mixin)(
        lambda **_: {"status": "success", "file": str(second.absolute())}
    )

    assert search()["still_indexing"]["names"] == ["b.pdf"]

    image_pass.cancel(
        sdk.config.cache_dir,
        sdk.file_metadata[str(second.absolute())]["image_pass_key"],
    )
    assert "still_indexing" not in search()


def test_a_relayed_turn_carries_the_notice_and_counts_as_a_turn():
    import inspect

    from gaia.ui.email_sidecar import relay

    assert inspect.unwrap(relay.relay_query) is not relay.relay_query


def test_reading_waits_for_a_chat_turn_to_finish(tmp_path):
    sdk = _sdk(tmp_path / "cache")
    sdk.index_document(str(_pdf(tmp_path / "doc.pdf", TEXT)))

    with chat_turn():
        worker = threading.Thread(
            target=image_pass._work,
            args=(sdk.config.cache_dir, sdk._read_queued_page),
            daemon=True,
        )
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
    wire = CanonicalTranslator("run", agent_id="gaia").translate(event)

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


def test_a_relayed_status_keeps_the_notice_for_the_app():
    from gaia.ui.email_sidecar import relay

    emitted = []
    handler = SimpleNamespace(
        _emit=emitted.append, set_relay_question=lambda _question: None
    )
    wire = {
        "type": "status",
        "message": "Still reading images in a.pdf",
        "partial_index": {"names": ["a.pdf"], "pages_done": 1, "pages_total": 4},
    }

    relay._dispatch_one(handler, wire)

    assert emitted[0]["notice"]["kind"] == "partial_index"
    assert emitted[0]["notice"]["names"] == ["a.pdf"]
