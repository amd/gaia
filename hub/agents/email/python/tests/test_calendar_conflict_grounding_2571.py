# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""#2571 — the agent must never narrate its own calendar-conflict verdict.

Asked to list events and flag conflicts, the agent used to call only
``list_calendar_events`` and then state a conflict conclusion it never
computed (and got backwards — two events overlapping by 30 minutes were
reported as "back-to-back and do not conflict"). ``detect_calendar_conflicts``
itself was always correct; the tool simply never ran.

Five layers, each deterministic (no LLM, no live calendar):

1. System-prompt guidance mandating the conflict tool for conflict
   questions — substring pins against the real ``_SYSTEM_PROMPT`` constant.
2. Tool-docstring guidance on both ``list_calendar_events`` and
   ``detect_calendar_conflicts`` — pins against the real ``_TOOL_REGISTRY``
   descriptions (the schema actually sent to the model).
3. ``response_has_ungrounded_conflict_claim`` / ``append_conflict_grounding_correction``
   — the pure, deterministic grounding guard. Two ways in: (a)
   ``list_calendar_events`` ran with >=2 events listed (below that, no
   conflict is even possible, so "no conflicts" is trivially true without
   the tool — see the "sparse calendar" cases), or (b) NEITHER calendar
   tool ran at all, but the response still cites >=2 specific times
   alongside conflict language — a claim with zero tool grounding, the
   emptier and more dangerous cousin of (a) — see the "no calendar tool
   called at all" cases.
4. ``_tool_names_from_conversation`` / ``_listed_event_count_from_conversation``
   — the trace/result extractors the guard reads.
5. ``EmailTriageAgent.process_query`` wiring — the guard actually runs on a
   turn's result.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.agent import (  # noqa: E402
    _SYSTEM_PROMPT,
    _tool_names_from_conversation,
)
from gaia_agent_email.tools.calendar_tools import (  # noqa: E402
    _listed_event_count_from_conversation,
    append_conflict_grounding_correction,
    response_has_ungrounded_conflict_claim,
)

# ---------------------------------------------------------------------------
# 1. System-prompt guidance
# ---------------------------------------------------------------------------


def test_system_prompt_has_a_calendar_conflicts_section():
    assert "CALENDAR CONFLICTS:" in _SYSTEM_PROMPT


def test_system_prompt_mandates_the_conflict_tool_for_conflict_questions():
    assert "MUST be answered by calling ``detect_calendar_conflicts``" in _SYSTEM_PROMPT


def test_system_prompt_tells_the_model_listing_is_not_conflict_detection():
    assert "does NOT determine whether they overlap" in _SYSTEM_PROMPT


def test_system_prompt_forbids_self_derived_overlap_verdicts():
    assert "never assert a conflict judgement" in _SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# 2. Tool-docstring guidance (the schema actually sent to the model)
# ---------------------------------------------------------------------------


class _RecordingMailBackend:
    """GmailBackend-protocol fake that answers every call with ``{}``."""

    def __getattr__(self, name):
        def _record(*args, **kwargs):
            return {}

        return _record


class _MinimalCalendarBackend:
    """Satisfies the CalendarBackend protocol just enough to construct."""


def _build_agent(tmp_path, monkeypatch):
    from gaia_agent_email.agent import EmailTriageAgent
    from gaia_agent_email.config import EmailAgentConfig

    cfg = EmailAgentConfig(
        gmail_backend=_RecordingMailBackend(),
        calendar_backend=_MinimalCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        start_scheduler=False,
    )
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
        mock_sdk.return_value = MagicMock()
        return EmailTriageAgent(config=cfg)


@pytest.fixture
def agent(tmp_path, monkeypatch):
    a = _build_agent(tmp_path, monkeypatch)
    try:
        yield a
    finally:
        a.close_db()


def test_list_calendar_events_docstring_disclaims_conflict_determination(agent):
    from gaia.agents.base.tools import _TOOL_REGISTRY

    desc = _TOOL_REGISTRY["list_calendar_events"]["description"]
    assert "does NOT determine whether they conflict" in desc
    assert "detect_calendar_conflicts" in desc


def test_detect_calendar_conflicts_docstring_mandates_calling_it(agent):
    from gaia.agents.base.tools import _TOOL_REGISTRY

    desc = _TOOL_REGISTRY["detect_calendar_conflicts"]["description"]
    assert "Call this whenever the user asks about conflicts" in desc


# ---------------------------------------------------------------------------
# 3. The pure, deterministic grounding guard
# ---------------------------------------------------------------------------


class TestResponseHasUngroundedConflictClaim:
    def test_fires_on_conflict_language_without_the_conflict_tool(self):
        assert (
            response_has_ungrounded_conflict_claim(
                "These two events are back-to-back and do not conflict.",
                ["list_calendar_events"],
                2,
            )
            is True
        )

    def test_does_not_fire_when_the_conflict_tool_ran(self):
        assert (
            response_has_ungrounded_conflict_claim(
                "These two events are back-to-back and do not conflict.",
                ["list_calendar_events", "detect_calendar_conflicts"],
                2,
            )
            is False
        )

    def test_does_not_fire_without_conflict_language(self):
        assert (
            response_has_ungrounded_conflict_claim(
                "Here are your upcoming events.",
                ["list_calendar_events"],
                2,
            )
            is False
        )

    def test_does_not_fire_when_the_calendar_was_never_listed(self):
        # Conflict-shaped word on a turn that never touched the calendar —
        # almost certainly means something else entirely (e.g. a
        # preferences clash), not a calendar-conflict verdict.
        assert (
            response_has_ungrounded_conflict_claim(
                "That preference conflicts with an existing rule.",
                ["get_preferences"],
                0,
            )
            is False
        )

    def test_matches_overlap_and_double_booked_phrasing_too(self):
        assert (
            response_has_ungrounded_conflict_claim(
                "You are double-booked this morning.",
                ["list_calendar_events"],
                2,
            )
            is True
        )
        assert (
            response_has_ungrounded_conflict_claim(
                "Your 9am and 9:30am events overlap.",
                ["list_calendar_events"],
                2,
            )
            is True
        )

    def test_empty_response_never_fires(self):
        assert (
            response_has_ungrounded_conflict_claim("", ["list_calendar_events"], 2)
            is False
        )

    def test_no_tools_at_all_never_fires(self):
        assert response_has_ungrounded_conflict_claim("conflict!", [], 0) is False

    # -- sparse calendar: 0 or 1 events means no conflict is even possible --
    # A real mailbox's calendar may be empty (or hold a single event), in
    # which case "no conflicts" is trivially, arithmetically true without
    # ever calling the tool — that is not the ungrounded-narration bug this
    # guard targets, and must not be flagged.

    def test_does_not_fire_with_zero_events_listed(self):
        assert (
            response_has_ungrounded_conflict_claim(
                "You have no events today, so there's nothing to conflict.",
                ["list_calendar_events"],
                0,
            )
            is False
        )

    def test_does_not_fire_with_exactly_one_event_listed(self):
        assert (
            response_has_ungrounded_conflict_claim(
                "You have one event today; it doesn't conflict with anything.",
                ["list_calendar_events"],
                1,
            )
            is False
        )

    def test_fires_with_exactly_two_events_listed(self):
        # The boundary where a conflict first becomes possible — and the
        # issue's own repro (2 planted events).
        assert (
            response_has_ungrounded_conflict_claim(
                "These two events do not conflict.",
                ["list_calendar_events"],
                2,
            )
            is True
        )

    def test_fires_with_more_than_two_events_listed(self):
        assert (
            response_has_ungrounded_conflict_claim(
                "None of your events conflict with each other.",
                ["list_calendar_events"],
                5,
            )
            is True
        )

    # -- no calendar tool called at all: the emptier, more dangerous case --
    # A conflict verdict citing specific times with NEITHER calendar tool in
    # the trace has nothing at all to cross-check against (dispatcher
    # follow-up on #2571, same failure shape as #2621's "confident claim,
    # empty tool trace"). ``listed_event_count`` is necessarily 0 here since
    # nothing was ever listed.

    def test_fires_with_no_calendar_tool_called_but_two_times_cited(self):
        assert (
            response_has_ungrounded_conflict_claim(
                "Your 7:00 AM and 7:30 AM meetings conflict.",
                ["search_messages"],
                0,
            )
            is True
        )

    def test_fires_with_no_tools_called_at_all_but_two_times_cited(self):
        assert (
            response_has_ungrounded_conflict_claim(
                "Your 7:00 AM and 7:30 AM meetings conflict.",
                [],
                0,
            )
            is True
        )

    def test_does_not_fire_with_no_calendar_tool_and_only_one_time_cited(self):
        # One concrete time isn't enough to describe two conflicting events.
        assert (
            response_has_ungrounded_conflict_claim(
                "Your 7:00 AM meeting doesn't conflict with anything.",
                ["search_messages"],
                0,
            )
            is False
        )

    def test_does_not_fire_with_no_calendar_tool_and_no_times_cited(self):
        # Conflict-shaped word, no calendar tool, no times cited at all —
        # this is not a calendar-conflict claim in the first place (e.g. a
        # preferences clash), matching
        # ``test_does_not_fire_when_the_calendar_was_never_listed`` above.
        assert (
            response_has_ungrounded_conflict_claim(
                "That preference conflicts with an existing rule.",
                ["get_preferences"],
                0,
            )
            is False
        )


class TestAppendConflictGroundingCorrection:
    def test_appends_without_deleting_the_original_text(self):
        original = "Here are your events: A, B. They do not conflict."
        corrected = append_conflict_grounding_correction(original)
        assert corrected.startswith(original)
        assert corrected != original
        assert "detect_calendar_conflicts" in corrected

    def test_handles_empty_input(self):
        corrected = append_conflict_grounding_correction("")
        assert "detect_calendar_conflicts" in corrected


# ---------------------------------------------------------------------------
# 4. Trace/result extractors the guard reads
# ---------------------------------------------------------------------------


class TestToolNamesFromConversation:
    def test_extracts_tool_names_in_call_order(self):
        conversation = [
            {"role": "user", "content": "list events and flag conflicts"},
            {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "list_calendar_events"}}],
            },
            {"role": "tool", "content": "{}"},
            {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "detect_calendar_conflicts"}}],
            },
        ]
        assert _tool_names_from_conversation(conversation) == [
            "list_calendar_events",
            "detect_calendar_conflicts",
        ]

    def test_empty_conversation_yields_no_tools(self):
        assert _tool_names_from_conversation([]) == []

    def test_ignores_assistant_messages_without_tool_calls(self):
        conversation = [{"role": "assistant", "content": "final answer"}]
        assert _tool_names_from_conversation(conversation) == []


def _list_events_tool_result(*event_summaries: str) -> dict:
    """A ``{"role": "tool", ...}`` conversation entry shaped like a real
    ``list_calendar_events`` result — the exact envelope
    ``_envelope_ok({"events": [...]})`` produces."""
    events = [{"id": s, "summary": s} for s in event_summaries]
    return {
        "role": "tool",
        "name": "list_calendar_events",
        "content": json.dumps({"ok": True, "data": {"events": events}}),
    }


class TestListedEventCountFromConversation:
    def test_zero_events_returns_zero(self):
        conversation = [_list_events_tool_result()]
        assert _listed_event_count_from_conversation(conversation) == 0

    def test_one_event_returns_one(self):
        conversation = [_list_events_tool_result("Budget sync")]
        assert _listed_event_count_from_conversation(conversation) == 1

    def test_multiple_events_returns_the_count(self):
        conversation = [_list_events_tool_result("Budget sync", "Design review", "1:1")]
        assert _listed_event_count_from_conversation(conversation) == 3

    def test_tool_never_called_returns_zero(self):
        conversation = [{"role": "assistant", "content": "no tools this turn"}]
        assert _listed_event_count_from_conversation(conversation) == 0

    def test_takes_the_max_across_multiple_calls(self):
        conversation = [
            _list_events_tool_result("A"),
            _list_events_tool_result("A", "B", "C"),
        ]
        assert _listed_event_count_from_conversation(conversation) == 3

    def test_error_envelope_is_skipped_not_crashed(self):
        conversation = [
            {
                "role": "tool",
                "name": "list_calendar_events",
                "content": json.dumps({"ok": False, "error": "backend unreachable"}),
            }
        ]
        assert _listed_event_count_from_conversation(conversation) == 0

    def test_malformed_json_is_skipped_not_crashed(self):
        conversation = [
            {"role": "tool", "name": "list_calendar_events", "content": "not json"}
        ]
        assert _listed_event_count_from_conversation(conversation) == 0

    def test_ignores_other_tools_results(self):
        conversation = [
            {
                "role": "tool",
                "name": "detect_calendar_conflicts",
                "content": json.dumps(
                    {"ok": True, "data": {"has_conflict": True, "conflicts": []}}
                ),
            }
        ]
        assert _listed_event_count_from_conversation(conversation) == 0


# ---------------------------------------------------------------------------
# 5. process_query wiring — the guard actually runs on a turn's result
# ---------------------------------------------------------------------------


class TestProcessQueryConflictGrounding:
    def test_appends_correction_when_conflict_claimed_without_the_tool(
        self, agent, monkeypatch
    ):
        from gaia.agents.base.agent import Agent

        # This is the issue's exact repro text and trace: one tool
        # (list_calendar_events, returning 2 overlapping events), a
        # self-narrated (and wrong) conflict verdict.
        canned = {
            "status": "success",
            "result": (
                "Here are your upcoming calendar events:\n\n"
                " • 7:00 AM - 8:00 AM: GAIA-M59-FIXTURE Budget sync\n"
                " • 7:30 AM - 8:30 AM: GAIA-M59-FIXTURE Overlapping "
                "design review\n\n"
                "It looks like these two events are scheduled back-to-back "
                "and do not conflict with each other."
            ),
            "conversation": [
                {
                    "role": "assistant",
                    "tool_calls": [{"function": {"name": "list_calendar_events"}}],
                },
                _list_events_tool_result(
                    "GAIA-M59-FIXTURE Budget sync",
                    "GAIA-M59-FIXTURE Overlapping design review",
                ),
            ],
        }
        monkeypatch.setattr(Agent, "process_query", lambda self, *a, **k: dict(canned))

        result = agent.process_query(
            "List my calendar events. Use your default window."
        )

        assert result["result"].startswith(canned["result"])
        assert "detect_calendar_conflicts" in result["result"]
        assert result["result"] != canned["result"]

    def test_leaves_response_untouched_when_the_conflict_tool_ran(
        self, agent, monkeypatch
    ):
        from gaia.agents.base.agent import Agent

        canned = {
            "status": "success",
            "result": "These two events overlap by 30 minutes.",
            "conversation": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"function": {"name": "list_calendar_events"}},
                        {"function": {"name": "detect_calendar_conflicts"}},
                    ],
                },
                _list_events_tool_result("Budget sync", "Design review"),
            ],
        }
        monkeypatch.setattr(Agent, "process_query", lambda self, *a, **k: dict(canned))

        result = agent.process_query("Do my morning events conflict?")

        assert result["result"] == canned["result"]

    def test_leaves_response_untouched_when_no_conflict_language_present(
        self, agent, monkeypatch
    ):
        from gaia.agents.base.agent import Agent

        canned = {
            "status": "success",
            "result": "You have 2 events this morning: Budget sync, Design review.",
            "conversation": [
                {
                    "role": "assistant",
                    "tool_calls": [{"function": {"name": "list_calendar_events"}}],
                },
                _list_events_tool_result("Budget sync", "Design review"),
            ],
        }
        monkeypatch.setattr(Agent, "process_query", lambda self, *a, **k: dict(canned))

        result = agent.process_query("List my calendar events.")

        assert result["result"] == canned["result"]

    def test_leaves_response_untouched_on_an_empty_calendar(self, agent, monkeypatch):
        """The real mailbox's calendar may be empty (#2571 dispatcher
        follow-up): with zero events listed, "no conflicts" is trivially
        true and must NOT be rewritten as if it were an ungrounded guess."""
        from gaia.agents.base.agent import Agent

        canned = {
            "status": "success",
            "result": "You have no upcoming events, so there are no conflicts.",
            "conversation": [
                {
                    "role": "assistant",
                    "tool_calls": [{"function": {"name": "list_calendar_events"}}],
                },
                _list_events_tool_result(),  # zero events
            ],
        }
        monkeypatch.setattr(Agent, "process_query", lambda self, *a, **k: dict(canned))

        result = agent.process_query("List my calendar events and flag conflicts.")

        assert result["result"] == canned["result"]

    def test_leaves_response_untouched_with_a_single_event(self, agent, monkeypatch):
        """Same as the empty-calendar case: one event can't conflict with
        itself, so no correction is warranted."""
        from gaia.agents.base.agent import Agent

        canned = {
            "status": "success",
            "result": "You have one event today; it doesn't conflict with anything.",
            "conversation": [
                {
                    "role": "assistant",
                    "tool_calls": [{"function": {"name": "list_calendar_events"}}],
                },
                _list_events_tool_result("Budget sync"),
            ],
        }
        monkeypatch.setattr(Agent, "process_query", lambda self, *a, **k: dict(canned))

        result = agent.process_query("List my calendar events and flag conflicts.")

        assert result["result"] == canned["result"]

    def test_appends_correction_when_conflict_claimed_with_zero_tool_calls(
        self, agent, monkeypatch
    ):
        """Dispatcher follow-up: a conflict verdict with NO calendar tool
        call at all (matching #2621's "confident claim, empty tool trace"
        shape) has nothing to cross-check against — the most dangerous
        version of this bug, and must still be caught."""
        from gaia.agents.base.agent import Agent

        canned = {
            "status": "success",
            "result": "Your 7:00 AM and 7:30 AM meetings conflict with each other.",
            "conversation": [],  # no tool_calls at all this turn
        }
        monkeypatch.setattr(Agent, "process_query", lambda self, *a, **k: dict(canned))

        result = agent.process_query("Do my morning meetings conflict?")

        assert result["result"].startswith(canned["result"])
        assert "detect_calendar_conflicts" in result["result"]
        assert result["result"] != canned["result"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
