"""Session-scoped agent retention (#2829, schema 2.12).

The sidecar advertises apiVersion 2.12, whose whole point is that a
``session_id`` resolves a RETAINED agent instead of a throwaway per call.
Without that, anything a turn puts on the instance — most visibly
``Agent.loaded_skills`` — is gone by the next turn, while the model keeps
telling the user the skill is still loaded.
"""

import threading

import pytest
from gaia_agent_gaia import session_registry as sr


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

    a = reg.get_or_create("a")
    reg.get_or_create("b")
    reg.get_or_create("c")  # over the cap → evicts the LRU ("a")

    assert reg.get("a") is None
    assert a.agent.closed is True
    assert reg.get("b") is not None
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
    monkeypatch.setattr(sr, "build_session_agent", lambda **kw: _FakeAgent(**kw))
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
    reg.clear()
