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
from unittest import mock

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
        self.approved = None  # outcome of a gated_call, once answered

    #: When set, the loop asks for approval of this (tool, args) before
    #: answering — the real handler emits needs_confirmation and blocks the
    #: agent thread, exactly as the base loop does for a gated tool.
    gated_call = None

    def process_query(self, query, max_steps=None):
        if self.block is not None:
            self.block.wait(timeout=10)
        if self.gated_call is not None:
            tool, args = self.gated_call
            self.approved = self.console.confirm_tool_execution(tool, args)
            return {"answer": f"approved={self.approved}"}
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
def switched(monkeypatch):
    """Records every live model switch, in place of a real client swap.

    ``_AgentSession.switch_model`` delegates to the stdio transport's real
    machinery, which builds an LLM client and talks to Lemonade — neither of
    which belongs in a route test.
    """
    calls: list = []

    def fake_switch(self, target):
        calls.append((target,))
        self.model_id = target
        return target

    monkeypatch.setattr(sr._AgentSession, "switch_model", fake_switch)
    return calls


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


def test_switching_model_on_a_live_session_switches_it(built, switched):
    """It used to answer 409 and tell the caller to start a new session_id.

    That was honest but lossy: a new session throws away the conversation and
    every loaded skill, which is exactly what a retained session is for. The
    stdio transport has always switched in place (``/model``), and the two
    transports must not disagree about what switching a model costs.
    """
    client, agents = built

    first = client.post("/v1/gaia/query", json=_body(session_id="s-1", model="model-a"))
    assert first.status_code == 200, first.text

    second = client.post(
        "/v1/gaia/query", json=_body(session_id="s-1", model="model-b")
    )

    assert second.status_code == 200, second.text
    assert switched == [("model-b",)], "the live switch must have been performed"
    # The whole point: the SAME agent, so history and loaded skills survive.
    assert len(agents) == 1


def test_a_switched_session_reports_its_new_model(built, switched):
    """A later turn on the old id must not re-switch, or every turn pays for it."""
    client, _ = built
    client.post("/v1/gaia/query", json=_body(session_id="s-1", model="model-a"))
    client.post("/v1/gaia/query", json=_body(session_id="s-1", model="model-b"))
    client.post("/v1/gaia/query", json=_body(session_id="s-1", model="model-b"))

    assert switched == [("model-b",)], "the second turn on model-b must be a no-op"


def test_a_failed_switch_leaves_the_session_on_its_old_model(built, monkeypatch):
    """All-or-nothing: a bad credential must not strand the conversation."""
    client, _ = built

    def explode(self, target):
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    monkeypatch.setattr(sr._AgentSession, "switch_model", explode)

    client.post("/v1/gaia/query", json=_body(session_id="s-1", model="model-a"))
    failed = client.post(
        "/v1/gaia/query", json=_body(session_id="s-1", model="claude-opus-5")
    )

    assert failed.status_code == 409, failed.text
    detail = failed.json()["detail"]
    assert "ANTHROPIC_API_KEY" in detail, "the real reason must reach the caller"
    assert "still running" in detail, "the caller must learn nothing was lost"


def test_the_session_survives_a_failed_switch(built, monkeypatch):
    """A 409 must release the run lock, or the session 409s forever after."""
    client, _ = built

    def explode(self, target):
        raise RuntimeError("nope")

    monkeypatch.setattr(sr._AgentSession, "switch_model", explode)
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


# ---------------------------------------------------------------------------
# Inference backend
# ---------------------------------------------------------------------------
#
# The stdio transport has always taken --use-claude. Refusing it here is what
# made moving the TUI onto the daemon a downgrade rather than a move
# (docs/plans/daemon-convergence.mdx §3.2).


def test_the_claude_provider_configures_the_backend_not_just_an_id(built):
    """`model` names a CLAUDE model under this provider.

    Threading it through as ``model_id`` would point the LOCAL client at an id
    Lemonade cannot serve — a failure that surfaces much later and much less
    clearly than the 400 this replaced.
    """
    client, agents = built

    response = client.post(
        "/v1/gaia/query",
        json=_body(session_id="s-1", model="claude-opus-5", provider="claude"),
    )

    assert response.status_code == 200, response.text
    assert len(agents) == 1
    built_with = agents[0].kwargs
    assert built_with.get("use_claude") is True
    assert built_with.get("claude_model") == "claude-opus-5"
    assert "model_id" not in built_with, "a Claude id must not reach the local client"


def test_the_local_provider_still_threads_a_model_id(built):
    client, agents = built

    client.post(
        "/v1/gaia/query",
        json=_body(session_id="s-1", model="Gemma-4-E4B-it-GGUF", provider="lemonade"),
    )

    built_with = agents[0].kwargs
    assert built_with.get("model_id") == "Gemma-4-E4B-it-GGUF"
    assert not built_with.get("use_claude")


def test_omitting_the_provider_stays_local(built):
    """The default must not change: this is the overwhelmingly common request."""
    client, agents = built

    client.post("/v1/gaia/query", json=_body(session_id="s-1", model="model-a"))

    assert agents[0].kwargs.get("model_id") == "model-a"
    assert not agents[0].kwargs.get("use_claude")


def test_an_unknown_provider_is_still_refused_loudly(built):
    """Widening the set must not turn it into 'anything goes'."""
    client, _ = built

    response = client.post(
        "/v1/gaia/query",
        json=_body(session_id="s-1", model="gpt-4", provider="openai"),
    )

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert "openai" in detail
    assert "claude" in detail and "lemonade" in detail, "name what IS allowed"


def test_a_brand_new_claude_session_is_not_immediately_switched(built, switched):
    """A session built with exactly what was asked for must not then 'change'.

    The two backends name their model in different kwargs — local passes
    ``model_id``, Claude passes ``claude_model``. While the registry recorded
    only the first, a Claude session reported no model at all, so the very
    request that created it looked like a model change and tried to switch an
    agent that had just been constructed correctly.
    """
    client, agents = built

    response = client.post(
        "/v1/gaia/query",
        json=_body(session_id="s-1", model="claude-opus-5", provider="claude"),
    )

    assert response.status_code == 200, response.text
    assert switched == [], "construction already applied the model; nothing to switch"
    assert len(agents) == 1


def test_a_second_turn_on_the_same_claude_model_does_not_re_switch(built, switched):
    client, _ = built
    body = _body(session_id="s-1", model="claude-opus-5", provider="claude")
    client.post("/v1/gaia/query", json=body)
    client.post(
        "/v1/gaia/query",
        json=_body(session_id="s-1", model="claude-opus-5", provider="claude"),
    )

    assert switched == []


# ---------------------------------------------------------------------------
# Tool confirmations over HTTP
# ---------------------------------------------------------------------------
#
# Before this the stream REFUSED a needs_confirmation and cancelled the run —
# "this streaming surface cannot collect that yet" — so a gated tool could not
# run over the daemon transport at all. These pin the seam that changed
# (docs/plans/daemon-convergence.mdx §3.4).


def test_a_decision_for_a_finished_run_is_refused_rather_than_dropped(built):
    """Dropping it would leave the caller believing it approved something."""
    client, _ = built
    run_id = str(uuid.uuid4())
    client.post("/v1/gaia/query", json=_body(session_id="s-1", run_id=run_id))

    response = client.post(
        f"/v1/gaia/query/{run_id}/tool_decision",
        json={"decision": "allow", "confirm_id": "c1"},
    )

    assert response.status_code == 404, response.text
    assert "not delivered" in response.json()["detail"]


@pytest.mark.parametrize("decision", ["allow", "deny", "always"])
def test_every_decision_the_stdio_channel_accepts_is_accepted_here(decision):
    """The two transports must not disagree about the vocabulary."""
    from gaia_agent import stdio

    assert decision in {
        stdio.DECISION_ALLOW,
        stdio.DECISION_DENY,
        stdio.DECISION_ALWAYS,
    }
    assert decision in server_mod._TOOL_DECISIONS


def test_an_unknown_decision_is_refused_not_guessed(built):
    """Fail closed: an unreadable decision is not consent."""
    client, _ = built
    response = client.post(
        f"/v1/gaia/query/{uuid.uuid4()}/tool_decision", json={"decision": "maybe"}
    )
    assert response.status_code == 422, response.text


def test_a_decision_for_an_unknown_run_is_a_404(built):
    client, _ = built
    response = client.post(
        "/v1/gaia/query/11111111-1111-4111-8111-111111111111/tool_decision",
        json={"decision": "allow"},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Bypass
# ---------------------------------------------------------------------------


def test_bypass_applies_to_the_session_and_survives_the_turn(built):
    """Bypass outliving a turn is the entire point of it."""
    client, _ = built
    client.post("/v1/gaia/query", json=_body(session_id="s-1"))

    response = client.post("/v1/gaia/sessions/s-1/bypass", json={"enabled": True})

    assert response.status_code == 200, response.text
    assert response.json() == {"session_id": "s-1", "enabled": True}
    assert sr.registry.get("s-1").permissions.bypass is True


def test_bypass_can_be_turned_back_off(built):
    client, _ = built
    client.post("/v1/gaia/query", json=_body(session_id="s-1"))
    client.post("/v1/gaia/sessions/s-1/bypass", json={"enabled": True})
    client.post("/v1/gaia/sessions/s-1/bypass", json={"enabled": False})

    assert sr.registry.get("s-1").permissions.bypass is False


def test_bypass_on_an_unknown_session_is_a_404_not_a_new_session(built):
    """Building an agent as a side effect of a settings toggle would be a bug,
    and a typo'd id would silently 'succeed' against it."""
    client, agents = built

    response = client.post(
        "/v1/gaia/sessions/never-seen/bypass", json={"enabled": True}
    )

    assert response.status_code == 404, response.text
    assert agents == [], "no agent may be built by a bypass toggle"


def test_the_session_hands_each_turn_its_accumulated_permission_state(built):
    """A fresh handler per turn means bypass and 'always' grants reset unless
    the session re-applies them."""
    client, _ = built
    client.post("/v1/gaia/query", json=_body(session_id="s-1"))
    client.post("/v1/gaia/sessions/s-1/bypass", json={"enabled": True})

    session = sr.registry.get("s-1")
    handler = _RecordingHandler()
    session.permissions.attach(handler)

    assert handler.auto_approve_gated_tools is True


class _RecordingHandler:
    """Minimal stand-in for SSEOutputHandler's permission surface."""

    def __init__(self):
        self.auto_approve_gated_tools = False
        self.confirm_timeout_seconds = 60.0
        self._grants = set()

    def session_grants(self):
        return self._grants


def test_a_gated_tool_runs_once_the_decision_arrives(built):
    """The whole point of §3.4, end to end over the real stream.

    The agent parks in ``confirm_tool_execution``; the client answers on
    ``/tool_decision``; the run resumes and reports what it was told. Before
    this the stream emitted a refusal and cancelled the run instead.
    """
    client, agents = built
    run_id = str(uuid.uuid4())

    def answer_when_asked():
        # The decision endpoint 409s until the prompt is actually pending, so
        # poll rather than sleep a guessed interval.
        for _ in range(400):
            response = client.post(
                f"/v1/gaia/query/{run_id}/tool_decision",
                json={"decision": "allow"},
            )
            if response.status_code == 200:
                return
            time.sleep(0.01)

    answerer = threading.Thread(target=answer_when_asked, daemon=True)

    def arm(**kw):
        agent = _ScriptedAgent(**kw)
        agent.gated_call = ("run_shell_command", {"command": "pwd"})
        agents.append(agent)
        answerer.start()
        return agent

    with mock.patch.object(sr, "build_session_agent", arm):
        response = client.post(
            "/v1/gaia/query", json=_body(session_id="s-gated", run_id=run_id)
        )

    assert response.status_code == 200, response.text
    events = _events(response)
    types = [e.get("type") for e in events]
    assert "needs_confirmation" in types, f"the prompt must reach the client: {types}"
    final = [e for e in events if e.get("type") == "final"]
    assert final, f"the run must finish, not be cancelled: {types}"
    assert "approved=True" in final[-1].get("answer", ""), final
    assert agents[0].approved is True


def test_a_one_shot_still_refuses_a_gated_tool(built):
    """Nobody is there to answer, so parking the run would read as a hang."""
    client, agents = built

    def arm(**kw):
        agent = _ScriptedAgent(**kw)
        agent.gated_call = ("run_shell_command", {"command": "pwd"})
        agents.append(agent)
        return agent

    with mock.patch.object(server_mod, "build_query_agent", arm):
        response = client.post("/v1/gaia/query", json=_body(can_answer_questions=False))

    events = _events(response)
    final = [e for e in events if e.get("type") == "final"]
    assert final, events
    assert "needs your explicit approval" in final[-1].get("answer", "")
