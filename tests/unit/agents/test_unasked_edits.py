# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A "change nothing else" edit that also removed a line is called out."""

import pytest

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
    assert not limits_edit("Use nothing else but tabs; reformat the whole file")


def test_common_words_do_not_hide_a_removed_line():
    request = (
        "In notes.txt change the title to Draft and add a line at the end. "
        "Change nothing else."
    )
    assert unasked(removed_lines("Call the bank", "Draft"), request) == [
        "Call the bank"
    ]


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

    twice = _agent(REQUEST)
    twice._note_edit_removals(edit, {"status": "success"})
    twice._note_edit_removals(edit, {"status": "success"})
    assert twice._turn_unasked_removals == [("todo.txt", "- eggs")]

    failed = _agent(REQUEST)
    failed._note_edit_removals(edit, {"status": "error", "error": "no match"})
    assert failed._turn_unasked_removals == []


def test_a_line_a_later_edit_puts_back_is_not_reported():
    agent = _agent(REQUEST)
    path = "C:/x/todo.txt"
    agent._note_edit_removals(
        {"file_path": path, "old_content": "- eggs", "new_content": "- apples"},
        {"status": "success"},
    )
    agent._note_edit_removals(
        {
            "file_path": path,
            "old_content": "- apples",
            "new_content": "- eggs\n- apples",
        },
        {"status": "success"},
    )
    assert agent._turn_unasked_removals == []


# An in-place edit the request asked for is not a removal, even though no word
# of the old line appears in the request.
IN_PLACE_EDITS = [
    (
        "Fix the typo in the first line of story.txt. Change nothing else.",
        "Teh quick brown fox jumps over the lazy dog.",
        "The quick brown fox jumps over the lazy dog.",
    ),
    (
        "Capitalize the heading in notes.md. Change nothing else.",
        "# shopping plan",
        "# Shopping Plan",
    ),
    (
        "Capitalize the heading in notes.md. Change nothing else.",
        "# intro",
        "# INTRO",
    ),
    (
        "Update the date to 2026-11-10 in report.md. Change nothing else.",
        "Due: 2025-03-14",
        "Due: 2026-11-10",
    ),
    (
        "Fix the grammar in the intro of essay.txt. Change nothing else.",
        "Me and him goes to the store every days.",
        "He and I go to the store every day.",
    ),
    (
        "Translate the greeting in page.txt into French. Change nothing else.",
        "Hello, welcome to our shop!",
        "Bonjour, bienvenue dans notre boutique !",
    ),
]


@pytest.mark.parametrize("request_text,old,new", IN_PLACE_EDITS)
def test_an_edit_asked_for_in_place_is_not_called_a_removal(request_text, old, new):
    agent = _agent(request_text)
    agent._note_edit_removals(
        {"file_path": "C:/x/f.txt", "old_content": old, "new_content": new},
        {"status": "success"},
    )
    assert agent._turn_unasked_removals == []


def test_the_gemma_replacement_is_still_called_out_next_to_in_place_edits():
    agent = _agent(REQUEST)
    agent._note_edit_removals(
        {
            "file_path": "C:/x/todo.txt",
            "old_content": "- milk\n- bread\n- eggs",
            "new_content": "- oat milk\n- bread\n- apples",
        },
        {"status": "success"},
    )
    assert agent._turn_unasked_removals == [("todo.txt", "- eggs")]


def test_a_deleted_line_with_a_near_twin_left_behind_is_still_caught():
    agent = _agent(REQUEST)
    agent._note_edit_removals(
        {
            "file_path": "C:/x/todo.txt",
            "old_content": "- egg box 1\n- egg box 2\n- milk",
            "new_content": "- egg box 2\n- oat milk",
        },
        {"status": "success"},
    )
    assert agent._turn_unasked_removals == [("todo.txt", "- egg box 1")]
