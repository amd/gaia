# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A run's replay buffer stays small, and re-attaching still shows the whole reply.

A long turn streams thousands of padded chunk events and a keepalive every
few seconds; keeping every frame made the buffer grow without limit, and every
re-attach replayed all of it. The buffer now keeps what a re-attaching client
needs to rebuild the reply, merged, and is capped.
"""

import json

import pytest

from gaia.ui import run_manager as rm
from gaia.ui.run_manager import DONE, Run

pytestmark = pytest.mark.asyncio


def _frame(event: dict) -> str:
    data = f"data: {json.dumps(event)}\n\n"
    if len(data) < 512:
        data += ": " + "x" * (512 - len(data) - 4) + "\n\n"
    return data


_KEEPALIVE = ": keepalive " + "x" * 490 + "\n\n"


def _events(frames):
    out = []
    for frame in frames:
        line = frame.split("\n", 1)[0]
        if line.startswith("data: "):
            out.append(json.loads(line[len("data: ") :]))
    return out


def _answer(events):
    return "".join(e["content"] for e in events if e["type"] == "chunk")


async def _replay(run):
    q = run.subscribe()
    frames = []
    while not q.empty():
        item = q.get_nowait()
        if item is not DONE:
            frames.append(item)
    run.unsubscribe(q)
    return frames


async def test_a_long_reply_replays_whole_from_a_small_buffer():
    run = Run("s1")
    live = []
    q = run.subscribe()
    run.emit(_frame({"type": "status", "status": "info", "message": "Connecting"}))
    for i in range(3000):
        run.emit(_frame({"type": "chunk", "content": f"w{i} "}))
        if i % 10 == 0:
            run.emit(_KEEPALIVE)
            run.emit(
                _frame({"type": "status", "status": "working", "message": f"{i}s"})
            )
    while not q.empty():
        live.append(q.get_nowait())
    run.unsubscribe(q)

    replayed = await _replay(run)

    assert _answer(_events(replayed)) == _answer(_events(live))
    assert len(run.buffer) < 700, "every frame of the turn is still kept"
    assert sum(len(f) for f in run.buffer) < 300_000
    # The client still flushes small frames immediately.
    assert all(len(f) >= 512 for f in replayed)


async def test_the_buffer_is_capped_and_says_what_it_dropped(monkeypatch):
    monkeypatch.setattr(rm, "MAX_REPLAY_BYTES", 50_000, raising=False)
    run = Run("s2")
    for i in range(400):
        run.emit(_frame({"type": "tool_result", "summary": f"r{i}", "data": "x" * 400}))
    run.emit(_frame({"type": "done", "message_id": 7, "content": "ok"}))

    events = _events(await _replay(run))

    assert sum(len(f) for f in run.buffer) <= 50_000 + 2_000
    assert events[-1]["type"] == "done"
    assert any(
        e["type"] == "status" and "earlier progress" in e["message"].lower()
        for e in events
    )
