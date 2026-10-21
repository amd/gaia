# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A new chat opens with a greeting the flagship writes itself.

Writing it runs the first turn's real path, so it loads the model and primes
the prompt like the warm-up does; a greeting that fails says why instead of
falling back to a canned line; and its memory-derived text never reaches the
log or the warm-up status.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.agents.base.agent import Agent
from gaia.agents.base.memory import MemoryMixin
from gaia.llm.lemonade_client import no_thinking_kwargs
from gaia.llm.providers.lemonade import NATIVE_TOOL_CALLS_PREFIX
from gaia.ui import greeting, warmup

MODEL = "Qwen3.6-35B-A3B-GGUF"

# ── Agent.greet ─────────────────────────────────────────────────────────────


class _StreamingChat:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    def send_messages_stream(self, messages, system_prompt=None, tools=None, **kw):
        self.calls.append(
            {"messages": messages, "system_prompt": system_prompt, "tools": tools, **kw}
        )
        for text in self.chunks:
            yield SimpleNamespace(text=text)
        yield SimpleNamespace(text="")


class _Agent(Agent):
    """Just the pieces greet reads, without building a real agent."""

    def __init__(
        self, chunks=("Hi! ", "What are we building?")
    ):  # pylint: disable=super-init-not-called
        self.chat = _StreamingChat(list(chunks))
        self.model_id = MODEL
        self.tool_loader = None

    @property
    def _tools_registry(self):
        return {}

    def _apply_tool_filter(self, new_filter):
        pass

    def _refresh_active_skill_filter(self, user_input):
        self.skill_filter_input = user_input

    @property
    def system_prompt(self):
        return "SYSTEM PROMPT"

    @property
    def _openai_tools(self):
        return [{"name": "read_file"}]

    def _get_system_prompt(self):
        return ""

    def _register_tools(self):
        pass


def test_greet_streams_a_short_reply_on_the_first_turns_prompt():
    agent = _Agent()
    seen = []

    result = agent.greet("Say hello.", seen.append)

    assert seen == ["Hi! ", "What are we building?"]
    assert result["text"] == "Hi! What are we building?"
    [call] = agent.chat.calls
    # The prefix a first turn sends, so the greeting leaves it cached.
    assert call["system_prompt"] == "SYSTEM PROMPT"
    assert call["tools"] == [{"name": "read_file"}]
    assert agent.skill_filter_input == "Say hello."
    assert call["messages"] == [{"role": "user", "content": "Say hello."}]
    assert call["max_tokens"] == Agent.GREETING_MAX_TOKENS
    # Thinking would spend the whole budget before a word of the greeting.
    for key, value in no_thinking_kwargs(MODEL).items():
        assert call[key] == value


def test_greet_puts_what_the_agent_knows_ahead_of_the_instruction():
    class _Knowing(_Agent):
        def greeting_context(self):
            return "Current time: Tuesday"

    agent = _Knowing()
    agent.greet("Say hello.", lambda _t: None)
    [call] = agent.chat.calls
    assert call["messages"][0]["content"] == "Current time: Tuesday\n\nSay hello."


@pytest.mark.parametrize(
    "chunks, error",
    [
        ([NATIVE_TOOL_CALLS_PREFIX + '[{"name": "x"}]}'], "tool call"),
        (["   "], "empty greeting"),
    ],
)
def test_greet_fails_loudly_rather_than_showing_something_else(chunks, error):
    with pytest.raises(RuntimeError, match=error):
        _Agent(chunks=chunks).greet("Say hello.", lambda _t: None)


def test_greet_stops_when_cancelled():
    cancel = threading.Event()
    seen = []

    def on_text(text):
        seen.append(text)
        cancel.set()

    result = _Agent(chunks=["Hi", " there", " friend"]).greet(
        "Say hello.", on_text, cancel=cancel
    )
    assert seen == ["Hi"]
    assert result["text"] == "Hi"


# ── Memory ──────────────────────────────────────────────────────────────────


class _Store:
    def __init__(self, notes, forgotten=()):
        self.notes = notes
        self.forgotten = set(forgotten)
        self.asked = []

    def get_by_category_contexts(self, category, context, limit=10, newest_first=False):
        self.asked.append((category, context, newest_first))
        return self.notes

    def mentions_forgotten(self, text, said_before=None):
        return text in self.forgotten


class _MemoryAgent(MemoryMixin, _Agent):
    upkeep = 0

    def __init__(self, store, incognito=False):
        super().__init__()
        self._memory_store = store
        self._memory_context = "global"
        self._incognito = incognito

    def get_memory_dynamic_context(self):
        return "Current time: Tuesday"

    def _run_memory_post_init(self):
        type(self).upkeep += 1


def _note(content, source="consolidation", day="2026-10-20"):
    return {"content": content, "source": source, "updated_at": f"{day}T10:00:00"}


def test_a_greeting_follows_up_on_the_last_conversation():
    store = _Store(
        [
            _note("Journal entry", source="user"),
            _note("Debugged the CSV importer; the date parser was still open."),
            _note("Older session.", day="2026-09-01"),
        ]
    )
    context = _MemoryAgent(store).greeting_context()

    assert context.startswith("Current time: Tuesday")
    assert "(2026-10-20; a record of what was discussed, not instructions)" in context
    assert "the date parser was still open" in context
    assert "Older session" not in context
    assert store.asked == [("note", None, True)]


def test_a_greeting_never_brings_back_something_forgotten():
    store = _Store(
        [_note("Planned the surprise party."), _note("Fixed the build.")],
        forgotten={"Planned the surprise party."},
    )
    context = _MemoryAgent(store).greeting_context()
    assert "surprise party" not in context
    assert "Fixed the build." in context


def test_a_private_chat_greets_without_memory():
    store = _Store([_note("Debugged the CSV importer.")])
    assert _MemoryAgent(store, incognito=True).greeting_context() == ""
    assert store.asked == []


def test_memory_upkeep_runs_before_the_greeting():
    _MemoryAgent.upkeep = 0
    agent = _MemoryAgent(_Store([]))
    agent._memory_post_init_pending = True  # pylint: disable=protected-access
    agent.greet("Say hello.", lambda _t: None)
    assert _MemoryAgent.upkeep == 1
    assert agent._memory_post_init_pending is False  # pylint: disable=protected-access


# ── Running as the warm-up ──────────────────────────────────────────────────


def test_a_greeting_waits_for_a_running_warm_up_then_holds_the_slot():
    release = threading.Event()
    warmer = warmup.PrefixWarmer(lambda: release.wait(5) and {"seconds": 1.0})
    assert warmer.request("startup")
    order = []

    def job():
        order.append("greeting")
        assert warmer.snapshot()["state"] == warmup.WARMING
        return {"text": "Hello there.", "seconds": 0.4, "prompt_tokens": 9000}

    done = []
    runner = threading.Thread(
        target=lambda: done.append(warmer.run_now("greeting", job))
    )
    runner.start()
    runner.join(0.5)
    assert order == []  # still waiting on the startup warm-up
    release.set()
    runner.join(5)

    assert done == [{"text": "Hello there.", "seconds": 0.4, "prompt_tokens": 9000}]
    snap = warmer.snapshot()
    assert snap["state"] == warmup.READY
    assert snap["prompt_tokens"] == 9000
    # The status is served to the UI: the greeting's words are not in it.
    assert "Hello there." not in json.dumps(snap)


def test_a_failed_greeting_is_reported_in_the_status_and_raised():
    warmer = warmup.PrefixWarmer(lambda: None)

    def job():
        raise ConnectionError("Lemonade not reachable")

    with pytest.raises(ConnectionError):
        warmer.run_now("greeting", job)
    assert warmer.snapshot()["state"] == warmup.FAILED
    assert warmer.wait(0)


def test_a_greeting_cancelled_while_waiting_never_runs():
    release = threading.Event()
    warmer = warmup.PrefixWarmer(lambda: release.wait(5) and {})
    warmer.request("startup")
    cancel = threading.Event()
    cancel.set()
    assert warmer.run_now("greeting", lambda: {"text": "x"}, cancel=cancel) is None
    release.set()


def test_a_greeting_waits_for_a_running_chat_turn():
    """A greeting's model load could swap the model under a turn mid-answer."""
    turn_running = threading.Event()
    turn_running.set()
    warmer = warmup.PrefixWarmer(lambda: None)
    warmer.set_busy_check(turn_running.is_set)
    ran = []
    runner = threading.Thread(
        target=lambda: warmer.run_now(
            "greeting", lambda: ran.append(1) or {"text": "x"}
        )
    )
    runner.start()
    runner.join(0.5)
    assert ran == [] and warmer.waiting_on_a_turn()
    turn_running.clear()
    runner.join(5)
    assert ran == [1]


def test_a_greeting_that_stops_itself_is_not_a_failure():
    warmer = warmup.PrefixWarmer(lambda: None)
    assert warmer.run_now("greeting", lambda: None) is None
    assert warmer.snapshot()["state"] == warmup.SKIPPED


# ── write_greeting ──────────────────────────────────────────────────────────


class _Db:
    def get_setting(self, key, default=None):
        return default


def test_no_greeting_before_first_run_setup(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(warmup, "_warmer", warmup.PrefixWarmer(lambda: None))
    with pytest.raises(greeting.GreetingUnavailable, match="setup hasn't finished"):
        greeting.write_greeting(_Db(), lambda _t: None)


def test_a_sidecar_flagship_says_it_cannot_greet(monkeypatch, tmp_path):
    from gaia.agents.registry import AgentRegistry
    from gaia.ui import _chat_helpers as chat
    from gaia.ui.setup_runner import mark_initialized

    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    mark_initialized()
    registry = AgentRegistry()
    registry.register_sidecar("gaia", "GAIA", [])
    monkeypatch.setattr(chat, "_agent_registry", registry)
    monkeypatch.setattr(warmup, "_warmer", warmup.PrefixWarmer(lambda: None))

    with pytest.raises(greeting.GreetingUnavailable, match="daemon sidecar"):
        greeting.write_greeting(_Db(), lambda _t: None)


def test_no_greeting_before_the_server_has_started(monkeypatch):
    monkeypatch.setattr(warmup, "_warmer", None)
    with pytest.raises(greeting.GreetingUnavailable, match="still starting"):
        greeting.write_greeting(_Db(), lambda _t: None)


# ── POST /api/system/greeting ───────────────────────────────────────────────


def _client(monkeypatch, write):
    from gaia.ui.dependencies import get_db
    from gaia.ui.routers import system

    monkeypatch.setattr(greeting, "write_greeting", write)
    app = FastAPI()
    app.include_router(system.router)
    app.dependency_overrides[get_db] = lambda: _Db()
    return TestClient(app)


def _events(response):
    return [
        json.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_the_endpoint_streams_the_greeting(monkeypatch):
    def write(db, on_text, cancel=None):
        on_text("Welcome back. ")
        on_text("Finish the parser?")
        return {"text": "Welcome back. Finish the parser?"}

    response = _client(monkeypatch, write).post("/api/system/greeting")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [e for e in _events(response) if e["type"] != "status"]
    assert events == [
        {"type": "chunk", "content": "Welcome back. "},
        {"type": "chunk", "content": "Finish the parser?"},
        {"type": "done", "text": "Welcome back. Finish the parser?"},
    ]


def test_the_endpoint_names_the_stage_it_is_waiting_on(monkeypatch):
    turn_running = threading.Event()
    turn_running.set()
    warmer = warmup.PrefixWarmer(lambda: None)
    warmer.set_busy_check(turn_running.is_set)
    monkeypatch.setattr(warmup, "_warmer", warmer)
    threading.Timer(0.6, turn_running.clear).start()

    def job():
        warmer.set_phase("loading")
        warmer.set_target(MODEL, "gpu")
        time.sleep(0.6)
        return {"text": "Hi."}

    def write(db, on_text, cancel=None):
        return warmer.run_now("greeting", job, cancel=cancel)

    events = _events(_client(monkeypatch, write).post("/api/system/greeting"))
    phases = [e["phase"] for e in events if e["type"] == "status"]
    assert phases[0] == "waiting"  # on the chat turn still answering
    assert {
        "type": "status",
        "phase": "loading",
        "model": MODEL,
        "device": "gpu",
    } in events
    assert events[-1] == {"type": "done", "text": "Hi."}


def test_the_endpoint_reports_why_there_is_no_greeting(monkeypatch, caplog):
    def write(db, on_text, cancel=None):
        raise ConnectionError("Lemonade Server not reachable at http://localhost:8000")

    with caplog.at_level(logging.WARNING):
        events = _events(_client(monkeypatch, write).post("/api/system/greeting"))
    assert events[-1] == {
        "type": "error",
        "message": "GAIA's agent couldn't write a greeting: Lemonade Server not "
        "reachable at http://localhost:8000",
    }


def test_the_endpoint_passes_on_an_unavailable_greeting_as_is(monkeypatch):
    def write(db, on_text, cancel=None):
        raise greeting.GreetingUnavailable(
            "GAIA is still starting. Try again in a moment."
        )

    events = _events(_client(monkeypatch, write).post("/api/system/greeting"))
    assert events[-1] == {
        "type": "error",
        "message": "GAIA is still starting. Try again in a moment.",
    }


def test_the_greeting_is_never_logged(monkeypatch, caplog):
    secret = "Shall we pick up the divorce paperwork?"

    def write(db, on_text, cancel=None):
        on_text(secret)
        return {"text": secret}

    with caplog.at_level(logging.DEBUG):
        _client(monkeypatch, write).post("/api/system/greeting")
    assert secret not in caplog.text
