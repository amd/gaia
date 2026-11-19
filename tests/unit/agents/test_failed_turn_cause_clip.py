# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A failed turn quotes a long error cut at a word, never mid-word.

Seen on v0.26.0-rc30 with a mistyped custom model: the reply ended
"... Received: no-such-model-xyz-123 Check t." because the raw error was cut
at exactly 300 characters and given a full stop.
"""

from unittest.mock import patch

import pytest

from gaia.agents.base.agent import _CAUSE_CHARS, Agent, _clip_cause

LEMONADE = (
    "Failed to load model 'no-such-model-xyz-123' on http://localhost:59117/api/v1: "
    "Failed to auto-download 'no-such-model-xyz-123': Download failed: When "
    "registering a new model, the model name must include the `user` namespace, "
    "for example `user.Phi-4-Mini-GGUF`. Received: no-such-model-xyz-123 Check "
    "that the model is available and the Lemonade server has enough memory."
)


class _PlainAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        pass


@pytest.fixture
def agent():
    with patch("gaia.agents.base.agent.AgentSDK"):
        a = _PlainAgent(silent_mode=True, skip_lemonade=True)
    a._save_failure_record = lambda _exc: None
    return a


def test_short_text_is_left_alone():
    assert _clip_cause("  Connection refused.  ") == "Connection refused."


def test_long_text_ends_at_the_last_sentence_that_fits():
    clipped = _clip_cause(LEMONADE)
    assert len(clipped) <= _CAUSE_CHARS
    assert clipped.endswith("`user.Phi-4-Mini-GGUF`.")


def test_with_no_sentence_end_it_cuts_at_a_word_and_says_so():
    text = "word " * 100
    clipped = _clip_cause(text)
    assert clipped.endswith("word…")
    assert len(clipped) <= _CAUSE_CHARS + 1


def test_one_unbroken_token_is_cut_and_marked():
    clipped = _clip_cause("x" * 400)
    assert clipped == "x" * _CAUSE_CHARS + "…"


def test_the_failed_turn_reply_never_ends_mid_word(agent):
    answer = agent._model_call_failed_answer(RuntimeError(LEMONADE), None, 1, [])

    assert "Check t." not in answer
    assert "`user.Phi-4-Mini-GGUF`." in answer


def test_a_word_cut_reply_has_no_full_stop_after_the_ellipsis(agent):
    answer = agent._model_call_failed_answer(RuntimeError("word " * 100), None, 1, [])

    assert "word…" in answer
    assert "…." not in answer


def test_the_closing_summary_note_never_ends_mid_word(agent):
    note = agent._closing_failure_note(
        RuntimeError("Check the server " * 40), "The summary"
    )

    assert note.startswith("The summary couldn't be written: Check the server")
    assert note.rsplit(" ", 1)[-1] in ("Check…", "the…", "server…")


def test_a_missing_model_closing_note_is_a_whole_sentence(agent):
    note = agent._closing_failure_note(RuntimeError(LEMONADE), "The summary")

    assert "`no-such-model-xyz-123`" in note
    assert note.endswith(".") and "…" not in note
