# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A file the user names as the source of the content is never a save target.

Treating one as a target makes the completion check ask the model to
``write_file`` the user's input, overwriting it, and caveats a correct answer.
"""

import pytest

from gaia.agents.base.agent import _claims_file_write
from gaia.agents.base.completion import (
    CompletionEvidence,
    destination_paths,
    named_outputs,
    save_obligations,
)

# (request, the only files it asks to save)
SAVES = [
    ("Save the action items in minutes.txt as actions.md", ["actions.md"]),
    ("Save the action items at notes.txt as out.md", ["out.md"]),
    ("Save the notes in notes.txt to out.md", ["out.md"]),
    ("Save the notes in notes.txt into out.md", ["out.md"]),
    ("Save the notes in notes.txt in summary.md", ["summary.md"]),
    ("Save the table in data.csv in out/report.md", ["out/report.md"]),
    ("Save the action items in the minutes.txt file as actions.md", ["actions.md"]),
    ("Save the minutes in minutes.txt, as actions.md", ["actions.md"]),
    ("Save the items in a.txt and b.txt as c.md", ["c.md"]),
    ("Save the summary from report.pdf as summary.md", ["summary.md"]),
    ("Save the summary from report.pdf to summary.md", ["summary.md"]),
    ("Export the rows from data.csv as data.json", ["data.json"]),
    ("Store the rows using data.csv into db.json", ["db.json"]),
    ("Save the text of notes.txt as notes_clean.txt", ["notes_clean.txt"]),
    ("Save a copy of notes.txt as notes.bak", ["notes.bak"]),
    ("Save minutes.txt as actions.md", ["actions.md"]),
    ("Save minutes.txt → actions.md", ["actions.md"]),
    ("Save the action items in minutes.txt -> actions.md", ["actions.md"]),
    ("Save the action items in minutes.txt => actions.md", ["actions.md"]),
    ("Copy notes.txt to backup.txt", ["backup.txt"]),
    ("Extract the action items from minutes.txt into actions.md", ["actions.md"]),
    ("Put the action items from minutes.txt in actions.md", ["actions.md"]),
    ("Put the action items in minutes.txt into actions.md", ["actions.md"]),
    ("Write the summary of notes.txt to out.md", ["out.md"]),
    (
        "Save the action items in `my minutes.txt` as `action items.md`",
        ["action items.md"],
    ),
    (
        'Save the action items in "C:/My Docs/minutes.txt" as "C:/My Docs/actions.md"',
        ["C:/My Docs/actions.md"],
    ),
    (
        "Save the action items in 'D:\\team notes\\minutes.txt' as "
        "'D:\\team notes\\actions.md'",
        ["D:\\team notes\\actions.md"],
    ),
    # A source named with "from" is never a target, even with no target named.
    ("Save the action items from minutes.txt", []),
    ("Save the totals of data.csv", []),
    ("Save the summary using report.pdf", []),
    ("Save the action items in minutes.txt as a markdown file", []),
]


@pytest.mark.parametrize("request_text,expected", SAVES)
def test_only_the_destination_is_a_save_target(request_text, expected):
    assert save_obligations(request_text)[0] == expected
    assert named_outputs(request_text) == expected


# The file the user names as where to save is a target, wherever it sits.
TARGETS = [
    ("Save the totals in notes.txt", ["notes.txt"]),
    ("Save the edits in notes.txt", ["notes.txt"]),
    ("Save notes.txt", ["notes.txt"]),
    ("Save it in out.md", ["out.md"]),
    ("Save it in notes.txt as well", ["notes.txt"]),
    ("Save it in 'my notes.txt'", ["my notes.txt"]),
    ("Save the action items in minutes.txt in the morning", ["minutes.txt"]),
    ("Save the notes from the meeting in minutes.txt", ["minutes.txt"]),
    ("Update notes.txt with the totals and save it in notes.txt", ["notes.txt"]),
    # Updating a file in place: the source is also the named destination.
    ("Save the cleaned version of notes.txt to notes.txt", ["notes.txt"]),
    ("Save the totals from notes.txt back to notes.txt", ["notes.txt"]),
    ("Save the fixed rows in data.csv as data.csv", ["data.csv"]),
    # "save X as Y" saves Y; "write X and Y" saves both.
    ("Save the report as report.md and summary.md", ["report.md", "summary.md"]),
    ("Write the summary to a.md and b.md", ["a.md", "b.md"]),
    ("Save the tracker as a.xlsx and the email as b.md", ["a.xlsx", "b.md"]),
    # Two places, each with its own content, are both targets.
    ("Save the summary in a.md and the totals in b.md", ["a.md", "b.md"]),
    ("Save the summary in a.md and the totals to b.md", ["a.md", "b.md"]),
]


@pytest.mark.parametrize("request_text,expected", TARGETS)
def test_a_named_destination_is_still_a_target(request_text, expected):
    assert save_obligations(request_text)[0] == expected


@pytest.mark.parametrize(
    "request_text",
    [
        # A rename moves the file; the check never writes the old name back.
        "Rename draft.txt to final.txt",
        "Rename the file draft.txt to final.txt and save it",
        "Move draft.txt to archive/draft.txt",
    ],
)
def test_a_renamed_file_is_never_a_save_target(request_text):
    assert "draft.txt" not in save_obligations(request_text)[0]
    assert "draft.txt" not in named_outputs(request_text)


def test_the_answer_naming_the_source_claims_only_the_destination():
    sentence = "I saved the action items in minutes.txt as actions.md."
    assert destination_paths(sentence) == ["actions.md"]


def test_a_removal_from_a_folder_still_names_the_folder():
    # "from" stays a place in cleanup claims; only save requests drop sources.
    assert destination_paths("the scratch files from out/tmp/") == ["out/tmp/"]


def test_a_finished_answer_is_never_told_to_write_its_source(tmp_path):
    ledger = CompletionEvidence(
        "Save the action items in minutes.txt as actions.md", str(tmp_path)
    )
    source = {"file_path": "minutes.txt", "content": "Action: ship it"}
    ledger.delivered("read_file", {"file_path": "minutes.txt"}, source, source)
    ledger.record(
        "write_file", {"file_path": "actions.md"}, {"file_path": "actions.md"}, True
    )
    result = {"file_path": "actions.md", "content": "- ship it"}
    ledger.delivered("read_file", {"file_path": "actions.md"}, result, result)
    gaps = ledger.gaps("Saved the action items to actions.md.", _claims_file_write)
    assert gaps == []
    assert "minutes.txt" not in " ".join(gaps)
