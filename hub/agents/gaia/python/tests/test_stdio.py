"""The collapsed stdin/stdout transport.

No daemon, no HTTP port, no bearer token, no model-slot lease. The properties
worth pinning are the ones the removed layers used to get wrong: the agent
outlives a turn, stdout carries JSON events and nothing else, and every turn
ends with exactly one terminal event.
"""

import io
import json

from gaia_agent import stdio


class _FakeHandler:
    """Stands in for SSEOutputHandler: a queue the agent pushes events onto."""

    def __init__(self):
        import queue

        self.event_queue = queue.Queue()

    def signal_done(self):
        self.event_queue.put(None)


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


def test_every_stdout_line_is_json():
    """One unstructured line desynchronises the reader's scanner permanently."""
    out = _run(_FakeAgent(), "hello")

    for line in _lines(out):
        json.loads(line)  # raises if the wire is polluted


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
        self.config = _AgentSDKConfigStub()  # ChatAgentConfig stand-in
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


def test_model_command_never_reaches_process_query():
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


def test_model_switch_to_claude_succeeds(monkeypatch):
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
    assert agent.rebuild_count == 1


def test_model_switch_to_local_succeeds(monkeypatch):
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


def test_model_state_event_names_the_resolved_model_not_a_launch_flag():
    agent = _ModelSwitchAgent()

    banner = stdio._model_state_event(agent)

    assert banner["type"] == "status"
    assert banner["model_id"] == "Gemma-4-E4B-it-GGUF"
    assert banner["model_backend"] == "lemonade"
    assert banner["model_remote"] is False


# ---------------------------------------------------------------------------
# _lemonade_models: goes through LemonadeClient, filters to chat-capable
# ---------------------------------------------------------------------------


class _FakeLemonadeClient:
    """Stands in for gaia.llm.lemonade_client.LemonadeClient — the seam
    _lemonade_models goes through instead of a bespoke `requests` call."""

    catalog = {"data": []}
    error = None

    def __init__(self, base_url=None, verbose=True):
        self.base_url = base_url or "http://127.0.0.1:13305/api/v1"

    def list_models(self, show_all=False):
        assert show_all is True, "labels/downloaded are only in the full catalog"
        if self.error is not None:
            raise self.error
        return self.catalog


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
