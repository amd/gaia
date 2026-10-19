# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Permission prompts and notices describe a call in plain words (#5260)."""

import threading
import time

import pytest

from gaia.agents.base.tool_grants import grant_scope
from gaia.ui.event_narration import grant_phrase, tool_phrase
from gaia.ui.sse_handler import SSEOutputHandler


def _events(handler):
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    return events


def _permission_request(tool, args):
    """The ``permission_request`` a fresh handler emits for one call."""
    handler = SSEOutputHandler()
    t = threading.Thread(target=handler.confirm_tool_execution, args=(tool, args))
    t.start()
    deadline = time.time() + 5.0
    found = None
    while found is None and time.time() < deadline:
        with handler.event_queue.mutex:
            found = next(
                (
                    e
                    for e in handler.event_queue.queue
                    if e.get("type") == "permission_request"
                ),
                None,
            )
        time.sleep(0.02)
    handler.cancelled.set()
    t.join(timeout=3.0)
    assert found is not None, "no permission_request emitted"
    return found


@pytest.mark.parametrize(
    "tool,subject,expected",
    [
        ("write_file", "notes.txt", "writing notes.txt"),
        ("edit_file", "app.py", "editing app.py"),
        ("replace_function", "app.py", "replacing app.py"),
        ("install_skill", "weather", "installing weather"),
        ("triage_inbox", "x", None),
        ("write_file", "", None),
    ],
)
def test_grant_phrase(tool, subject, expected):
    assert grant_phrase(tool, subject) == expected


def test_tool_phrase_reuses_narration():
    assert tool_phrase("write_file") == "writing file"
    assert tool_phrase("run_shell_command") == "running a shell command"


def test_path_grant_names_its_file_as_subject():
    scope = grant_scope("write_file", {"file_path": "C:/work/prompt-test.txt"})
    assert scope.subject == "prompt-test.txt"
    # The machine label is what the MCP control matches, so it is unchanged.
    assert scope.label == "write_file on prompt-test.txt"


def test_shell_grant_has_no_subject():
    assert grant_scope("run_shell_command", {"command": "git status"}).subject == ""


def test_permission_request_carries_plain_wording():
    event = _permission_request("write_file", {"file_path": "/tmp/prompt-test.txt"})
    assert event["tool"] == "write_file"
    assert event["tool_label"] == "writing file"
    assert event["always_scope"] == "write_file on prompt-test.txt"
    assert event["always_label"] == "writing prompt-test.txt"


def test_shell_permission_request_keeps_the_command_as_its_scope():
    event = _permission_request("run_shell_command", {"command": "git status"})
    assert event["always_scope"] == "git status"
    assert "always_label" not in event


def test_full_access_notice_names_no_tool():
    handler = SSEOutputHandler()
    handler.auto_approve_gated_tools = True
    assert handler.confirm_tool_execution("write_file", {"file_path": "a.txt"})
    messages = [e["message"] for e in _events(handler) if e.get("type") == "status"]
    assert messages == [
        "Full access is on, so GAIA didn't ask first. Writing file: a.txt"
    ]


def test_accept_edits_notice_names_no_tool(tmp_path):
    handler = SSEOutputHandler()
    handler.accept_edits = True
    handler.edit_roots = (str(tmp_path),)
    target = str(tmp_path / "a.txt")
    assert handler.confirm_tool_execution("edit_file", {"file_path": target})
    messages = [e["message"] for e in _events(handler) if e.get("type") == "status"]
    assert len(messages) == 1
    assert messages[0].startswith(
        "Accept edits is on, so GAIA didn't ask first. Editing file: "
    )
    assert "edit_file" not in messages[0]
