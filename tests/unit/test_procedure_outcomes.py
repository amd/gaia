# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A learned procedure's track record keeps counting after it is adopted (#2865).

A procedure's ``success_count`` / ``attempt_count`` used to be written once, at
insert, and never again: a procedure that kept failing once recalled still
carried the record it was born with, so nothing could ever retire it.

The outcome that counts is a person's answer. In a turn where a procedure was
recalled into context, every confirmation prompt a person answers is evidence:
a decline makes the turn a failed attempt, approvals with no decline make it a
successful one. A turn nobody was asked about records nothing, and neither do
tool calls that merely did not raise. A procedure whose record drops below the
bar it was learned at is disabled, so it is no longer recalled.
"""

from __future__ import annotations

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
    MIN_SUCCESS_RATE,
    DistilledProcedure,
    load_synthesis_config,
)

_CONFIG = load_synthesis_config({})

# ---------------------------------------------------------------------------
# Store: counting an outcome on a procedure row
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield db
    db.close()


def _procedure(store, successes: int = 9, attempts: int = 9, **kw) -> str:
    return store.put_skill(
        name=kw.pop("name", "triage-support-ticket"),
        when_to_use=kw.pop("when_to_use", "Triage an inbound support ticket."),
        markdown_body="1. Read the ticket.\n2. Label it.",
        success_count=successes,
        attempt_count=attempts,
        **kw,
    )


def _record(store, skill_id):
    (row,) = store.search_skills(
        skill_id=skill_id, enabled_only=False, include_superseded=True
    )
    return row["success_count"], row["attempt_count"]


class TestRecordSkillOutcome:
    def test_a_success_adds_one_to_both_counters(self, store):
        sid = _procedure(store)
        assert store.record_skill_outcome([sid], success=True) == {sid: (10, 10)}
        assert _record(store, sid) == (10, 10)

    def test_a_failure_adds_an_attempt_only(self, store):
        sid = _procedure(store)
        assert store.record_skill_outcome([sid], success=False) == {sid: (9, 10)}
        assert _record(store, sid) == (9, 10)

    def test_the_birth_record_is_kept_and_added_to(self, store):
        sid = _procedure(store, successes=4, attempts=5)
        store.record_skill_outcome([sid], success=True)
        store.record_skill_outcome([sid], success=False)
        assert _record(store, sid) == (5, 7)

    def test_each_recalled_procedure_is_counted(self, store):
        first = _procedure(store)
        second = _procedure(store, name="label-a-ticket", when_to_use="Label it.")
        counts = store.record_skill_outcome([first, second], success=False)
        assert counts == {first: (9, 10), second: (9, 10)}

    def test_a_procedure_listed_twice_is_counted_once(self, store):
        sid = _procedure(store)
        store.record_skill_outcome([sid, sid], success=True)
        assert _record(store, sid) == (10, 10)

    def test_a_procedure_forgotten_since_recall_is_skipped(self, store):
        sid = _procedure(store)
        assert store.record_skill_outcome(["proc_gone", sid], success=True) == {
            sid: (10, 10)
        }

    def test_nothing_is_written_after_an_erase(self, store):
        sid = _procedure(store)
        before = store.erase_count()
        store.clear_all()
        _procedure(store)
        assert store.record_skill_outcome([sid], success=True, erase_count=before) == {}

    def test_no_ids_writes_nothing(self, store):
        assert store.record_skill_outcome([], success=True) == {}


class TestDisableSkill:
    def test_a_disabled_procedure_is_not_recalled(self, store):
        sid = _procedure(store)
        assert store.disable_skill(sid) is True
        assert store.search_skills(skill_id=sid) == []
        (row,) = store.search_skills(skill_id=sid, enabled_only=False)
        assert row["enabled"] is False or row["enabled"] == 0

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

    def recall(self, *skill_ids):
        skills = [
            DistilledProcedure(
                name=f"p{i}", when_to_use="w", body="b", skill_id=skill_id
            )
            for i, skill_id in enumerate(skill_ids)
        ]
        with patch.object(
            _Host, "_recall_skills_for_turn", return_value=(skills, _CONFIG)
        ):
            self._refresh_recalled_skills("goal")


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
        host._note_person_decision(False)
        host._record_procedure_outcome()
        assert _record(store, sid) == (9, 10)

    def test_an_approval_counts_as_a_success(self, host, store):
        sid = _procedure(store)
        host.recall(sid)
        host._note_person_decision(True)
        host._record_procedure_outcome()
        assert _record(store, sid) == (10, 10)

    def test_one_decline_among_approvals_fails_the_turn(self, host, store):
        sid = _procedure(store)
        host.recall(sid)
        for approved in (True, False, True):
            host._note_person_decision(approved)
        host._record_procedure_outcome()
        assert _record(store, sid) == (9, 10)

    def test_a_turn_nobody_was_asked_about_records_nothing(self, host, store):
        """Tool calls that did not raise are not an outcome."""
        sid = _procedure(store)
        host.recall(sid)
        host._record_procedure_outcome()
        assert _record(store, sid) == (9, 9)

    def test_a_turn_without_a_recalled_procedure_records_nothing(self, host, store):
        sid = _procedure(store)
        host._note_person_decision(False)
        host._record_procedure_outcome()
        assert _record(store, sid) == (9, 9)

    def test_a_turn_is_counted_once(self, host, store):
        sid = _procedure(store)
        host.recall(sid)
        host._note_person_decision(True)
        host._record_procedure_outcome()
        host._record_procedure_outcome()
        assert _record(store, sid) == (10, 10)

    def test_a_decision_before_the_turn_started_is_ignored(self, host, store):
        sid = _procedure(store)
        host.recall(sid)
        host._turn_person_decisions = None
        host._note_person_decision(False)
        host._record_procedure_outcome()
        assert _record(store, sid) == (9, 9)

    def test_a_private_turn_records_nothing(self, host, store):
        sid = _procedure(store)
        host.recall(sid)
        host._incognito = True
        host._note_person_decision(False)
        host._record_procedure_outcome()
        assert _record(store, sid) == (9, 9)

    def test_memory_off_records_nothing(self, host):
        host._memory_store = None
        host.recall("proc_x")
        host._note_person_decision(False)
        host._record_procedure_outcome()

    def test_an_erase_during_the_turn_drops_the_record(self, host, store):
        sid = _procedure(store)
        host.recall(sid)
        host._note_person_decision(True)
        store.clear_all()
        host._record_procedure_outcome()
        assert _record(store, sid) == (9, 9)


class TestInContext:
    def test_a_body_the_prompt_dropped_is_not_counted(self, host, store):
        shown = _procedure(store)
        dropped = _procedure(store, name="long-one", when_to_use="Long.")
        skills = [
            DistilledProcedure("short-one", "w", "1. Do it.", skill_id=shown),
            DistilledProcedure("long-one", "w", "x" * 5000, skill_id=dropped),
        ]
        with patch.object(
            _Host, "_recall_skills_for_turn", return_value=(skills, _CONFIG)
        ):
            host._refresh_recalled_skills("goal")
        host._note_person_decision(False)
        host._record_procedure_outcome()
        assert _record(store, shown) == (9, 10)
        assert _record(store, dropped) == (9, 9)

    def test_a_cut_body_still_shown_is_counted(self, host, store):
        sid = _procedure(store)
        skills = [DistilledProcedure("long-one", "w", "1. a\n" * 2000, skill_id=sid)]
        with patch.object(
            _Host, "_recall_skills_for_turn", return_value=(skills, _CONFIG)
        ):
            host._refresh_recalled_skills("goal")
        host._note_person_decision(False)
        host._record_procedure_outcome()
        assert _record(store, sid) == (9, 10)


class TestDemotion:
    def test_repeated_declines_disable_the_procedure(self, host, store, caplog):
        sid = _procedure(store, successes=9, attempts=9)
        declines = 0
        with caplog.at_level(logging.WARNING, logger="gaia.agents.base"):
            while store.search_skills(skill_id=sid):
                host.recall(sid)
                host._turn_person_decisions = []
                host._note_person_decision(False)
                host._record_procedure_outcome()
                declines += 1
                assert declines < 10, "never disabled"
        successes, attempts = _record(store, sid)
        assert successes / attempts < MIN_SUCCESS_RATE
        # 9/10 and 9/11 still clear the bar it was learned at.
        assert declines == 3
        assert any("disabled" in r.getMessage() for r in caplog.records)

    def test_a_procedure_still_clearing_the_bar_stays(self, host, store):
        sid = _procedure(store, successes=9, attempts=9)
        host.recall(sid)
        host._note_person_decision(False)
        host._record_procedure_outcome()
        assert store.search_skills(skill_id=sid)

    def test_a_success_never_disables(self, host, store):
        sid = _procedure(store, successes=1, attempts=9)
        host.recall(sid)
        host._note_person_decision(True)
        host._record_procedure_outcome()
        assert store.search_skills(skill_id=sid)

    def test_only_the_failing_procedure_is_disabled(self, host, store):
        weak = _procedure(store, successes=8, attempts=10)
        strong = _procedure(
            store, successes=40, attempts=40, name="label-it", when_to_use="Label."
        )
        host.recall(weak, strong)
        host._note_person_decision(False)
        host._record_procedure_outcome()
        assert store.search_skills(skill_id=weak) == []
        assert store.search_skills(skill_id=strong)


# ---------------------------------------------------------------------------
# The agent loop reports only a person's answer
# ---------------------------------------------------------------------------


class _ScriptedConsole(SilentConsole):
    def __init__(self, approve: bool, reason=None, timed_out=False, auto=False):
        super().__init__()
        self._approve = approve
        self._reason = reason
        self._timed_out = timed_out
        self._auto = auto

    def confirm_tool_execution(self, tool_name, tool_args):
        if self._approve:
            return True
        return self.deny_tool_execution(
            tool_name, self._reason or user_denial_message(tool_name), self._timed_out
        )

    def auto_approve_confirmations_enabled(self) -> bool:
        return self._auto


class _GatedAgent(Agent):
    CONFIRMATION_REQUIRED_TOOLS = frozenset({"probe_write"})

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

    def _note_person_decision(self, approved: bool) -> None:
        self.decisions.append(approved)


def _agent(console) -> _GatedAgent:
    with patch("gaia.agents.base.agent.AgentSDK"):
        _GatedAgent._console_override = console
        agent = _GatedAgent(silent_mode=True, skip_lemonade=True)
    agent.decisions = []
    return agent


class TestAgentReportsDecisions:
    def test_a_person_declining_is_reported(self):
        agent = _agent(_ScriptedConsole(approve=False))
        result = agent._execute_tool("probe_write", {"text": "x"})
        assert result["status"] == "denied"
        assert agent.decisions == [False]

    def test_a_person_approving_is_reported(self):
        agent = _agent(_ScriptedConsole(approve=True))
        agent._execute_tool("probe_write", {"text": "x"})
        assert agent.decisions == [True]

    def test_an_auto_approval_is_not_a_person(self):
        agent = _agent(_ScriptedConsole(approve=True, auto=True))
        agent._execute_tool("probe_write", {"text": "x"})
        assert agent.decisions == []

    def test_a_prompt_nobody_answered_is_not_a_person(self):
        agent = _agent(_ScriptedConsole(approve=False, timed_out=True))
        agent._execute_tool("probe_write", {"text": "x"})
        assert agent.decisions == []

    def test_an_unattended_refusal_is_not_a_person(self):
        console = _ScriptedConsole(
            approve=False, reason=unattended_denial_message("probe_write")
        )
        agent = _agent(console)
        agent._execute_tool("probe_write", {"text": "x"})
        assert agent.decisions == []

    def test_an_ungated_tool_is_not_a_decision(self):
        agent = _agent(_ScriptedConsole(approve=False))
        agent.CONFIRMATION_REQUIRED_TOOLS = frozenset()
        type(agent).CONFIRMATION_REQUIRED_TOOLS = frozenset()
        try:
            agent._execute_tool("probe_write", {"text": "x"})
        finally:
            type(agent).CONFIRMATION_REQUIRED_TOOLS = frozenset({"probe_write"})
        assert agent.decisions == []
