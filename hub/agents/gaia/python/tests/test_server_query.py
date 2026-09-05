# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Run lifecycle on ``POST /v1/gaia/query``.

Every test here drives the real ``build_app()`` over HTTP with a scripted agent
injected at the two construction seams. A mocked route would prove the handler
was called; only the real app proves a one-shot agent is actually torn down, a
duplicate ``run_id`` is actually refused, and the SSE contract still ends each
run with exactly one terminal event.

What these pin, and the bug each one caught:

* **One-shot teardown** — ``/query`` without a ``session_id`` built an agent that
  nothing ever closed, so a host issuing one-shot queries accumulated a RAG/FAISS
  index, a scratchpad DB handle and an HTTP session per request until the process
  died.
* **Duplicate run_id** — the run table took the newer run over the older one, so
  the older became uncancellable and either stream's teardown dropped the other's
  entry. ``run_id`` is client-minted, so a client bug reaches this.
* **Per-turn model** — a retained session ignored a changed ``model``, answering
  on the old one with no error at all.
* **Cancel before start** — ``run_id`` is minted before the POST, so a cancel can
  legitimately arrive first; it used to be dropped and the run proceeded.
"""

from __future__ import annotations

import json
import threading
import time
import uuid

import pytest

pytest.importorskip("gaia_agent")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent import caller_auth  # noqa: E402
from gaia_agent import server as server_mod  # noqa: E402
from gaia_agent import session_registry as sr  # noqa: E402

_BASE_URL = "http://127.0.0.1:8141"


class _ScriptedAgent:
    """Stands in for GaiaAgent: identity, teardown, and a scriptable loop."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = 0
        self.console = None
        self.conversation_history = []
        self._cancel_event = None
        self.saw_cancelled = None
        self.raise_on_query = False
        self.block = None  # optional threading.Event to park the loop on

    def process_query(self, query, max_steps=None):
        if self.block is not None:
            self.block.wait(timeout=10)
        # Mirrors the base loop, which checks the flag at its first step
        # boundary — before any model call.
        self.saw_cancelled = bool(
            self._cancel_event is not None and self._cancel_event.is_set()
        )
        if self.raise_on_query:
            raise RuntimeError("scripted failure")
        if self.saw_cancelled:
            return {"answer": "stopped before doing any work"}
        return {"answer": f"answered: {query}"}

    def close(self):
        self.closed += 1


@pytest.fixture
def built(monkeypatch):
    """The real app, with both agent-construction seams scripted.

    Yields a ``(client, agents)`` pair; ``agents`` collects every agent built,
    in construction order, so a test can assert on teardown.
    """
    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)

    agents: list[_ScriptedAgent] = []

    def build(**kw):
        agent = _ScriptedAgent(**kw)
        agents.append(agent)
        return agent

    monkeypatch.setattr(server_mod, "build_query_agent", build)
    monkeypatch.setattr(sr, "build_session_agent", build)

    sr.registry.clear()
    server_mod._registry = server_mod._RunRegistry()

    client = TestClient(server_mod.build_app(), base_url=_BASE_URL)
    try:
        yield client, agents
    finally:
        sr.registry.clear()
        server_mod._registry = server_mod._RunRegistry()
        caller_auth.reset()


def _body(**overrides):
    payload = {
        "query": "hello",
        "run_id": str(uuid.uuid4()),
        "context": [],
    }
    payload.update(overrides)
    return payload


def _events(response):
    """Parse the canonical events out of an SSE response body."""
    out = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            out.append(json.loads(line[len("data: ") :]))
    return out


def _terminals(response):
    from gaia.ui.sse_translation import TERMINAL_TYPES

    return [e for e in _events(response) if e.get("type") in TERMINAL_TYPES]


def _wait_until(predicate, timeout=5.0):
    """Poll for a condition the run thread satisfies after the response ends.

    The stream returns as soon as the done-sentinel lands; teardown happens a
    moment later in the worker thread's ``finally``.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------------------
# Bypass permissions never reach this transport (#3373)
#
# Bypass lifts the shell guardrails as well as the confirmation prompt, so an
# HTTP-reachable agent running under it is remote code execution rather than a
# relaxed permission model. It is a stdio affordance — one local parent on a
# private pipe — and must stay one.
# ---------------------------------------------------------------------------


def test_a_query_runs_with_the_shell_gates_on(built):
    client, agents = built

    r = client.post("/v1/gaia/query", json=_body())

    assert r.status_code == 200, r.text
    assert agents[0].console is not None
    assert agents[0].console.bypass_permissions is False


def test_the_request_body_cannot_ask_for_bypass(built):
    """extra='forbid' is what makes this unreachable; pin it, so adding a
    bypass field fails here instead of shipping."""
    client, _agents = built

    r = client.post("/v1/gaia/query", json=_body(bypass_permissions=True))

    assert r.status_code == 422, r.text


def _all_route_paths(router) -> set:
    """Every path reachable under *router*, descending into sub-routers.

    The route list is not flat on every FastAPI version: ``include_router`` can
    leave an ``_IncludedRouter`` wrapper that carries ``.routes`` but no
    ``.path``. Reading ``.path`` off each entry raises there, and skipping the
    entries that lack one would walk right past the mounted API.
    """
    paths = set()
    for route in getattr(router, "routes", ()):
        path = getattr(route, "path", None)
        if path is not None:
            paths.add(path)
        if hasattr(route, "routes"):
            paths |= _all_route_paths(route)
    return paths


def test_the_http_transport_exposes_no_bypass_control():
    """The stdio control channel carries a bypass verb; HTTP has no equivalent
    route, and must not grow one without revisiting the reasoning above."""
    paths = _all_route_paths(server_mod.build_app())

    # Guard the guard. This assertion is only worth anything if the walk
    # actually reached the mounted routes — a version bump that changed the
    # route tree again would otherwise leave it passing on an empty set.
    assert "/v1/gaia/query" in paths, f"route walk found no API routes: {sorted(paths)}"

    assert not [p for p in paths if "bypass" in p.lower()]


def test_the_route_walk_descends_into_included_routers():
    """Covers the shape this repo's pinned FastAPI does not produce locally.

    On the CI version, ``include_router`` leaves a wrapper carrying ``.routes``
    and no ``.path``. Without a real one to test against, the walk is asserted
    on a stand-in of that shape — otherwise the guard above would be a fix
    nobody had run.
    """

    class _Leaf:
        def __init__(self, path):
            self.path = path

    class _IncludedRouterLike:
        """No .path, only .routes — what broke the flat comprehension."""

        def __init__(self, routes):
            self.routes = routes

    class _App:
        routes = [
            _Leaf("/health"),
            _IncludedRouterLike([_Leaf("/v1/gaia/query"), _Leaf("/v1/gaia/init")]),
        ]

    assert _all_route_paths(_App()) == {"/health", "/v1/gaia/query", "/v1/gaia/init"}


# ---------------------------------------------------------------------------
# One-shot teardown
# ---------------------------------------------------------------------------


def test_a_one_shot_run_closes_its_agent(built):
    """Without this, every session-less /query leaks a whole agent."""
    client, agents = built
    r = client.post("/v1/gaia/query", json=_body())

    assert r.status_code == 200, r.text
    assert len(agents) == 1
    assert _wait_until(lambda: agents[0].closed == 1), "one-shot agent was never closed"


def test_a_one_shot_agent_is_closed_even_when_the_run_raises(built, monkeypatch):
    """The leak must not come back on the failure path."""
    client, agents = built

    def build(**kw):
        agent = _ScriptedAgent(**kw)
        agent.raise_on_query = True
        agents.append(agent)
        return agent

    monkeypatch.setattr(server_mod, "build_query_agent", build)
    r = client.post("/v1/gaia/query", json=_body())

    assert r.status_code == 200, r.text
    assert _wait_until(lambda: agents[0].closed == 1)
    assert len(_terminals(r)) == 1, _events(r)


def test_a_retained_session_agent_is_never_closed_by_the_run(built):
    """The registry owns a session agent; closing it here would hand the next
    turn a dead agent with shut SQLite handles."""
    client, agents = built
    r = client.post("/v1/gaia/query", json=_body(session_id="s-1"))

    assert r.status_code == 200, r.text
    assert len(agents) == 1
    # Give the worker thread the same window the one-shot test allows.
    assert not _wait_until(lambda: agents[0].closed > 0, timeout=0.5)
    assert sr.registry.get("s-1") is not None


def test_one_terminal_event_per_run(built):
    """The frozen contract: exactly one final-or-error, always."""
    client, _ = built
    r = client.post("/v1/gaia/query", json=_body())
    assert len(_terminals(r)) == 1, _events(r)


# ---------------------------------------------------------------------------
# Duplicate run_id
# ---------------------------------------------------------------------------


def test_a_duplicate_in_flight_run_id_is_refused(built):
    """A reused run_id used to overwrite the live run, stranding it."""
    client, _ = built
    run_id = str(uuid.uuid4())

    live = server_mod._QueryRun(run_id, object(), object())
    server_mod._registry.add(live)

    r = client.post("/v1/gaia/query", json=_body(run_id=run_id))

    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert run_id in detail
    assert "fresh UUID" in detail


def test_the_refused_duplicate_leaves_the_live_run_registered(built):
    """The sharp half of the bug: the loser's cleanup dropped the winner's entry,
    which is what made the live run uncancellable."""
    client, _ = built
    run_id = str(uuid.uuid4())
    live = server_mod._QueryRun(run_id, object(), object())
    server_mod._registry.add(live)

    client.post("/v1/gaia/query", json=_body(run_id=run_id))

    assert server_mod._registry.get(run_id) is live


def test_the_refused_duplicate_does_not_leak_its_agent(built):
    """The rejected request still built a one-shot agent; it must be torn down."""
    client, agents = built
    run_id = str(uuid.uuid4())
    server_mod._registry.add(server_mod._QueryRun(run_id, object(), object()))

    r = client.post("/v1/gaia/query", json=_body(run_id=run_id))

    assert r.status_code == 409
    assert len(agents) == 1
    assert agents[0].closed == 1


def test_run_registry_rejects_a_duplicate_directly():
    run_id = str(uuid.uuid4())
    reg = server_mod._RunRegistry()
    reg.add(server_mod._QueryRun(run_id, object(), object()))

    with pytest.raises(server_mod.DuplicateRunError):
        reg.add(server_mod._QueryRun(run_id, object(), object()))


def test_a_reused_run_id_is_fine_once_the_first_run_finished(built):
    """Only an IN-FLIGHT collision is refused — the table is keyed on live runs."""
    client, _ = built
    run_id = str(uuid.uuid4())

    first = client.post("/v1/gaia/query", json=_body(run_id=run_id))
    assert first.status_code == 200
    assert _wait_until(lambda: server_mod._registry.get(run_id) is None)

    second = client.post("/v1/gaia/query", json=_body(run_id=run_id))
    assert second.status_code == 200, second.text


# ---------------------------------------------------------------------------
# Per-turn model on a retained session
# ---------------------------------------------------------------------------


def test_switching_model_on_a_live_session_is_refused(built):
    """It used to run the OLD model with no error and no warning."""
    client, agents = built

    first = client.post("/v1/gaia/query", json=_body(session_id="s-1", model="model-a"))
    assert first.status_code == 200, first.text

    second = client.post(
        "/v1/gaia/query", json=_body(session_id="s-1", model="model-b")
    )

    assert second.status_code == 409, second.text
    detail = second.json()["detail"]
    assert "model-a" in detail and "model-b" in detail
    assert "new session_id" in detail
    # The refusal must not have built a second agent, nor stranded the session.
    assert len(agents) == 1


def test_the_refused_switch_leaves_the_session_usable(built):
    """A 409 must release the run lock, or the session 409s forever after."""
    client, _ = built
    client.post("/v1/gaia/query", json=_body(session_id="s-1", model="model-a"))
    client.post("/v1/gaia/query", json=_body(session_id="s-1", model="model-b"))

    again = client.post("/v1/gaia/query", json=_body(session_id="s-1", model="model-a"))
    assert again.status_code == 200, again.text


def test_the_same_model_across_turns_is_allowed(built):
    client, agents = built
    body = dict(session_id="s-1", model="model-a")
    assert client.post("/v1/gaia/query", json=_body(**body)).status_code == 200
    assert client.post("/v1/gaia/query", json=_body(**body)).status_code == 200
    assert len(agents) == 1  # same retained agent both turns


def test_omitting_model_continues_on_the_session_model(built):
    """A caller expressing no preference is not a switch request."""
    client, _ = built
    client.post("/v1/gaia/query", json=_body(session_id="s-1", model="model-a"))
    r = client.post("/v1/gaia/query", json=_body(session_id="s-1"))
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Cancel arriving before the run registers
# ---------------------------------------------------------------------------


def test_a_cancel_that_beats_the_run_still_stops_it(built):
    """run_id is minted before the POST, so cancel-arrives-first is a real
    ordering. It used to be dropped and the run went on to call the model."""
    client, agents = built
    run_id = str(uuid.uuid4())

    cancel = client.post(f"/v1/gaia/query/{run_id}/cancel")
    assert cancel.status_code == 200
    # No live run was stopped, and saying otherwise would be a lie...
    assert cancel.json()["cancelled"] is False

    r = client.post("/v1/gaia/query", json=_body(run_id=run_id))

    assert r.status_code == 200, r.text
    # ...but the run that arrives afterwards starts already cancelled.
    assert agents[0].saw_cancelled is True
    assert len(_terminals(r)) == 1


def test_cancelling_a_run_that_just_finished_does_not_arm_a_tombstone(built):
    """A cancel racing the run's own completion is the documented normal case,
    so it must not leave an entry behind that nothing can ever consume — and
    must not pre-cancel a reuse of that id, which the run table allows once the
    first run is done."""
    client, agents = built
    run_id = str(uuid.uuid4())

    first = client.post("/v1/gaia/query", json=_body(run_id=run_id))
    assert first.status_code == 200
    assert _wait_until(lambda: server_mod._registry.get(run_id) is None)

    late = client.post(f"/v1/gaia/query/{run_id}/cancel")
    assert late.json()["cancelled"] is False
    assert run_id not in server_mod._registry._precancelled

    second = client.post("/v1/gaia/query", json=_body(run_id=run_id))
    assert second.status_code == 200, second.text
    assert agents[1].saw_cancelled is False, (
        "a reused run_id was pre-cancelled by a cancel aimed at the run that "
        "already finished under it"
    )


def test_a_late_cancel_still_reports_it_stopped_nothing(built):
    """The response contract for the completion race is unchanged."""
    client, _ = built
    run_id = str(uuid.uuid4())
    client.post("/v1/gaia/query", json=_body(run_id=run_id))
    assert _wait_until(lambda: server_mod._registry.get(run_id) is None)

    assert client.post(f"/v1/gaia/query/{run_id}/cancel").json()["cancelled"] is False


def test_a_cancel_for_a_never_seen_id_is_still_remembered(built):
    """The distinction the fix turns on: unknown-and-unstarted still arms, so
    the cancel-beats-the-POST race stays closed."""
    client, _ = built
    run_id = str(uuid.uuid4())

    client.post(f"/v1/gaia/query/{run_id}/cancel")

    assert run_id in server_mod._registry._precancelled


def test_an_early_cancel_applies_once_and_only_to_its_own_run_id(built):
    """The tombstone is consumed on use, so a later run under the same id — or
    any other id — is unaffected."""
    client, agents = built
    run_id = str(uuid.uuid4())
    client.post(f"/v1/gaia/query/{run_id}/cancel")

    client.post("/v1/gaia/query", json=_body(run_id=run_id))
    assert _wait_until(lambda: server_mod._registry.get(run_id) is None)

    client.post("/v1/gaia/query", json=_body(run_id=run_id))
    assert agents[1].saw_cancelled is False

    client.post("/v1/gaia/query", json=_body())
    assert agents[2].saw_cancelled is False


def test_cancelling_a_live_run_reports_it_stopped(built, monkeypatch):
    """The existing contract for the normal case must not have moved."""
    client, agents = built
    run_id = str(uuid.uuid4())
    gate = threading.Event()

    def build(**kw):
        agent = _ScriptedAgent(**kw)
        agent.block = gate
        agents.append(agent)
        return agent

    monkeypatch.setattr(server_mod, "build_query_agent", build)

    result = {}

    def run():
        result["response"] = client.post("/v1/gaia/query", json=_body(run_id=run_id))

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        assert _wait_until(lambda: server_mod._registry.get(run_id) is not None)
        cancel = client.post(f"/v1/gaia/query/{run_id}/cancel")
        assert cancel.json()["cancelled"] is True
    finally:
        gate.set()
        worker.join(timeout=10)

    assert result["response"].status_code == 200
