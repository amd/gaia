# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for #3887: final answers that only describe the next step.

In a 168-run benchmark, 11 of 26 failed runs ended on a plan, a narrated next
step, or a tool call typed out as text, and the loop accepted it as the answer.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import (
    _MAX_UNFINISHED_ANSWER_REPROMPTS,
    Agent,
    _unfinished_answer_kind,
)
from gaia.agents.base.verification import strip_verification_scope

# Endings from the #3887 table. Rows 2, 5 and 11 are truncated in the issue,
# so their lead-in is reconstructed around the visible text. Row 6 (an offer
# to "redo it") is omitted: its visible text alone is not a narrated step.
FAILED_RUN_ENDINGS = [
    pytest.param(
        "Window should be clear now. Fetching issue 3.",
        "narration",
        id="row1-fetching",
    ),
    pytest.param(
        "Issues 1-3 are labeled. Let me see the full issue list and the "
        "genuine answer for Issue #4.",
        "narration",
        id="row2-let-me-see",
    ),
    pytest.param(
        "Now I need to update the README.md to document the TOYBOX_CONFIG "
        "environment variable.",
        "narration",
        id="row3-now-i-need-to",
    ),
    pytest.param(
        "`duplicate` is on #5. Second write:",
        "narration",
        id="row4-trailing-colon",
    ),
    pytest.param(
        "The label call hit a secondary rate limit. I just need to wait and "
        "retry labeling #4 as `question`.",
        "narration",
        id="row5-just-need-to",
    ),
    pytest.param(
        "I'll load the GitHub tools first.\n\n"
        '<invoke name="load_tools">\n'
        '<parameter name="names">["github"]</parameter>\n'
        "</invoke>",
        "tool_markup",
        id="row7-invoke-markup",
    ),
    pytest.param(
        "Plan to find the duplicate:\n"
        "1. List open issues\n"
        "2. Fetch the body of #2\n"
        "3. Compare and label\n\n"
        "Executing step 1 and 2 in parallel (independent commands):",
        "narration",
        id="row8-executing-steps-colon",
    ),
    pytest.param(
        "Plan:\n1. Create tests/test_cli.py\n2. Run pytest\n\n"
        "First step: create the test file.",
        "narration",
        id="row9-first-step",
    ),
    pytest.param(
        "Plan:\n1. Add the env var lookup\n2. Update README.md\n\n"
        "Executing step **1** :",
        "narration",
        id="row10-bold-step-colon",
    ),
    pytest.param(
        "The report tool is unavailable, so I'll compute the weekly report "
        "by hand.\n\n"
        "1. Count issues opened this week.\n"
        "2. Count pull requests merged.\n"
        "3. List the most active contributors.\n\n"
        "Let me start by counting the issues opened this week.",
        "narration",
        id="row11-multi-paragraph-plan",
    ),
    pytest.param(
        "Corrected plan:\n1. Write tests/test_cli.py\n2. Run pytest",
        "narration",
        id="issue-corrected-plan",
    ),
    pytest.param(
        "Calling it now.\n"
        '{"tool": "add_label", "tool_args": {"issue": 4, "label": "question"',
        "tool_markup",
        id="truncated-tool-json",
    ),
]


@pytest.mark.parametrize("answer,expected", FAILED_RUN_ENDINGS)
def test_failed_run_endings_are_caught(answer, expected):
    assert _unfinished_answer_kind(answer) == expected


def test_long_plan_is_not_exempted_by_length():
    plan = "\n".join(f"{i}. Do sub-task number {i} of the report." for i in range(40))
    answer = f"{plan}\n\nExecuting step 1:"
    assert len(answer) > 1000
    assert _unfinished_answer_kind(answer) == "narration"


COMPLETED_ANSWERS = [
    pytest.param("Let me know if you want changes.", id="let-me-know"),
    pytest.param(
        "Done. I updated README.md and added tests/test_cli.py; all 12 tests "
        "pass.\n\nLet me know if you'd like any changes.",
        id="summary-then-let-me-know",
    ),
    pytest.param(
        "Updated the config loader to read TOYBOX_CONFIG.\n\n"
        "Next steps:\n- Run the full test suite\n- Tag a release",
        id="next-steps-heading-with-bullets",
    ),
    pytest.param(
        "Updated the config loader.\n\n**Next steps:**\n\n"
        "1. Run the full test suite.\n2. Tag a release.",
        id="bold-next-steps-heading-numbered",
    ),
    pytest.param(
        "## Summary\nAll four issues are labeled.\n\n## Next steps\n"
        "- Close #5 as a duplicate of #2",
        id="markdown-heading-next-steps",
    ),
    pytest.param(
        "I'll leave the config as-is since it already reads TOYBOX_CONFIG.",
        id="ill-leave-as-is",
    ),
    pytest.param(
        "The next step for you is to run `gaia init`.",
        id="next-step-for-user",
    ),
    pytest.param(
        "Here's the updated function:\n\n```python\ndef load():\n"
        "    return os.environ['TOYBOX_CONFIG']\n```",
        id="ends-with-code-block",
    ),
    pytest.param(
        "Tool calls use this shape:\n\n```json\n"
        '{"tool": "search", "tool_args": {"query": "x"}}\n```\n\n'
        "That is the format the agent expects.",
        id="documented-tool-json-in-fence",
    ),
    pytest.param(
        "Fetching the page returned 404, so I couldn't read the article.",
        id="fetching-outcome",
    ),
    pytest.param(
        "Labeled #4 as `question` and #5 as `duplicate`. All triaged :tada:",
        id="emoji-shortcode-colon",
    ),
    pytest.param(
        "The error was: `KeyError: 'TOYBOX_CONFIG'`",
        id="inline-code-with-colon",
    ),
    pytest.param("The backup job runs daily at 09:30.", id="clock-time"),
    pytest.param(
        "Weekly report:\n1. 14 issues opened\n2. 9 PRs merged\n"
        "3. Top contributor: alice",
        id="report-numbered-list",
    ),
    pytest.param(
        "I checked every open issue. Nothing else needs a label, so there is "
        "nothing more I need to do.",
        id="need-to-mid-sentence",
    ),
    pytest.param("", id="empty"),
]


@pytest.mark.parametrize("answer", COMPLETED_ANSWERS)
def test_completed_answers_are_not_caught(answer):
    assert _unfinished_answer_kind(answer) is None


# ---------------------------------------------------------------------------
# Loop integration
# ---------------------------------------------------------------------------


class _DummyAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "You are a test agent."

    def _register_tools(self) -> None:
        pass

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def agent():
    with patch("gaia.agents.base.agent.AgentSDK"):
        a = _DummyAgent(silent_mode=True, skip_lemonade=True)
        a.streaming = False
        return a


def _stub_chat(agent, *answers):
    responses = [json.dumps({"thought": "", "answer": a}) for a in answers]
    sent = []

    def _send(messages, *_, **__):
        sent.append([dict(m) for m in messages])
        resp = MagicMock()
        resp.text = responses.pop(0)
        resp.stats = {}
        return resp

    chat = MagicMock()
    chat.send_messages = MagicMock(side_effect=_send)
    agent.chat = chat
    return sent


def _final_text(result):
    return strip_verification_scope(result["result"]).strip()


NARRATION = "Now I need to update the README.md to document TOYBOX_CONFIG."


def test_narration_is_reprompted_then_answer_accepted(agent):
    sent = _stub_chat(agent, NARRATION, "README.md now documents TOYBOX_CONFIG.")

    result = agent.process_query("Document the env var", max_steps=10)

    assert len(sent) == 2
    assert "described your next step instead of doing it" in (sent[1][-1]["content"])
    assert _final_text(result) == "README.md now documents TOYBOX_CONFIG."


def test_tool_markup_is_reprompted_as_malformed_call(agent):
    markup = '<invoke name="load_tools"><parameter name="names">x</parameter>'
    sent = _stub_chat(agent, markup, "Loaded.")

    result = agent.process_query("Load the tools", max_steps=10)

    assert len(sent) == 2
    assert "tool call written out as text" in sent[1][-1]["content"]
    assert _final_text(result) == "Loaded."


def test_reprompts_are_bounded_per_turn(agent):
    answers = [f"Executing step {i}:" for i in range(5)]
    sent = _stub_chat(agent, *answers)

    result = agent.process_query("Do the task", max_steps=20)

    assert len(sent) == _MAX_UNFINISHED_ANSWER_REPROMPTS + 1
    assert _final_text(result) == f"Executing step {_MAX_UNFINISHED_ANSWER_REPROMPTS}:"


def test_no_reprompt_on_last_step(agent):
    sent = _stub_chat(agent, NARRATION)

    result = agent.process_query("Document the env var", max_steps=1)

    assert len(sent) == 1
    assert _final_text(result) == NARRATION


def test_completed_answer_is_not_reprompted(agent):
    sent = _stub_chat(agent, "Done. Let me know if you want changes.")

    result = agent.process_query("Do the task", max_steps=10)

    assert len(sent) == 1
    assert _final_text(result) == "Done. Let me know if you want changes."


@pytest.mark.parametrize(
    "answer",
    [
        "I apologize for the confusion. Let me explain what I would have done with prompt enhancement...",
        "Here is the fix. Let me clarify one detail: the config is optional.",
        "All three files are updated. Let me summarize the changes.",
    ],
)
def test_answer_style_let_me_phrases_are_not_narration(answer):
    assert _unfinished_answer_kind(answer) is None
