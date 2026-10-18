# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Scheduled runs take their turn on the model, and a timed-out one stops.

A scheduled run builds and runs a flagship agent. It must wait in the same line
as chat turns (two agents on one model evict each other's cache, and agent
construction writes the shared tool registry), and a run that times out must
tell its agent to stop and must not leave threads piling up behind it.
"""

import asyncio
import threading

import pytest
from fastapi.testclient import TestClient

from gaia.ui import _chat_helpers as helpers
from gaia.ui import server
from gaia.ui.turn_queue import TurnQueue

pytestmark = pytest.mark.allow_network


@pytest.fixture
def queue(monkeypatch):
    # The app's startup must not reach a real model server.
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:9/api/v1")
    q = TurnQueue()
    monkeypatch.setattr("gaia.ui.turn_queue.turn_queue", q)
    # The app's startup sets a process-wide registry; keep it out of later tests.
    monkeypatch.setattr(helpers, "_agent_registry", None)
    return q


def test_scheduled_run_waits_for_the_chat_holding_the_model(monkeypatch, queue):
    started = threading.Event()

    def fake_run(_db, _prompt, *_args, **_kwargs):
        started.set()
        return "scheduled answer"

    monkeypatch.setattr(server, "_run_scheduled_prompt", fake_run)
    with TestClient(server.create_app(db_path=":memory:")) as client:
        executor = client.app.state.scheduler._executor

        async def scenario():
            await queue.acquire("a-chat")
            run = asyncio.create_task(executor("summarise my inbox"))
            await asyncio.sleep(0.3)
            assert not started.is_set(), "ran while a chat held the model"
            queue.release()
            assert await asyncio.wait_for(run, 5) == "scheduled answer"
            assert not queue.locked()

        client.portal.call(scenario)


def test_timed_out_scheduled_run_stops_and_keeps_the_model_until_it_has(
    monkeypatch, queue
):
    monkeypatch.setenv("GAIA_SCHEDULE_TIMEOUT", "0.2")
    # An older build freed the model after this grace even if still running.
    monkeypatch.setattr(server, "_SCHEDULE_STOP_GRACE_SECONDS", 0.1, raising=False)
    unstick = threading.Event()
    calls = []

    def stuck_run(_db, _prompt, *_args, **kwargs):
        calls.append(kwargs.get("cancel_event"))
        unstick.wait(30)  # Ignores the cancel, like a hung tool call.
        return "late"

    monkeypatch.setattr(server, "_run_scheduled_prompt", stuck_run)
    try:
        with TestClient(server.create_app(db_path=":memory:")) as client:
            executor = client.app.state.scheduler._executor

            async def scenario():
                first = asyncio.create_task(executor("first"))
                await asyncio.sleep(0.6)
                assert calls[0] is not None and calls[0].is_set(), "not told to stop"
                # Still running: it keeps the model, so others wait in line.
                assert queue.locked() and not first.done()
                second = asyncio.create_task(executor("second"))
                await asyncio.sleep(0.2)
                assert len(calls) == 1, "a second run started on top of the stuck one"

                unstick.set()
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(first, 5)
                assert await asyncio.wait_for(second, 5) == "late"
                assert not queue.locked()

            client.portal.call(scenario)
    finally:
        unstick.set()
