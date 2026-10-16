# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An empty file the user asked for is written; a stand-in for real content is not."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent, _plan_step_placeholder, _write_stand_in
from gaia.agents.base.tools import _TOOL_REGISTRY, tool

EMPTY = ["", "\n", "   ", " \n\t\n"]


@pytest.mark.parametrize("content", EMPTY)
@pytest.mark.parametrize(
    "file_path,request_text",
    [
        ("notes.txt", "Create an empty notes.txt"),
        ("notes.txt", "create an empty notes.txt please"),
        ("NOTES.TXT", "Create an empty notes.txt"),
        ("C:/w/notes.txt", "Create an empty notes.txt in C:/w"),
        ("C:\\w\\notes.txt", "Create an empty file C:\\w\\notes.txt"),
        ("notes.txt", "Make a blank file called notes.txt"),
        ("notes.txt", "Make a new blank text file named notes.txt."),
        ("todo.md", "touch todo.md"),
        ("notes.txt", "Create notes.txt (empty)"),
        ("notes.txt", "Create notes.txt, empty for now"),
        ("notes.txt", "Leave notes.txt blank"),
        ("notes.txt", "Add an empty notes.txt to the repo"),
        ("notes.txt", "Create an empty notes.txt with nothing in it"),
        ("notes.txt", "Create an empty notes.txt and a report.md with the summary"),
        ("data/log.csv", "Create an empty log.csv in data/"),
        ("b.txt", "Create empty files a.txt, b.txt and c.txt"),
        ("c.txt", "Create empty files a.txt, b.txt and c.txt"),
        ("my notes.txt", "Create an empty `my notes.txt`"),
        ("C:/My Docs/notes.txt", 'Create an empty "C:/My Docs/notes.txt"'),
        ("b.txt", "Summarize report.pdf. Then create an empty b.txt."),
    ],
)
def test_an_empty_file_the_user_asked_for_is_written(file_path, request_text, content):
    args = {"file_path": file_path, "content": content}
    assert _write_stand_in("write_file", args, request_text) is None
    assert _plan_step_placeholder("write_file", args, request=request_text) is None


@pytest.mark.parametrize("content", EMPTY + [None])
@pytest.mark.parametrize(
    "file_path,request_text",
    [
        ("notes.txt", None),
        ("notes.txt", ""),
        ("notes.txt", "Create notes.txt with the meeting summary"),
        ("notes.txt", "Write the summary to notes.txt. The old file is empty."),
        ("notes.txt", "Is notes.txt empty?"),
        ("notes.txt", "Check whether notes.txt is blank"),
        ("notes.txt", "notes.txt is empty, write the summary into it"),
        ("notes.txt", "Don't leave notes.txt empty."),
        ("notes.txt", "Fill the empty notes.txt with the summary"),
        ("notes.txt", "Put the summary in the empty notes.txt"),
        ("notes.txt", "Add the totals to the blank notes.txt"),
        ("notes.txt", "Create an empty notes.txt with the action items"),
        ("other.txt", "Create an empty notes.txt"),
        ("notes.txt.bak", "Create an empty notes.txt"),
        # The empty request covers one file, not the next one with content.
        ("report.md", "Create an empty notes.txt and a report.md with the summary"),
        ("a.txt", "Create a.txt and an empty b.txt"),
        ("a.txt", "Create a.txt with the totals, and an empty b.txt"),
        (
            "summary.md",
            "Read the empty-lines report and save the summary to summary.md",
        ),
    ],
)
def test_an_empty_write_nobody_asked_for_is_refused(file_path, request_text, content):
    args = {"file_path": file_path, "content": content}
    assert _write_stand_in("write_file", args, request_text) == ("content", content)


@pytest.mark.parametrize(
    "content",
    [
        "PLACEHOLDER",
        "TODO",
        "TODO: fill in",
        "TODO - fill this in later",
        "todo: fill me in",
        "Fill in later.",
        "[Content goes here]",
        "Your content here",
        "Insert content here",
        "Add content here.",
        "<content>",
        "...",
    ],
)
@pytest.mark.parametrize(
    "request_text",
    [
        "Write the meeting summary to notes.txt",
        # Asking for an empty file never licenses a stand-in word.
        "Create an empty notes.txt",
    ],
)
def test_a_stand_in_for_content_is_still_refused(content, request_text):
    args = {"file_path": "notes.txt", "content": content}
    assert _write_stand_in("write_file", args, request_text) is not None


@pytest.mark.parametrize(
    "content",
    [
        "TODO: write the intro\n",
        "Fill in the form at the bottom of the page and mail it to HR.",
        "Content here is licensed under MIT.",
        "# TODO\nprint(1)\n",
    ],
)
def test_real_content_mentioning_a_todo_is_written(content):
    args = {"file_path": "notes.md", "content": content}
    assert _write_stand_in("write_file", args, "Write notes.md") is None


@pytest.mark.parametrize(
    "file_path", ["pkg/__init__.py", "logs/.gitkeep", "site/.nojekyll", "py.typed"]
)
def test_a_scaffold_file_is_written_empty_without_being_asked(file_path):
    args = {"file_path": file_path, "content": ""}
    assert _write_stand_in("write_file", args, "Set up the package") is None


class _WriterAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        agent = self

        @tool
        def write_file(file_path: str, content: str) -> dict:
            """Write a file."""
            agent.writes.append((file_path, content))
            return {"status": "success", "file_path": file_path}


@pytest.fixture
def writer():
    snapshot = dict(_TOOL_REGISTRY)
    with patch("gaia.agents.base.agent.AgentSDK"):
        built = _WriterAgent(silent_mode=True, skip_lemonade=True)
    built.writes = []
    built.streaming = False
    built.verification_scope_statement = lambda: None
    built._tool_requires_confirmation = lambda *a, **kw: False
    yield built
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _script(agent, *replies):
    queue = list(replies)
    sent = []

    def _reply(*_, **kwargs):
        sent.append([dict(m) for m in kwargs.get("messages", [])])
        return SimpleNamespace(text=queue.pop(0), stats={})

    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})
    chat.send_messages = MagicMock(side_effect=_reply)
    agent.chat = chat
    return sent


def _write(path, content):
    return json.dumps(
        {"tool": "write_file", "tool_args": {"file_path": path, "content": content}}
    )


def test_create_an_empty_file_runs_once_in_the_loop(writer):
    sent = _script(
        writer, _write("notes.txt", ""), json.dumps({"answer": "Created notes.txt."})
    )
    writer.process_query("Create an empty notes.txt")
    assert writer.writes == [("notes.txt", "")]
    assert not any("NOT run" in str(m.get("content")) for m in sent[-1])


def test_an_empty_write_for_requested_content_is_refused_in_the_loop(writer):
    summary = "The team ships on Friday.\n"
    sent = _script(
        writer,
        _write("notes.txt", ""),
        _write("notes.txt", summary),
        json.dumps({"answer": "Wrote the summary to notes.txt."}),
    )
    writer.process_query("Write a one-line summary of the plan to notes.txt")
    assert writer.writes == [("notes.txt", summary)]
    assert any("NOT run" in str(m.get("content")) for m in sent[1])


def test_the_previous_turn_does_not_license_an_empty_write(writer):
    _script(
        writer,
        _write("notes.txt", ""),
        json.dumps({"answer": "Created notes.txt."}),
        _write("notes.txt", ""),
        json.dumps({"answer": "Done."}),
    )
    writer.process_query("Create an empty notes.txt")
    writer.process_query("Now put the meeting summary in notes.txt")
    assert writer.writes == [("notes.txt", "")]
