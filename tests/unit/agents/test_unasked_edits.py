# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A "change nothing else" edit that also removed a line is called out."""

import os
import time

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


TODO_PATH = os.path.abspath("C:/x/todo.txt")


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
    assert limited._turn_unasked_removals == [(TODO_PATH, "- eggs")]

    open_ended = _agent("Tidy up todo.txt however you like.")
    open_ended._note_edit_removals(edit, {"status": "success"})
    assert open_ended._turn_unasked_removals == []

    twice = _agent(REQUEST)
    twice._note_edit_removals(edit, {"status": "success"})
    twice._note_edit_removals(edit, {"status": "success"})
    assert twice._turn_unasked_removals == [(TODO_PATH, "- eggs")]

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


LIMIT = " Change nothing else."
PORT = "Set the port to 9090 in config.ini." + LIMIT
RELEASE = "Update the version to 2.0 in release.md." + LIMIT

# Edits that did what a "change nothing else" request asked: no note.
CORRECT_EDITS = [
    (
        "Fix the typo in the first line of story.txt." + LIMIT,
        "Teh quick brown fox jumps over the lazy dog.",
        "The quick brown fox jumps over the lazy dog.",
    ),
    (
        "Capitalize the heading in notes.md." + LIMIT,
        "# shopping plan",
        "# Shopping Plan",
    ),
    ("Capitalize the heading in notes.md." + LIMIT, "# intro", "# INTRO"),
    (
        "Update the date to 2026-11-10 in report.md." + LIMIT,
        "Due: 2025-03-14",
        "Due: 2026-11-10",
    ),
    (
        "Fix the grammar in the intro of essay.txt." + LIMIT,
        "Me and him goes to the store every days.",
        "He and I go to the store every day.",
    ),
    (
        "Translate the greeting in page.txt into French." + LIMIT,
        "Hello, welcome to our shop!",
        "Bonjour, bienvenue dans notre boutique !",
    ),
    ("Change the title to Final in notes.md." + LIMIT, "# Draft\n- a", "# Final\n- a"),
    (REQUEST, "- milk\n- eggs", "- oat milk\n- eggs\n- apples"),
    (PORT, "host = local\nport = 8080", "host = local\nport = 9090"),
    (
        "Bump the version to 1.3.0 in setup.cfg." + LIMIT,
        "version = 1.2.0",
        "version = 1.3.0",
    ),
    (
        "Fix the spelling in readme.txt." + LIMIT,
        "Recieve the pakage tomorow.",
        "Receive the package tomorrow.",
    ),
    (
        "Rephrase the second sentence to sound friendlier." + LIMIT,
        "Hi.\nSubmit the form by Friday.",
        "Hi.\nPlease send us the form whenever you can before Friday.",
    ),
    (
        "Convert the list in notes.md to a numbered list." + LIMIT,
        "- buy milk\n- call mom",
        "1. buy milk\n2. call mom",
    ),
    ("Add '- apples' at the end of todo.txt." + LIMIT, "- milk", "- milk\n- apples"),
    ("Delete '- eggs' from todo.txt." + LIMIT, "- milk\n- eggs", "- milk"),
    (
        "Mark the first task as done." + LIMIT,
        "[ ] Pay rent\n[ ] Buy stamps",
        "[x] Pay rent\n[ ] Buy stamps",
    ),
    ("Change the greeting to 'Hi there'." + LIMIT, "Hello world", "Hi there"),
    (
        "Fix the indentation in app.py." + LIMIT,
        "def f():\n  return x",
        "def f():\n    return x",
    ),
    (
        "Rename the function load to load_config in app.py." + LIMIT,
        "def load(path):",
        "def load_config(path):",
    ),
    (
        "Update the copyright year to 2026." + LIMIT,
        "Copyright 2025 Acme",
        "Copyright 2026 Acme",
    ),
    (
        "Translate the menu into Spanish." + LIMIT,
        "Coffee - 3 dollars",
        "Cafe - 3 dolares",
    ),
    ("Correct the heading." + LIMIT, "# Weekyl Reprot", "# Weekly Report"),
    ("Make the heading uppercase." + LIMIT, "## next steps", "## NEXT STEPS"),
    (
        "Move the meeting to Friday." + LIMIT,
        "Team sync on Monday at 10",
        "Team sync on Friday at 10",
    ),
    (
        "Change the owner to Dana. Don't touch anything else.",
        "Assignee: Sam",
        "Assignee: Dana",
    ),
    (
        "Reword the warning so it is less alarming. Leave the rest as is.",
        "DANGER: never press this button!\nVersion 4",
        "Please avoid pressing this button.\nVersion 4",
    ),
]

# Edits that also changed a line nobody asked about: the note names it.
UNASKED_CHANGES = [
    (REQUEST, "- milk\n- eggs", "- oat milk\n- apples", "- eggs"),
    (REQUEST, "- milk\n- eggs", "- oat milk\n- bread\n- apples", "- eggs"),
    (REQUEST, "- milk\n- sugar", "- oat milk\n- honey\n- apples", "- sugar"),
    (
        REQUEST,
        "- milk\n- [ ] Pay rent",
        "- oat milk\n- [ ] Buy stamps",
        "- [ ] Pay rent",
    ),
    (
        "Fix the typo in the title of tasks.md." + LIMIT,
        "# Tsaks\n1. Call mom\n2. Pay rent",
        "# Tasks\n1. Call mom\n2. Buy stamps",
        "2. Pay rent",
    ),
    (
        "Mark laundry as done." + LIMIT,
        "[ ] laundry\n[ ] dishes",
        "[x] laundry\n[ ] groceries",
        "[ ] dishes",
    ),
    (
        PORT,
        "port = 8080\nssl_verify = true",
        "port = 9090\nssl_verify = false",
        "ssl_verify = true",
    ),
    (
        PORT,
        "port = 8080\nenabled = true",
        "port = 9090\nenabled = false",
        "enabled = true",
    ),
    (PORT, "port = 8080\ndebug: off", "port = 9090\ndebug: on", "debug: off"),
    (
        PORT,
        "port = 8080\nmax_retries = 3",
        "port = 9090\nmax_retries = 5",
        "max_retries = 3",
    ),
    (
        PORT,
        "port = 8080\nversion = 1.2.0",
        "port = 9090\nversion = 1.3.0",
        "version = 1.2.0",
    ),
    (
        "Change the timeout to 60." + LIMIT,
        "timeout: 30\nauth: required",
        "timeout: 60\nauth: none",
        "auth: required",
    ),
    (
        "Rename the variable count to total in app.py." + LIMIT,
        "count = 0\ncheck_auth(user)",
        "total = 0\nparse(data)",
        "check_auth(user)",
    ),
    (
        RELEASE,
        "Version 1.9\nDo not deploy on weekends.",
        "Version 2.0\nDeploying on weekends is fine.",
        "Do not deploy on weekends.",
    ),
    (
        RELEASE,
        "Version 1.9\nDo not deploy on weekends.",
        "Version 2.0\nDeploy on weekends.",
        "Do not deploy on weekends.",
    ),
    (
        RELEASE,
        "Version 1.9\nDo not deploy on weekends.",
        "Version 2.0\nAlways deploy on weekends.",
        "Do not deploy on weekends.",
    ),
    (
        "Fix the typo in the heading of policy.md." + LIMIT,
        "# Polcy\nDo not deploy on weekends.",
        "# Policy\nDeploys are fine any day.",
        "Do not deploy on weekends.",
    ),
    (
        "Fix the typo in line 1." + LIMIT,
        "Teh plan\nBudget: 500",
        "The plan\nBudget: 5000",
        "Budget: 500",
    ),
    (
        "Delete '- eggs' from todo.txt." + LIMIT,
        "- milk\n- eggs\n- bread",
        "- milk",
        "- bread",
    ),
    (
        "Translate the greeting into French." + LIMIT,
        "Hello!\nPrice: 10 EUR",
        "Bonjour !\nPrice: 12 EUR",
        "Price: 10 EUR",
    ),
    (
        "Rewrite the intro to be shorter." + LIMIT,
        "Intro: a long and winding text\nDeadline: 2026-03-01",
        "Intro: short.\nDeadline: 2026-04-01",
        "Deadline: 2026-03-01",
    ),
    (
        "Capitalize the heading." + LIMIT,
        "# plan\nOwner: Sam",
        "# Plan\nOwner: Alex",
        "Owner: Sam",
    ),
    (
        "Fix the grammar in the first sentence." + LIMIT,
        "Me and him goes there.\nThe API key expires in 30 days.",
        "He and I go there.\nThe API key never expires.",
        "The API key expires in 30 days.",
    ),
    (
        "Set DEBUG to False in settings.py." + LIMIT,
        'DEBUG = True\nALLOWED_HOSTS = ["example.com"]',
        'DEBUG = False\nALLOWED_HOSTS = ["*"]',
        'ALLOWED_HOSTS = ["example.com"]',
    ),
    (
        "Rename the target clean to tidy in Makefile." + LIMIT,
        "clean:\nrm -rf build",
        "tidy:\nrm -rf /",
        "rm -rf build",
    ),
    (
        "Change the room to B12." + LIMIT,
        "Room: A4\nMeeting at 10am",
        "Room: B12\nMeeting at 11am",
        "Meeting at 10am",
    ),
    pytest.param(
        "Change the subject to Q3 invoice." + LIMIT,
        "Subject: invoice\nSend invoice to alice@example.com",
        "Subject: Q3 invoice\nSend invoice to bob@example.com",
        "Send invoice to alice@example.com",
        marks=pytest.mark.xfail(
            strict=True, reason="a line sharing a word with the request reads as named"
        ),
    ),
]


def _recorded(request_text, old, new):
    agent = _agent(request_text)
    agent._note_edit_removals(
        {"file_path": "C:/x/f.txt", "old_content": old, "new_content": new},
        {"status": "success"},
    )
    return [line for _, line in agent._turn_unasked_removals]


@pytest.mark.parametrize("request_text,old,new", CORRECT_EDITS)
def test_an_edit_that_did_what_was_asked_gets_no_note(request_text, old, new):
    assert _recorded(request_text, old, new) == []


@pytest.mark.parametrize("request_text,old,new,lost", UNASKED_CHANGES)
def test_a_line_changed_without_being_asked_is_called_out(request_text, old, new, lost):
    assert lost in _recorded(request_text, old, new)


def test_the_corpora_cover_enough_cases():
    assert len(CORRECT_EDITS) >= 24
    assert len(UNASKED_CHANGES) >= 27


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
    assert agent._turn_unasked_removals == [(TODO_PATH, "- eggs")]


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
    assert agent._turn_unasked_removals == [(TODO_PATH, "- egg box 1")]


ASKED_DELETIONS = [
    (
        "Delete line 3." + LIMIT,
        "alpha one\nbeta two\ngamma three",
        "alpha one\nbeta two",
    ),
    ("Remove the last item. Don't touch anything else.", "- milk\n- eggs", "- milk"),
    ("Remove the second item." + LIMIT, "- milk\n- eggs\n- bread", "- milk\n- bread"),
    (
        "Remove all comments." + LIMIT,
        "x = 1  # set x\n# helper\ny = 2",
        "x = 1\ny = 2",
    ),
    (
        "Delete the completed tasks, leave the rest as is.",
        "- [x] laundry\n- [ ] dishes",
        "- [ ] dishes",
    ),
    (
        "Get rid of the duplicates." + LIMIT,
        "- milk\n- eggs\n- milk again",
        "- milk\n- eggs",
    ),
]


@pytest.mark.parametrize("request_text,old,new", ASKED_DELETIONS)
def test_a_deletion_the_request_asked_for_gets_no_note(request_text, old, new):
    assert _recorded(request_text, old, new) == []


def test_a_deletion_request_still_catches_a_line_swapped_for_new_text():
    assert _recorded(
        "Delete line 3." + LIMIT, "- milk\n- eggs", "- milk\n- apples"
    ) == ["- eggs"]


RESHAPED_EDITS = [
    (
        "Merge the two lines into one." + LIMIT,
        "first half of\nthe sentence",
        "first half of the sentence",
    ),
    (
        "Split the line at the comma." + LIMIT,
        "alpha beta, gamma delta",
        "alpha beta,\ngamma delta",
    ),
    (
        "Wrap the paragraph at 40 columns." + LIMIT,
        "The quick brown fox jumps over the lazy dog and keeps running far away",
        "The quick brown fox jumps over the lazy\ndog and keeps running far away",
    ),
    (
        "Fix the off-by-one bug." + LIMIT,
        "for i in range(1, n):",
        "for i in range(0, n):",
    ),
    (
        "Change the loop to use enumerate." + LIMIT,
        "for i in range(len(xs)):\n    print(i, xs[i])",
        "for i, x in enumerate(xs):\n    print(i, x)",
    ),
]


@pytest.mark.parametrize("request_text,old,new", RESHAPED_EDITS)
def test_a_line_reshaped_or_fixed_in_place_gets_no_note(request_text, old, new):
    assert _recorded(request_text, old, new) == []


def test_a_large_edit_is_skipped_quickly():
    old = "\n".join(f"row {i} alpha{i} beta{i * 7}" for i in range(2000))
    new = "\n".join(f"line {i} gamma{i} delta{i * 3}" for i in range(2000))
    start = time.perf_counter()
    assert _recorded(PORT, old, new) == []
    assert time.perf_counter() - start < 1.0


def test_naming_the_line_without_owning_up_keeps_the_note():
    removals = [("todo.txt", "- eggs")]
    assert removal_note(removals, "I appended apples; eggs is still there.")
    assert removal_note([("todo.txt", "- tea")], "I updated the steam settings.")
    assert removal_note(removals, "Done. I took out eggs.") == ""
    assert removal_note(removals, "I overwrote the eggs line.") == ""


def test_removals_are_kept_per_file_not_per_name():
    agent = _agent(PORT)
    agent._note_edit_removals(
        {
            "file_path": "C:/a/config.ini",
            "old_content": "port=8080\nhost=example",
            "new_content": "port=9090",
        },
        {"status": "success"},
    )
    agent._note_edit_removals(
        {
            "file_path": "C:/b/config.ini",
            "old_content": "[x]",
            "new_content": "[x]\nhost=example",
        },
        {"status": "success"},
    )
    assert agent._turn_unasked_removals == [
        (os.path.abspath("C:/a/config.ini"), "host=example")
    ]


def test_a_restore_that_differs_in_case_or_spacing_clears_the_removal():
    agent = _agent(PORT)
    path = "C:/a/config.ini"
    agent._note_edit_removals(
        {
            "file_path": path,
            "old_content": "port=8080\nhost=example",
            "new_content": "port=9090",
        },
        {"status": "success"},
    )
    agent._note_edit_removals(
        {
            "file_path": path,
            "old_content": "port=9090",
            "new_content": "port=9090\n  Host=Example ",
        },
        {"status": "success"},
    )
    assert agent._turn_unasked_removals == []


def test_more_phrasings_limit_an_edit():
    assert limits_edit("Set the port, don\u2019t change anything else.")
    assert limits_edit("Only change the port, keep everything else the same.")
    assert limits_edit("Set the port; nothing else should change.")
    assert limits_edit("Set the port. Don't change other lines.")
    assert limits_edit("Set the port and don't change any other line.")


def test_the_note_is_short_grouped_and_counts_what_it_leaves_out():
    removals = [("C:/x/todo.txt", "- eggs"), ("C:/x/todo.txt", "x" * 300)]
    removals += [("C:/x/a.txt", "`code` here")]
    removals += [("C:/x/b.txt", f"line {i} stuff") for i in range(4)]
    note = removal_note(removals, "Done.")
    assert "`- eggs`, `" + "x" * 79 + "\u2026` from todo.txt" in note
    assert "`` `code` here `` from a.txt" in note
    assert note.endswith("and 2 more lines, which the request did not ask to change.")
    assert note.count("\n") == 0


def test_the_note_tells_apart_files_that_share_a_name():
    note = removal_note(
        [("C:/a/config.ini", "host=example"), ("C:/b/config.ini", "port=1")], "Done."
    )
    assert f"from a{os.sep}config.ini" in note
    assert f"from b{os.sep}config.ini" in note
