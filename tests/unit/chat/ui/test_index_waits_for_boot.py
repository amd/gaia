# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A document added while GAIA's model server is still starting waits for it.

At launch the "Checking LLM server" boot step starts GAIA's own Lemonade. A
document added in those seconds failed with "Lemonade Server is not reachable"
(or a 401 from a server still writing its key), though chat waited.
"""

import asyncio
import threading
from pathlib import Path

import pytest

import gaia.ui._chat_helpers as helpers
from gaia.ui import dispatch
from gaia.ui.dispatch import DispatchQueue, JobStatus


@pytest.fixture(autouse=True)
def _no_boot_queue():
    dispatch.BOOT_QUEUE = None
    yield
    dispatch.BOOT_QUEUE = None


async def test_wait_for_step_returns_once_the_step_finishes():
    queue = DispatchQueue(max_workers=1)
    release = threading.Event()
    queue.dispatch("Checking LLM server", release.wait, step="check_server")
    waiter = asyncio.create_task(queue.wait_for_step("check_server", 10))
    await asyncio.sleep(0.5)
    assert not waiter.done()
    release.set()
    assert await waiter == JobStatus.DONE
    await queue.shutdown()


async def test_wait_for_step_gives_up_at_its_timeout():
    queue = DispatchQueue(max_workers=1)
    release = threading.Event()
    queue.dispatch("Checking LLM server", release.wait, step="check_server")
    assert await queue.wait_for_step("check_server", 0.3) == JobStatus.RUNNING
    release.set()
    await queue.shutdown()


async def test_an_unknown_step_does_not_wait():
    queue = DispatchQueue(max_workers=1)
    assert await queue.wait_for_step("check_server", 10) is None
    await queue.shutdown()


async def test_indexing_starts_only_after_the_server_check(monkeypatch, tmp_path):
    queue = DispatchQueue(max_workers=1)
    dispatch.BOOT_QUEUE = queue
    release = threading.Event()
    queue.dispatch("Checking LLM server", release.wait, step="check_server")
    started = []

    class _Rag:
        def __init__(self, *_a, **_k):
            started.append(True)
            raise RuntimeError("indexing reached the RAG SDK")

    monkeypatch.setattr("gaia.rag.sdk.RAGSDK", _Rag)
    doc = Path(tmp_path / "notes.txt")
    doc.write_text("hello", encoding="utf-8")

    task = asyncio.create_task(helpers._index_document(doc, progress_key="doc-1"))
    try:
        await asyncio.sleep(0.6)
        assert started == []
        # The library row says why it hasn't started, instead of looking stalled.
        assert helpers.INDEX_PROGRESS.get("doc-1", {}).get("phase") == "starting"
        release.set()
        with pytest.raises(Exception):
            await asyncio.wait_for(task, 30)
        assert started == [True]
    finally:
        release.set()  # a failing assert must not leave the boot job blocking exit
        task.cancel()
        await queue.shutdown()
