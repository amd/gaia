# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for ``gaia_agent_email.answer_grounding`` — the deterministic
post-checks the agent runs on its own final answer text before returning it.

Covers four related honesty defects, all guarded by the same mechanism:

- a mutation narrated as done with no matching tool call this turn
- a "no urgent/actionable" claim contradicted by the same turn's own scan
- "unread across your mailboxes" overclaiming a per-INBOX-scoped number
- internal render/envelope scaffolding (markers, field-name labels, raw
  provider ids, undecoded unicode escapes) echoed into user-facing prose

Every pure function is tested directly, with no agent construction and no
LLM/network access. ``TestProcessQueryWiring`` additionally proves the
mechanism is actually wired into ``EmailTriageAgent.process_query``, not
just correct in isolation. ``TestPromptWording`` locks the system prompt's
own claims to what the code actually does, so the two cannot drift apart
silently.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# parents[0]=tests/ [1]=python/ [2]=email/ [3]=agents/ [4]=hub/ [5]=repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.agent import EmailTriageAgent, _SYSTEM_PROMPT  # noqa: E402
from gaia_agent_email.answer_grounding import (  # noqa: E402
    UNGROUNDED_SUCCESS_FALLBACK,
    decode_stray_unicode_escapes,
    find_scaffolding_leak,
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
