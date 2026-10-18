# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Scheduled runs take their turn on the model, and a timed-out one stops.

A scheduled run builds and runs a flagship agent. It must wait in the same line
as chat turns (two agents on one model evict each other's cache, and agent
construction writes the shared tool registry), and a run that times out must
tell its agent to stop, keep the model while a model call may still run, and
free it once the run is stuck in a tool.

These drive the scheduler's executor (``_ScheduledRuns``) directly: starting
the whole app would leave its start-up jobs running after the test.
"""

import asyncio
import threading

import pytest

from gaia.ui import server
from gaia.ui.database import ChatDatabase
from gaia.ui.turn_queue import TurnQueue

pytestmark = pytest.mark.asyncio


@pytest.fixture
def queue(monkeypatch):
    q = TurnQueue()
    monkeypatch.setattr("gaia.ui.turn_queue.turn_queue", q)
    return q


@pytest.fixture
def db():
    database = ChatDatabase(":memory:")
    yield database
    database.close()


async def test_scheduled_run_waits_for_the_chat_holding_the_model(
    monkeypatch, queue, db
):
    started = threading.Event()

    def fake_run(_db, _prompt, *_args, **_kwargs):
        started.set()
        return "scheduled answer"

    monkeypatch.setattr(server, "_run_scheduled_prompt", fake_run)
    runs = server._ScheduledRuns(db, timeout=5)

    await queue.acquire("a-chat")
    run = asyncio.create_task(runs.run("summarise my inbox"))
    await asyncio.sleep(0.3)
    assert not started.is_set(), "ran while a chat held the model"
    queue.release()
    assert await asyncio.wait_for(run, 5) == "scheduled answer"
    assert not queue.locked()


async def test_timed_out_scheduled_run_stops_and_keeps_the_model_until_it_has(
    monkeypatch, queue, db
):
    unstick = threading.Event()
    calls = []

    def stuck_run(_db, _prompt, *_args, **kwargs):
        calls.append(kwargs.get("cancel_event"))
        unstick.wait(30)  # Generating, and ignoring the stop for now.
        return "late"

    monkeypatch.setattr(server, "_run_scheduled_prompt", stuck_run)
    runs = server._ScheduledRuns(db, timeout=0.2)
    try:
        first = asyncio.create_task(runs.run("first"))
        await asyncio.sleep(0.6)
        assert calls[0] is not None and calls[0].is_set(), "not told to stop"
        # Still in its model call: it keeps the model, so others wait in line.
        assert queue.locked() and not first.done()
        second = asyncio.create_task(runs.run("second"))
        await asyncio.sleep(0.2)
        assert len(calls) == 1, "a second run started on top of the stuck one"

        unstick.set()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(first, 5)
        assert await asyncio.wait_for(second, 5) == "late"
        assert not queue.locked()
    finally:
        unstick.set()


async def test_a_timed_out_run_stuck_in_a_tool_frees_the_model(monkeypatch, queue, db):
    unstick = threading.Event()

    def hung_tool(_db, _prompt, *_args, console=None, **_kwargs):
        console.print_tool_usage("run_shell_command")
        unstick.wait(30)
        return "late"

    monkeypatch.setattr(server, "_run_scheduled_prompt", hung_tool)
    runs = server._ScheduledRuns(db, timeout=0.2)
    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(runs.run("hangs in a tool"), 5)
        assert not queue.locked(), "a run stuck in a tool held the GPU"
    finally:
        unstick.set()
