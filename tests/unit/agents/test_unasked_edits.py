# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A "change nothing else" edit that also removed a line is called out."""

from gaia.agents.base.agent import Agent
from gaia.agents.base.unasked_edits import (
    limits_edit,
    removal_note,
    removed_lines,
    unasked,
)

REQUEST = (
    'In todo.txt change "milk" to "oat milk" and add "- apples" at the end. '
    "Change nothing else."
)


def test_the_requests_that_limit_an_edit():
    assert limits_edit(REQUEST)
    assert limits_edit("Fix the typo and don't touch anything else")
    assert limits_edit("Update the title, leave the rest as is")
    assert not limits_edit("Rewrite the intro so it reads better")


def test_the_gemma_edit_that_replaced_eggs_is_caught():
    # The second of Gemma's two edits on rc16: "- eggs" became "- apples".
    removed = removed_lines("- eggs", "- apples")
    assert unasked(removed, REQUEST) == ["- eggs"]


def test_a_line_the_request_names_is_not_flagged():
    removed = removed_lines("- milk", "- oat milk")
    assert removed == ["- milk"]
    assert unasked(removed, REQUEST) == []


def test_an_append_removes_nothing():
    assert removed_lines("- eggs", "- eggs\n- apples") == []


def test_the_note_names_the_line_unless_the_answer_already_does():
    removals = [("todo.txt", "- eggs")]
    claimed = 'I changed "milk" to "oat milk" and appended "- apples".'
    note = removal_note(removals, claimed)
    assert "`- eggs` from todo.txt" in note
    assert removal_note(removals, "I replaced eggs with apples.") == ""


class _Bare(Agent):
    def _register_tools(self):
        pass


def _agent(request):
    agent = _Bare.__new__(_Bare)
    agent._original_user_input = request
    agent._turn_unasked_removals = []
    return agent


def test_the_agent_keeps_unasked_removals_only_for_limited_requests():
    edit = {
        "file_path": "C:/x/todo.txt",
        "old_content": "- eggs",
        "new_content": "- apples",
    }

    limited = _agent(REQUEST)
    limited._note_edit_removals(edit, {"status": "success"})
    assert limited._turn_unasked_removals == [("todo.txt", "- eggs")]

    open_ended = _agent("Tidy up todo.txt however you like.")
    open_ended._note_edit_removals(edit, {"status": "success"})
    assert open_ended._turn_unasked_removals == []

    failed = _agent(REQUEST)
    failed._note_edit_removals(edit, {"status": "error", "error": "no match"})
    assert failed._turn_unasked_removals == []
