# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The flagship stdio transport's permission control channel.

The defect these pin: before the control channel existed the agent could ask
"may I run this?" and the answer had nowhere to travel, so every gated tool
auto-denied after a timeout and the user never got a way to say yes.
"""

import io
import json
import queue
import sys
import threading
import time

import pytest

# The stdio transport ships with the standalone gaia-agent wheel; skip when the
# core-only test job hasn't installed it.
pytest.importorskip("gaia_agent")

from gaia_agent.stdio import (  # noqa: E402
    PermissionState,
    _pump_stdin,
    apply_control,
    parse_control,
    run_turn,
)


class GatedAgent:
    """Calls one confirmation-gated tool and reports what the console decided."""

    def __init__(self, tool="run_shell_command", args=None):
        self.console = None
        self.tool = tool
        self.args = {"command": "pwd"} if args is None else args

    def process_query(self, _query):
        allowed = self.console.confirm_tool_execution(self.tool, self.args)
        return {"answer": f"decision={allowed}"}


def drive(state, decisions, agent=None, timeout=10.0):
    """Run one turn, feeding *decisions* once the prompt is actually on the wire."""
    agent = agent or GatedAgent()
    out = io.StringIO()

    def respond():
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if "needs_confirmation" in out.getvalue():
                break
            time.sleep(0.01)
        for decision in decisions:
            apply_control(
                {"gaia_control": "tool_decision", "decision": decision}, state
            )

    responder = threading.Thread(target=respond)
    responder.start()
    try:
        run_turn(agent, "go", out, state=state)
    finally:
        responder.join(timeout=timeout)
    return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def events_of(events, kind):
    return [e for e in events if e.get("type") == kind]


def final_answer(events):
    return events_of(events, "final")[-1]["answer"]


class TestControlLinesAreNotQueries:
    """A question that happens to look like JSON must still be a question."""

    @pytest.mark.parametrize(
        "line",
        [
            "what does pwd do",
            '{"foo": 1}',
            '{"type": "tool_decision"}',
            "not json {",
            '["gaia_control"]',
        ],
    )
    def test_plain_lines_stay_queries(self, line):
        assert parse_control(line) is None

    def test_control_key_is_the_discriminator(self):
        parsed = parse_control('{"gaia_control":"bypass","enabled":true}')
        assert parsed is not None and parsed["enabled"] is True


class TestYesNoAlways:
    def test_allow_runs_the_tool_once(self):
        events = drive(PermissionState(), ["allow"])
        assert "decision=True" in final_answer(events)

    def test_deny_refuses_the_tool(self):
        events = drive(PermissionState(), ["deny"])
        assert "decision=False" in final_answer(events)

    def test_the_prompt_names_the_actual_invocation(self):
        """A prompt that hides the payload trains people to blind-approve."""
        events = drive(PermissionState(), ["deny"])
        summary = events_of(events, "needs_confirmation")[0]["summary"]
        assert 'command="pwd"' in summary, summary

    def test_always_suppresses_the_next_prompt_for_that_tool(self):
        state = PermissionState()
        first = drive(state, ["always"])
        assert events_of(first, "needs_confirmation"), "the first call must ask"
        assert "decision=True" in final_answer(first)

        # Nobody answers the second turn. If it prompted, it would still be
        # waiting — an "always" that does not stick is the defect this catches.
        second = drive(state, [])
        assert not events_of(second, "needs_confirmation")
        assert "decision=True" in final_answer(second)

    def test_always_does_not_leak_to_another_command(self):
        """The whole point of the narrow scope, end to end.

        Approving `gh issue list` for the session must not silently approve
        `rm -rf build` — the prompt described one command, so the grant covers
        one command.
        """
        state = PermissionState()
        drive(
            state,
            ["always"],
            agent=GatedAgent(args={"command": "gh issue list"}),
        )

        # The same command again: covered, no prompt.
        again = drive(state, [], agent=GatedAgent(args={"command": "gh issue list"}))
        assert not events_of(again, "needs_confirmation")
        assert "decision=True" in final_answer(again)

        # A different command on the SAME tool: must ask.
        other = drive(
            state, ["deny"], agent=GatedAgent(args={"command": "rm -rf build"})
        )
        assert events_of(other, "needs_confirmation"), "a different command must ask"
        assert "decision=False" in final_answer(other)

    def test_always_does_not_leak_to_another_tool(self):
        state = PermissionState()
        drive(state, ["always"], agent=GatedAgent(args={"command": "gh issue list"}))

        other = drive(
            state,
            ["deny"],
            agent=GatedAgent(tool="write_file", args={"file_path": "/tmp/x"}),
        )
        assert events_of(other, "needs_confirmation"), "a different tool must ask"

    def test_the_prompt_advertises_what_always_would_grant(self):
        events = drive(PermissionState(), ["deny"])
        prompt = events_of(events, "needs_confirmation")[0]
        assert prompt.get("always_scope") == "pwd", prompt

    def test_a_call_that_cannot_be_scoped_offers_no_always(self):
        """A bare shell invocation bounds nothing, so nothing may be granted."""
        events = drive(
            PermissionState(),
            ["deny"],
            agent=GatedAgent(args={"command": "bash -c 'rm -rf /'"}),
        )
        prompt = events_of(events, "needs_confirmation")[0]
        assert "always_scope" not in prompt, prompt

    def test_always_on_an_unscopable_call_does_not_grant_anything(self):
        """Answering "always" anyway approves this call only — never a grant."""
        state = PermissionState()
        first = drive(
            state,
            ["always"],
            agent=GatedAgent(args={"command": "bash -c whoami"}),
        )
        assert "decision=True" in final_answer(first), "the one call is approved"

        second = drive(
            state,
            ["deny"],
            agent=GatedAgent(args={"command": "bash -c whoami"}),
        )
        assert events_of(second, "needs_confirmation"), "it must ask again"
        assert "decision=False" in final_answer(second)


class TestBypassMode:
    def test_off_by_default(self):
        assert PermissionState().bypass is False

    def test_on_runs_gated_tools_without_asking(self):
        state = PermissionState()
        state.set_bypass(True)
        events = drive(state, [])
        assert not events_of(events, "needs_confirmation")
        assert "decision=True" in final_answer(events)

    def test_on_still_says_what_it_ran(self):
        """Silent autonomy is the thing being avoided, not the goal."""
        state = PermissionState()
        state.set_bypass(True)
        events = drive(state, [])
        warnings = [
            e for e in events_of(events, "status") if "Bypass" in str(e.get("message"))
        ]
        assert warnings and "run_shell_command" in warnings[0]["message"]

    def test_off_restores_prompting_immediately(self):
        state = PermissionState(bypass=True)
        state.set_bypass(False)
        events = drive(state, ["deny"])
        assert events_of(events, "needs_confirmation")
        assert "decision=False" in final_answer(events)

    def test_launch_flag_is_honoured(self):
        assert PermissionState(bypass=True).bypass is True


class TestFailClosed:
    def test_an_unreadable_decision_denies(self):
        events = drive(PermissionState(), ["maybe"])
        assert "decision=False" in final_answer(events)

    def test_a_decision_for_a_stale_prompt_is_dropped(self):
        """Approving what you read, not what arrived while you were reading."""
        state = PermissionState()
        out = io.StringIO()

        def respond():
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                if "needs_confirmation" in out.getvalue():
                    break
                time.sleep(0.01)
            apply_control(
                {
                    "gaia_control": "tool_decision",
                    "decision": "allow",
                    "confirm_id": "belongs-to-a-prompt-that-is-gone",
                },
                state,
            )
            time.sleep(0.3)
            apply_control({"gaia_control": "tool_decision", "decision": "deny"}, state)

        responder = threading.Thread(target=respond)
        responder.start()
        try:
            run_turn(GatedAgent(), "go", out, state=state)
        finally:
            responder.join(timeout=10.0)

        events = [
            json.loads(line) for line in out.getvalue().splitlines() if line.strip()
        ]
        assert "decision=False" in final_answer(events)

    def test_a_decision_with_no_turn_running_is_ignored(self):
        # Must not raise: a late keystroke after a turn ended is ordinary.
        apply_control(
            {"gaia_control": "tool_decision", "decision": "allow"}, PermissionState()
        )

    def test_an_unknown_verb_is_ignored(self):
        state = PermissionState()
        apply_control({"gaia_control": "reboot_the_planet"}, state)
        assert state.bypass is False

    def test_a_turn_with_no_control_channel_cannot_be_approved(self, monkeypatch):
        """No state means no responder, so the gate must not open by default."""
        from gaia.ui.sse_handler import SSEOutputHandler

        monkeypatch.setattr(SSEOutputHandler, "confirm_timeout_seconds", 0.5)
        out = io.StringIO()
        run_turn(GatedAgent(), "go", out)
        events = [
            json.loads(line) for line in out.getvalue().splitlines() if line.strip()
        ]
        assert "decision=False" in final_answer(events)

    def test_a_bounded_wait_that_expires_denies(self, monkeypatch):
        """Whatever else changes, expiry never approves."""
        from gaia.ui.sse_handler import SSEOutputHandler

        monkeypatch.setattr(SSEOutputHandler, "confirm_timeout_seconds", 0.5)
        handler = SSEOutputHandler()
        assert handler.confirm_tool_execution("write_file", {"path": "/tmp/x"}) is False
        assert "timed out" in handler.confirmation_denied_reason("write_file")


class TestGrantsSurviveTheTurnBoundary:
    def test_detach_carries_grants_back_into_the_session(self):
        from gaia.ui.sse_handler import SSEOutputHandler

        call = ("write_file", {"file_path": "/tmp/x"})

        state = PermissionState()
        handler = SSEOutputHandler()
        state.attach(handler)
        handler.grant_call_for_session(*call)
        state.detach(handler)

        nxt = SSEOutputHandler()
        state.attach(nxt)
        assert nxt.call_is_granted(*call)
        # And it is still only that call.
        assert not nxt.call_is_granted("write_file", {"file_path": "/tmp/other"})

    def test_attach_hands_over_an_unbounded_wait(self):
        """A modal on screen must not expire under the person reading it."""
        from gaia.ui.sse_handler import SSEOutputHandler

        handler = SSEOutputHandler()
        assert handler.confirm_timeout_seconds is not None
        PermissionState().attach(handler)
        assert handler.confirm_timeout_seconds is None


class TestStdinClosingEndsAParkedTurn:
    """A confirmation waits for a person, so nothing else bounds it.

    That makes stdin closing the only other way the wait can end. The sentinel
    the pump queues on EOF sits BEHIND the running turn, so on its own it never
    reaches a turn parked on a prompt: the child outlived its parent, kept the
    model slot, and only a kill ended it.
    """

    @staticmethod
    def _park_a_turn(state, out, agent):
        turn = threading.Thread(
            target=run_turn,
            args=(agent, "go", out),
            kwargs={"state": state},
            daemon=True,
        )
        turn.start()
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if "needs_confirmation" in out.getvalue():
                return turn
            time.sleep(0.01)
        raise AssertionError("the turn never reached the confirmation")

    def test_eof_ends_a_turn_parked_on_a_confirmation(self, monkeypatch):
        state = PermissionState()
        out = io.StringIO()
        turn = self._park_a_turn(state, out, GatedAgent())

        monkeypatch.setattr(sys, "stdin", io.StringIO(""))  # the host went away
        queries = queue.Queue()
        _pump_stdin(queries, state)

        turn.join(timeout=15.0)
        assert not turn.is_alive(), "the turn never ended — the process is immortal"

        events = [
            json.loads(line) for line in out.getvalue().splitlines() if line.strip()
        ]
        terminals = [e for e in events if e.get("type") in ("final", "error")]
        assert len(terminals) == 1, "the turn must still end with ONE terminal event"
        assert "decision=False" in final_answer(events), "an abandoned prompt is a deny"
        assert queries.get_nowait() is None, "the run-loop sentinel is still queued"

    def test_eof_with_no_turn_running_is_ordinary(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        queries = queue.Queue()

        _pump_stdin(queries, PermissionState())  # must not raise

        assert queries.get_nowait() is None

    def test_cancelling_reports_whether_there_was_a_turn(self):
        from gaia.ui.sse_handler import SSEOutputHandler

        state = PermissionState()
        assert state.cancel_active() is False

        handler = SSEOutputHandler()
        state.attach(handler)
        assert state.cancel_active() is True
        assert handler.cancelled.is_set()


class TestTheHandoffIsAtomic:
    """A decision must reach the handler that is live when it is applied.

    The race has no black-box seam — it needs the turn thread to detach and the
    next to attach between two statements — so the invariant is asserted
    directly instead: the lock is still held when the handler is used. With it
    dropped first, a decision carrying no ``confirm_id`` (the TUI omits it when
    empty) is accepted by a handler nobody is waiting on while the live prompt
    keeps waiting, and the "no turn is running" warning never fires either.
    """

    class _Handler:
        auto_approve_gated_tools = False
        confirm_timeout_seconds = 60.0

        def __init__(self):
            self.cancelled = threading.Event()
            self.calls = []

        def session_grants(self):
            return set()

        def resolve_tool_confirmation(self, **kwargs):
            self.calls.append(kwargs)
            return True

    def test_the_lock_is_held_while_the_handler_is_used(self):
        state = PermissionState()
        handler = self._Handler()
        state.attach(handler)
        held = []
        original = handler.resolve_tool_confirmation

        def _record(**kwargs):
            held.append(state._lock.locked())
            return original(**kwargs)

        handler.resolve_tool_confirmation = _record

        state.resolve("allow", None)

        assert held == [True], "the handler was used after the lock was dropped"
        assert handler.calls == [
            {"approved": True, "always": False, "confirm_id": None}
        ]

    def test_a_detached_handler_never_sees_the_decision(self):
        state = PermissionState()
        handler = self._Handler()
        state.attach(handler)
        state.detach(handler)

        state.resolve("allow", None)

        assert handler.calls == []
