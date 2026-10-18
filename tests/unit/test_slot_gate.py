# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Side work shares the model with the conversation, without overflowing it.

Two sequences on llama.cpp's two slots decode at nearly twice one's throughput
(88-90 vs 46-49 tok/s, Gemma-4-E4B on a Radeon 8060S), so a memory extraction
must not make the user's turn wait behind it (#5022). These pin the rules that
keep the overlap safe: one request per slot, a KV budget across slots, loads
exclusive, and a process's concurrent requests sharing one broker lease.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from gaia.daemon import broker_client
from gaia.daemon.broker import ModelSlotBroker
from gaia.daemon.constants import BROKER_TOKEN_ENV_VAR, BROKER_URL_ENV_VAR
from gaia.llm import slot_gate
from gaia.llm.slot_gate import SlotGate, estimate_tokens


@pytest.fixture(autouse=True)
def _fresh_gates(monkeypatch):
    """A hold leaked by one test must not block the next."""
    monkeypatch.setattr(slot_gate, "_GATES", {})


def _in_thread(fn):
    """Run *fn* on a thread; return (thread, started-event, done-event)."""
    started, done = threading.Event(), threading.Event()

    def run():
        started.set()
        fn()
        done.set()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, started, done


def test_the_estimate_counts_the_prompt_and_every_token_it_may_emit():
    assert estimate_tokens(3000, 500) == 1000 + 500


def test_both_slots_run_together_when_they_fit():
    gate = SlotGate()
    inside, leave = threading.Event(), threading.Event()

    def side():
        with gate.hold(1, 10_000, 65_536):
            inside.set()
            leave.wait(2)

    with gate.hold(0, 30_000, 65_536):
        t, _, _ = _in_thread(side)
        assert inside.wait(2)
        assert gate.active_slots() == {0: 30_000, 1: 10_000}
        leave.set()
        t.join(2)
    assert gate.active_slots() == {}


def test_a_request_that_would_overflow_the_pool_waits():
    gate = SlotGate()
    entered = threading.Event()

    def side():
        with gate.hold(1, 40_000, 65_536):
            entered.set()

    with gate.hold(0, 30_000, 65_536):
        t, _, _ = _in_thread(side)
        assert not entered.wait(0.3)
    assert entered.wait(2)
    t.join(2)


def test_a_request_bigger_than_the_pool_still_runs_alone():
    gate = SlotGate()
    with gate.hold(0, 100_000, 65_536):
        assert gate.active_tokens() == 100_000


def test_a_load_waits_for_active_requests_and_blocks_new_ones():
    gate = SlotGate()
    order = []
    loading = threading.Event()

    def load():
        with gate.exclusive():
            loading.set()
            order.append("load")
            time.sleep(0.1)

    def late_request():
        with gate.hold(1, 10, 65_536):
            order.append("late")

    with gate.hold(0, 10, 65_536):
        loader, _, _ = _in_thread(load)
        time.sleep(0.1)
        assert not loading.is_set()
        late, _, _ = _in_thread(late_request)
        time.sleep(0.1)
        order.append("first done")
    loader.join(2)
    late.join(2)
    assert order == ["first done", "load", "late"]


def test_the_conversation_may_reload_mid_request():
    """The pre-flight inside a turn's own hold decides a reload is needed."""
    gate = SlotGate()
    with gate.hold(0, 10, 65_536):
        with gate.exclusive():
            assert gate.active_slots() == {0: 10}
        assert gate.active_slots() == {0: 10}
    assert gate.active_slots() == {}


def test_a_reload_waits_for_side_work_already_running():
    gate = SlotGate()
    side_may_finish = threading.Event()
    reloaded = threading.Event()

    def side():
        with gate.hold(1, 10, 65_536):
            side_may_finish.wait(2)

    t, _, _ = _in_thread(side)
    time.sleep(0.05)
    with gate.hold(0, 10, 65_536):
        threading.Timer(0.2, side_may_finish.set).start()
        with gate.exclusive():
            reloaded.set()
            assert gate.active_slots() == {0: 10}
    t.join(2)
    assert reloaded.is_set()


def test_side_work_that_needs_a_load_steps_off_its_slot_first():
    """Two holders each waiting for the other to finish would wait forever."""
    gate = SlotGate()
    turn_loading, side_inside = threading.Event(), threading.Event()
    order = []

    def turn_reload():
        with gate.hold(0, 10, 65_536):
            side_inside.wait(2)
            with gate.exclusive():
                turn_loading.set()
                order.append("turn load")

    def side():
        with gate.hold(1, 10, 65_536):
            side_inside.set()
            turn_loading.wait(0.2)
            with gate.exclusive():
                order.append("side load")
            assert gate.active_slots().get(1) == 10
            order.append("side request")

    t, _, _ = _in_thread(turn_reload)
    s_, _, _ = _in_thread(side)
    t.join(3)
    s_.join(3)
    assert not t.is_alive() and not s_.is_alive()
    assert sorted(order) == ["side load", "side request", "turn load"]
    assert order.index("side load") < order.index("side request")
    assert gate.active_slots() == {}


def test_a_nested_hold_on_another_slot_is_refused():
    gate = SlotGate()
    with gate.hold(0, 10, 65_536):
        with pytest.raises(RuntimeError, match="still holding slot 0"):
            with gate.hold(1, 10, 65_536):
                pass
    assert gate.active_slots() == {}


def test_a_pending_load_is_visible_to_new_requests():
    gate = SlotGate()
    with gate.hold(0, 10, 65_536):
        t, _, _ = _in_thread(lambda: gate.exclusive().__enter__())
        time.sleep(0.1)
        assert gate.exclusive_pending()
    t.join(2)


def test_a_nested_hold_is_free():
    gate = SlotGate()
    with gate.hold(0, 10, 65_536):
        with gate.hold(0, 10, 65_536):
            assert gate.active_slots() == {0: 10}
        assert gate.active_slots() == {0: 10}
    assert gate.active_slots() == {}


# ── one broker lease per process, shared by its concurrent requests ─────────


@pytest.fixture
def local_broker(monkeypatch):
    for var in (BROKER_URL_ENV_VAR, BROKER_TOKEN_ENV_VAR):
        os.environ.pop(var, None)
    broker_client._held.set(None)
    monkeypatch.setattr(broker_client, "_discovery_enabled", False)
    monkeypatch.setattr(broker_client, "_attached", None)
    monkeypatch.setattr(broker_client, "_broker_unsupported", False)
    monkeypatch.setattr(broker_client, "_shared_lease", None)
    monkeypatch.setattr("gaia.daemon.client.attach", lambda *a, **k: None)
    monkeypatch.setenv(BROKER_URL_ENV_VAR, "http://127.0.0.1:0")
    monkeypatch.setenv(BROKER_TOKEN_ENV_VAR, "test-token")
    broker = ModelSlotBroker()
    calls = {"acquired": 0, "released": 0}

    def acquire(model, *, priority=None, timeout=None, request_timeout=310.0):
        calls["acquired"] += 1
        lease = broker.acquire(model, priority=priority or "background", holder="t")
        return {"lease_id": lease.lease_id, "model": model, "waited": False}

    def release(lease_id, *, request_timeout=10.0):
        calls["released"] += 1
        broker.release(lease_id)

    monkeypatch.setattr(broker_client, "acquire_lease", acquire)
    monkeypatch.setattr(broker_client, "release_lease", release)
    yield calls
    for var in (BROKER_URL_ENV_VAR, BROKER_TOKEN_ENV_VAR):
        os.environ.pop(var, None)
    broker_client._held.set(None)


def test_concurrent_requests_in_one_process_share_one_lease(local_broker):
    joined = threading.Event()

    def side():
        with broker_client.model_lease("m", shared=True, max_hold_s=10):
            joined.set()

    with broker_client.model_lease("m", shared=True, max_hold_s=10):
        t, _, _ = _in_thread(side)
        assert joined.wait(2)
        t.join(2)
        assert local_broker["released"] == 0
    assert local_broker == {"acquired": 1, "released": 1}


def test_the_lease_outlives_its_owner_while_a_joiner_holds_it(local_broker):
    joined, leave = threading.Event(), threading.Event()

    def side():
        with broker_client.model_lease("user.m", shared=True, max_hold_s=10):
            joined.set()
            leave.wait(2)

    with broker_client.model_lease("m", shared=True, max_hold_s=10):
        t, _, _ = _in_thread(side)
        assert joined.wait(2)
    assert local_broker["released"] == 0
    leave.set()
    t.join(2)
    assert local_broker == {"acquired": 1, "released": 1}


def test_a_load_does_not_join_a_shared_lease(local_broker):
    loaded = threading.Event()

    def load():
        with broker_client.model_lease("m"):
            loaded.set()

    with broker_client.model_lease("m", shared=True, max_hold_s=10):
        t, _, _ = _in_thread(load)
        assert not loaded.wait(0.3)
    assert loaded.wait(2)
    t.join(2)
    assert local_broker == {"acquired": 2, "released": 2}


def test_a_different_model_does_not_join(local_broker):
    entered = threading.Event()

    def other():
        with broker_client.model_lease("other", shared=True, max_hold_s=10):
            entered.set()

    with broker_client.model_lease("m", shared=True, max_hold_s=10):
        t, _, _ = _in_thread(other)
        assert not entered.wait(0.3)
    assert entered.wait(2)
    t.join(2)


def test_a_request_that_could_outlast_the_lease_ttl_does_not_join(local_broker):
    """Reclaimed mid-request, the lease would let another process swap models."""
    entered = threading.Event()

    def late():
        with broker_client.model_lease("m", shared=True, max_hold_s=900):
            entered.set()

    with broker_client.model_lease("m", shared=True, max_hold_s=10):
        t, _, _ = _in_thread(late)
        assert not entered.wait(0.3)
    assert entered.wait(2)
    t.join(2)
    assert local_broker["acquired"] == 2
