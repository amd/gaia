# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Classification of messages that explicitly ask for a tool."""

from __future__ import annotations

import pytest

pytest.importorskip("gaia_agent_chat")

from gaia_agent_chat.tool_requests import requested_tools  # noqa: E402

REGISTRY = {
    "run_shell_command",
    "get_shell_state",
    "browse_directory",
    "tree",
    "recall",
    "read_file",
}
SHELL = ["run_shell_command", "get_shell_state"]


@pytest.mark.parametrize(
    "message",
    [
        "Use your shell tool to run pwd and tell me the directory.",
        "pwd",
        "What's the current working directory?",
        "Which folder is the working dir?",
        "what directory am I in?",
        "Open a terminal and list the processes.",
        "Run this command: git status",
        "Can you do that from the command line?",
        "Try it in PowerShell.",
        "run ls in my home folder",
        "Run git status for me.",
        "Which directory are you in?",
        "Execute these commands one by one.",
        "Do it in bash.",
    ],
)
def test_shell_requests_ask_for_the_shell(message):
    assert requested_tools(message, REGISTRY) == SHELL


@pytest.mark.parametrize(
    "message",
    [
        "Hi, how are you?",
        "Summarize the attached report.",
        "Show me the directory tree of my project.",
        "What do you recall about my trip?",
        "How many items are in that directory?",
        "Explain how a seashell forms.",
        "The airport terminal was busy.",
        "In a nutshell, explain the history of bash scripting.",
        "Save it to the current folder.",
    ],
)
def test_ordinary_requests_ask_for_nothing(message):
    assert requested_tools(message, REGISTRY) == []


def test_a_tool_named_outright_is_requested():
    assert requested_tools("Call browse_directory on C:/tmp", REGISTRY) == [
        "browse_directory"
    ]


def test_a_named_tool_comes_before_the_shell_signal_and_is_not_duplicated():
    msg = "Use run_shell_command in the shell, then browse_directory."
    assert requested_tools(msg, REGISTRY) == [
        "run_shell_command",
        "browse_directory",
        "get_shell_state",
    ]


def test_only_registered_tools_are_returned():
    """A profile without the shell tools gets no shell request."""
    assert requested_tools("run pwd in the shell", {"read_file"}) == []
    assert requested_tools("call delete_everything now", REGISTRY) == []
