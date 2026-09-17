# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A reasoning model's deliberation must not reach the user as the answer.

Paired ``<think>…</think>`` was always stripped. The case that shipped is an
unpaired closing tag: the opening tag goes out on a separate reasoning
channel, so the text arrives as ``<deliberation></think><answer>``, the
paired pattern matches nothing, and the whole thing — stray tag included —
is emitted as the final answer.

The unpaired rule is destructive and is therefore split into its own
function. Applied to a raw response before parsing, it deletes everything
before the tag — including a whole tool call whose arguments merely mention
those characters. The first version of this fix did exactly that and cost a
benchmark run eight tasks, every one of them ending with the answer ``"}``.
"""

import json

import pytest

from gaia.agents.base.agent import strip_orphan_reasoning, strip_reasoning_blocks


class TestPairedBlocks:
    """Safe before parsing: a paired block sits outside the JSON payload."""

    def test_a_paired_block_is_removed(self):
        assert strip_reasoning_blocks("<think>hmm</think>The answer is 42.") == (
            "The answer is 42."
        )

    def test_several_paired_blocks_are_removed(self):
        assert strip_reasoning_blocks("<think>a</think>One. <think>b</think>Two.") == (
            "One. Two."
        )

    def test_a_block_spanning_lines_is_removed(self):
        assert strip_reasoning_blocks("<think>a\nb\nc</think>\nDone.") == "Done."

    def test_a_tool_call_after_a_paired_block_survives_intact(self):
        call = '{"tool": "read_file", "tool_args": {"path": "a.txt"}}'
        assert json.loads(strip_reasoning_blocks(f"<think>plan</think>{call}"))


class TestRawResponsesAreNeverTruncated:
    """The regression: a payload merely containing the tag was destroyed."""

    @pytest.mark.parametrize(
        "raw",
        [
            # A command that greps for the literal tag.
            '{"tool": "run_shell_command", '
            '"tool_args": {"command": "grep -rn \'</think>\' ."}}',
            # An answer whose text ends with a stray tag.
            '{"answer": "I thought about it</think>"}',
            # Both: a paired block, then a payload mentioning the tag.
            '<think>plan</think>{"answer": "done</think>"}',
        ],
    )
    def test_the_json_still_parses(self, raw):
        cleaned = strip_reasoning_blocks(raw)
        assert json.loads(cleaned), f"strip corrupted the payload: {cleaned!r}"


class TestOrphanClosingTag:
    """Answer text only. The observed leak: deliberation, tag, real answer."""

    def test_everything_before_a_lone_closing_tag_goes(self):
        text = (
            "I can't access the directory. I'll ask the user to attach the "
            "files.</think>I can't reach the task files — could you attach "
            "`config.py`?"
        )
        cleaned = strip_orphan_reasoning(text)
        assert "</think>" not in cleaned
        assert cleaned.startswith("I can't reach the task files")

    def test_a_trailing_closing_tag_leaves_an_empty_answer(self):
        # Nothing follows the tag, so there is no answer to keep. Empty is
        # honest; emitting the reasoning would be a confident non-answer.
        assert strip_orphan_reasoning("all of this was thinking</think>") == ""

    @pytest.mark.parametrize(
        "text",
        ["The answer is 42.", "Use the <div> tag in the template.", ""],
    )
    def test_text_without_the_tag_is_untouched(self, text):
        assert strip_orphan_reasoning(text) == text

    def test_an_unclosed_opening_tag_is_left_alone(self):
        # No closing tag means no reliable boundary, and guessing one would
        # risk eating a real answer.
        assert strip_orphan_reasoning("<think>still going") == "<think>still going"
