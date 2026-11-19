# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Parsing and framing of ``/<skill-name> request`` (gaia.skills.command)."""

import pytest

from gaia.skills.command import (
    SkillCommand,
    parse_skill_command,
    skill_command_message,
)


@pytest.mark.parametrize(
    "text, name, typed",
    [
        ("/pdf", "pdf", ""),
        ("/pdf   make a report  ", "pdf", "make a report"),
        (
            "/web-research\nfind the docs\nand cite them",
            "web-research",
            "find the docs\nand cite them",
        ),
        ("/pdf\tx", "pdf", "x"),
    ],
)
def test_parses_a_command(text, name, typed):
    command = parse_skill_command(text)
    assert command == SkillCommand(name=name, request=typed)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "pdf",
        "/",
        "/Pdf",
        "/tmp/x.txt",
        "/pdf.",
        "/-pdf",
        "/pdf--x",
        "say /pdf",
        " /pdf",
    ],
)
def test_rejects_text_that_is_not_a_command(text):
    assert parse_skill_command(text) is None


def test_keeps_host_notes_in_front():
    lead = "[This chat is working in the folder /w/[x]. Here.]\n\n"
    command = parse_skill_command(lead + "/pdf go")
    assert command == SkillCommand(name="pdf", request="go", lead=lead)


def test_success_names_the_skill_its_folder_and_warnings():
    message = skill_command_message(
        SkillCommand("pdf", "make a report"),
        {"status": "success", "directory": "/s/pdf", "warning": "No reportlab."},
    )
    assert message == (
        "[GAIA: the user typed /pdf to run the pdf skill. It is loaded and its "
        "instructions are in your system prompt. Follow them for this request. "
        "Paths in those instructions are relative to /s/pdf. No reportlab.]"
        "\n\nmake a report"
    )


def test_failure_relays_the_reason_and_forbids_the_task():
    message = skill_command_message(
        SkillCommand("pdf", "make a report", lead="[note]\n\n"),
        {"status": "error", "error": "Skill 'pdf' needs a larger model."},
    )
    assert message.startswith("[note]\n\n[GAIA: the user typed /pdf")
    assert "needs a larger model." in message
    assert "Do not attempt their request without the skill." in message
    assert message.endswith("Their request was: make a report")


def test_an_arabic_request_is_kept_whole():
    command = parse_skill_command("/pdf لخّص هذا الملف في صفحة واحدة")
    assert command == SkillCommand(name="pdf", request="لخّص هذا الملف في صفحة واحدة")


@pytest.mark.parametrize(
    "text",
    [
        # A pasted document or quoted reply that mentions a command later on.
        "Here is the doc:\n\n/pdf make a report",
        "> /pdf make a report",
        "```\n/pdf make a report\n```",
        # A host note must be followed by a blank line, not just a newline.
        "[note]\n/pdf go",
        # Right-to-left marks or other characters before the slash.
        "‏/pdf go",
        "/pdf/../../etc go",
    ],
)
def test_a_command_anywhere_but_the_start_does_not_run(text):
    assert parse_skill_command(text) is None
