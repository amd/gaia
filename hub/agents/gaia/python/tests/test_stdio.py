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
