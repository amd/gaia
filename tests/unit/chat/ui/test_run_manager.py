# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for the per-session background chat-run registry (#1580).

These exercise ``RunManager``/``Run`` in isolation — no agent, no LLM, no
HTTP. The lifecycle coroutine is a stub that emits a few SSE strings, so we
can assert the registry's contract: runs outlive subscriber disconnects,
late subscribers get a full replay, completion closes subscribers and
deregisters the run, and overlapping starts are rejected.
"""

import asyncio

import pytest

from gaia.ui.run_manager import DONE, Run, RunManager


class _FakeHandler:
    """Minimal stand-in for SSEOutputHandler — only ``cancelled`` is used."""

    def __init__(self):
        import threading

        self.cancelled = threading.Event()


async def _drain(q: asyncio.Queue) -> list:
    """Drain a subscriber queue up to (and excluding) the DONE sentinel."""
    out = []
    while True:
        item = await q.get()
        if item is DONE:
            break
        out.append(item)
    return out


async def test_run_completes_and_deregisters():
    mgr = RunManager()

    async def lifecycle(run: Run):
        run.handler = _FakeHandler()
        run.emit("data: a\n\n")
        run.emit("data: b\n\n")

    run = mgr.start("sess-1", lifecycle)
    assert mgr.is_running("sess-1")
    assert "sess-1" in mgr.active_sessions()

    q = run.subscribe()
    events = await _drain(q)

    assert events == ["data: a\n\n", "data: b\n\n"]
    await run.task  # ensure the lifecycle's finally has run
    assert not mgr.is_running("sess-1")
    assert run.done.is_set()


async def test_run_survives_subscriber_disconnect():
    """A run keeps emitting + completing after its only subscriber detaches."""
    mgr = RunManager()
    gate = asyncio.Event()

    async def lifecycle(run: Run):
        run.emit("data: first\n\n")
        await gate.wait()  # hold until the test detaches the subscriber
        run.emit("data: after-disconnect\n\n")

    run = mgr.start("sess-2", lifecycle)
    q = run.subscribe()
    assert await q.get() == "data: first\n\n"

    # Simulate the browser navigating away: drop the subscriber mid-run.
    run.unsubscribe(q)
    assert run.subscribers == []

    # The run must continue and finish regardless.
    gate.set()
    await run.task
    assert run.done.is_set()
    assert not mgr.is_running("sess-2")
    # The post-disconnect event still landed in the replay buffer.
    assert "data: after-disconnect\n\n" in run.buffer


async def test_late_subscriber_gets_full_replay():
    """Attaching after events were emitted replays history then live events."""
    mgr = RunManager()
    emitted_two = asyncio.Event()
    release = asyncio.Event()

    async def lifecycle(run: Run):
        run.emit("data: 1\n\n")
        run.emit("data: 2\n\n")
        emitted_two.set()
        await release.wait()
        run.emit("data: 3\n\n")

    run = mgr.start("sess-3", lifecycle)
    await emitted_two.wait()

    # Attach late — should replay 1 & 2, then receive live 3.
    q = run.subscribe()
    release.set()
    events = await _drain(q)

    assert events == ["data: 1\n\n", "data: 2\n\n", "data: 3\n\n"]
    await run.task


async def test_subscribe_after_done_terminates_immediately():
    mgr = RunManager()

    async def lifecycle(run: Run):
        run.emit("data: only\n\n")

    run = mgr.start("sess-4", lifecycle)
    await run.task  # run fully finished before anyone subscribes

    q = run.subscribe()
    events = await _drain(q)
    assert events == ["data: only\n\n"]
    # No lingering registration for a finished run.
    assert run.subscribers == []


async def test_overlapping_start_raises():
    mgr = RunManager()
    release = asyncio.Event()

    async def lifecycle(run: Run):
        await release.wait()

    mgr.start("sess-5", lifecycle)
    with pytest.raises(RuntimeError):
        mgr.start("sess-5", lifecycle)

    release.set()


async def test_cancel_sets_handler_flag():
    mgr = RunManager()
    started = asyncio.Event()
    release = asyncio.Event()

    async def lifecycle(run: Run):
        run.handler = _FakeHandler()
        started.set()
        await release.wait()

    run = mgr.start("sess-6", lifecycle)
    await started.wait()

    assert mgr.cancel("sess-6") is True
    assert run.handler.cancelled.is_set()

    release.set()
    await run.task

    # Cancelling an unknown / finished session is a safe no-op.
    assert mgr.cancel("nope") is False


async def test_active_sessions_reflects_lifecycle():
    mgr = RunManager()
    release = asyncio.Event()

    async def lifecycle(run: Run):
        await release.wait()

    mgr.start("a", lifecycle)
    mgr.start("b", lifecycle)
    assert set(mgr.active_sessions()) == {"a", "b"}

    release.set()
    await asyncio.gather(*(r.task for r in [mgr.get("a"), mgr.get("b")] if r))
    assert mgr.active_sessions() == []
