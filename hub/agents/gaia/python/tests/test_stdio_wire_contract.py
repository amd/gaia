# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Pin ``gaia_agent.stdio`` against the shared TUI wire fixture.

The Go TUI keeps its own copies of these literals, and its end-to-end tests
drive a Go mock agent, so a rename here would leave every Go test green while
the real TUI's permission answers, cancels and /clear silently stopped landing.
The Go side checks the same fixture (``tui/internal/client/wire_contract_test.go``).
"""

import io
import json
import queue
import sys
from pathlib import Path

import pytest
from gaia_agent import stdio

from gaia.ui.sse_translation import TERMINAL_TYPES, CanonicalTranslator

FIXTURE = (
    Path(__file__).resolve().parents[5]
    / "tests"
    / "fixtures"
    / "stdio"
    / "gaia_stdio_wire.json"
)
WIRE = json.loads(FIXTURE.read_text(encoding="utf-8"))
STDIN = WIRE["stdin"]
EVENTS = WIRE["events"]


class _RecordingState(stdio.PermissionState):
    """Records what each control verb reached, instead of acting on it."""

    def __init__(self):
        super().__init__()
        self.calls = []

    def set_bypass(self, enabled):
        self.calls.append(("bypass", enabled))

    def cancel_active(self, reason="stdin closed mid-turn"):
        # The pump also cancels on EOF; only the host's verb counts here.
        if reason != "stdin closed mid-turn":
            self.calls.append(("cancel", reason))
        return True

    def resolve(self, decision, confirm_id):
        self.calls.append(("tool_decision", decision, confirm_id))


def _pump(monkeypatch, lines):
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join(lines) + "\n"))
    queries = queue.Queue()
    state = _RecordingState()
    stdio._pump_stdin(queries, state)
    drained = []
    while not queries.empty():
        drained.append(queries.get_nowait())
    return drained, state


def test_envelope_keys_match():
    assert stdio.CONTROL_KEY == STDIN["control_key"]
    assert stdio.QUERY_KEY == STDIN["query_key"]


def test_every_control_verb_the_agent_declares_is_in_the_fixture():
    declared = {
        value
        for name, value in vars(stdio).items()
        if name.startswith("CONTROL_") and name != "CONTROL_KEY"
    }
    assert declared == set(STDIN["control_verbs"])


def test_decision_values_match():
    declared = {
        value for name, value in vars(stdio).items() if name.startswith("DECISION_")
    }
    assert declared == set(STDIN["decisions"])


def test_each_control_verb_is_handled_by_the_stdin_pump(monkeypatch):
    """Validity of the call: every verb the fixture lists reaches its handler."""
    lines = [
        json.dumps({STDIN["control_key"]: "tool_decision", "decision": "allow"}),
        json.dumps({STDIN["control_key"]: "bypass", "enabled": True}),
        json.dumps({STDIN["control_key"]: "cancel"}),
        json.dumps({STDIN["control_key"]: "clear_history"}),
    ]
    assert {json.loads(line)[STDIN["control_key"]] for line in lines} == set(
        STDIN["control_verbs"]
    )

    drained, state = _pump(monkeypatch, lines)

    assert state.calls == [
        ("tool_decision", "allow", None),
        ("bypass", True),
        ("cancel", "host asked to cancel"),
    ]
    assert isinstance(drained[0], stdio._ClearHistory)
    assert drained[1:] == [None]


@pytest.mark.parametrize("decision", STDIN["decisions"])
def test_each_decision_arrives_unchanged_with_its_confirm_id(monkeypatch, decision):
    """Each decision must survive as sent; an unknown one is downgraded to deny."""
    fields = STDIN["control_verbs"]["tool_decision"]["fields"]
    message = {STDIN["control_key"]: "tool_decision"}
    message.update(dict(zip(fields, [decision, "confirm-7"])))

    _, state = _pump(monkeypatch, [json.dumps(message)])

    assert state.calls == [("tool_decision", decision, "confirm-7")]


def test_bypass_reads_its_declared_field(monkeypatch):
    (field,) = STDIN["control_verbs"]["bypass"]["fields"]
    _, state = _pump(
        monkeypatch, [json.dumps({STDIN["control_key"]: "bypass", field: True})]
    )

    assert state.calls == [("bypass", True)]


def test_a_wrapped_query_is_unwrapped(monkeypatch):
    drained, _ = _pump(monkeypatch, [json.dumps({STDIN["query_key"]: "one\ntwo"})])

    assert drained == ["one\ntwo", None]


def test_query_sentinels_match():
    sentinels = STDIN["query_sentinels"]
    assert stdio.CLEAR_CONVERSATION_QUERY == sentinels["clear_conversation"]["query"]
    assert stdio.MEMORY_DUMP_QUERY == sentinels["memory_dump"]["query"]


def test_clear_conversation_is_acknowledged_with_the_answer_the_tui_waits_for():
    class _Agent:
        conversation_history = [{"role": "user", "content": "earlier"}]

    agent = _Agent()
    out = io.StringIO()

    stdio.dispatch_query(
        agent, STDIN["query_sentinels"]["clear_conversation"]["query"], out
    )

    assert agent.conversation_history == []
    assert json.loads(out.getvalue()) == {
        "type": "final",
        "answer": STDIN["query_sentinels"]["clear_conversation"]["ack_answer"],
    }


def test_terminal_types_are_canonical_event_types():
    assert TERMINAL_TYPES <= set(EVENTS)


def _translated_events():
    """Every canonical event type, built the way run_turn builds its translator."""
    translator = CanonicalTranslator(run_id=None, agent_id=stdio.AGENT_ID, debug=False)
    source = [
        {"type": "status", "message": "Searching files"},
        {"type": "chunk", "content": "partial "},
        {"type": "tool_start", "tool": "read_file"},
        {"type": "tool_args", "tool": "read_file", "args": {"path": "a.txt"}},
        {"type": "tool_result", "summary": "read 3 lines", "success": True},
        {
            "type": "permission_request",
            "tool": "run_shell_command",
            "args": {"command": "gh issue list"},
            "confirm_id": "c1",
            "always_scope": "gh issue list",
        },
        {
            "type": "user_input_request",
            "message": "Which repo?",
            "request_id": "r1",
            "options": [{"value": "a", "label": "A"}],
            "timeout_seconds": 30,
        },
    ]
    events = []
    for event in source:
        events.extend(translator.translate(event))
    finals = CanonicalTranslator(
        run_id=None, agent_id=stdio.AGENT_ID, debug=False
    ).translate({"type": "answer", "content": "done", "steps": 2})
    errors = CanonicalTranslator(
        run_id=None, agent_id=stdio.AGENT_ID, debug=False
    ).translate({"type": "agent_error", "content": "boom"})
    return events + finals + errors


def test_every_field_the_tui_reads_is_emitted():
    """A field the Go structs decode but the agent stops sending renders blank."""
    emitted = {}
    for event in _translated_events():
        emitted.setdefault(event["type"], set()).update(event)

    assert set(emitted) == set(EVENTS), "an event type has no emitter"
    for etype, spec in EVENTS.items():
        expected = set(spec["fields"]) - set(spec.get("not_sent_over_stdio", []))
        missing = expected - emitted[etype]
        assert not missing, f"{etype} no longer carries {sorted(missing)}"


def test_status_extension_fields_match_the_model_state_ping(monkeypatch):
    class _Lemonade:
        def __init__(self, **_kwargs):
            self.base_url = "http://127.0.0.1:13305/api/v1"

        def health_check(self):
            return {"version": "10.8.0"}

    class _Config:
        use_claude = False
        base_url = None

    class _Chat:
        config = _Config()
        effective_model = "Gemma-4-E4B-it-GGUF"

    class _Agent:
        chat = _Chat()

    monkeypatch.setattr(stdio, "LemonadeClient", _Lemonade)

    ping = stdio._model_state_event(_Agent())

    assert ping["type"] == "status"
    assert set(ping) - {"type"} == set(EVENTS["status"]["fields"]) | set(
        EVENTS["status"]["stdio_extension"]
    )
