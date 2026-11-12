# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A question nobody answered is never read as the user's answer.

In rc18 an unanswered "What kind of cleanup?" returned the model's own default
as if the user had picked it; the agent moved 15 files and said "Done". The
model must hear that nobody answered, and a change made on that assumption
must still reach a person, even with Full access on.
"""

import pytest

from gaia.agents.base.call_risk import NEEDS_PERSON_ARG
from gaia.agents.base.tools import _TOOL_REGISTRY


class _Console:
    def __init__(self, reply="__NO_RESPONSE__"):
        self.reply = reply
        self.asked = []

    def request_user_input_blocking(self, **kwargs):
        self.asked.append(kwargs)
        return self.reply


@pytest.fixture
def agent():
    from gaia_agent.chat_agent import ChatAgent

    stub = object.__new__(ChatAgent)
    stub.console = _Console()
    stub.observers = []
    stub._unanswered_question = None
    saved = dict(_TOOL_REGISTRY)
    try:
        ChatAgent._register_loop_control_tools(stub)
        yield stub
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def _ask(**kwargs):
    return _TOOL_REGISTRY["request_user_input"]["function"](**kwargs)


CLEANUP = dict(
    message="What kind of cleanup do you want for la1?",
    choices=["Organize into subfolders by type", "Delete duplicates"],
    default_if_no_response="Organize into subfolders by type",
    timeout_seconds=300,
)


def test_a_timeout_with_a_default_says_nobody_answered(agent):
    result = _ask(**CLEANUP)

    assert result != CLEANUP["default_if_no_response"]
    assert result.startswith("__NO_RESPONSE__")
    assert "did not answer" in result
    # The default is named as an assumption the reply must own up to.
    assert "Organize into subfolders by type" in result
    assert "assum" in result
    # The console is never handed the default to return as an answer.
    assert agent.console.asked[-1]["default_if_no_response"] is None


def test_a_timeout_without_a_default_still_says_so(agent):
    result = _ask(message="Which repo?", timeout_seconds=60)

    assert result.startswith("__NO_RESPONSE__")
    assert "did not answer" in result
    assert agent._unanswered_question == "Which repo?"


def test_a_real_answer_matching_the_default_is_the_answer(agent):
    agent.console.reply = "Organize into subfolders by type"

    assert _ask(**CLEANUP) == "Organize into subfolders by type"
    assert agent._unanswered_question is None


@pytest.mark.parametrize(
    "tool_name, tool_args",
    [
        ("write_file", {"file_path": "C:/x/a.txt", "content": "x"}),
        ("edit_file", {"file_path": "C:/x/a.txt", "old": "a", "new": "b"}),
        ("run_shell_command", {"command": "move a.txt sub\\a.txt"}),
        ("run_python", {"code": "import shutil; shutil.move('a', 'b')"}),
    ],
)
def test_a_change_after_an_unanswered_question_needs_a_person(
    agent, tool_name, tool_args
):
    _ask(**CLEANUP)

    shown = agent.confirmation_args_for_call(tool_name, tool_args)

    assert shown.get(NEEDS_PERSON_ARG) is True


@pytest.mark.parametrize(
    "tool_name, tool_args",
    [
        ("read_file", {"file_path": "C:/x/a.txt"}),
        ("run_shell_command", {"command": "dir C:\\x"}),
    ],
)
def test_reading_after_an_unanswered_question_is_unchanged(agent, tool_name, tool_args):
    _ask(**CLEANUP)

    shown = agent.confirmation_args_for_call(tool_name, tool_args)

    assert NEEDS_PERSON_ARG not in (shown or {})


def test_a_change_after_an_answered_question_is_unchanged(agent):
    agent.console.reply = "Delete duplicates"
    _ask(**CLEANUP)

    shown = agent.confirmation_args_for_call(
        "write_file", {"file_path": "C:/x/a.txt", "content": "x"}
    )

    assert NEEDS_PERSON_ARG not in shown


def test_a_later_answer_lifts_the_hold(agent):
    _ask(**CLEANUP)
    agent.console.reply = "Delete duplicates"
    _ask(**CLEANUP)

    assert agent._unanswered_question is None
