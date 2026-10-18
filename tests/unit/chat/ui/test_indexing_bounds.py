# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Document indexing is bounded: in parallelism, in time, and by Cancel.

Indexing a big PDF can embed for many minutes. It must not take over the
event loop's shared thread pool (the chat stream loop needs it), only a few
documents index at once, Cancel stops the embedding, and a stuck index ends
with an error instead of running forever.
"""

import asyncio
import threading
import time
from unittest.mock import patch

import pytest

from gaia.ui import _chat_helpers as helpers

pytestmark = pytest.mark.asyncio


class _SlowRag:
    """Embeds in steps, reporting progress like RAGSDK.index_document."""

    steps = 40
    step_seconds = 0.05
    running = 0
    peak = 0
    stopped_early = 0
    threads: list = []
    lock = threading.Lock()

    def __init__(self, _config):
        pass

    def index_document(self, _path, progress_callback=None):
        cls = type(self)
        with cls.lock:
            cls.running += 1
            cls.peak = max(cls.peak, cls.running)
            cls.threads.append(threading.current_thread().name)
        try:
            for done in range(cls.steps):
                time.sleep(cls.step_seconds)
                if progress_callback is not None:
                    try:
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
    rag = type("Rag", (_SlowRag,), {"running": 0, "peak": 0, "stopped_early": 0})
    rag.threads = []
    doc = tmp_path / "big.pdf"
    doc.write_bytes(b"%PDF-1.4")
    with patch("gaia.rag.sdk.RAGSDK", rag):
        yield rag, doc


async def test_only_a_few_documents_index_at_once_off_the_shared_pool(slow_rag):
    rag, doc = slow_rag
    rag.steps = 4

    counts = await asyncio.gather(*(helpers._index_document(doc) for _ in range(6)))

    assert counts == [4] * 6
    assert rag.peak <= 2
    assert all(name.startswith("gaia-index") for name in rag.threads)


async def test_cancel_stops_the_embedding(slow_rag):
    rag, doc = slow_rag
    task = asyncio.create_task(helpers._index_document(doc))
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    for _ in range(40):
        if rag.running == 0:
            break
        await asyncio.sleep(0.05)
    assert rag.running == 0, "the embedding kept running after Cancel"
    assert rag.stopped_early == 1


async def test_a_stuck_index_ends_with_an_error(slow_rag):
    rag, doc = slow_rag
    with patch.object(helpers, "_INDEX_TIMEOUT_S", 0.3, create=True):
        with pytest.raises(RuntimeError, match="GAIA_INDEX_TIMEOUT"):
            await asyncio.wait_for(helpers._index_document(doc), 5)

    for _ in range(40):
        if rag.running == 0:
            break
        await asyncio.sleep(0.05)
    assert rag.running == 0
