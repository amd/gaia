# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit spec for the model-slot broker queue logic (#2151 / V2-11 · §0.12).

Pure in-process tests — no daemon, no Lemonade, no network. The broker's whole
job is deciding *who goes next*, so these tests pin: serialization (one lease at
a time), interactive-over-background priority queueing, hot-model affinity as the
priority tie-break, the ``switching model…`` wait hook, TTL reclaim of a leaked
lease, and the loud release/timeout errors.
"""

from __future__ import annotations

import threading
import time

import pytest

from gaia.daemon.broker import (
    DEFAULT_LEASE_TTL_S,
    Lease,
    LeaseNotHeldError,
    LeasePriority,
    LeaseTimeoutError,
    ModelSlotBroker,
)

# ---------------------------------------------------------------------------
# LeasePriority.parse — loud on garbage, no silent default
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("interactive", LeasePriority.INTERACTIVE),
        ("INTERACTIVE", LeasePriority.INTERACTIVE),
        ("  background ", LeasePriority.BACKGROUND),
        (LeasePriority.INTERACTIVE, LeasePriority.INTERACTIVE),
        (0, LeasePriority.BACKGROUND),
        (1, LeasePriority.INTERACTIVE),
    ],
)
def test_priority_parse_accepts_valid(value, expected):
    assert LeasePriority.parse(value) == expected


@pytest.mark.parametrize("bad", ["urgent", "", 5, -1, None, True, 1.5])
def test_priority_parse_rejects_invalid(bad):
    with pytest.raises(ValueError):
        LeasePriority.parse(bad)


# ---------------------------------------------------------------------------
# Serialization — one lease at a time, even for different models
# ---------------------------------------------------------------------------


def test_immediate_grant_when_slot_free():
    broker = ModelSlotBroker()
    lease = broker.acquire("model-a", holder="host")
    assert isinstance(lease, Lease)
    assert lease.model == "model-a"
    assert broker.snapshot()["active"]["model"] == "model-a"


def test_second_acquire_blocks_until_release_even_for_different_model():
    """Two agents requesting DIFFERENT models serialize — the core acceptance
    criterion: no race-evict, both complete, but never concurrently."""
    broker = ModelSlotBroker()
    first = broker.acquire("model-a", holder="agent-a")

    granted_b = threading.Event()
    result = {}

    def _take_b():
        lease = broker.acquire("model-b", holder="agent-b")
        result["lease"] = lease
        granted_b.set()

    t = threading.Thread(target=_take_b)
    t.start()
    # B must NOT be granted while A holds the slot.
    assert not granted_b.wait(timeout=0.5)
    assert "lease" not in result

    broker.release(first.lease_id)
    assert granted_b.wait(timeout=2.0)
    t.join(timeout=2.0)
    assert result["lease"].model == "model-b"


def test_release_wrong_id_raises_loud():
    broker = ModelSlotBroker()
    broker.acquire("model-a")
    with pytest.raises(LeaseNotHeldError):
        broker.release("not-the-active-id")


def test_release_when_nothing_held_raises_loud():
    broker = ModelSlotBroker()
    with pytest.raises(LeaseNotHeldError):
        broker.release("anything")


def test_acquire_timeout_raises_when_slot_stays_held():
    broker = ModelSlotBroker()
    broker.acquire("model-a", holder="agent-a")
    with pytest.raises(LeaseTimeoutError):
        broker.acquire("model-b", holder="agent-b", timeout=0.2)


# ---------------------------------------------------------------------------
# Priority queueing — interactive jumps ahead of queued background
# ---------------------------------------------------------------------------


def test_interactive_jumps_ahead_of_queued_background():
    """While a lease is held, a later INTERACTIVE waiter is granted before an
    earlier BACKGROUND waiter (queueing, not preemption of the held lease)."""
    broker = ModelSlotBroker()
    held = broker.acquire("model-hot", holder="holder")

    order = []
    order_lock = threading.Lock()
    enqueued = threading.Semaphore(0)

    def _waiter(model, priority, label):
        # Signal that this thread is about to enter acquire, so the test can
        # order enqueues deterministically before releasing the held lease.
        enqueued.release()
        lease = broker.acquire(model, priority=priority, holder=label)
        with order_lock:
            order.append(label)
        broker.release(lease.lease_id)

    bg = threading.Thread(
        target=_waiter, args=("model-bg", LeasePriority.BACKGROUND, "bg")
    )
    bg.start()
    enqueued.acquire()
    # Give the background waiter time to actually enqueue (seq order) first.
    _wait_until(lambda: _num_waiting(broker) == 1, timeout=2.0)

    fg = threading.Thread(
        target=_waiter, args=("model-fg", LeasePriority.INTERACTIVE, "fg")
    )
    fg.start()
    enqueued.acquire()
    _wait_until(lambda: _num_waiting(broker) == 2, timeout=2.0)

    # Release the held lease: fg (interactive) must win despite arriving second.
    broker.release(held.lease_id)
    bg.join(timeout=3.0)
    fg.join(timeout=3.0)
    assert order == ["fg", "bg"]


# ---------------------------------------------------------------------------
# Hot-model affinity — priority tie broken toward the already-loaded model
# ---------------------------------------------------------------------------


def test_hot_model_affinity_breaks_priority_tie():
    """Equal priority: the waiter whose model is already loaded is preferred,
    avoiding a needless evict+reload."""
    broker = ModelSlotBroker()
    # model-hot becomes the loaded/hot model.
    held = broker.acquire("model-hot", holder="holder")

    order = []
    order_lock = threading.Lock()
    enqueued = threading.Semaphore(0)

    def _waiter(model, label):
        enqueued.release()
        lease = broker.acquire(model, priority=LeasePriority.BACKGROUND, holder=label)
        with order_lock:
            order.append(label)
        broker.release(lease.lease_id)

    # "cold" arrives first, "hot" (matching the loaded model) arrives second.
    cold = threading.Thread(target=_waiter, args=("model-cold", "cold"))
    cold.start()
    enqueued.acquire()
    _wait_until(lambda: _num_waiting(broker) == 1, timeout=2.0)

    hot = threading.Thread(target=_waiter, args=("model-hot", "hot"))
    hot.start()
    enqueued.acquire()
    _wait_until(lambda: _num_waiting(broker) == 2, timeout=2.0)

    broker.release(held.lease_id)
    cold.join(timeout=3.0)
    hot.join(timeout=3.0)
    # Affinity: the hot-model waiter wins the tie despite arriving later.
    assert order == ["hot", "cold"]


def test_fifo_when_priority_and_affinity_equal():
    broker = ModelSlotBroker()
    held = broker.acquire("model-x", holder="holder")

    order = []
    order_lock = threading.Lock()
    enqueued = threading.Semaphore(0)

    def _waiter(label):
        enqueued.release()
        lease = broker.acquire(
            "model-y", priority=LeasePriority.BACKGROUND, holder=label
        )
        with order_lock:
            order.append(label)
        broker.release(lease.lease_id)

    first = threading.Thread(target=_waiter, args=("first",))
    first.start()
    enqueued.acquire()
    _wait_until(lambda: _num_waiting(broker) == 1, timeout=2.0)
    second = threading.Thread(target=_waiter, args=("second",))
    second.start()
    enqueued.acquire()
    _wait_until(lambda: _num_waiting(broker) == 2, timeout=2.0)

    broker.release(held.lease_id)
    first.join(timeout=3.0)
    second.join(timeout=3.0)
    assert order == ["first", "second"]


# ---------------------------------------------------------------------------
# switching model… wait hook (legibility)
# ---------------------------------------------------------------------------


def test_on_wait_not_called_on_immediate_grant():
    broker = ModelSlotBroker()
    calls = []
    broker.acquire("model-a", on_wait=calls.append)
    assert calls == []


def test_on_wait_reports_switching_model_when_queued_behind_other_model():
    broker = ModelSlotBroker()
    held = broker.acquire("model-a", holder="holder")

    reasons = []
    got_reason = threading.Event()

    def _take():
        def _hook(reason):
            reasons.append(reason)
            got_reason.set()

        lease = broker.acquire("model-b", holder="agent-b", on_wait=_hook)
        broker.release(lease.lease_id)

    t = threading.Thread(target=_take)
    t.start()
    assert got_reason.wait(timeout=2.0)
    assert reasons and "switching model" in reasons[0]
    broker.release(held.lease_id)
    t.join(timeout=2.0)


# ---------------------------------------------------------------------------
# TTL reclaim of a leaked lease (injected clock — deterministic)
# ---------------------------------------------------------------------------


def test_stale_lease_reclaimed_after_ttl():
    clock = {"t": 1000.0}
    broker = ModelSlotBroker(lease_ttl_s=30.0, time_fn=lambda: clock["t"])
    broker.acquire("model-a", holder="crashed-holder")
    # Holder "crashes" without releasing; advance time past the TTL.
    clock["t"] += 31.0
    # A new acquire reclaims the stale lease and is granted.
    lease = broker.acquire("model-b", holder="agent-b", timeout=1.0)
    assert lease.model == "model-b"


def test_default_ttl_is_generous():
    assert DEFAULT_LEASE_TTL_S >= 300.0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _num_waiting(broker: ModelSlotBroker) -> int:
    return len(broker.snapshot()["waiting"])


def _wait_until(pred, timeout=2.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(interval)
    raise AssertionError("condition not met within timeout")
