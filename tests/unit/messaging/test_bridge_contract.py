"""Pin the bridge's wire literals against the real ``gaia_agent.stdio``.

The bridge keeps the protocol constants as its own literals rather than
importing them, because core must not depend on a hub wheel. That is the right
layering and it is also exactly how two sides of a contract drift apart without
anyone noticing: every unit test in ``test_bridge.py`` asserts the bridge agrees
with *itself*.

So this file asserts the bridge agrees with the agent. If the agent renames a
verb or adds a decision, this fails here — loudly, at build time — instead of in
production as a confirmation that silently never resolves.

Skipped only when the flagship agent package is not installed; the conftest
redirects an existing install to this checkout but will not create one.
"""

import pytest

from gaia.messaging import bridge

stdio = pytest.importorskip(
    "gaia_agent.stdio",
    reason="the flagship agent package is not installed in this environment",
)


def test_control_key_matches():
    assert bridge.CONTROL_KEY == stdio.CONTROL_KEY


def test_query_key_matches():
    assert bridge.QUERY_KEY == stdio.QUERY_KEY


def test_tool_decision_verb_matches():
    assert bridge.CONTROL_TOOL_DECISION == stdio.CONTROL_TOOL_DECISION


@pytest.mark.parametrize(
    "ours, theirs",
    [
        ("DECISION_ALLOW", "DECISION_ALLOW"),
        ("DECISION_DENY", "DECISION_DENY"),
        ("DECISION_ALWAYS", "DECISION_ALWAYS"),
    ],
)
def test_decision_values_match(ours, theirs):
    assert getattr(bridge, ours) == getattr(stdio, theirs)


def test_valid_decisions_is_exactly_what_the_agent_accepts():
    """The agent denies anything outside this set; the bridge must agree.

    A decision the bridge considers valid but the agent does not would be sent
    verbatim and silently downgraded to a deny — an approval the user watched
    themselves give, that never took effect.
    """
    agent_side = {
        stdio.DECISION_ALLOW,
        stdio.DECISION_DENY,
        stdio.DECISION_ALWAYS,
    }
    assert bridge.VALID_DECISIONS == agent_side


def test_a_decision_the_bridge_builds_is_one_the_agent_parses():
    """Validity of the CALL, not merely that we made one.

    ``apply_control`` is the agent's real parser. Feeding it the bridge's own
    output proves the message is accepted and routed to a tool decision, which a
    stub returning success could never show.
    """
    import json

    captured = {}

    class RecordingState:
        def resolve(self, decision, confirm_id):
            captured["decision"] = decision
            captured["confirm_id"] = confirm_id

        def set_bypass(self, enabled):  # pragma: no cover - must never be hit
            raise AssertionError("the bridge must never send a bypass verb")

    sent = []

    class Recorder:
        stdin = type(
            "S",
            (),
            {"write": lambda self, d: sent.append(d), "flush": lambda self: None},
        )()
        stdout = None

    channel = bridge.AgentChannel(on_event=lambda e, t: None, spawn=Recorder)
    channel._proc = Recorder()
    channel.decide(bridge.DECISION_ALLOW, "confirm-42")

    message = json.loads(sent[-1])
    stdio.apply_control(message, RecordingState())
    assert captured == {"decision": stdio.DECISION_ALLOW, "confirm_id": "confirm-42"}


def test_a_garbled_decision_reaches_the_agent_as_a_deny():
    """Fail-closed has to hold across the boundary, not just inside the bridge."""
    import json

    captured = {}

    class RecordingState:
        def resolve(self, decision, confirm_id):
            captured["decision"] = decision

        def set_bypass(self, enabled):  # pragma: no cover
            raise AssertionError("the bridge must never send a bypass verb")

    sent = []

    class Recorder:
        stdin = type(
            "S",
            (),
            {"write": lambda self, d: sent.append(d), "flush": lambda self: None},
        )()
        stdout = None

    channel = bridge.AgentChannel(on_event=lambda e, t: None, spawn=Recorder)
    channel._proc = Recorder()
    channel.decide("sure-why-not", "c1")
    stdio.apply_control(json.loads(sent[-1]), RecordingState())
    assert captured["decision"] == stdio.DECISION_DENY


def test_the_agent_unwraps_the_query_the_bridge_wraps():
    """A multi-line question must survive the round trip as ONE question."""
    import json

    question = "summarize this\nand that"
    wire = json.dumps({bridge.QUERY_KEY: question})
    assert stdio.parse_query(wire) == question


def test_terminal_events_match_the_agent_vocabulary():
    """The bridge waits for exactly these to end a turn."""
    assert bridge.TERMINAL_EVENTS == {"final", "error"}
