# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""One visible line for the model: FIFO, place in line, withdraw (#5226)."""

import asyncio

import pytest

from gaia.ui.turn_queue import TurnCancelled, TurnQueue, ordinal

pytestmark = pytest.mark.asyncio


async def test_free_slot_is_taken_without_waiting_or_announcing():
    q = TurnQueue()
    seen = []
    await q.acquire("a", on_position=seen.append)
    assert q.holder == "a"
    assert seen == []
    q.release()
    assert not q.locked()


async def test_waiters_run_in_arrival_order_and_hear_their_place():
    q = TurnQueue()
    await q.acquire("a")
    places = {"b": [], "c": []}
    b = asyncio.create_task(q.acquire("b", on_position=places["b"].append))
    c = asyncio.create_task(q.acquire("c", on_position=places["c"].append))
    await asyncio.sleep(0)
    assert q.waiting() == ["b", "c"]
    assert q.position("c") == 2

    q.release()
    await b
    assert q.holder == "b"
    assert places == {"b": [1], "c": [2, 1]}

    q.release()
    await c
    assert q.holder == "c"
    q.release()
    assert not q.locked()


async def test_cancel_withdraws_a_waiter_and_moves_the_line_up():
    q = TurnQueue()
    await q.acquire("a")
    places = []
    b = asyncio.create_task(q.acquire("b"))
    c = asyncio.create_task(q.acquire("c", on_position=places.append))
    await asyncio.sleep(0)

    assert q.cancel("b")
    with pytest.raises(TurnCancelled):
        await b
    assert q.waiting() == ["c"]
    assert places == [2, 1]
    assert not q.cancel("a")  # Holding, not waiting.

    q.release()
    await c
    assert q.holder == "c"


async def test_a_timed_out_waiter_leaves_the_line():
    q = TurnQueue()
    await q.acquire("a")
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q.acquire("b"), timeout=0.01)
    assert q.waiting() == []
    q.release()
    assert not q.locked()


async def test_release_without_a_holder_fails_loudly():
    with pytest.raises(RuntimeError, match="no turn holding it"):
        TurnQueue().release()


@pytest.mark.parametrize(
    "n,expected",
    [(1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (11, "11th"), (22, "22nd")],
)
async def test_ordinal(n, expected):
    assert ordinal(n) == expected


async def test_stop_in_the_tick_after_handover_still_withdraws_the_turn():
    q = TurnQueue()
    await q.acquire("a")
    b = asyncio.create_task(q.acquire("b"))
    c = asyncio.create_task(q.acquire("c"))
    await asyncio.sleep(0)

    q.release()  # "b" now holds the slot but has not resumed yet.
    assert q.holder == "b"
    assert q.cancel("b")
    with pytest.raises(TurnCancelled):
        await b
    await c
    assert q.holder == "c"
