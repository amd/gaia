# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""#2571 — the agent must never narrate its own calendar-conflict verdict.

Asked to list events and flag conflicts, the agent used to call only
``list_calendar_events`` and then state a conflict conclusion it never
computed (and got backwards — two events overlapping by 30 minutes were
reported as "back-to-back and do not conflict"). ``detect_calendar_conflicts``
itself was always correct; the tool simply never ran.

Four layers, each deterministic (no LLM, no live calendar):

1. System-prompt guidance mandating the conflict tool for conflict
   questions — substring pins against the real ``_SYSTEM_PROMPT`` constant.
2. Tool-docstring guidance on both ``list_calendar_events`` and
   ``detect_calendar_conflicts`` — pins against the real ``_TOOL_REGISTRY``
   descriptions (the schema actually sent to the model).
3. ``response_has_ungrounded_conflict_claim`` / ``append_conflict_grounding_correction``
   — the pure, deterministic grounding guard.
4. ``EmailTriageAgent.process_query`` wiring — the guard actually runs on a
   turn's result.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.agent import (  # noqa: E402
    _SYSTEM_PROMPT,
    _tool_names_from_conversation,
)
from gaia_agent_email.tools.calendar_tools import (  # noqa: E402
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
            )
            is True
        )

    def test_does_not_fire_when_the_conflict_tool_ran(self):
        assert (
            response_has_ungrounded_conflict_claim(
                "These two events are back-to-back and do not conflict.",
                ["list_calendar_events", "detect_calendar_conflicts"],
            )
            is False
        )

    def test_does_not_fire_without_conflict_language(self):
        assert (
            response_has_ungrounded_conflict_claim(
                "Here are your upcoming events.",
                ["list_calendar_events"],
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
            )
            is False
        )

    def test_matches_overlap_and_double_booked_phrasing_too(self):
        assert (
            response_has_ungrounded_conflict_claim(
                "You are double-booked this morning.",
                ["list_calendar_events"],
            )
            is True
        )
        assert (
            response_has_ungrounded_conflict_claim(
                "Your 9am and 9:30am events overlap.",
                ["list_calendar_events"],
            )
            is True
        )

    def test_empty_response_never_fires(self):
        assert (
            response_has_ungrounded_conflict_claim("", ["list_calendar_events"])
            is False
        )

    def test_no_tools_at_all_never_fires(self):
        assert response_has_ungrounded_conflict_claim("conflict!", []) is False


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
# _tool_names_from_conversation — the trace extractor the guard reads
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


# ---------------------------------------------------------------------------
# 4. process_query wiring — the guard actually runs on a turn's result
# ---------------------------------------------------------------------------


class TestProcessQueryConflictGrounding:
    def test_appends_correction_when_conflict_claimed_without_the_tool(
        self, agent, monkeypatch
    ):
        from gaia.agents.base.agent import Agent

        # This is the issue's exact repro text and trace: one tool
        # (list_calendar_events), a self-narrated (and wrong) conflict
        # verdict.
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
            ],
        }
        monkeypatch.setattr(Agent, "process_query", lambda self, *a, **k: dict(canned))

        result = agent.process_query("List my calendar events.")

        assert result["result"] == canned["result"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
