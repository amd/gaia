# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A scheduled run uses the same GAIA agent a chat does, and says so when it fails.

A desktop install ships the flagship only as a daemon sidecar binary, so a
scheduled run that imported the in-process package failed every time with a
"not installed" message naming an install command that did not work. These
pin that a scheduled run relays to the sidecar when that is how the flagship
runs, that a missing flagship points at ``gaia init``, and that a failed run's
error reaches the schedules API.
"""

import json
import sys
import types
from types import SimpleNamespace

import pytest

import gaia.ui._chat_helpers as chat_helpers_module
from gaia.agents.registry import AgentRegistry
from gaia.hub.installer import register_installed_sidecars
from gaia.ui import server
from gaia.ui.database import ChatDatabase
from gaia.ui.email_sidecar import daemon_client as daemon_client_module
from gaia.ui.sse_handler import SSEOutputHandler


@pytest.fixture
def db():
    database = ChatDatabase(":memory:")
    yield database
    database.close()


@pytest.fixture
def sidecar_flagship(tmp_path, monkeypatch):
    """The registry after a desktop (binary) install of the flagship — no wheel."""
    root = tmp_path / "agents"
    (root / "gaia").mkdir(parents=True)
    (root / "gaia" / ".installed").write_text(
        json.dumps(
            {
                "id": "gaia",
                "version": "0.3.1",
                "language": "python",
                "installed_at": "2026-10-01T00:00:00Z",
                "artifact_sha256": "0" * 64,
                "artifact_kind": "binary",
                "executable": "gaia-agent-gaia",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "gaia.hub.installer.default_install_root", lambda: root, raising=False
    )
    registry = AgentRegistry()
    registry._agents.clear()
    register_installed_sidecars(registry)
    assert registry.get("gaia").is_sidecar is True
    monkeypatch.setattr(chat_helpers_module, "_agent_registry", registry)
    # The in-process package is absent, as on a desktop install.
    monkeypatch.setitem(sys.modules, "gaia_agent", None)
    monkeypatch.setitem(sys.modules, "gaia_agent.agent", None)
    return registry


class _FakeProxy:
    """The flagship sidecar's /v1/gaia surface, recording what it was sent."""

    agent_id = "gaia"

    def __init__(self, events):
        self.events = events
        self.bodies = []
        self.decisions = []
        self.cancelled = []

    def init(self):
        return 200, {"ready": True}

    def query_stream(self, body, read_timeout, on_response):
        self.bodies.append(body)
        on_response(SimpleNamespace(close=lambda: None))
        for event in self.events:
            yield event

    def tool_decision(self, run_id, decision, confirm_id):
        self.decisions.append((run_id, decision, confirm_id))
        return {"delivered": True}

    def cancel_query(self, run_id):
        self.cancelled.append(run_id)


def _sidecar_answers(monkeypatch, events, api_version="2.18"):
    proxy = _FakeProxy(events)
    handle = SimpleNamespace(api_version=api_version, proxy=lambda: proxy)
    acquired = []

    def _acquire(agent_id):
        acquired.append(agent_id)
        return handle

    monkeypatch.setattr(daemon_client_module, "acquire_handle", _acquire)
    return proxy, acquired


def _final(answer, **extra):
    return {"type": "final", "answer": answer, **extra}


# ── Which agent a scheduled run uses ───────────────────────────────────────


@pytest.mark.parametrize("memory_on", [True, False], ids=["memory_on", "memory_off"])
def test_a_desktop_install_relays_the_scheduled_prompt_to_the_sidecar(
    monkeypatch, sidecar_flagship, db, memory_on
):
    db.set_setting("memory_enabled", "true" if memory_on else "false")
    proxy, acquired = _sidecar_answers(
        monkeypatch,
        [{"type": "token", "delta": "Sunny."}, _final("Sunny, 21 C.")],
    )
    console = SSEOutputHandler(background_mode=True)

    answer = server._run_scheduled_prompt(db, "weather today?", console=console)

    assert answer == "Sunny, 21 C."
    assert acquired == ["gaia"]
    [body] = proxy.bodies
    # A fresh run: no transcript and no sidecar session to carry state over.
    assert body["query"] == "weather today?"
    assert body["context"] == []
    assert "session_id" not in body
    assert "documents" not in body
    assert "model" not in body
    assert body["max_steps"] == server._SCHEDULED_MAX_STEPS
    assert body["memory"] == ("on" if memory_on else "off")


def test_a_relayed_scheduled_run_raises_the_agents_error(
    monkeypatch, sidecar_flagship, db
):
    _sidecar_answers(
        monkeypatch, [{"type": "error", "detail": "Lemonade is not running"}]
    )

    with pytest.raises(RuntimeError, match="Lemonade is not running"):
        server._run_scheduled_prompt(
            db, "weather?", console=SSEOutputHandler(background_mode=True)
        )


def test_a_relayed_run_whose_model_failed_is_a_failed_run(
    monkeypatch, sidecar_flagship, db
):
    _sidecar_answers(
        monkeypatch, [_final("The model stopped responding.", failed=True)]
    )

    with pytest.raises(RuntimeError, match="The model stopped responding"):
        server._run_scheduled_prompt(
            db, "weather?", console=SSEOutputHandler(background_mode=True)
        )


def test_a_relayed_run_that_ends_without_an_answer_fails_loudly(
    monkeypatch, sidecar_flagship, db
):
    _sidecar_answers(monkeypatch, [{"type": "status", "message": "thinking"}])

    with pytest.raises(RuntimeError, match="(?i)ended unexpectedly"):
        server._run_scheduled_prompt(
            db, "weather?", console=SSEOutputHandler(background_mode=True)
        )


def test_a_relayed_tool_prompt_is_denied_since_nobody_can_answer(
    monkeypatch, sidecar_flagship, db
):
    proxy, _ = _sidecar_answers(
        monkeypatch,
        [
            {
                "type": "needs_confirmation",
                "action": "run_shell_command",
                "summary": "Run 'ls'?",
                "confirm_id": "c-1",
            },
            _final("I could not run that unattended."),
        ],
    )

    answer = server._run_scheduled_prompt(
        db, "list my files", console=SSEOutputHandler(background_mode=True)
    )

    assert answer == "I could not run that unattended."
    [(_, decision, confirm_id)] = proxy.decisions
    assert (decision, confirm_id) == ("deny", "c-1")


def test_a_stopped_scheduled_run_is_not_sent(monkeypatch, sidecar_flagship, db):
    proxy, acquired = _sidecar_answers(monkeypatch, [_final("late")])
    console = SSEOutputHandler(background_mode=True)
    console.cancelled.set()

    with pytest.raises(RuntimeError, match="stopped"):
        server._run_scheduled_prompt(db, "weather?", console=console)
    assert acquired == [] and proxy.bodies == []


def test_a_wheel_install_still_runs_in_process(monkeypatch, db):
    """A source or wheel install registers a real factory: no relay."""
    registry = SimpleNamespace(
        get=lambda _id: SimpleNamespace(is_sidecar=False),
        get_load_error=lambda _id: None,
    )
    monkeypatch.setattr(chat_helpers_module, "_agent_registry", registry)

    def _no_relay(*_args, **_kwargs):
        raise AssertionError("an in-process flagship was relayed")

    monkeypatch.setattr(daemon_client_module, "acquire_handle", _no_relay)

    class _Agent:
        def __init__(self, config):
            self.config = config

        def process_query(self, prompt):
            return {"result": f"in-process: {prompt}"}

    fake_mod = types.ModuleType("gaia_agent.agent")
    fake_mod.GaiaAgent = _Agent
    fake_mod.GaiaAgentConfig = SimpleNamespace
    monkeypatch.setitem(sys.modules, "gaia_agent", types.ModuleType("gaia_agent"))
    monkeypatch.setitem(sys.modules, "gaia_agent.agent", fake_mod)

    assert server._run_scheduled_prompt(db, "hi") == "in-process: hi"


def test_an_in_process_run_whose_model_failed_is_a_failed_run(monkeypatch, db):
    """Same rule as the relay: a model failure is a failed run, not an answer."""
    registry = SimpleNamespace(
        get=lambda _id: SimpleNamespace(is_sidecar=False),
        get_load_error=lambda _id: None,
    )
    monkeypatch.setattr(chat_helpers_module, "_agent_registry", registry)

    class _Agent:
        def __init__(self, config):
            self.config = config

        def process_query(self, prompt):
            return {"result": "Couldn't reach the local LLM.", "model_failed": True}

    fake_mod = types.ModuleType("gaia_agent.agent")
    fake_mod.GaiaAgent = _Agent
    fake_mod.GaiaAgentConfig = SimpleNamespace
    monkeypatch.setitem(sys.modules, "gaia_agent", types.ModuleType("gaia_agent"))
    monkeypatch.setitem(sys.modules, "gaia_agent.agent", fake_mod)

    with pytest.raises(RuntimeError, match="Couldn't reach the local LLM"):
        server._run_scheduled_prompt(db, "hi")


# ── A missing flagship ──────────────────────────────────────────────────────


def test_no_flagship_at_all_says_to_run_gaia_init(monkeypatch, db):
    registry = AgentRegistry()
    registry._agents.clear()
    monkeypatch.setattr(registry, "refresh_installed", lambda: None)
    monkeypatch.setattr(chat_helpers_module, "_agent_registry", registry)

    with pytest.raises(RuntimeError) as exc:
        server._run_scheduled_prompt(db, "hi")

    message = str(exc.value)
    assert "gaia init" in message
    assert "git+" not in message and "pip install" not in message


def test_a_registered_flagship_whose_package_is_gone_says_to_run_gaia_init(
    monkeypatch, db
):
    registry = SimpleNamespace(
        get=lambda _id: SimpleNamespace(is_sidecar=False),
        get_load_error=lambda _id: None,
    )
    monkeypatch.setattr(chat_helpers_module, "_agent_registry", registry)
    monkeypatch.setitem(sys.modules, "gaia_agent", None)
    monkeypatch.setitem(sys.modules, "gaia_agent.agent", None)

    with pytest.raises(RuntimeError) as exc:
        server._run_scheduled_prompt(db, "hi")

    message = str(exc.value)
    assert "gaia init" in message
    assert "git+" not in message


def test_a_run_before_agent_discovery_fails_loudly(monkeypatch, db):
    monkeypatch.setattr(chat_helpers_module, "_agent_registry", None)

    with pytest.raises(RuntimeError, match="discovering agents"):
        server._run_scheduled_prompt(db, "hi")


# ── A failed run is visible ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_failed_run_exposes_its_error_until_a_run_succeeds(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from gaia.ui.routers.schedules import router
    from gaia.ui.scheduler import Scheduler

    outcomes = [RuntimeError("The GAIA agent is not installed. Run `gaia init`.")]

    async def executor(_prompt):
        if outcomes:
            raise outcomes.pop()
        return "all good"

    scheduler = Scheduler(db=db, executor=executor)
    await scheduler.start()
    await scheduler.create_task("news", "every 1h", "summarise the news")
    try:
        task_obj = scheduler._tasks["news"]

        await scheduler._execute_task(task_obj)
        app = FastAPI()
        app.include_router(router)
        app.state.scheduler = scheduler
        with TestClient(app) as client:
            [listed] = client.get("/api/schedules").json()["schedules"]
        assert listed["error_count"] == 1
        assert listed["last_error"] == (
            "The GAIA agent is not installed. Run `gaia init`."
        )
        # It survives a restart.
        [row] = db.list_scheduled_tasks()
        assert row["last_error"] == listed["last_error"]

        await scheduler._execute_task(task_obj)
        assert scheduler.get_task("news")["last_error"] is None
        assert db.list_scheduled_tasks()[0]["last_error"] is None
    finally:
        await scheduler.shutdown()
