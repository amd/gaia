# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The collapsed stdin/stdout transport.

No daemon, no HTTP port, no bearer token, no model-slot lease. The properties
worth pinning are the ones the removed layers used to get wrong: the agent
outlives a turn, stdout carries JSON events and nothing else, and every turn
ends with exactly one terminal event.
"""

import io
import json
import logging
import os
import queue
import sys

import pytest
from gaia_agent import stdio


def _logger_tree():
    return [logging.getLogger()] + [
        lg
        for lg in list(logging.root.manager.loggerDict.values())
        if isinstance(lg, logging.Logger)
    ]


@pytest.fixture
def configure_logging(tmp_path, monkeypatch):
    """Run ``_configure_logging`` for real, then put the process back.

    It rebinds ``sys.stdout`` and rewrites every logger in the tree, so without
    this teardown one test would reconfigure logging for the whole session.
    Returns a callable taking the stand-in for the real stdout pipe.
    """
    monkeypatch.setenv(stdio.LOG_PATH_ENV, str(tmp_path / "agent.log"))
    saved_stdout = sys.stdout
    saved = [(lg, list(lg.handlers), lg.level, lg.propagate) for lg in _logger_tree()]
    pre_existing = {id(h) for _, handlers, _, _ in saved for h in handlers}

    yield lambda wire, dev=False: stdio._configure_logging(wire, dev=dev)

    # Only the handlers this call opened are closed — closing one that already
    # belonged to the session would break every later test that logs through it.
    for lg in _logger_tree():
        for handler in list(lg.handlers):
            if id(handler) not in pre_existing and isinstance(
                handler, logging.FileHandler
            ):
                handler.close()
        lg.handlers = []
    for lg, handlers, level, propagate in saved:
        lg.handlers, lg.level, lg.propagate = handlers, level, propagate
    sys.stdout = saved_stdout


def _log_text(path):
    for handler in logging.getLogger(stdio.AUDIT_LOGGER_NAME).handlers:
        handler.flush()
    for handler in logging.getLogger().handlers:
        handler.flush()
    return path.read_text(encoding="utf-8")


class _FakeAgent:
    """Records turns so a test can prove the SAME instance served both."""

    def __init__(self, script=None):
        self.console = None
        self.queries = []
        self.loaded_skills = {}
        self._script = script or []

    def process_query(self, query):
        self.queries.append(query)
        for event in self._script:
            self.console.event_queue.put(dict(event))
        return {"answer": f"answered: {query}"}


def _lines(buf):
    return [line for line in buf.getvalue().split("\n") if line.strip()]


def _run(agent, query):
    out = io.StringIO()
    stdio.run_turn(agent, query, out)
    return out


# ---------------------------------------------------------------------------
# What actually keeps the wire clean
# ---------------------------------------------------------------------------
#
# One unstructured line desynchronises the reader's line scanner permanently.
# Asserting that ``_write``'s json.dumps output parses as JSON cannot catch
# that — the polluters are a library logging to stdout and a stray ``print``,
# and both are stopped by ``_configure_logging``.


def test_a_library_logging_to_stdout_cannot_reach_the_wire(configure_logging):
    """Handlers built at import time already hold the real stdout."""
    wire = io.StringIO()
    noisy = logging.getLogger("some.noisy.library")
    noisy.addHandler(logging.StreamHandler(wire))

    configure_logging(wire, dev=True)
    noisy.error("a library complaining on stdout")

    assert wire.getvalue() == "", "a log record reached the wire"


def test_a_stray_print_cannot_reach_the_wire(configure_logging):
    """``print`` in code we do not control is the other way stdout gets dirty."""
    wire = io.StringIO()
    sys.stdout = wire  # stand in for the real pipe; the fixture restores it

    configure_logging(wire, dev=False)
    print("a stray print from a library")

    assert wire.getvalue() == "", "a print reached the wire"
    assert sys.stdout is sys.stderr


def test_a_turn_still_writes_only_json_events(configure_logging):
    """And with logging locked down, the turn's own output is still parseable."""
    wire = io.StringIO()
    sys.stdout = wire
    configure_logging(wire, dev=True)

    out = _run(_FakeAgent(), "hello")

    assert wire.getvalue() == ""
    for line in _lines(out):
        json.loads(line)


# ---------------------------------------------------------------------------
# The permission audit trail
# ---------------------------------------------------------------------------
#
# The switch that makes every gated tool run unattended must leave a record in
# a NORMAL session. apply_control writes nothing to stdout by design, so if the
# log drops it too, enabling bypass happened nowhere at all.


def test_a_bypass_toggle_is_recorded_at_the_default_log_level(configure_logging):
    wire = io.StringIO()
    path = configure_logging(wire, dev=False)  # user mode, NOT --dev

    stdio.PermissionState().set_bypass(True)

    assert "Bypass permissions ENABLED" in _log_text(path)
    assert wire.getvalue() == "", "the audit trail must never touch the wire"


def test_turning_bypass_off_is_recorded_too(configure_logging):
    wire = io.StringIO()
    path = configure_logging(wire, dev=False)

    stdio.PermissionState(bypass=True).set_bypass(False)

    assert "Bypass permissions disabled" in _log_text(path)


def test_launching_unattended_is_recorded_too(configure_logging):
    """--bypass-permissions never goes through set_bypass, so the strongest
    case for a record is the one that had none."""
    wire = io.StringIO()
    path = configure_logging(wire, dev=False)

    stdio.PermissionState(bypass=True)

    assert "Bypass permissions ENABLED at launch" in _log_text(path)


def test_a_denied_and_dropped_decision_is_recorded_at_the_default_level(
    configure_logging,
):
    """An unreadable decision is denied; with no turn running it is dropped.

    Both are security history, and both used to log one tier below what user
    mode keeps.
    """
    wire = io.StringIO()
    path = configure_logging(wire, dev=False)

    stdio.apply_control(
        {stdio.CONTROL_KEY: stdio.CONTROL_TOOL_DECISION, "decision": "maybe"},
        stdio.PermissionState(),
    )

    text = _log_text(path)
    assert "Unknown tool decision 'maybe' — denying" in text
    assert "no turn is running" in text
    assert wire.getvalue() == ""


def test_turn_ends_with_exactly_one_terminal_event():
    """A turn with no terminal event leaves the reader blocked on a dead pipe."""
    out = _run(_FakeAgent(), "hello")

    terminals = [
        json.loads(line)
        for line in _lines(out)
        if json.loads(line).get("type") in ("final", "error")
    ]
    assert len(terminals) == 1
    assert terminals[0]["type"] == "final"
    assert "answered: hello" in terminals[0]["answer"]


def test_the_console_is_handed_back_when_the_turn_ends():
    """Between turns the agent must not hold a dead handler.

    Base-agent threads outlive their turn (``_call_tool_bounded`` leaves a
    timed-out worker running), so a stale handler's queue grows with events
    nobody will ever drain, and its ``cancelled`` flag is already set.
    """
    agent = _FakeAgent()
    real_console = object()
    agent.console = real_console

    _run(agent, "hello")

    assert agent.console is real_console


def test_the_agent_survives_between_turns():
    """The whole point of the collapse: state set in one turn is there in the next.

    Under the old per-request construction a skill loaded in turn 1 was gone by
    turn 2, while the model kept telling the user it was still active.
    """
    agent = _FakeAgent()

    _run(agent, "first")
    agent.loaded_skills["github-triage"] = object()
    _run(agent, "second")

    assert agent.queries == ["first", "second"]
    assert "github-triage" in agent.loaded_skills


def test_an_agent_exception_becomes_a_terminal_error():
    """A crashed turn must report, not hang the reader."""

    class _Boom(_FakeAgent):
        def process_query(self, query):
            raise RuntimeError("tool exploded")

    out = _run(_Boom(), "hello")

    terminals = [
        json.loads(line)
        for line in _lines(out)
        if json.loads(line).get("type") in ("final", "error")
    ]
    assert len(terminals) == 1
    assert terminals[0]["type"] == "error"
    assert "tool exploded" in terminals[0]["detail"]


def test_unreachable_lemonade_gets_actionable_copy():
    """The raw urllib3 repr tells a user nothing; name the fix instead."""
    detail = stdio._terminal_error(
        ConnectionError("Max retries exceeded ... Connection refused")
    )["detail"]

    assert "Lemonade" in detail
    assert "lemonade-server serve" in detail


def test_an_anthropic_outage_is_not_blamed_on_lemonade():
    """The same "connection refused" words; the Lemonade fix points at the
    wrong backend, so a user restarts a server that was never involved."""
    detail = stdio._terminal_error(
        ConnectionError("anthropic: Max retries exceeded ... Connection refused")
    )["detail"]

    assert "lemonade-server serve" not in detail
    assert "Max retries exceeded" in detail


def test_an_anthropic_sdk_exception_is_recognised_by_its_module():
    """The text need not say "anthropic" — the exception's own module does."""

    class APIConnectionError(ConnectionError):
        pass

    APIConnectionError.__module__ = "anthropic._exceptions"

    detail = stdio._terminal_error(APIConnectionError("Connection refused"))["detail"]

    assert "lemonade-server serve" not in detail


def test_a_memory_dump_failure_becomes_a_terminal_error(monkeypatch):
    """A bad dump query is a real bug, not "you have no memories"."""

    def _boom(_agent):
        raise RuntimeError("memory schema drifted")

    monkeypatch.setattr(stdio, "build_memory_dump", _boom)

    event = stdio._memory_dump_event(_FakeAgent())

    assert event["type"] == "error"
    assert "memory schema drifted" in event["detail"]


def test_log_path_defaults_to_the_shared_file(monkeypatch):
    """No override: the historic shared location, unchanged."""
    monkeypatch.delenv(stdio.LOG_PATH_ENV, raising=False)

    assert stdio.log_path().name == "gaia-agent.log"
    assert stdio.log_path().parent.name == "logs"


def test_log_path_honours_the_env_override(tmp_path, monkeypatch):
    """Several agents can run at once and they all append to one file.

    Interleaved records from two sessions are worse than none: a timeout logged
    by a neighbouring agent reads as a failure of the one being watched. A
    harness driving a single TUI needs a private log to attribute anything.
    """
    private = tmp_path / "session" / "agent.log"
    monkeypatch.setenv(stdio.LOG_PATH_ENV, str(private))

    assert stdio.log_path() == private


def test_log_path_ignores_a_blank_override(tmp_path, monkeypatch):
    """An empty/whitespace value is an unset variable, not a request to log to ''."""
    monkeypatch.setenv(stdio.LOG_PATH_ENV, "   ")

    assert stdio.log_path().name == "gaia-agent.log"


# ---------------------------------------------------------------------------
# Conversation continuity
# ---------------------------------------------------------------------------
#
# The bug these guard: Agent composes each request as
# [system, *conversation_history, user], and nothing in the base class ever
# appends to conversation_history. The HTTP surface fills it per request; this
# transport did not, so every TUI turn reached the model as exactly two
# messages — system + the current question — and the agent could not resolve a
# reference to anything said one turn earlier.
#
# Observed: one turn after triaging amd/gaia, "cool, can you print issue 2975?"
# got "I need to know which repository it belongs to".
#
# test_the_agent_survives_between_turns did NOT catch this. It asserts that
# OBJECT state (agent.loaded_skills) survives, which it does — the agent is the
# same object. History is not accumulated object state; nobody was appending.


class _HistoryAgent(_FakeAgent):
    """A fake carrying the base class's conversation_history attribute."""

    def __init__(self):
        super().__init__()
        self.conversation_history = []


def test_a_turn_is_recorded_for_the_next_prompt():
    agent = _HistoryAgent()

    stdio._record_turn(agent, "who owns amd/gaia?", "AMD does.")

    assert agent.conversation_history == [
        {"role": "user", "content": "who owns amd/gaia?"},
        {"role": "assistant", "content": "AMD does."},
    ]


def test_history_accumulates_across_turns():
    """The actual regression: turn 2 must be able to see turn 1."""
    agent = _HistoryAgent()

    stdio._record_turn(agent, "list issues in amd/gaia", "#2975, #2974, #2973")
    stdio._record_turn(agent, "print issue 2975", "...")

    contents = [m["content"] for m in agent.conversation_history]
    assert "list issues in amd/gaia" in contents, "the repo turn was not carried"
    assert len(agent.conversation_history) == 4


def test_history_is_trimmed_in_whole_turns():
    """A window opening on an answer whose question was dropped reads as the
    model asserting something unprompted."""
    agent = _HistoryAgent()

    for i in range(stdio.MAX_HISTORY_TURNS + 6):
        stdio._record_turn(agent, f"q{i}", f"a{i}")

    assert len(agent.conversation_history) == stdio.MAX_HISTORY_TURNS * 2
    assert agent.conversation_history[0]["role"] == "user"
    assert agent.conversation_history[-1]["role"] == "assistant"


def test_an_empty_query_is_not_recorded():
    agent = _HistoryAgent()

    stdio._record_turn(agent, "   ", "something")

    assert agent.conversation_history == []


def test_an_agent_without_history_is_left_alone():
    """Never invent the attribute on an agent that does not define it."""
    agent = _FakeAgent()

    stdio._record_turn(agent, "hello", "hi")

    assert not hasattr(agent, "conversation_history")


def test_a_real_turn_lands_in_history():
    """End to end through _run, not just the helper."""
    agent = _HistoryAgent()

    _run(agent, "first question")

    assert [m["role"] for m in agent.conversation_history] == ["user", "assistant"]
    assert agent.conversation_history[0]["content"] == "first question"


class _PolicyBlockedAgent(_HistoryAgent):
    """Emits a mid-run governance block, then keeps working and answers.

    ``policy_alert`` maps to a canonical ``error`` (see sse_translation) while
    the run itself continues — the exact shape the write clamp exists for.
    """

    def process_query(self, query):
        self.queries.append(query)
        self.console.event_queue.put(
            {
                "type": "policy_alert",
                "reason": "blocked by policy",
                "tool": "write_file",
            }
        )
        return {"answer": "I carried on afterwards"}


def test_nothing_is_written_after_the_first_terminal_event():
    """The reader stops at the first terminal event, so a later ``final`` would
    be consumed as the opening events of the NEXT turn."""
    out = _run(_PolicyBlockedAgent(), "go")

    events = [json.loads(line) for line in _lines(out)]
    terminals = [e for e in events if e.get("type") in ("final", "error")]
    assert len(terminals) == 1, events
    assert terminals[0]["type"] == "error"
    assert events[-1] is terminals[0], "something was written after the terminal event"


def test_a_turn_that_ended_in_an_error_event_is_not_recorded():
    """Replaying a failure as if it were an answer teaches the model that the
    failure is what it said."""
    agent = _PolicyBlockedAgent()

    _run(agent, "go")

    assert agent.conversation_history == []


def test_a_crashed_turn_is_not_recorded_either():
    """The other error exit: process_query raised, so there is no answer."""

    class _BoomWithHistory(_HistoryAgent):
        def process_query(self, query):
            raise RuntimeError("tool exploded")

    agent = _BoomWithHistory()

    _run(agent, "hello")

    assert agent.conversation_history == []


def test_an_empty_final_is_still_recorded():
    """Dropping it would also drop the QUESTION, and "try that again" must not
    reach a model with no record it was asked."""

    class _SilentAgent(_HistoryAgent):
        def process_query(self, query):
            self.queries.append(query)
            return {"answer": ""}

    agent = _SilentAgent()

    _run(agent, "say nothing")

    assert [m["content"] for m in agent.conversation_history] == ["say nothing", ""]


# ---------------------------------------------------------------------------
# Live model switching (/model)
# ---------------------------------------------------------------------------
#
# Conversation history and loaded_skills must survive a switch — that's the
# whole point of swapping the client in place instead of respawning the
# child. Every fake here reuses the SAME agent object across assertions so a
# test that mutated agent.chat.llm_client but forgot to touch
# conversation_history would show up as a real regression.


class _AgentSDKConfigStub:
    """Stands in for gaia.chat.sdk.AgentConfig — the fields _switch_model reads."""

    def __init__(self):
        self.base_url = "http://127.0.0.1:13305/api/v1"
        self.system_prompt = "you are gaia"
        self.use_claude = False
        self.model = "Gemma-4-E4B-it-GGUF"
        self.claude_model = "claude-sonnet-5"


class _ChatAgentConfigStub:
    """Stands in for ChatAgentConfig, which names the field ``model_id``.

    Not the same shape as AgentConfig above: a stub carrying ``model`` here
    lets ``_apply_switch`` CREATE ``model_id``, so nothing can assert what
    rollback restores it to.
    """

    def __init__(self):
        self.use_claude = False
        self.model_id = None
        self.claude_model = "claude-sonnet-5"


class _AgentSDKStub:
    """Stands in for AgentSDK — the fields _switch_model / _model_state_event touch."""

    def __init__(self):
        self.config = _AgentSDKConfigStub()
        self.llm_client = "lemonade-client-0"

    @property
    def effective_model(self):
        return self.config.claude_model if self.config.use_claude else self.config.model


class _ModelSwitchAgent(_FakeAgent):
    """A fake carrying the live-model-switch surface stdio.py mutates."""

    def __init__(self):
        super().__init__()
        self.chat = _AgentSDKStub()
        self.config = _ChatAgentConfigStub()
        self._use_claude = False
        self.model_id = self.chat.config.model
        self.rebuild_count = 0

    def rebuild_system_prompt(self):
        self.rebuild_count += 1


def _model_run(agent, query):
    out = io.StringIO()
    stdio.run_model_command(agent, query, out)
    return out


def _events(out):
    return [json.loads(line) for line in _lines(out)]


def test_is_model_command_recognises_both_forms():
    assert stdio.is_model_command("/model")
    assert stdio.is_model_command("/model claude-sonnet-5")
    assert stdio.is_model_command("  /model claude-sonnet-5  ")
    assert not stdio.is_model_command("what model do you use?")
    assert not stdio.is_model_command("/modeling clay")


def test_model_command_never_reaches_process_query(stub_lemonade):
    """The LLM must never be asked to "answer" a slash command."""
    agent = _ModelSwitchAgent()

    _model_run(agent, "/model")

    assert agent.queries == []


def test_model_no_arg_lists_switchable_models(monkeypatch):
    from gaia_agent import stdio as stdio_mod

    monkeypatch.setattr(
        stdio_mod, "_lemonade_models", lambda base_url: ["Gemma-4-E4B-it-GGUF"]
    )
    agent = _ModelSwitchAgent()

    out = _model_run(agent, "/model")

    events = _events(out)
    assert len(events) == 1 and events[0]["type"] == "final"
    answer = events[0]["answer"]
    assert "claude-sonnet-5" in answer
    assert "Gemma-4-E4B-it-GGUF" in answer
    assert "current" in answer  # the resolved default is marked


def test_model_switch_to_claude_succeeds(monkeypatch, stub_lemonade):
    from gaia_agent import stdio as stdio_mod

    sentinel_client = object()
    monkeypatch.setattr(stdio_mod, "create_client", lambda **kwargs: sentinel_client)
    agent = _ModelSwitchAgent()

    out = _model_run(agent, "/model claude-opus-5")

    events = _events(out)
    assert [e["type"] for e in events] == ["status", "final"]
    banner = events[0]
    assert banner["model_id"] == "claude-opus-5"
    assert banner["model_display"] == "Opus 5"
    assert banner["model_backend"] == "claude"
    assert banner["model_remote"] is True
    assert "Opus 5" in events[1]["answer"]

    # The live client actually moved, and history-carrying state was never
    # touched by the swap.
    assert agent.chat.llm_client is sentinel_client
    assert agent.chat.config.use_claude is True
    assert agent.chat.config.claude_model == "claude-opus-5"
    assert agent._use_claude is True
    assert agent.model_id == "claude-opus-5"
    assert agent.config.use_claude is True
    assert agent.config.claude_model == "claude-opus-5"
    # Left absent-shaped: a Claude switch names no local model id.
    assert agent.config.model_id is None
    assert agent.rebuild_count == 1


def test_model_switch_to_local_succeeds(monkeypatch, stub_lemonade):
    from gaia_agent import stdio as stdio_mod

    sentinel_client = object()
    monkeypatch.setattr(
        stdio_mod, "_lemonade_models", lambda base_url: ["Qwen3-4B-Instruct-2507-GGUF"]
    )
    monkeypatch.setattr(stdio_mod, "create_client", lambda **kwargs: sentinel_client)
    agent = _ModelSwitchAgent()
    agent.chat.config.use_claude = True  # start on Claude, switch back to local

    out = _model_run(agent, "/model Qwen3-4B-Instruct-2507-GGUF")

    events = _events(out)
    assert [e["type"] for e in events] == ["status", "final"]
    assert events[0]["model_backend"] == "lemonade"
    assert events[0]["model_remote"] is False

    assert agent.chat.llm_client is sentinel_client
    assert agent.chat.config.use_claude is False
    assert agent.chat.config.model == "Qwen3-4B-Instruct-2507-GGUF"
    assert agent._use_claude is False
    assert agent.model_id == "Qwen3-4B-Instruct-2507-GGUF"
    assert agent.config.model_id == "Qwen3-4B-Instruct-2507-GGUF"


def test_model_switch_unknown_claude_id_is_refused_not_accepted(monkeypatch):
    from gaia_agent import stdio as stdio_mod

    called = []
    monkeypatch.setattr(
        stdio_mod, "create_client", lambda **kwargs: called.append(kwargs)
    )
    agent = _ModelSwitchAgent()
    previous_client = agent.chat.llm_client

    out = _model_run(agent, "/model claude-nonexistent-9")

    events = _events(out)
    assert len(events) == 1 and events[0]["type"] == "error"
    assert "Unknown Claude model" in events[0]["detail"]
    for valid_id in stdio_mod.CLAUDE_MODELS:
        assert valid_id in events[0]["detail"]
    # Never even attempted to build a client for a name that isn't offered.
    assert called == []
    # Nothing about the session moved.
    assert agent.chat.llm_client is previous_client
    assert agent.chat.config.use_claude is False
    assert agent._use_claude is False
    assert agent.rebuild_count == 0


def test_model_switch_unknown_local_id_is_refused_not_accepted(monkeypatch):
    from gaia_agent import stdio as stdio_mod

    monkeypatch.setattr(
        stdio_mod, "_lemonade_models", lambda base_url: ["Gemma-4-E4B-it-GGUF"]
    )
    agent = _ModelSwitchAgent()
    previous_client = agent.chat.llm_client

    out = _model_run(agent, "/model TotallyMadeUpModel-GGUF")

    events = _events(out)
    assert len(events) == 1 and events[0]["type"] == "error"
    assert "Unknown local model" in events[0]["detail"]
    assert "Gemma-4-E4B-it-GGUF" in events[0]["detail"]
    assert agent.chat.llm_client is previous_client
    assert agent.rebuild_count == 0


def test_model_switch_missing_credential_leaves_previous_model_running(monkeypatch):
    """FAIL LOUDLY: a bad/missing credential must not half-swap the session."""
    from gaia_agent import stdio as stdio_mod

    def _boom(**kwargs):
        raise ValueError(
            "ANTHROPIC_API_KEY not found in environment.\n\nRun `claude setup-token`..."
        )

    monkeypatch.setattr(stdio_mod, "create_client", _boom)
    agent = _ModelSwitchAgent()
    previous_client = agent.chat.llm_client

    out = _model_run(agent, "/model claude-sonnet-5")

    events = _events(out)
    assert len(events) == 1 and events[0]["type"] == "error"
    assert "ANTHROPIC_API_KEY" in events[0]["detail"]
    # Left on its previous WORKING model, not half-swapped.
    assert agent.chat.llm_client is previous_client
    assert agent.chat.config.use_claude is False
    assert agent._use_claude is False
    assert agent.model_id == "Gemma-4-E4B-it-GGUF"
    assert agent.rebuild_count == 0


def test_model_switch_lemonade_unreachable_is_actionable_and_leaves_model_running(
    monkeypatch,
):
    from gaia_agent import stdio as stdio_mod

    def _unreachable(base_url):
        raise RuntimeError(
            f"Lemonade Server is not reachable at {base_url} (connection refused). "
            "Start it with `lemonade-server serve`, then retry."
        )

    monkeypatch.setattr(stdio_mod, "_lemonade_models", _unreachable)
    agent = _ModelSwitchAgent()
    previous_client = agent.chat.llm_client

    out = _model_run(agent, "/model some-local-model")

    events = _events(out)
    assert len(events) == 1 and events[0]["type"] == "error"
    assert "13305" in events[0]["detail"]
    assert "lemonade-server serve" in events[0]["detail"]
    assert agent.chat.llm_client is previous_client
    assert agent.rebuild_count == 0


def test_model_switch_survives_alongside_history_and_skills(monkeypatch):
    """The whole point: switching models must not disturb what makes the
    flagship agent stateful (see test_the_agent_survives_between_turns)."""
    from gaia_agent import stdio as stdio_mod

    monkeypatch.setattr(stdio_mod, "create_client", lambda **kwargs: object())
    agent = _ModelSwitchAgent()
    agent.conversation_history = [{"role": "user", "content": "hello"}]
    agent.loaded_skills["github-triage"] = object()

    _model_run(agent, "/model claude-sonnet-5")

    assert agent.conversation_history == [{"role": "user", "content": "hello"}]
    assert "github-triage" in agent.loaded_skills


def test_model_state_event_names_the_resolved_model_not_a_launch_flag(stub_lemonade):
    agent = _ModelSwitchAgent()

    banner = stdio._model_state_event(agent)

    assert banner["type"] == "status"
    assert banner["model_id"] == "Gemma-4-E4B-it-GGUF"
    assert banner["model_backend"] == "lemonade"
    assert banner["model_remote"] is False
    assert banner["lemonade_reachable"] is True
    assert banner["lemonade_version"] == "8.1.2"


def test_a_bad_base_url_is_reported_as_such_not_as_a_dead_server(monkeypatch, caplog):
    """A malformed LEMONADE_BASE_URL read to the user as "Lemonade isn't
    running", so they restarted a server that was never the problem."""

    class _Unbuildable:
        def __init__(self, base_url=None, verbose=True):
            raise ValueError(f"invalid base_url {base_url!r}")

    monkeypatch.setattr(stdio, "LemonadeClient", _Unbuildable)

    with caplog.at_level(logging.WARNING, logger=stdio.logger.name):
        state = stdio._lemonade_health("http:/not-a-url")

    assert state["lemonade_reachable"] is False
    # Same payload shape as the reachable branch: a consumer reading the URL
    # must not silently lose the field.
    assert state["lemonade_base_url"] == "http:/not-a-url"
    assert "invalid base_url" in caplog.text


def test_the_rollback_restores_an_absent_model_id(monkeypatch, stub_lemonade):
    """_apply_switch CREATES cfg.model_id; rollback must put back what was
    there, not None-because-nobody-looked."""
    from gaia_agent import stdio as stdio_mod

    monkeypatch.setattr(
        stdio_mod, "_lemonade_models", lambda base_url: ["Qwen3-4B-Instruct-2507-GGUF"]
    )
    monkeypatch.setattr(stdio_mod, "create_client", lambda **kwargs: object())
    agent = _ModelSwitchAgent()
    agent.config.model_id = "Gemma-4-E4B-it-GGUF"

    def _boom():
        raise RuntimeError("prompt composition bug")

    agent.rebuild_system_prompt = _boom

    _model_run(agent, "/model Qwen3-4B-Instruct-2507-GGUF")

    assert agent.config.model_id == "Gemma-4-E4B-it-GGUF"


# ---------------------------------------------------------------------------
# _lemonade_models: goes through LemonadeClient, filters to chat-capable
# ---------------------------------------------------------------------------


class _FakeLemonadeClient:
    """Stands in for gaia.llm.lemonade_client.LemonadeClient — the seam
    _lemonade_models and _lemonade_health go through instead of a real socket."""

    catalog = {"data": []}
    error = None

    def __init__(self, base_url=None, verbose=True):
        self.base_url = base_url or "http://127.0.0.1:13305/api/v1"

    def list_models(self, show_all=False):
        assert show_all is True, "labels/downloaded are only in the full catalog"
        if self.error is not None:
            raise self.error
        return self.catalog

    def health_check(self):
        return {"version": "8.1.2"}


@pytest.fixture
def stub_lemonade(monkeypatch):
    """Keep a unit test off the network.

    ``_model_state_event`` calls ``_lemonade_health`` unconditionally, so any
    test reaching it builds a real client and opens a socket to the stub
    config's hardcoded port — machine-dependent, a timeout per test in CI, and
    a hang if anything else is listening there.
    """
    _FakeLemonadeClient.error = None
    _FakeLemonadeClient.catalog = {"data": []}
    monkeypatch.setattr(stdio, "LemonadeClient", _FakeLemonadeClient)
    return _FakeLemonadeClient


def test_lemonade_models_excludes_embedding_and_image_and_not_downloaded(monkeypatch):
    """Offering a non-chat model as a switch target reports success and
    breaks silently on the NEXT turn — far from the mistake. Must never
    happen."""
    from gaia_agent import stdio as stdio_mod

    fake = _FakeLemonadeClient
    fake.catalog = {
        "data": [
            {"id": "Gemma-4-E4B-it-GGUF", "downloaded": True, "labels": ["hot"]},
            {
                "id": "nomic-embed-text-v2-moe-GGUF",
                "downloaded": True,
                "labels": ["embeddings"],
            },
            {"id": "SDXL-Turbo", "downloaded": True, "labels": ["image"]},
            {"id": "Qwen3-4B-Instruct-2507-GGUF", "downloaded": False, "labels": []},
            {"id": "Phi-4-mini-instruct-GGUF", "downloaded": True, "labels": []},
        ]
    }
    fake.error = None
    monkeypatch.setattr(stdio_mod, "LemonadeClient", fake)

    models = stdio_mod._lemonade_models("http://127.0.0.1:13305/api/v1")

    assert models == ["Gemma-4-E4B-it-GGUF", "Phi-4-mini-instruct-GGUF"]


def test_lemonade_models_unreachable_names_url_and_fix(monkeypatch):
    from gaia_agent import stdio as stdio_mod

    fake = _FakeLemonadeClient
    fake.error = stdio_mod.LemonadeClientError("connection refused")
    monkeypatch.setattr(stdio_mod, "LemonadeClient", fake)

    try:
        stdio_mod._lemonade_models("http://127.0.0.1:13305/api/v1")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "13305" in str(exc)
        assert "lemonade-server serve" in str(exc)
    finally:
        fake.error = None


# ---------------------------------------------------------------------------
# Atomic apply: a switch is all-or-nothing, including rebuild_system_prompt
# ---------------------------------------------------------------------------


def test_model_switch_rolls_back_when_rebuild_system_prompt_fails(monkeypatch):
    """The original implementation mutated agent/chat and THEN called
    rebuild_system_prompt() — a failure there escaped as a raw exception
    with the client already swapped. Caught in review; this pins the fix."""
    from gaia_agent import stdio as stdio_mod

    monkeypatch.setattr(stdio_mod, "create_client", lambda **kwargs: object())
    agent = _ModelSwitchAgent()
    previous_client = agent.chat.llm_client

    def _boom():
        raise RuntimeError("prompt composition bug")

    agent.rebuild_system_prompt = _boom
    assert agent.chat.config.claude_model != "claude-opus-5", "precondition"

    out = _model_run(agent, "/model claude-opus-5")

    events = _events(out)
    assert len(events) == 1 and events[0]["type"] == "error"
    assert "rolled back" in events[0]["detail"]
    # Fully back to the previous WORKING state, not half-swapped — in
    # particular claude_model must NOT have latched onto the failed target.
    assert agent.chat.llm_client is previous_client
    assert agent.chat.config.use_claude is False
    assert agent.chat.config.claude_model != "claude-opus-5"
    assert agent._use_claude is False
    assert agent.model_id == "Gemma-4-E4B-it-GGUF"
    assert agent.config.use_claude is False


class TestAMultiLineQuestionArrivesWhole:
    """stdin is read a line at a time, so a raw multi-line query is not one query.

    Pasting five commit messages and asking for a changelog sent five separate
    questions. The agent answered the first — "that's the only commit you sent"
    — and the other four lines were never part of the question at all. Every
    multi-line paste, every Alt+Enter composition, hit this.
    """

    def test_a_wrapped_query_keeps_its_newlines(self):
        from gaia_agent.stdio import QUERY_KEY, parse_query

        question = "line one\nline two\nline three"
        wire = json.dumps({QUERY_KEY: question})

        assert "\n" not in wire, "the wire form must be a single line"
        assert parse_query(wire) == question

    def test_a_bare_line_is_still_a_query(self):
        """An older host sends the question verbatim; that must keep working."""
        from gaia_agent.stdio import parse_query

        assert parse_query("what is 17 times 23?") == "what is 17 times 23?"

    @pytest.mark.parametrize(
        "line",
        [
            '{"not_a_query": "x"}',
            '{"gaia_query": 42}',
            "{ this is not json",
            '{"role": "user", "content": "explain this JSON to me"}',
        ],
    )
    def test_a_question_that_looks_like_json_stays_a_question(self, line):
        from gaia_agent.stdio import parse_query

        assert parse_query(line) == line

    def test_control_messages_are_still_routed_away_from_queries(self):
        from gaia_agent.stdio import CONTROL_KEY, parse_control, parse_query

        control = json.dumps({CONTROL_KEY: "bypass", "enabled": True})
        assert parse_control(control) is not None
        # And a query is never mistaken for control.
        assert parse_control(json.dumps({"gaia_query": "hello"})) is None
        assert parse_query(json.dumps({"gaia_query": "hello"})) == "hello"


# ---------------------------------------------------------------------------
# The stdin pump
# ---------------------------------------------------------------------------
#
# It is the process's only exit signal and the only thing that can answer a
# confirmation while a turn is running, and it had no tests at all.


def _pump(monkeypatch, text):
    """Drive _pump_stdin over *text* and return (queued items, state)."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))
    queries = queue.Queue()
    state = stdio.PermissionState()
    stdio._pump_stdin(queries, state)
    drained = []
    while True:
        try:
            drained.append(queries.get_nowait())
        except queue.Empty:
            break
    return drained, state


def test_the_pump_routes_control_away_from_queries(monkeypatch):
    lines = [
        "",
        "   ",
        json.dumps({stdio.CONTROL_KEY: "bypass", "enabled": True}),
        "what is 2+2?",
        json.dumps({stdio.QUERY_KEY: "line one\nline two"}),
    ]

    drained, state = _pump(monkeypatch, "\n".join(lines) + "\n")

    assert state.bypass is True, "the control line never reached apply_control"
    assert drained == ["what is 2+2?", "line one\nline two", None]


def test_the_pump_ends_with_the_sentinel_on_eof(monkeypatch):
    """The sentinel is what breaks main's run loop; without it the process
    waits on a queue nothing will ever fill again."""
    drained, _ = _pump(monkeypatch, "hello\n")

    assert drained[-1] is None


def test_a_control_line_that_explodes_does_not_take_the_pump_down(monkeypatch):
    """Losing this thread means every later confirmation hangs with nothing
    able to answer it — so the swallow here is deliberate, not an oversight."""

    def _boom(_message, _state):
        raise RuntimeError("control handler bug")

    monkeypatch.setattr(stdio, "apply_control", _boom)
    lines = [json.dumps({stdio.CONTROL_KEY: "bypass", "enabled": True}), "still here?"]

    drained, _ = _pump(monkeypatch, "\n".join(lines) + "\n")

    assert drained == ["still here?", None]


def test_the_pump_queues_the_sentinel_even_when_stdin_itself_raises(monkeypatch):
    """A decode error out of the iteration used to skip the sentinel entirely."""

    class _ExplodingStdin:
        def __iter__(self):
            return self

        def __next__(self):
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(sys, "stdin", _ExplodingStdin())
    queries = queue.Queue()

    with pytest.raises(UnicodeDecodeError):
        stdio._pump_stdin(queries, stdio.PermissionState())

    assert queries.get_nowait() is None


# ---------------------------------------------------------------------------
# The argv contract
# ---------------------------------------------------------------------------
#
# tui/internal/client/factory.go pins these spellings as literal Go string
# constants and the catalog appends --json-events. A rename passes every other
# Python test here and fails at spawn as a generic "exited (code 2)".


def test_the_parser_accepts_the_spellings_the_go_side_pins():
    args = stdio.build_parser().parse_args(
        [
            "--use-claude",
            "--claude-model",
            "claude-opus-5",
            "--bypass-permissions",
            "--json-events",
            "--dev",
        ]
    )

    assert args.use_claude is True
    assert args.claude_model == "claude-opus-5"
    assert args.bypass_permissions is True
    assert args.json_events is True
    assert args.dev is True


def test_the_parser_defaults_to_local_and_prompting():
    args = stdio.build_parser().parse_args([])

    assert args.use_claude is False
    assert args.bypass_permissions is False, "permissions must never default off"
    assert args.claude_model is None
    assert args.model is None


# ---------------------------------------------------------------------------
# main: the run loop's own failure handling
# ---------------------------------------------------------------------------


class _DeadAfter:
    """A pipe that dies partway through, the way a parent exiting does."""

    def __init__(self, alive_writes):
        self.remaining = alive_writes
        self.written = []

    def write(self, text):
        if self.remaining <= 0:
            raise BrokenPipeError(32, "The pipe is being closed")
        self.remaining -= 1
        self.written.append(text)
        return len(text)

    def flush(self):
        if self.remaining <= 0:
            raise BrokenPipeError(32, "The pipe is being closed")


def test_main_exits_cleanly_when_the_parent_closes_the_pipe(monkeypatch):
    """A broken pipe IS the parent leaving, so there is nobody to report to.

    The recovery path called ``_write`` again on the same dead pipe, raised a
    second time, and escaped ``main`` — so the process died by traceback
    instead of through its clean exit.
    """
    import gaia_agent.agent as agent_mod

    class _Agent:
        def __init__(self, config=None):
            self.console = None

        def process_query(self, query):
            return {"answer": "hi"}

    monkeypatch.setattr(agent_mod, "GaiaAgent", _Agent)
    monkeypatch.setattr(agent_mod, "GaiaAgentConfig", lambda **kwargs: None)
    monkeypatch.setattr(stdio, "_configure_logging", lambda out, dev=False: None)
    monkeypatch.setattr(stdio, "_model_state_event", lambda agent: {"type": "status"})
    monkeypatch.setattr(sys, "stdin", io.StringIO("hello\n"))
    # The model banner goes out, then the parent exits mid-turn.
    monkeypatch.setattr(sys, "stdout", _DeadAfter(2))

    exits = []
    monkeypatch.setattr(os, "_exit", lambda code: exits.append(code))

    stdio.main([])

    assert exits == [0], "main must reach its clean exit, not die by traceback"


def test_main_reports_a_crashed_turn_and_keeps_going(monkeypatch):
    """The other half of the same handler: a live wire still gets the error."""
    import gaia_agent.agent as agent_mod

    class _Agent:
        def __init__(self, config=None):
            self.console = None

        def process_query(self, query):
            return {"answer": "hi"}

    def _boom(*args, **kwargs):
        raise RuntimeError("dispatch bug")

    monkeypatch.setattr(agent_mod, "GaiaAgent", _Agent)
    monkeypatch.setattr(agent_mod, "GaiaAgentConfig", lambda **kwargs: None)
    monkeypatch.setattr(stdio, "_configure_logging", lambda out, dev=False: None)
    monkeypatch.setattr(stdio, "_model_state_event", lambda agent: {"type": "status"})
    monkeypatch.setattr(stdio, "dispatch_query", _boom)
    monkeypatch.setattr(sys, "stdin", io.StringIO("hello\n"))
    wire = io.StringIO()
    monkeypatch.setattr(sys, "stdout", wire)
    monkeypatch.setattr(os, "_exit", lambda code: None)

    stdio.main([])

    events = [json.loads(line) for line in _lines(wire)]
    assert events[-1]["type"] == "error"
    assert "dispatch bug" in events[-1]["detail"]
