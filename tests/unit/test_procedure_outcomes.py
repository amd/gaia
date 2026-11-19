# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A learned procedure's track record keeps counting after it is adopted (#2865).

A procedure's ``success_count`` / ``attempt_count`` used to be written once, at
insert, and never again: a procedure that kept failing once recalled still
carried the record it was born with, so nothing could ever retire it.

The outcome that counts is a person's answer to a prompt they were shown, for a
tool the procedure uses. In a turn where a procedure was recalled into context,
a decline for one of its tools makes the turn a failed attempt, approvals with
no decline a successful one. A prompt for another tool, a call approved without
a prompt, and a turn nobody was asked about record nothing. Once a procedure has
a few outcomes since it was learned, a record below the bar it was learned at
disables it, so it is no longer recalled.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.console import (
    SilentConsole,
    unattended_denial_message,
    user_denial_message,
)
from gaia.agents.base.memory_store import MemoryStore
from gaia.agents.base.procedural_memory import ProceduralMemoryMixin
from gaia.agents.base.skill_synthesis import (
    MIN_OUTCOMES_TO_RETIRE,
    MIN_SUCCESS_RATE,
    DistilledProcedure,
    load_synthesis_config,
)

_CONFIG = load_synthesis_config({})
_TOOL = "probe_write"

# ---------------------------------------------------------------------------
# Store: counting an outcome on a procedure row
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield db
    db.close()


def _procedure(
    store, successes: int = 9, attempts: int = 9, sessions: int = 1, **kw
) -> str:
    """A procedure learned from *sessions* runs with these birth tool-call counts."""
    return store.put_skill(
        name=kw.pop("name", "triage-support-ticket"),
        when_to_use=kw.pop("when_to_use", "Triage an inbound support ticket."),
        markdown_body="1. Read the ticket.\n2. Label it.",
        tools_required=kw.pop("tools_required", [_TOOL]),
        success_count=successes,
        attempt_count=attempts,
        provenance=kw.pop(
            "provenance",
            {
                "source": "synthesized",
                "from_sessions": [f"s{i}" for i in range(1, sessions + 1)],
            },
        ),
        **kw,
    )


def _record(store, skill_id):
    (row,) = store.search_skills(
        skill_id=skill_id, enabled_only=False, include_superseded=True
    )
    return row["success_count"], row["attempt_count"]


def _enabled(store, skill_id) -> bool:
    return bool(store.search_skills(skill_id=skill_id))


def _rc_provenance(outcomes: int, sessions: int = 3) -> dict:
    """Provenance as an RC since v0.26.0-rc6 wrote it: outcomes, no approvals."""
    return {
        "source": "synthesized",
        "from_sessions": [f"s{i}" for i in range(1, sessions + 1)],
        "outcomes": outcomes,
    }


class TestRecordSkillOutcome:
    def test_a_success_adds_one_to_both_counters(self, store):
        sid = _procedure(store)
        assert store.record_skill_outcome([sid], success=True) == {
            sid: (10, 10, 1, 1, 1, 0)
        }
        assert _record(store, sid) == (10, 10)

    def test_a_failure_adds_an_attempt_only(self, store):
        sid = _procedure(store)
        assert store.record_skill_outcome([sid], success=False) == {
            sid: (9, 10, 1, 0, 1, 0)
        }
        assert _record(store, sid) == (9, 10)

    def test_the_birth_record_is_kept_and_added_to(self, store):
        sid = _procedure(store, successes=4, attempts=5)
        store.record_skill_outcome([sid], success=True)
        assert store.record_skill_outcome([sid], success=False) == {
            sid: (5, 7, 2, 1, 1, 0)
        }

    def test_the_record_names_the_sessions_it_was_learned_from(self, store):
        sid = _procedure(store, sessions=3)
        record = store.record_skill_outcome([sid], success=False)[sid]
        assert (record.outcomes, record.outcome_successes) == (1, 0)
        assert record.learned_from == 3

    def test_outcomes_since_learning_live_beside_the_provenance(self, store):
        sid = _procedure(store)
        store.record_skill_outcome([sid], success=True)
        (row,) = store.search_skills(skill_id=sid)
        provenance = row["provenance"]
        if isinstance(provenance, str):
            provenance = json.loads(provenance)
        assert provenance == {
            "source": "synthesized",
            "from_sessions": ["s1"],
            "outcomes": 1,
            "outcome_successes": 1,
        }

    def test_an_rc_row_marks_its_earlier_outcomes_unscored(self, store):
        """An RC row counted outcomes but not approvals; those stay unscored."""
        sid = _procedure(store, provenance=_rc_provenance(outcomes=2))
        record = store.record_skill_outcome([sid], success=True)[sid]
        assert (record.outcomes, record.outcome_successes) == (3, 1)
        assert record.unscored_outcomes == 2
        assert record.scored_outcomes == 1
        provenance = store.search_skills(skill_id=sid)[0]["provenance"]
        assert provenance["unscored_outcomes"] == 2
        # Marked once: later outcomes are scored, the unscored count holds.
        record = store.record_skill_outcome([sid], success=False)[sid]
        assert (record.unscored_outcomes, record.scored_outcomes) == (2, 2)

    def test_a_new_row_never_gets_an_unscored_count(self, store):
        sid = _procedure(store)
        store.record_skill_outcome([sid], success=False)
        store.record_skill_outcome([sid], success=True)
        provenance = store.search_skills(skill_id=sid)[0]["provenance"]
        assert "unscored_outcomes" not in provenance

    def test_a_row_without_provenance_starts_counting(self, store):
        sid = _procedure(store, provenance=None)
        assert store.record_skill_outcome([sid], success=False) == {
            sid: (9, 10, 1, 0, 0, 0)
        }

    def test_each_recalled_procedure_is_counted(self, store):
        first = _procedure(store)
        second = _procedure(store, name="label-a-ticket", when_to_use="Label it.")
        counts = store.record_skill_outcome([first, second], success=False)
        assert counts == {first: (9, 10, 1, 0, 1, 0), second: (9, 10, 1, 0, 1, 0)}

    def test_a_procedure_listed_twice_is_counted_once(self, store):
        sid = _procedure(store)
        store.record_skill_outcome([sid, sid], success=True)
        assert _record(store, sid) == (10, 10)

    def test_a_procedure_forgotten_since_recall_is_skipped(self, store):
        sid = _procedure(store)
        assert store.record_skill_outcome(["proc_gone", sid], success=True) == {
            sid: (10, 10, 1, 1, 1, 0)
        }

    def test_nothing_is_written_after_an_erase(self, store):
        sid = _procedure(store)
        before = store.erase_count()
        store.clear_all()
        assert store.record_skill_outcome([sid], success=True, erase_count=before) == {}
        assert _record(store, sid) == (9, 9)

    def test_no_ids_writes_nothing(self, store):
        assert store.record_skill_outcome([], success=True) == {}


class TestDisableSkill:
    def test_a_disabled_procedure_is_not_recalled(self, store):
        sid = _procedure(store)
        assert store.disable_skill(sid) is True
        assert store.search_skills(skill_id=sid) == []
        assert store.search_skills(skill_id=sid, enabled_only=False)

    def test_disabling_an_unknown_id_says_so(self, store):
        assert store.disable_skill("proc_gone") is False


# ---------------------------------------------------------------------------
# The turn: which procedures were in context, and what the person said
# ---------------------------------------------------------------------------


class _Host(ProceduralMemoryMixin):
    def __init__(self, store):
        self._memory_store = store
        self._incognito = False
        self._turn_erase_count = store.erase_count()
        self._turn_person_decisions = []

    def recall(self, *skill_ids, tools=(_TOOL,)):
        skills = [
            DistilledProcedure(
                name=f"p{i}",
                when_to_use="w",
                body="b",
                tools_required=list(tools),
                skill_id=skill_id,
            )
            for i, skill_id in enumerate(skill_ids)
        ]
        self._recall(skills)

    def _recall(self, skills):
        with patch.object(
            _Host, "_recall_skills_for_turn", return_value=(skills, _CONFIG)
        ):
            self._refresh_recalled_skills("goal")

    def turn(self, *answers):
        """One turn: each answer is ``approved`` or ``(tool, approved)``."""
        self._turn_person_decisions = []
        for answer in answers:
            tool, approved = answer if isinstance(answer, tuple) else (_TOOL, answer)
            self._note_person_decision(tool, approved)
        self._record_procedure_outcome()


@pytest.fixture(autouse=True)
def _default_settings():
    with patch("gaia.agents.base.memory._load_memory_settings", return_value={}):
        yield


@pytest.fixture
def host(store):
    return _Host(store)


class TestTurnOutcome:
    def test_a_decline_counts_as_a_failed_attempt(self, host, store):
        sid = _procedure(store)
        host.recall(sid)
        host.turn(False)
        assert _record(store, sid) == (9, 10)

    def test_an_approval_counts_as_a_success(self, host, store):
        sid = _procedure(store)
        host.recall(sid)
        host.turn(True)
        assert _record(store, sid) == (10, 10)

    def test_one_decline_among_approvals_fails_the_turn(self, host, store):
        sid = _procedure(store)
        host.recall(sid)
        host.turn(True, False, True)
        assert _record(store, sid) == (9, 10)

    def test_declining_an_unrelated_tool_leaves_the_procedure_alone(self, host, store):
        sid = _procedure(store)
        host.recall(sid)
        host.turn(("run_shell_command", False))
        assert _record(store, sid) == (9, 9)

    def test_only_its_own_tools_decide_the_outcome(self, host, store):
        sid = _procedure(store)
        host.recall(sid)
        host.turn(("run_shell_command", False), (_TOOL, True))
        assert _record(store, sid) == (10, 10)

    def test_each_procedure_is_judged_on_its_own_tools(self, host, store):
        writes = _procedure(store)
        shells = _procedure(store, name="run-it", when_to_use="Run it.")
        host._recall(
            [
                DistilledProcedure("w", "w", "b", [_TOOL], skill_id=writes),
                DistilledProcedure(
                    "s", "w", "b", ["run_shell_command"], skill_id=shells
                ),
            ]
        )
        host.turn((_TOOL, True), ("run_shell_command", False))
        assert _record(store, writes) == (10, 10)
        assert _record(store, shells) == (9, 10)

    def test_a_procedure_listing_no_tools_is_never_counted(self, host, store):
        sid = _procedure(store, tools_required=[])
        host.recall(sid, tools=())
        host.turn(False)
        assert _record(store, sid) == (9, 9)

    def test_a_turn_nobody_was_asked_about_records_nothing(self, host, store):
        """Tool calls that did not raise are not an outcome."""
        sid = _procedure(store)
        host.recall(sid)
        host.turn()
        assert _record(store, sid) == (9, 9)

    def test_a_turn_without_a_recalled_procedure_records_nothing(self, host, store):
        sid = _procedure(store)
        host.turn(False)
        assert _record(store, sid) == (9, 9)

    def test_a_turn_is_counted_once(self, host, store):
        sid = _procedure(store)
        host.recall(sid)
        host.turn(True)
        host._record_procedure_outcome()
        assert _record(store, sid) == (10, 10)

    def test_a_decision_before_the_turn_started_is_ignored(self, host, store):
        sid = _procedure(store)
        host.recall(sid)
        host._turn_person_decisions = None
        host._note_person_decision(_TOOL, False)
        host._record_procedure_outcome()
        assert _record(store, sid) == (9, 9)

    def test_a_private_turn_records_nothing(self, host, store):
        sid = _procedure(store)
        host.recall(sid)
        host._incognito = True
        host.turn(False)
        assert _record(store, sid) == (9, 9)

    def test_memory_off_records_nothing(self, host):
        host._memory_store = None
        host.recall("proc_x")
        host.turn(False)

    def test_an_erase_during_the_turn_drops_the_record(self, host, store):
        sid = _procedure(store)
        host.recall(sid)
        host._turn_person_decisions = [(_TOOL, True)]
        store.clear_all()
        host._record_procedure_outcome()
        assert _record(store, sid) == (9, 9)


class TestInContext:
    def test_a_body_the_prompt_dropped_is_not_counted(self, host, store):
        shown = _procedure(store)
        dropped = _procedure(store, name="long-one", when_to_use="Long.")
        host._recall(
            [
                DistilledProcedure("short-one", "w", "1. Do it.", [_TOOL], shown),
                DistilledProcedure("long-one", "w", "x" * 5000, [_TOOL], dropped),
            ]
        )
        host.turn(False)
        assert _record(store, shown) == (9, 10)
        assert _record(store, dropped) == (9, 9)

    def test_a_cut_body_still_shown_is_counted(self, host, store):
        sid = _procedure(store)
        host._recall(
            [DistilledProcedure("long-one", "w", "1. a\n" * 2000, [_TOOL], sid)]
        )
        host.turn(False)
        assert _record(store, sid) == (9, 10)


class TestDemotion:
    def test_a_new_procedure_survives_one_decline(self, host, store):
        """3/3 then one decline is 3/4, under the bar, but one outcome is not a record."""
        sid = _procedure(store, successes=3, attempts=3, sessions=3)
        host.recall(sid)
        host.turn(False)
        assert _record(store, sid) == (3, 4)
        assert _enabled(store, sid)

    def test_birth_tool_calls_do_not_outweigh_three_declines(self, host, store):
        """Three runs of five calls each: three declines retire it, as for 3/3."""
        sid = _procedure(store, successes=15, attempts=15, sessions=3)
        host.recall(sid)
        for _ in range(MIN_OUTCOMES_TO_RETIRE):
            host.turn(False)
        assert not _enabled(store, sid)

    def test_runs_judged_per_session_hold_exactly_at_the_bar(self, host, store):
        """3 learned runs + 5 accepted turns: 8/10 is at the bar, 8/11 below."""
        sid = _procedure(store, successes=15, attempts=15, sessions=3)
        host.recall(sid)
        for _ in range(5):
            host.turn(True)
        host.turn(False)
        host.turn(False)
        assert _enabled(store, sid)
        host.turn(False)
        assert not _enabled(store, sid)

    def test_a_new_procedure_declined_three_times_is_disabled(
        self, host, store, caplog
    ):
        sid = _procedure(store, successes=3, attempts=3, sessions=3)
        host.recall(sid)
        with caplog.at_level(logging.WARNING, logger="gaia.agents.base"):
            for _ in range(MIN_OUTCOMES_TO_RETIRE - 1):
                host.turn(False)
                assert _enabled(store, sid)
            host.turn(False)
        assert _record(store, sid) == (3, 6)
        assert not _enabled(store, sid)
        assert any("disabled" in r.getMessage() for r in caplog.records)

    def test_a_proven_procedure_survives_one_decline(self, host, store):
        sid = _procedure(store, successes=9, attempts=9, sessions=9)
        host.recall(sid)
        host.turn(False)
        assert _record(store, sid) == (9, 10)
        assert _enabled(store, sid)

    def test_a_proven_procedure_is_disabled_once_it_falls_below_the_bar(
        self, host, store
    ):
        sid = _procedure(store, successes=9, attempts=9, sessions=9)
        host.recall(sid)
        declines = 0
        while _enabled(store, sid):
            host.turn(False)
            declines += 1
            assert declines < 10, "never disabled"
        successes, attempts = _record(store, sid)
        assert successes / attempts < MIN_SUCCESS_RATE
        # 9/10 and 9/11 still clear the bar it was learned at.
        assert declines == 3

    def test_approvals_after_learning_count_toward_the_floor(self, host, store):
        sid = _procedure(store, successes=3, attempts=3, sessions=3)
        host.recall(sid)
        host.turn(True)
        host.turn(True)
        host.turn(False)  # 5/6, three outcomes, still above the bar
        assert _enabled(store, sid)

    def test_rc_approvals_are_not_read_as_declines(self, host, store):
        """Learned from 3 runs, approved twice on an RC, then one decline: 5/6,
        not 3/6 — the RC approvals have no approval count to read."""
        sid = _procedure(
            store, successes=5, attempts=5, provenance=_rc_provenance(outcomes=2)
        )
        host.recall(sid)
        host.turn(False)
        assert _enabled(store, sid)

    def test_an_rc_row_still_retires_on_its_new_record(self, host, store):
        """The unscored outcomes don't shield it: declines counted since do."""
        sid = _procedure(
            store, successes=5, attempts=5, provenance=_rc_provenance(outcomes=2)
        )
        host.recall(sid)
        for _ in range(MIN_OUTCOMES_TO_RETIRE - 1):
            host.turn(False)
        assert _enabled(store, sid), "judged before its new record was complete"
        host.turn(False)  # 3 runs + 3 scored declines: 3/6
        assert not _enabled(store, sid)

    def test_an_rc_row_with_approvals_since_survives_a_decline(self, host, store):
        sid = _procedure(
            store, successes=5, attempts=5, provenance=_rc_provenance(outcomes=4)
        )
        host.recall(sid)
        host.turn(True)
        host.turn(True)
        host.turn(False)  # 3 runs + 2/3 scored: 5/6
        assert _enabled(store, sid)

    def test_a_success_never_disables(self, host, store):
        sid = _procedure(store, successes=1, attempts=9)
        host.recall(sid)
        for _ in range(MIN_OUTCOMES_TO_RETIRE):
            host.turn(True)
        assert _enabled(store, sid)

    def test_only_the_failing_procedure_is_disabled(self, host, store):
        weak = _procedure(store, successes=8, attempts=10, sessions=3)
        strong = _procedure(
            store,
            successes=40,
            attempts=40,
            sessions=40,
            name="label-it",
            when_to_use="Label.",
        )
        host.recall(weak, strong)
        for _ in range(MIN_OUTCOMES_TO_RETIRE):
            host.turn(False)
        assert not _enabled(store, weak)
        assert _enabled(store, strong)


# ---------------------------------------------------------------------------
# The agent loop reports only a prompt a person answered
# ---------------------------------------------------------------------------


class _ScriptedConsole(SilentConsole):
    """Answers like a console: *prompted* says whether a person was asked."""

    def __init__(self, approve: bool, prompted: bool = True, **denial):
        super().__init__()
        self._approve = approve
        self._prompted = prompted
        self._denial = denial

    def confirm_tool_execution(self, tool_name, tool_args):
        self._answered_prompt = None
        if self._prompted:
            self.note_prompt_answered(tool_name)
        if self._approve:
            return True
        return self.deny_tool_execution(
            tool_name,
            self._denial.get("reason") or user_denial_message(tool_name),
            self._denial.get("timed_out", False),
        )


class _GatedAgent(Agent):
    CONFIRMATION_REQUIRED_TOOLS = frozenset({_TOOL})

    def _get_system_prompt(self):
        return "probe"

    def _register_tools(self):
        from gaia.agents.base.tools import tool

        @tool
        def probe_write(text: str) -> dict:
            """Write text."""
            return {"status": "success", "written": text}

    def _create_console(self):
        return self._console_override

    def _note_person_decision(self, tool_name: str, approved: bool) -> None:
        self.decisions.append((tool_name, approved))


def _agent(console) -> _GatedAgent:
    with patch("gaia.agents.base.agent.AgentSDK"):
        _GatedAgent._console_override = console
        agent = _GatedAgent(silent_mode=True, skip_lemonade=True)
    agent.decisions = []
    return agent


class TestAgentReportsDecisions:
    def test_a_person_declining_is_reported(self):
        agent = _agent(_ScriptedConsole(approve=False))
        result = agent._execute_tool(_TOOL, {"text": "x"})
        assert result["status"] == "denied"
        assert agent.decisions == [(_TOOL, False)]

    def test_a_person_approving_is_reported(self):
        agent = _agent(_ScriptedConsole(approve=True))
        agent._execute_tool(_TOOL, {"text": "x"})
        assert agent.decisions == [(_TOOL, True)]

    def test_an_approval_with_no_prompt_shown_is_not_a_person(self):
        """Auto-approval, accept-edits and standing grants all land here."""
        agent = _agent(_ScriptedConsole(approve=True, prompted=False))
        agent._execute_tool(_TOOL, {"text": "x"})
        assert agent.decisions == []

    def test_a_prompt_nobody_answered_is_not_a_person(self):
        agent = _agent(_ScriptedConsole(approve=False, prompted=False, timed_out=True))
        agent._execute_tool(_TOOL, {"text": "x"})
        assert agent.decisions == []

    def test_an_unattended_refusal_is_not_a_person(self):
        console = _ScriptedConsole(
            approve=False,
            prompted=False,
            reason=unattended_denial_message(_TOOL),
        )
        agent = _agent(console)
        agent._execute_tool(_TOOL, {"text": "x"})
        assert agent.decisions == []

    def test_an_answer_for_another_tool_is_not_this_calls(self):
        console = _ScriptedConsole(approve=True, prompted=False)
        console.note_prompt_answered("run_shell_command")
        agent = _agent(console)
        agent._execute_tool(_TOOL, {"text": "x"})
        assert agent.decisions == []


class TestConsolesReportAnsweredPrompts:
    def test_an_answer_is_taken_once(self):
        console = SilentConsole()
        console.note_prompt_answered(_TOOL)
        assert console.take_prompt_answered(_TOOL) is True
        assert console.take_prompt_answered(_TOOL) is False

    def test_a_terminal_grant_shows_no_prompt(self, monkeypatch):
        from gaia.agents.base.console import AgentConsole

        console = AgentConsole()
        monkeypatch.setattr(console, "call_is_granted", lambda *_a: True)
        assert console.confirm_tool_execution(_TOOL, {"text": "x"}) is True
        assert console.take_prompt_answered(_TOOL) is False

    def test_a_terminal_yes_is_an_answered_prompt(self, monkeypatch):
        from gaia.agents.base import console as console_mod

        console = console_mod.AgentConsole()
        monkeypatch.setattr(console_mod, "terminal_is_interactive", lambda: True)
        monkeypatch.setattr(console, "_render_confirmation_request", lambda *_a: None)
        monkeypatch.setattr(console, "_read_confirmation_answer", lambda *_a: "y")
        assert console.confirm_tool_execution(_TOOL, {"text": "x"}) is True
        assert console.take_prompt_answered(_TOOL) is True

    def test_agent_ui_accept_edits_shows_no_prompt(self, tmp_path):
        from gaia.ui.sse_handler import SSEOutputHandler

        handler = SSEOutputHandler()
        with patch.object(handler, "edit_is_auto_accepted", return_value=True):
            assert handler.confirm_tool_execution("write_file", {}) is True
        assert handler.take_prompt_answered("write_file") is False

    def test_agent_ui_standing_grant_shows_no_prompt(self):
        from gaia.ui.sse_handler import SSEOutputHandler

        handler = SSEOutputHandler()
        with patch.object(handler, "call_is_granted", return_value=True):
            assert handler.confirm_tool_execution(_TOOL, {}) is True
        assert handler.take_prompt_answered(_TOOL) is False
