# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Classification of messages that explicitly ask for a tool."""

from __future__ import annotations

import pytest

pytest.importorskip("gaia_agent")

from gaia_agent.tool_requests import requested_tools  # noqa: E402

REGISTRY = {
    "run_shell_command",
    "get_shell_state",
    "browse_directory",
    "tree",
    "recall",
    "read_file",
    "analyze_image",
    "transcribe_media",
}
SHELL = ["run_shell_command"]


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


@pytest.mark.parametrize(
    ("message", "tool"),
    [
        ("What is in C:/Users/me/Pictures/receipt.PNG?", "analyze_image"),
        ("describe photo.jpeg for me", "analyze_image"),
        ("Summarize standup.mp3", "transcribe_media"),
        ("who speaks first in ~/calls/review.mp4", "transcribe_media"),
    ],
)
def test_a_named_picture_or_recording_asks_for_its_tool(message, tool):
    assert requested_tools(message, REGISTRY) == [tool]


def test_a_file_any_tool_can_read_asks_for_nothing():
    assert requested_tools("Summarize report.pdf and notes.md", REGISTRY) == []
    assert requested_tools("the .mp3s are fine, .wavy lines too", REGISTRY) == []


def test_a_tool_named_outright_is_requested():
    assert requested_tools("Call browse_directory on C:/tmp", REGISTRY) == [
        "browse_directory"
    ]


def test_a_named_tool_comes_before_the_shell_signal_and_is_not_duplicated():
    msg = "Use run_shell_command in the shell, then browse_directory."
    assert requested_tools(msg, REGISTRY) == [
        "run_shell_command",
        "browse_directory",
    ]


def test_only_registered_tools_are_returned():
    """A profile without the shell tools gets no shell request."""
    assert requested_tools("run pwd in the shell", {"read_file"}) == []
    assert requested_tools("call delete_everything now", REGISTRY) == []


@pytest.mark.parametrize(
    "message",
    [
        "Can you change your voice?",
        "Use a different voice.",
        "Switch to a British voice.",
        "Speak a bit slower please.",
        "Talk louder.",
        "Can you be quieter?",
        "Turn the volume down.",
        "Turn off the captions.",
        "Stop using the wake word.",
    ],
)
def test_requests_about_her_own_voice_ask_for_the_setting_tool(message):
    registry = REGISTRY | {"set_voice_setting", "text_to_speech"}
    assert "set_voice_setting" in requested_tools(message, registry)


@pytest.mark.parametrize(
    "message",
    [
        "What is the volume of a cylinder?",
        "Add captions to this video file.",
        "She has a lovely voice.",
        "Speed up this query.",
        "Check my email.",
    ],
)
def test_other_requests_do_not_ask_for_the_setting_tool(message):
    registry = REGISTRY | {"set_voice_setting", "text_to_speech"}
    assert "set_voice_setting" not in requested_tools(message, registry)


def test_the_setting_tool_is_not_offered_where_it_is_not_registered():
    assert requested_tools("Change your voice.", REGISTRY) == []
