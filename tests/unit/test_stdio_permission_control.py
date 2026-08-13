# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The flagship stdio transport's permission control channel.

The defect these pin: before the control channel existed the agent could ask
"may I run this?" and the answer had nowhere to travel, so every gated tool
auto-denied after a timeout and the user never got a way to say yes.
"""

import io
import json
import threading
import time

import pytest
from gaia_agent.stdio import (
    PermissionState,
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

    def test_always_grants_only_the_tool_it_was_given_for(self):
        """The grant is per tool name — no wider, and no narrower."""
        state = PermissionState()
        drive(state, ["always"], agent=GatedAgent(tool="run_shell_command"))

        other = drive(state, ["deny"], agent=GatedAgent(tool="write_file"))
        assert events_of(other, "needs_confirmation"), "a different tool must ask"

        # Same tool, different arguments: still covered, because the backend
        # grant is by name. The UI must promise exactly this and no less.
        same = drive(
            state,
            [],
            agent=GatedAgent(tool="run_shell_command", args={"command": "rm -rf /"}),
        )
        assert not events_of(same, "needs_confirmation")


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

        state = PermissionState()
        handler = SSEOutputHandler()
        state.attach(handler)
        handler.approve_tool_for_session("write_file")
        state.detach(handler)

        nxt = SSEOutputHandler()
        state.attach(nxt)
        assert nxt.tool_approved_for_session("write_file")

    def test_attach_hands_over_an_unbounded_wait(self):
        """A modal on screen must not expire under the person reading it."""
        from gaia.ui.sse_handler import SSEOutputHandler

        handler = SSEOutputHandler()
        assert handler.confirm_timeout_seconds is not None
        PermissionState().attach(handler)
        assert handler.confirm_timeout_seconds is None
