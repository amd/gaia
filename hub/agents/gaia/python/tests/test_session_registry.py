# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Session-scoped agent retention (#2829, schema 2.12).

The sidecar advertises apiVersion 2.12, whose whole point is that a
``session_id`` resolves a RETAINED agent instead of a throwaway per call.
Without that, anything a turn puts on the instance — most visibly
``Agent.loaded_skills`` — is gone by the next turn, while the model keeps
telling the user the skill is still loaded.
"""

import threading

import pytest
from gaia_agent import session_registry as sr


class _FakeAgent:
    """Stands in for GaiaAgent: just enough surface to track identity + teardown."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.loaded_skills = {}
        self.closed = False

    def close_db(self):
        self.closed = True


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setattr(sr, "build_session_agent", lambda **kw: _FakeAgent(**kw))
    reg = sr._SessionRegistry()
    yield reg
    reg.clear()


def test_same_session_id_returns_the_same_agent(registry):
    """The regression this module exists for: turn 2 must see turn 1's agent."""
    first = registry.get_or_create("session-a")
    first.agent.loaded_skills["github-triage"] = object()

    second = registry.get_or_create("session-a")

    assert second is first
    assert second.agent is first.agent
    assert "github-triage" in second.agent.loaded_skills


def test_different_session_ids_get_isolated_agents(registry):
    a = registry.get_or_create("session-a")
    b = registry.get_or_create("session-b")

    a.agent.loaded_skills["github-triage"] = object()

    assert a.agent is not b.agent
    assert b.agent.loaded_skills == {}


def test_delete_tears_down_the_agent(registry):
    session = registry.get_or_create("session-a")
    agent = session.agent

    assert registry.delete("session-a") is True
    assert agent.closed is True
    assert registry.get("session-a") is None
    # A fresh get_or_create rebuilds rather than resurrecting the dead one.
    assert registry.get_or_create("session-a").agent is not agent


def test_delete_unknown_session_is_false(registry):
    assert registry.delete("never-existed") is False


def test_lru_cap_evicts_the_oldest_idle_session(monkeypatch):
    monkeypatch.setattr(sr, "build_session_agent", lambda **kw: _FakeAgent(**kw))
    reg = sr._SessionRegistry(max_sessions=2)

    reg.get_or_create("a")
    b = reg.get_or_create("b")
    # Re-touch "a" with an explicit, later timestamp — "a" is now MRU, "b" is
    # now LRU. Setting _last_used directly (rather than a second
    # get_or_create + relying on the clock having ticked) keeps this
    # deterministic regardless of monotonic-clock resolution.
    reg._last_used["a"] = reg._last_used["b"] + 1
    reg.get_or_create("c")  # over the cap → evicts the LRU

    # Insertion order alone (FIFO) would evict "a", the first one created —
    # this re-touch is what proves the cap tracks LAST USE, not creation order.
    assert reg.get("a") is not None
    assert reg.get("b") is None
    assert b.agent.closed is True
    assert reg.get("c") is not None
    reg.clear()


def test_cap_refuses_rather_than_evicting_a_running_turn(monkeypatch):
    """Nothing idle to evict → refuse loudly, never silently exceed the cap."""
    monkeypatch.setattr(sr, "build_session_agent", lambda **kw: _FakeAgent(**kw))
    reg = sr._SessionRegistry(max_sessions=1)

    busy = reg.get_or_create("busy")
    busy.run_lock.acquire()  # simulate a turn in flight
    try:
        with pytest.raises(RuntimeError, match="already active"):
            reg.get_or_create("newcomer")
    finally:
        busy.run_lock.release()
        reg.clear()


def test_reap_skips_a_session_mid_turn(monkeypatch):
    monkeypatch.setattr(sr, "build_session_agent", lambda **kw: _FakeAgent(**kw))
    reg = sr._SessionRegistry(idle_ttl_seconds=-1)  # everything is "expired"

    running = reg.get_or_create("running")
    running.run_lock.acquire()
    idle = reg.get_or_create("idle")
    try:
        reaped = reg.reap()
        assert "idle" in reaped
        assert "running" not in reaped
        assert reg.get("running") is not None
        assert idle.agent.closed is True
    finally:
        running.run_lock.release()
        reg.clear()


def test_concurrent_creates_for_one_id_yield_one_agent(monkeypatch):
    """Construction happens outside the lock, so the loser's agent is discarded."""
    created = []

    def fake_build(**kw):
        agent = _FakeAgent(**kw)
        created.append(agent)  # every attempt, including the ones that lose
        return agent

    monkeypatch.setattr(sr, "build_session_agent", fake_build)
    reg = sr._SessionRegistry()
    results = []
    barrier = threading.Barrier(4)

    def create():
        barrier.wait()
        results.append(reg.get_or_create("shared"))

    threads = [threading.Thread(target=create) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len({id(s) for s in results}) == 1
    winner = results[0]
    # The winning agent is one of `results`' — never returned to any caller
    # only to also get closed, since that would hand out a dead agent.
    assert winner.agent.closed is False
    # Every OTHER built agent lost the race and must have been torn down —
    # not merely dropped, or this is a handle leak (DB connections, RAG
    # index files) on every race, not just a missed assertion. The barrier
    # makes a real race likely but does not guarantee all 4 threads build
    # before the winner is registered, so assert on whatever losers actually
    # occurred rather than a fixed count.
    losers = [agent for agent in created if agent is not winner.agent]
    assert all(agent.closed for agent in losers)
    reg.clear()


# ---------------------------------------------------------------------------
# Reclaimed-after-eviction: the caller must be told when a session_id comes
# back with a fresh, skill-less agent instead of the one it had — silently
# doing that is the exact failure mode this module exists to prevent.
# ---------------------------------------------------------------------------


def test_fresh_session_id_is_never_flagged_reclaimed(registry):
    session = registry.get_or_create("never-seen-before")
    assert session.reclaimed_after_eviction is False


def test_get_or_create_after_lru_eviction_flags_reclaimed(monkeypatch):
    monkeypatch.setattr(sr, "build_session_agent", lambda **kw: _FakeAgent(**kw))
    reg = sr._SessionRegistry(max_sessions=1)

    reg.get_or_create("a")
    reg.get_or_create("b")  # over the cap → evicts "a"

    reclaimed = reg.get_or_create("a")
    assert reclaimed.reclaimed_after_eviction is True
    # The tombstone is consumed on first reuse — a THIRD, genuinely distinct
    # session_id must not inherit it.
    fresh = reg.get_or_create("never-used-before")
    assert fresh.reclaimed_after_eviction is False
    reg.clear()


def test_get_or_create_after_idle_reap_flags_reclaimed(monkeypatch):
    monkeypatch.setattr(sr, "build_session_agent", lambda **kw: _FakeAgent(**kw))
    reg = sr._SessionRegistry(idle_ttl_seconds=-1)  # everything is "expired"

    reg.get_or_create("a")
    reg.reap()

    reclaimed = reg.get_or_create("a")
    assert reclaimed.reclaimed_after_eviction is True
    reg.clear()


def test_reclaimed_flag_is_not_repeated_on_the_next_lookup(monkeypatch):
    """The registry flags it once, at creation; the caller owns consuming it."""
    monkeypatch.setattr(sr, "build_session_agent", lambda **kw: _FakeAgent(**kw))
    reg = sr._SessionRegistry(max_sessions=1)

    reg.get_or_create("a")
    reg.get_or_create("b")  # evicts "a"
    reclaimed = reg.get_or_create("a")
    assert reclaimed.reclaimed_after_eviction is True

    reclaimed.reclaimed_after_eviction = False  # server.py's consume step
    same = reg.get_or_create("a")
    assert same is reclaimed
    assert same.reclaimed_after_eviction is False
    reg.clear()
