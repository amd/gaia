# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for ``gaia_agent_email.answer_grounding`` — the deterministic
post-checks the agent runs on its own final answer text before returning it.

Covers related honesty defects, all guarded by the same mechanism:

- a mutation narrated as done with no matching tool call this turn
- a "no urgent/actionable" claim contradicted by the same turn's own scan
- "unread across your mailboxes" overclaiming a per-INBOX-scoped number
- internal render/envelope scaffolding (markers, field-name labels, raw
  provider ids, undecoded unicode escapes) echoed into user-facing prose
- a calendar conflict/overlap verdict narrated without ``detect_calendar_
  conflicts`` actually running this turn (#2571 — folded in from what used
  to be a standalone ``process_query`` hook)
- a "nothing needs you" claim contradicted by the cached attention card
  the TUI already rendered this process (#2636)

Every pure function is tested directly, with no agent construction and no
LLM/network access. ``TestProcessQueryWiring`` additionally proves the
mechanism is actually wired into ``EmailTriageAgent.process_query``, not
just correct in isolation. ``TestGroundFinalAnswerComposition`` proves the
calendar and attention-card checks — the two APPEND-only guards — compose
sequentially rather than one short-circuiting the other. ``TestPromptWording``
locks the system prompt's own claims to what the code actually does, so the
two cannot drift apart silently.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# parents[0]=tests/ [1]=python/ [2]=email/ [3]=agents/ [4]=hub/ [5]=repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email import attention_cache  # noqa: E402
from gaia_agent_email.agent import EmailTriageAgent, _SYSTEM_PROMPT  # noqa: E402
from gaia_agent_email.answer_grounding import (  # noqa: E402
    UNGROUNDED_SUCCESS_FALLBACK,
    decode_stray_unicode_escapes,
    find_attention_card_contradiction,
    find_fabricated_attendee_claim,
    find_scaffolding_leak,
    find_ungrounded_calendar_conflict_claim,
    find_ungrounded_invite_claim,
    find_ungrounded_success_claim,
    find_unlicensed_cross_mailbox_claim,
    find_unqualified_negative_claim,
    ground_final_answer,
    strip_scaffolding_leaks,
    tools_called_this_turn,
)
from gaia_agent_email.config import EmailAgentConfig  # noqa: E402

from gaia.database.mixin import DatabaseMixin  # noqa: E402
from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures — a conversation trace shaped like the real agent loop's, without
# constructing a live agent or calling an LLM.
# ---------------------------------------------------------------------------


def _tool_entry(name: str, data: dict) -> dict:
    """A role=tool conversation entry in the native tool-calling wire shape
    ``_create_tool_message`` produces: ``content`` is a list of text blocks
    holding the ``{"ok": true, "data": ...}`` envelope as a JSON string."""
    envelope = json.dumps({"ok": True, "data": data})
    return {
        "role": "tool",
        "name": name,
        "content": [{"type": "text", "text": envelope}],
    }


def _list_events_tool_entry(event_count: int) -> dict:
    """A ``role: tool`` entry for ``list_calendar_events`` shaped exactly as
    ``calendar_tools._listed_event_count_from_conversation`` reads it: plain
    JSON-string ``content`` (NOT the list-wrapped native-tool-calling block
    ``_tool_entry`` above produces) holding ``{"ok": true, "data": {"events":
    [...]}}``.
    """
    events = [{"id": str(i), "summary": f"event {i}"} for i in range(event_count)]
    return {
        "role": "tool",
        "name": "list_calendar_events",
        "content": json.dumps({"ok": True, "data": {"events": events}}),
    }


def _events_tool_entry(tool_name: str, events: list, *, key: str = "events") -> dict:
    """A ``role: tool`` entry for ``list_calendar_events`` or
    ``detect_calendar_conflicts`` carrying explicit event dicts (so a test
    can control ``attendees`` directly) — same plain-JSON-string content
    shape ``_list_events_tool_entry`` and ``_listed_event_count_from_
    conversation`` read, generalized past a bare count.
    """
    return {
        "role": "tool",
        "name": tool_name,
        "content": json.dumps({"ok": True, "data": {key: events}}),
    }


def _event(*, attendees=None, **overrides) -> dict:
    base = {
        "id": "evt1",
        "summary": "Design review",
        "start": "2026-08-06T09:00:00-04:00",
        "end": "2026-08-06T10:00:00-04:00",
        "attendees": attendees if attendees is not None else [],
    }
    base.update(overrides)
    return base


def _detect_conflicts_tool_entry() -> dict:
    """A minimal ``role: tool`` entry recording that
    ``detect_calendar_conflicts`` ran this turn — ``tools_called_this_turn``
    only reads ``name``, so the content shape doesn't matter here."""
    return {
        "role": "tool",
        "name": "detect_calendar_conflicts",
        "content": json.dumps({"ok": True, "data": {"has_conflict": False}}),
    }


def _prescan_envelope(**overrides) -> dict:
    base = {
        "kind": "email_pre_scan",
        "urgent": [],
        "actionable": [],
        "needs_review": [],
        "scanned": 100,
        "total_unread": 100,
        "degraded": False,
    }
    base.update(overrides)
    return base


def _attention_item(kind: str, **overrides) -> dict:
    base = {
        "kind": kind,
        "message_id": "m1",
        "thread_id": "t1",
        "sender": "colleague@example.com",
        "subject": "subject",
        "why": "why",
    }
    base.update(overrides)
    return base


def _cached_attention_view(*, items: list, scanned: int = 100) -> dict:
    """A record shaped like ``attention_cache.peek()``'s return value --
    i.e. what ``GET /v1/email/attention`` last computed (minus
    ``_computed_at``, which ``_store_attention_view`` stamps separately)."""
    return {
        "kind": "email_attention",
        "items": items,
        "coverage": {
            "scanned": scanned,
            "total_unread": None,
            "scan_truncated": False,
            "degraded": False,
        },
        "generated_at": "2026-01-01T00:00:00+00:00",
    }


def _store_attention_view(
    *, items: list, scanned: int = 100, age_seconds: float = 0.0
) -> None:
    """Populate ``attention_cache`` as if ``GET /v1/email/attention`` had
    computed this view ``age_seconds`` ago. Threads ``computed_at`` through
    explicitly -- ``attention_cache.store`` stamps "now" by default, which
    would silently discard a deliberately-aged fixture otherwise."""
    view = _cached_attention_view(items=items, scanned=scanned)
    attention_cache.store(view, computed_at=time.time() - age_seconds)


@pytest.fixture(autouse=True)
def _clean_attention_cache():
    """The attention cache is a process-global (#2636) -- reset it around
    every test in this module so one test's cached view can never leak into
    another's assertions, regardless of run order."""
    attention_cache.reset()
    yield
    attention_cache.reset()


# ---------------------------------------------------------------------------
# tools_called_this_turn / last_tool_payload — shared parsing helpers
# ---------------------------------------------------------------------------


class TestSharedHelpers:
    def test_tools_called_this_turn_empty_conversation(self):
        assert tools_called_this_turn([]) == []
        assert tools_called_this_turn(None) == []

    def test_tools_called_this_turn_lists_every_tool_role_entry(self):
        convo = [
            {"role": "user", "content": "archive it"},
            {"role": "assistant", "content": None, "tool_calls": []},
            _tool_entry("archive_message", {"archived": True}),
        ]
        assert tools_called_this_turn(convo) == ["archive_message"]

    def test_ignores_non_tool_roles(self):
        convo = [{"role": "assistant", "content": "sure, archiving now"}]
        assert tools_called_this_turn(convo) == []


# ---------------------------------------------------------------------------
# find_ungrounded_success_claim — the empty-tool-trace guard
# ---------------------------------------------------------------------------


class TestFindUngroundedSuccessClaim:
    @pytest.mark.parametrize(
        "phrase",
        [
            "The message has been archived.",
            "I've starred that message for you.",
            "Done — marked read.",
            "It has been moved to Trash.",
            "Your message is now starred.",
            "I have successfully archived all 3 messages.",
            "Both have been marked read.",
            "I have trashed that email.",
        ],
    )
    def test_detects_completion_claim_with_empty_tool_trace(self, phrase):
        assert find_ungrounded_success_claim(phrase, []) == find_ungrounded_success_claim(
            phrase, None
        )
        assert find_ungrounded_success_claim(phrase, []) is not None

    @pytest.mark.parametrize(
        "phrase",
        [
            "Would you like me to archive it?",
            "Once archived, a message leaves your inbox.",
            "I can mark messages as read if you'd like.",
            "Archiving removes it from view.",
            "No messages needed to be archived.",
            "Here's your inbox pre-scan — 5 actionable, 1 suggested archive.",
            "Let me know if you'd like me to star anything.",
        ],
    )
    def test_no_false_positive_on_non_completion_language(self, phrase):
        assert find_ungrounded_success_claim(phrase, []) is None

    def test_grounded_when_a_tool_actually_ran_this_turn(self):
        convo = [_tool_entry("archive_message", {"archived": True})]
        assert (
            find_ungrounded_success_claim("The message has been archived.", convo)
            is None
        )

    def test_grounded_even_when_the_tool_called_is_unrelated(self):
        # AC2's literal wording is "tool trace is empty" -- any tool call at
        # all clears it, since the model has no other side-effect channel.
        convo = [_tool_entry("list_inbox", {"messages": []})]
        assert (
            find_ungrounded_success_claim("The message has been archived.", convo)
            is None
        )

    def test_empty_or_missing_answer_never_flagged(self):
        assert find_ungrounded_success_claim("", []) is None
        assert find_ungrounded_success_claim(None, []) is None


# ---------------------------------------------------------------------------
# find_unqualified_negative_claim — the self-contradiction guard
# ---------------------------------------------------------------------------


class TestFindUnqualifiedNegativeClaim:
    def test_no_pre_scan_tool_call_this_turn_is_never_flagged(self):
        assert find_unqualified_negative_claim("No urgent items.", []) is None

    def test_true_no_urgent_no_actionable_claim_is_not_flagged(self):
        convo = [_tool_entry("pre_scan_inbox", _prescan_envelope())]
        text = "No urgent or actionable items found."
        assert find_unqualified_negative_claim(text, convo) is None

    def test_no_urgent_claim_contradicted_by_a_non_empty_urgent_list(self):
        envelope = _prescan_envelope(urgent=[{"message_id": "m1"}])
        convo = [_tool_entry("pre_scan_inbox", envelope)]
        reason = find_unqualified_negative_claim("No urgent items today.", convo)
        assert reason is not None
        assert "urgent" in reason

    def test_no_actionable_claim_contradicted_by_a_non_empty_actionable_list(self):
        envelope = _prescan_envelope(actionable=[{"message_id": "m1"}])
        convo = [_tool_entry("pre_scan_inbox", envelope)]
        reason = find_unqualified_negative_claim(
            "no urgent or actionable items found", convo
        )
        assert reason is not None
        assert "actionable" in reason

    def test_unqualified_all_clear_under_coverage_is_flagged(self):
        envelope = _prescan_envelope(scanned=25, total_unread=557)
        convo = [_tool_entry("pre_scan_inbox", envelope)]
        reason = find_unqualified_negative_claim("Nothing needs you right now.", convo)
        assert reason is not None
        assert "25" in reason and "557" in reason

    def test_qualified_all_clear_under_coverage_is_not_flagged(self):
        envelope = _prescan_envelope(scanned=25, total_unread=557)
        convo = [_tool_entry("pre_scan_inbox", envelope)]
        text = "Nothing needs you — 25 messages scanned, 557 unread so far."
        assert find_unqualified_negative_claim(text, convo) is None

    def test_all_clear_with_full_coverage_is_not_flagged(self):
        # scanned >= total_unread: nothing was actually left uncovered.
        envelope = _prescan_envelope(scanned=25, total_unread=25)
        convo = [_tool_entry("pre_scan_inbox", envelope)]
        assert find_unqualified_negative_claim("Nothing needs you.", convo) is None

    def test_unknown_total_unread_is_not_flagged(self):
        envelope = _prescan_envelope(scanned=25, total_unread=None)
        convo = [_tool_entry("pre_scan_inbox", envelope)]
        assert find_unqualified_negative_claim("Nothing needs you.", convo) is None


# ---------------------------------------------------------------------------
# find_unlicensed_cross_mailbox_claim
# ---------------------------------------------------------------------------


class TestFindUnlicensedCrossMailboxClaim:
    def test_detects_the_live_observed_phrasing(self):
        envelope = _prescan_envelope(total_unread=557)
        convo = [_tool_entry("pre_scan_inbox", envelope)]
        text = "You have 557 unread across your connected mailboxes."
        assert find_unlicensed_cross_mailbox_claim(text, convo) is not None

    def test_detects_accounts_phrasing_too(self):
        envelope = _prescan_envelope(total_unread=557)
        convo = [_tool_entry("pre_scan_inbox", envelope)]
        text = "557 unread across your accounts."
        assert find_unlicensed_cross_mailbox_claim(text, convo) is not None

    def test_single_mailbox_scoped_phrasing_is_not_flagged(self):
        envelope = _prescan_envelope(total_unread=557)
        convo = [_tool_entry("pre_scan_inbox", envelope)]
        text = "You have 557 unread messages in your inbox."
        assert find_unlicensed_cross_mailbox_claim(text, convo) is None

    def test_no_total_unread_value_is_not_flagged(self):
        envelope = _prescan_envelope(total_unread=None)
        convo = [_tool_entry("pre_scan_inbox", envelope)]
        text = "You have unread mail across your connected mailboxes."
        assert find_unlicensed_cross_mailbox_claim(text, convo) is None

    def test_no_pre_scan_tool_call_is_not_flagged(self):
        text = "557 unread across your connected mailboxes."
        assert find_unlicensed_cross_mailbox_claim(text, []) is None


# ---------------------------------------------------------------------------
# find_scaffolding_leak / strip_scaffolding_leaks
# ---------------------------------------------------------------------------

_LEAKED_TEXT = (
    "Informational Count: 22 messages were categorized as general information.\n\n"
    "[shown to the user]\n\n"
    "\u2022 [suggested_archives] \"DeepLearning.AI\" hello@deeplearning.ai \u2014 AI "
    "writes your code. Who reviews it? (id 19faf805d75f51eb)\n"
    "\u2022 [suggested_archives] The Associated Press donations@apnews.com \u2014 "
    "Thank you for signing up \\u2013 support AP\\u2019s mission "
    "(id 19fae1b5f0917d6f)"
)


class TestFindScaffoldingLeak:
    def test_detects_shown_to_user_marker(self):
        assert find_scaffolding_leak("some text\n\n[shown to the user]\nmore") is not None

    def test_detects_envelope_field_label(self):
        assert find_scaffolding_leak("• [suggested_archives] DeepLearning.AI") is not None

    def test_detects_raw_message_id(self):
        assert find_scaffolding_leak("archived it (id 19faf805d75f51eb)") is not None

    def test_detects_undecoded_unicode_escape(self):
        assert find_scaffolding_leak("support AP\\u2019s mission") is not None

    def test_clean_text_is_not_flagged(self):
        clean = "Two newsletters look safe to archive from DeepLearning.AI and AP."
        assert find_scaffolding_leak(clean) is None

    def test_empty_text_is_not_flagged(self):
        assert find_scaffolding_leak("") is None
        assert find_scaffolding_leak(None) is None


class TestStripScaffoldingLeaks:
    def test_removes_every_pattern_from_the_verbatim_example(self):
        cleaned = strip_scaffolding_leaks(_LEAKED_TEXT)
        assert "[shown to the user]" not in cleaned
        assert "[suggested_archives]" not in cleaned
        assert "19faf805d75f51eb" not in cleaned
        assert "19fae1b5f0917d6f" not in cleaned
        assert "\\u2013" not in cleaned
        assert "\\u2019" not in cleaned
        # Targeted, not a wipe: the actual content survives.
        assert "DeepLearning.AI" in cleaned
        assert "Associated Press" in cleaned
        assert find_scaffolding_leak(cleaned) is None

    def test_decodes_the_escape_to_a_real_character(self):
        cleaned = strip_scaffolding_leaks("signing up \\u2013 support")
        assert "\u2013" in cleaned  # the real en dash, not the escape text

    def test_leaves_clean_text_unchanged_apart_from_whitespace_trim(self):
        clean = "Two newsletters look safe to archive."
        assert strip_scaffolding_leaks(clean) == clean


class TestDecodeStrayUnicodeEscapes:
    def test_decodes_known_escapes(self):
        assert decode_stray_unicode_escapes("a\\u2013b\\u2019c") == "a\u2013b\u2019c"

    def test_leaves_windows_path_untouched(self):
        # Capital "\U" must never be mistaken for a lowercase \u escape.
        path = "saved to C:\\Users\\me\\Documents"
        assert decode_stray_unicode_escapes(path) == path

    def test_leaves_text_without_backslash_u_untouched(self):
        text = "nothing to decode here"
        assert decode_stray_unicode_escapes(text) == text


# ---------------------------------------------------------------------------
# find_ungrounded_calendar_conflict_claim (#2571) — folded in from what used
# to be a standalone calendar-only hook in EmailTriageAgent.process_query.
# The detection logic itself lives in calendar_tools.response_has_ungrounded_
# conflict_claim; this wrapper only supplies the turn's own tool trace.
# ---------------------------------------------------------------------------


class TestFindUngroundedCalendarConflictClaim:
    def test_fires_when_events_listed_without_the_conflict_tool(self):
        convo = [_list_events_tool_entry(2)]
        reason = find_ungrounded_calendar_conflict_claim(
            "These two events are back-to-back and do not conflict.", convo
        )
        assert reason is not None

    def test_grounded_when_detect_calendar_conflicts_ran(self):
        convo = [_list_events_tool_entry(2), _detect_conflicts_tool_entry()]
        assert (
            find_ungrounded_calendar_conflict_claim(
                "These two events overlap by 30 minutes.", convo
            )
            is None
        )

    def test_not_flagged_below_two_listed_events(self):
        convo = [_list_events_tool_entry(1)]
        assert (
            find_ungrounded_calendar_conflict_claim(
                "This event doesn't conflict with anything.", convo
            )
            is None
        )

    def test_fires_with_no_calendar_tool_but_two_times_cited(self):
        reason = find_ungrounded_calendar_conflict_claim(
            "Your 7:00 AM and 7:30 AM meetings conflict.", []
        )
        assert reason is not None

    def test_no_conflict_language_is_not_flagged(self):
        convo = [_list_events_tool_entry(2)]
        assert (
            find_ungrounded_calendar_conflict_claim(
                "Here are your two events today.", convo
            )
            is None
        )

    def test_empty_or_missing_answer_never_flagged(self):
        assert find_ungrounded_calendar_conflict_claim("", []) is None
        assert find_ungrounded_calendar_conflict_claim(None, []) is None


# ---------------------------------------------------------------------------
# find_ungrounded_invite_claim (#2766) — "proposals are not invites". No
# tool here can currently confirm a genuine received/sent invite, so a
# completion-framed invite claim is always ungrounded except when this turn
# actually called create_event_from_email.
# ---------------------------------------------------------------------------


class TestFindUngroundedInviteClaim:
    @pytest.mark.parametrize(
        "phrase",
        [
            "An invite has been confirmed as sent.",
            "Yes, Tomasz sent you invites for three meetings.",
            "You have received a calendar invite from the vendor.",
            "The invite was sent yesterday.",
            "I can confirm the invite has been sent to your inbox.",
        ],
    )
    def test_detects_invite_claim_with_no_grounding_tool(self, phrase):
        reason = find_ungrounded_invite_claim(phrase, [])
        assert reason is not None

    @pytest.mark.parametrize(
        "phrase",
        [
            "No invite has been sent — this is just a proposal.",
            "None of them were flagged as formal calendar invites.",
            "These aren't formal calendar invites, just requests to chat.",
            "Would you like me to send an invite for this?",
            "Alice proposed meeting Thursday at 2pm.",
            "Here are your three upcoming meetings.",
            "The vendor said an invite would be sent soon.",
            "Nobody has sent you an invite yet.",
            "Three people proposed times to meet, but no invite has actually "
            "been sent.",
        ],
    )
    def test_no_false_positive(self, phrase):
        assert find_ungrounded_invite_claim(phrase, []) is None

    def test_grounded_when_create_event_from_email_ran_this_turn(self):
        convo = [_tool_entry("create_event_from_email", {"event_id": "e1"})]
        text = "I've created the event and sent an invite to the attendee."
        assert find_ungrounded_invite_claim(text, convo) is None

    def test_unrelated_tool_call_does_not_ground_the_claim(self):
        convo = [_list_events_tool_entry(2)]
        text = "An invite has been confirmed as sent."
        assert find_ungrounded_invite_claim(text, convo) is not None

    def test_negation_far_from_the_word_invite_still_suppresses(self):
        text = "It's not true that an invite was sent for this one."
        assert find_ungrounded_invite_claim(text, []) is None

    def test_negation_in_a_different_sentence_does_not_suppress(self):
        # The negation belongs to an earlier, unrelated sentence -- it must
        # not launder a real claim in the next one.
        text = "No urgent items today. An invite has been confirmed as sent."
        assert find_ungrounded_invite_claim(text, []) is not None

    def test_empty_or_missing_answer_never_flagged(self):
        assert find_ungrounded_invite_claim("", []) is None
        assert find_ungrounded_invite_claim(None, []) is None


# ---------------------------------------------------------------------------
# find_fabricated_attendee_claim (#2766) — "any attendee name is a
# fabrication" when the calendar tool's own result carries none.
# ---------------------------------------------------------------------------


class TestFindFabricatedAttendeeClaim:
    def test_flags_attendee_language_when_every_listed_event_is_empty(self):
        convo = [_events_tool_entry("list_calendar_events", [_event()])]
        text = "The attendees for this meeting are Jane Doe and John Smith."
        reason = find_fabricated_attendee_claim(text, convo)
        assert reason is not None

    def test_flags_invitee_language_too(self):
        convo = [_events_tool_entry("list_calendar_events", [_event()])]
        text = "The invitees include Jane Doe."
        assert find_fabricated_attendee_claim(text, convo) is not None

    def test_not_flagged_when_a_listed_event_actually_has_attendees(self):
        convo = [
            _events_tool_entry(
                "list_calendar_events",
                [_event(attendees=[{"email": "jane@example.com"}])],
            )
        ]
        text = "The attendees for this meeting are jane@example.com."
        assert find_fabricated_attendee_claim(text, convo) is None

    def test_not_flagged_when_neither_calendar_tool_ran(self):
        text = "The attendees for this meeting are Jane Doe."
        assert find_fabricated_attendee_claim(text, []) is None

    def test_not_flagged_with_no_attendee_shaped_claim(self):
        convo = [_events_tool_entry("list_calendar_events", [_event()])]
        text = "You have one meeting: Design review at 9am."
        assert find_fabricated_attendee_claim(text, convo) is None

    def test_checks_detect_calendar_conflicts_results_too(self):
        convo = [
            _events_tool_entry("detect_calendar_conflicts", [_event()], key="conflicts")
        ]
        text = "The attendees include Jane Doe."
        assert find_fabricated_attendee_claim(text, convo) is not None

    def test_any_event_with_attendees_across_multiple_clears_it(self):
        # Two events this turn; only one carries attendees -- the model may
        # be describing THAT one, so this guard (which doesn't try to match
        # a specific name to a specific event) stays silent.
        convo = [
            _events_tool_entry(
                "list_calendar_events",
                [
                    _event(id="evt1"),
                    _event(id="evt2", attendees=[{"email": "jane@example.com"}]),
                ],
            )
        ]
        text = "The attendees are jane@example.com."
        assert find_fabricated_attendee_claim(text, convo) is None

    def test_empty_or_missing_answer_never_flagged(self):
        convo = [_events_tool_entry("list_calendar_events", [_event()])]
        assert find_fabricated_attendee_claim("", convo) is None
        assert find_fabricated_attendee_claim(None, convo) is None


# ---------------------------------------------------------------------------
# ground_final_answer — the orchestration a real turn goes through
# ---------------------------------------------------------------------------


class TestGroundFinalAnswer:
    def test_success_claim_replaces_the_whole_answer(self):
        result = {"result": "The message has been archived.", "conversation": []}
        out = ground_final_answer(result)
        assert out["result"] == UNGROUNDED_SUCCESS_FALLBACK

    def test_contradicted_negative_claim_replaces_with_grounded_summary(self):
        envelope = _prescan_envelope(urgent=[{"message_id": "m1"}])
        result = {
            "result": "No urgent items today.",
            "conversation": [_tool_entry("pre_scan_inbox", envelope)],
        }
        out = ground_final_answer(result)
        assert "no urgent" not in out["result"].lower().replace("no urgent items", "")
        assert "1 urgent" in out["result"]

    def test_cross_mailbox_overclaim_replaces_with_grounded_summary(self):
        envelope = _prescan_envelope(total_unread=557)
        result = {
            "result": "557 unread across your connected mailboxes.",
            "conversation": [_tool_entry("pre_scan_inbox", envelope)],
        }
        out = ground_final_answer(result)
        assert "across your" not in out["result"].lower()
        assert "557" in out["result"]

    def test_scaffolding_is_cleaned_without_wholesale_replacement(self):
        result = {
            "result": "Here's your inbox pre-scan.\n\n" + _LEAKED_TEXT,
            "conversation": [],
        }
        out = ground_final_answer(result)
        assert "Here's your inbox pre-scan." in out["result"]
        assert "[shown to the user]" not in out["result"]
        assert "DeepLearning.AI" in out["result"]

    def test_clean_grounded_answer_is_unchanged(self):
        text = "Here's your inbox pre-scan — 5 actionable, 1 suggested archive."
        result = {"result": text, "conversation": []}
        out = ground_final_answer(result)
        assert out["result"] == text

    def test_missing_or_non_string_result_key_is_left_alone(self):
        assert ground_final_answer({}) == {}
        assert ground_final_answer({"result": None}) == {"result": None}
        assert ground_final_answer({"result": 42}) == {"result": 42}


# ---------------------------------------------------------------------------
# find_attention_card_contradiction — reconciles prose against the cached
# attention view (#2636), which is NEVER a tool result in this turn's own
# trace (build_attention_view_impl has no @tool wrapper -- #2582 built it
# purely for the TUI's on-open render), so this guard is turn-INdependent
# by necessity: it reads the same process-global cache api_routes.py's
# GET /v1/email/attention already serves the TUI from.
# ---------------------------------------------------------------------------


class TestFindAttentionCardContradiction:
    def test_no_cache_at_all_is_never_flagged(self):
        # Card never rendered this process -- nothing to reconcile against,
        # not a hidden fallback (there is genuinely no known card state).
        assert (
            find_attention_card_contradiction("No urgent or actionable items found.")
            is None
        )

    def test_cached_but_empty_items_is_never_flagged(self):
        _store_attention_view(items=[])
        assert find_attention_card_contradiction("Nothing needs you right now.") is None

    def test_all_clear_claim_contradicted_by_non_empty_card(self):
        _store_attention_view(items=[_attention_item("action_item")], scanned=7)
        reason = find_attention_card_contradiction(
            "No urgent or actionable items found."
        )
        assert reason is not None
        assert "1" in reason

    def test_category_specific_claim_contradicted(self):
        _store_attention_view(items=[_attention_item("meeting_request")])
        reason = find_attention_card_contradiction("No meeting proposals right now.")
        assert reason is not None
        assert "meeting_request" in reason

    def test_grounded_claim_naming_the_real_count_is_not_flagged(self):
        _store_attention_view(items=[_attention_item("action_item")])
        text = "You have 1 action item to review."
        assert find_attention_card_contradiction(text) is None

    def test_stale_cache_past_ttl_declines_to_correct(self):
        from gaia_agent_email.attention_cache import ATTENTION_CACHE_TTL_SECONDS

        _store_attention_view(
            items=[_attention_item("action_item")],
            age_seconds=ATTENTION_CACHE_TTL_SECONDS + 1,
        )
        # The card may since have been cleared -- correcting from data this
        # old would risk asserting a since-resolved item is still open,
        # which is #2636's own dishonesty pointed the other way.
        assert find_attention_card_contradiction("No action items right now.") is None

    def test_cache_just_within_ttl_still_corrects(self):
        from gaia_agent_email.attention_cache import ATTENTION_CACHE_TTL_SECONDS

        _store_attention_view(
            items=[_attention_item("action_item")],
            age_seconds=ATTENTION_CACHE_TTL_SECONDS - 1,
        )
        assert (
            find_attention_card_contradiction("No action items right now.") is not None
        )

    def test_empty_or_missing_answer_never_flagged(self):
        _store_attention_view(items=[_attention_item("action_item")])
        assert find_attention_card_contradiction("") is None
        assert find_attention_card_contradiction(None) is None


# ---------------------------------------------------------------------------
# ground_final_answer wiring for the attention-card guard -- appends a
# correction rather than replacing (unlike the pre_scan guard above): the
# false clause here is typically one clause inside an otherwise-useful
# answer, so scrubbing the whole message is a worse trade for the user than
# qualifying it (#2636).
# ---------------------------------------------------------------------------


class TestGroundFinalAnswerAttentionCard:
    def test_appends_correction_without_destroying_original_text(self):
        _store_attention_view(
            items=[_attention_item("action_item"), _attention_item("meeting_request")],
            scanned=42,
        )
        result = {
            "result": "Hi! No urgent or actionable items found.",
            "conversation": [],
        }
        out = ground_final_answer(result)
        assert "Hi! No urgent or actionable items found." in out["result"]
        assert "attention card" in out["result"].lower()
        assert "42" in out["result"]

    def test_no_cache_leaves_the_answer_untouched(self):
        result = {"result": "No urgent or actionable items found.", "conversation": []}
        out = ground_final_answer(result)
        assert out["result"] == "No urgent or actionable items found."

    def test_stale_cache_leaves_the_answer_untouched(self):
        from gaia_agent_email.attention_cache import ATTENTION_CACHE_TTL_SECONDS

        _store_attention_view(
            items=[_attention_item("action_item")],
            age_seconds=ATTENTION_CACHE_TTL_SECONDS + 1,
        )
        result = {"result": "No action items right now.", "conversation": []}
        out = ground_final_answer(result)
        assert out["result"] == "No action items right now."


# ---------------------------------------------------------------------------
# ground_final_answer wiring for the calendar-conflict guard (#2571) — the
# fold's own append-only check, exercised through the single orchestration
# entry point rather than the pure function directly.
# ---------------------------------------------------------------------------


class TestGroundFinalAnswerCalendarConflict:
    def test_appends_correction_without_destroying_original_text(self):
        text = (
            "Here are your events: Budget sync, Design review. These two "
            "events are back-to-back and do not conflict."
        )
        result = {
            "result": text,
            "conversation": [_list_events_tool_entry(2)],
        }
        out = ground_final_answer(result)
        assert text in out["result"]
        assert "detect_calendar_conflicts" in out["result"]
        assert out["result"] != text

    def test_grounded_by_the_conflict_tool_is_left_untouched(self):
        text = "These two events overlap by 30 minutes."
        result = {
            "result": text,
            "conversation": [_list_events_tool_entry(2), _detect_conflicts_tool_entry()],
        }
        out = ground_final_answer(result)
        assert out["result"] == text


# ---------------------------------------------------------------------------
# ground_final_answer wiring for the invite-claim and fabricated-attendee
# guards (#2766) — same append-only shape as the calendar-conflict guard
# above, exercised through the single orchestration entry point.
# ---------------------------------------------------------------------------


class TestGroundFinalAnswerInviteAndAttendee:
    def test_appends_invite_correction_without_destroying_original_text(self):
        text = "An invite has been confirmed as sent for the ObjectWin meeting."
        result = {"result": text, "conversation": []}
        out = ground_final_answer(result)
        assert text in out["result"]
        assert "confirm" in out["result"].lower()
        assert out["result"] != text

    def test_invite_claim_grounded_by_create_event_from_email_is_untouched(self):
        text = "I've created the event and sent an invite to the attendee."
        result = {
            "result": text,
            "conversation": [
                _tool_entry("create_event_from_email", {"event_id": "e1"})
            ],
        }
        out = ground_final_answer(result)
        assert out["result"] == text

    def test_appends_attendee_correction_without_destroying_original_text(self):
        text = "The attendees for this meeting are Jane Doe and John Smith."
        result = {
            "result": text,
            "conversation": [_events_tool_entry("list_calendar_events", [_event()])],
        }
        out = ground_final_answer(result)
        assert text in out["result"]
        assert "don't list any attendees" in out["result"]
        assert out["result"] != text

    def test_attendee_claim_grounded_by_real_attendees_is_untouched(self):
        text = "The attendees for this meeting are jane@example.com."
        result = {
            "result": text,
            "conversation": [
                _events_tool_entry(
                    "list_calendar_events",
                    [_event(attendees=[{"email": "jane@example.com"}])],
                )
            ],
        }
        out = ground_final_answer(result)
        assert out["result"] == text

    def test_both_corrections_compose_on_one_turn(self):
        text = (
            "An invite has been confirmed as sent. The attendees are Jane "
            "Doe and John Smith."
        )
        result = {
            "result": text,
            "conversation": [_events_tool_entry("list_calendar_events", [_event()])],
        }
        out = ground_final_answer(result)
        assert text in out["result"]
        assert "confirm" in out["result"].lower()
        assert "don't list any attendees" in out["result"]


# ---------------------------------------------------------------------------
# THE composition test — the fold's single most important guarantee. All
# four APPEND-only checks (calendar-conflict #2571, attention-card #2636,
# invite-claim and fabricated-attendee #2766) are independent of each other;
# an early ``return`` between any two (a bug the reference integration
# actually hit once) would silently suppress whichever check runs later.
# All four must be able to fire, in sequence, on the very same turn.
# ---------------------------------------------------------------------------


class TestGroundFinalAnswerComposition:
    def test_calendar_and_attention_card_corrections_both_appear_on_one_turn(self):
        _store_attention_view(items=[_attention_item("action_item")], scanned=7)
        text = (
            "Here are your events: Budget sync, Design review. These two "
            "events are back-to-back and do not conflict. No action items "
            "right now."
        )
        result = {
            "result": text,
            "conversation": [_list_events_tool_entry(2)],
        }
        out = ground_final_answer(result)

        # The original answer survives untouched — both checks append.
        assert text in out["result"]
        # The calendar-conflict correction fired.
        assert "detect_calendar_conflicts" in out["result"]
        # The attention-card correction ALSO fired — neither short-circuited
        # the other.
        assert "attention card" in out["result"].lower()
        assert "7" in out["result"]

    def test_all_four_append_guards_fire_on_one_turn(self):
        _store_attention_view(items=[_attention_item("action_item")], scanned=7)
        text = (
            "Here are your events: Budget sync, Design review. These two "
            "events are back-to-back and do not conflict. No action items "
            "right now. An invite has been confirmed as sent, and the "
            "attendees are Jane Doe and John Smith."
        )
        result = {
            "result": text,
            "conversation": [
                _events_tool_entry(
                    "list_calendar_events",
                    [_event(id="evt1"), _event(id="evt2")],
                )
            ],
        }
        out = ground_final_answer(result)

        assert text in out["result"]
        assert "detect_calendar_conflicts" in out["result"]
        assert "attention card" in out["result"].lower()
        assert "confirm" in out["result"].lower()
        assert "don't list any attendees" in out["result"]


# ---------------------------------------------------------------------------
# Wiring — EmailTriageAgent.process_query actually calls ground_final_answer
# ---------------------------------------------------------------------------

EMBEDDING_DIM = 768


class _MinimalCalendarBackend:
    pass


def _fake_embed(_text: str) -> np.ndarray:
    vec = np.ones(EMBEDDING_DIM, dtype=np.float32)
    vec /= np.linalg.norm(vec)
    return vec


def _build_agent(tmp_path: Path) -> EmailTriageAgent:
    """A real ``EmailTriageAgent`` with the LLM/network boundary mocked out —
    mirrors the proven construction pattern used for undo-batch testing
    (``test_undo_reachable_2456.py``), so ``process_query``'s own override
    logic runs for real rather than being reimplemented against a stand-in.
    """
    backend = FakeGmailBackend(user_email="me@example.com")
    cfg = EmailAgentConfig(
        gmail_backend=backend,
        calendar_backend=_MinimalCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        debug=False,
    )
    with (
        patch("gaia.agents.base.agent.AgentSDK") as mock_sdk,
        patch("gaia.agents.base.memory.MemoryMixin._get_embedder", return_value=MagicMock()),
        patch("gaia.agents.base.memory.MemoryMixin._embed_text", side_effect=_fake_embed),
        patch("gaia.agents.base.memory.MemoryMixin._backfill_embeddings", return_value=0),
        patch("gaia.agents.base.memory.MemoryMixin._rebuild_faiss_index"),
        patch("gaia.agents.base.memory.MemoryMixin.init_system_context"),
    ):
        mock_sdk.return_value = MagicMock()
        agent = EmailTriageAgent(config=cfg)
    return agent


class TestProcessQueryWiring:
    def test_ungrounded_success_claim_is_rewritten_by_the_real_override(self, tmp_path):
        agent = _build_agent(tmp_path)
        try:
            canned = {
                "status": "success",
                "result": "The message has been archived.",
                "conversation": [{"role": "user", "content": "archive it"}],
                "steps_taken": 1,
            }
            with patch(
                "gaia.agents.base.agent.Agent.process_query", return_value=canned
            ):
                out = agent.process_query("archive that message")
            assert out["result"] == UNGROUNDED_SUCCESS_FALLBACK
        finally:
            agent.close_db()

    def test_grounded_answer_passes_through_unchanged(self, tmp_path):
        agent = _build_agent(tmp_path)
        try:
            text = "Here's your inbox pre-scan — 5 actionable, 1 suggested archive."
            canned = {
                "status": "success",
                "result": text,
                "conversation": [
                    {"role": "user", "content": "pre-scan my inbox"},
                    _tool_entry("pre_scan_inbox", _prescan_envelope()),
                ],
                "steps_taken": 2,
            }
            with patch(
                "gaia.agents.base.agent.Agent.process_query", return_value=canned
            ):
                out = agent.process_query("pre-scan my inbox")
            assert out["result"] == text
        finally:
            agent.close_db()

    def test_attention_card_contradiction_is_appended_by_the_real_override(
        self, tmp_path
    ):
        # #2636: the model never called any attention-view tool this turn
        # (there isn't one -- build_attention_view_impl has no @tool wrapper),
        # yet the cached card the TUI already rendered this process disagrees
        # with the answer. The real process_query override must still catch it.
        _store_attention_view(items=[_attention_item("action_item")], scanned=7)
        agent = _build_agent(tmp_path)
        try:
            canned = {
                "status": "success",
                "result": "No urgent or actionable items found.",
                "conversation": [
                    {"role": "user", "content": "anything need my attention?"}
                ],
                "steps_taken": 1,
            }
            with patch(
                "gaia.agents.base.agent.Agent.process_query", return_value=canned
            ):
                out = agent.process_query("anything need my attention?")
            assert "No urgent or actionable items found." in out["result"]
            assert "attention card" in out["result"].lower()
        finally:
            agent.close_db()

    def test_calendar_conflict_correction_is_appended_by_the_real_override(
        self, tmp_path
    ):
        # #2571, folded from what used to be a standalone hook in
        # process_query — must still fire through the single
        # ground_final_answer call site, not a separate code path.
        agent = _build_agent(tmp_path)
        try:
            text = (
                "These two events are scheduled back-to-back and do not "
                "conflict with each other."
            )
            canned = {
                "status": "success",
                "result": text,
                "conversation": [
                    {"role": "user", "content": "list my events and flag conflicts"},
                    _list_events_tool_entry(2),
                ],
                "steps_taken": 1,
            }
            with patch(
                "gaia.agents.base.agent.Agent.process_query", return_value=canned
            ):
                out = agent.process_query("list my events and flag conflicts")
            assert text in out["result"]
            assert "detect_calendar_conflicts" in out["result"]
        finally:
            agent.close_db()


# ---------------------------------------------------------------------------
# Prompt wording — locks the system prompt's claims to what the code does
# ---------------------------------------------------------------------------


class TestPromptWording:
    def test_old_misleading_fraction_example_is_gone(self):
        # The exact "X of Y unread scanned" example this batch replaced --
        # scanned is not a subset of total_unread for every surface it can
        # describe, so this specific phrasing must not reappear.
        assert "12 of 508 unread scanned" not in _SYSTEM_PROMPT

    def test_scanned_and_total_unread_stated_as_separate_facts(self):
        assert "not a fraction" in _SYSTEM_PROMPT
        assert "X of Y unread" in _SYSTEM_PROMPT  # named as the forbidden pattern

    def test_cross_mailbox_overclaim_is_explicitly_forbidden(self):
        assert "across your mailboxes" in _SYSTEM_PROMPT
        assert "across your accounts" in _SYSTEM_PROMPT

    def test_negative_claim_qualification_present(self):
        assert 'no urgent items"' in _SYSTEM_PROMPT
        assert 'no actionable items"' in _SYSTEM_PROMPT

    def test_tool_call_discipline_section_present(self):
        assert "A TOOL CALL IS THE ONLY WAY SOMETHING HAPPENED" in _SYSTEM_PROMPT

    def test_check_followups_enumeration_guidance_present(self):
        assert "EVERY entry individually" in _SYSTEM_PROMPT
        assert "``count``" in _SYSTEM_PROMPT

    def test_scaffolding_guard_language_present(self):
        assert "provider message ids" in _SYSTEM_PROMPT
        assert "bracketed note" in _SYSTEM_PROMPT
