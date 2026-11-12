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


SPEECH_REGISTRY = REGISTRY | {"text_to_speech", "dictate_document"}


@pytest.mark.parametrize(
    "message",
    [
        "Dictate this document",
        "Turn this PDF into audio",
        "read report.pdf to me",
        "Make an audiobook of notes.md",
        "Save it as an MP3",
        "I'd like an audio version of the handbook",
    ],
)
def test_a_document_asked_for_as_audio_gets_the_dictation_tool_first(message):
    assert requested_tools(message, SPEECH_REGISTRY) == [
        "dictate_document",
        "text_to_speech",
    ]


@pytest.mark.parametrize(
    "message", ["Read this aloud", "Say it out loud", "Narrate the story of the fox"]
)
def test_speaking_text_asks_for_text_to_speech_alone(message):
    assert requested_tools(message, SPEECH_REGISTRY) == ["text_to_speech"]


@pytest.mark.parametrize(
    "message",
    [
        # Audio that already exists is the transcription tools' job.
        "Listen to the recording and summarize it",
        "Summarize the dictation transcript",
        "Convert this video to audio",
        "Save the transcript as an mp3",
        "Transcribe this audio",
        # Ordinary requests that share a word.
        "I dictated notes yesterday",
        "Convert it to PDF",
        "Read the file",
    ],
)
def test_requests_that_only_sound_like_speech_ask_for_nothing(message):
    assert requested_tools(message, SPEECH_REGISTRY) == []


def test_speech_tools_are_only_offered_where_registered():
    assert requested_tools("Dictate this document", REGISTRY) == []


def test_a_dictation_naming_its_output_file_does_not_ask_to_transcribe_it():
    msg = "Dictate report.pdf and save it as C:/out/report.mp3"
    assert requested_tools(msg, SPEECH_REGISTRY) == [
        "dictate_document",
        "text_to_speech",
    ]


@pytest.mark.parametrize(
    "message",
    [
        "Summarize standup.mp3",
        "Turn the meeting in standup.mp4 into audio",
        "Read the transcript of call.wav to me",
    ],
)
def test_an_existing_recording_still_asks_to_transcribe_beside_speech(message):
    found = requested_tools(message, SPEECH_REGISTRY)
    assert "transcribe_media" in found
    assert "dictate_document" not in found
