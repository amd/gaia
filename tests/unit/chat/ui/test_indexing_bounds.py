# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Document indexing is bounded: in parallelism, by progress, and by Cancel.

Indexing a big PDF can extract and embed for many minutes. It must not take
over the event loop's shared thread pool (the chat stream loop needs it), only
a few documents index at once, a small upload never waits behind big ones,
Cancel stops the work, and an index that stops making progress ends with an
error while one that keeps moving runs to the end.
"""

import asyncio
import threading
import time
from unittest.mock import patch

import pytest

from gaia.ui import _chat_helpers as helpers

pytestmark = pytest.mark.asyncio


class _SlowRag:
    """Indexes in steps, reporting progress like RAGSDK.index_document."""

    steps = 40
    step_seconds = 0.05
    # Seconds to go silent after the first step (a stalled extraction).
    stall_seconds = 0.0
    # Paths that wait on this event before their first step (big documents).
    hold = None
    hold_suffix = "big.pdf"

    def __init__(self, _config):
        pass

    def index_document(self, path, progress_callback=None, heartbeat=None):
        cls = type(self)
        with cls.lock:
            cls.running += 1
            cls.peak = max(cls.peak, cls.running)
            cls.threads.append(threading.current_thread().name)
        try:
            if cls.hold is not None and path.endswith(cls.hold_suffix):
                cls.hold.wait(10)
            for done in range(cls.steps):
                time.sleep(cls.step_seconds)
                if done == 1 and cls.stall_seconds:
                    time.sleep(cls.stall_seconds)
                try:
                    if heartbeat is not None:
                        heartbeat()
                    if progress_callback is not None:
                        progress_callback(done, cls.steps)
                except BaseException:
                    cls.stopped_early += 1
                    raise
            return {"success": True, "num_chunks": cls.steps}
        finally:
            with cls.lock:
                cls.running -= 1


@pytest.fixture
def slow_rag(tmp_path):
    rag = type(
        "Rag",
        (_SlowRag,),
        {
            "running": 0,
            "peak": 0,
            "stopped_early": 0,
            "threads": [],
            "lock": threading.Lock(),
        },
    )
    doc = tmp_path / "doc.pdf"
    doc.write_bytes(b"%PDF-1.4")
    with patch("gaia.rag.sdk.RAGSDK", rag):
        yield rag, doc


async def _until_idle(rag):
    for _ in range(60):
        if rag.running == 0:
            return
        await asyncio.sleep(0.05)


async def test_only_a_few_documents_index_at_once_off_the_shared_pool(slow_rag):
    rag, doc = slow_rag
    rag.steps = 4

    counts = await asyncio.gather(*(helpers._index_document(doc) for _ in range(6)))

    assert counts == [4] * 6
    assert rag.peak <= 2
    assert all(name.startswith("gaia-index") for name in rag.threads)


async def test_cancel_stops_the_work(slow_rag):
    rag, doc = slow_rag
    task = asyncio.create_task(helpers._index_document(doc))
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await _until_idle(rag)
    assert rag.running == 0, "the index kept running after Cancel"
    assert rag.stopped_early == 1


async def test_a_large_document_that_keeps_moving_is_not_stopped(slow_rag):
    rag, doc = slow_rag
    rag.steps = 30  # 1.5 s in all, progress every 0.05 s
    with patch.object(helpers, "_INDEX_TIMEOUT_S", 0.4):
        assert await asyncio.wait_for(helpers._index_document(doc), 10) == 30


async def test_a_stalled_index_ends_with_an_error_and_stops(slow_rag):
    rag, doc = slow_rag
    rag.stall_seconds = 1.5
    with patch.object(helpers, "_INDEX_TIMEOUT_S", 0.3):
        with pytest.raises(RuntimeError, match="no progress"):
            await asyncio.wait_for(helpers._index_document(doc), 5)

    await _until_idle(rag)
    assert rag.running == 0
    assert rag.stopped_early == 1


async def test_a_small_upload_does_not_wait_behind_big_documents(slow_rag, tmp_path):
    rag, small = slow_rag
    rag.steps = 2
    rag.hold = threading.Event()
    big = tmp_path / "big.pdf"
    big.write_bytes(b"%PDF-1.4" + b"x" * 200)
    try:
        with patch.object(helpers, "_INDEX_SMALL_FILE_BYTES", 100):
            bigs = [asyncio.create_task(helpers._index_document(big)) for _ in range(3)]
            await asyncio.sleep(0.2)
            assert await asyncio.wait_for(helpers._index_document(small), 3) == 2
    finally:
        rag.hold.set()
    assert await asyncio.gather(*bigs) == [2, 2, 2]


async def test_time_waiting_for_a_worker_does_not_count(slow_rag):
    rag, doc = slow_rag
    rag.steps = 6
    rag.step_seconds = 0.1
    with patch.object(helpers, "_INDEX_TIMEOUT_S", 0.5):
        counts = await asyncio.gather(*(helpers._index_document(doc) for _ in range(4)))
    assert counts == [6] * 4


class _QueuedForEmbedderRag(_SlowRag):
    """Waits for the embedder's model-slot lease first, hearing the broker's
    queue updates the way model_lease delivers them, then indexes."""

    queued_seconds = 1.5
    steps = 2

    def index_document(self, path, progress_callback=None, heartbeat=None):
        from gaia.daemon import broker_client

        cls = type(self)
        deadline = time.monotonic() + cls.queued_seconds
        try:
            while time.monotonic() < deadline:
                broker_client.notify_wait(
                    {"event": "queued", "scope": "daemon", "position": 1}
                )
                time.sleep(0.1)
        except BaseException:
            cls.stopped_early += 1
            raise
        return super().index_document(path, progress_callback, heartbeat)


@pytest.fixture
def queued_rag(slow_rag):
    _rag, doc = slow_rag
    queued = type(
        "QueuedRag",
        (_QueuedForEmbedderRag,),
        {
            "running": 0,
            "peak": 0,
            "stopped_early": 0,
            "threads": [],
            "lock": threading.Lock(),
        },
    )
    with patch("gaia.rag.sdk.RAGSDK", queued):
        yield queued, doc


async def test_waiting_for_the_embedder_behind_a_chat_is_not_stopped(queued_rag):
    """A wait for the model slot is not a stall: the broker reports it every
    few seconds, and each report counts as progress."""
    rag, doc = queued_rag
    with patch.object(helpers, "_INDEX_TIMEOUT_S", 0.4):
        assert await asyncio.wait_for(helpers._index_document(doc), 10) == 2
    assert rag.stopped_early == 0


async def test_cancel_stops_an_index_still_waiting_for_the_embedder(queued_rag):
    rag, doc = queued_rag
    rag.queued_seconds = 5
    task = asyncio.create_task(helpers._index_document(doc))
    await asyncio.sleep(0.4)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    deadline = time.monotonic() + 3
    while rag.stopped_early == 0 and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert rag.stopped_early == 1, "the queued wait kept going after Cancel"
