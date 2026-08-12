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
