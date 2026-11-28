# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Parsing and framing of ``/<skill-name>`` commands (gaia.skills.command)."""

import pytest

from gaia.skills.command import (
    SkillCommand,
    names_a_skill,
    parse_skill_command,
    skill_command_message,
)

INSTALLED = {"pdf", "web-research", "garden-kit:plant-care", "humanizer", "docx"}


def parse(text):
    return parse_skill_command(text, INSTALLED)


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
        ("/garden-kit:plant-care water it", "garden-kit:plant-care", "water it"),
        (" /pdf go", "pdf", "go"),
        ("/Pdf go", "pdf", "go"),
        ("/pdf: make a report", "pdf", "make a report"),
    ],
)
def test_a_leading_command_is_cut_from_the_request(text, name, typed):
    assert parse(text) == SkillCommand(name=name, request=typed)


@pytest.mark.parametrize(
    "text, name",
    [
        ("rewrite this with /humanizer", "humanizer"),
        ("rewrite this with /humanizer.", "humanizer"),
        ("rewrite this with /HUMANIZER please", "humanizer"),
        ("can you /pdf this?\nthanks", "pdf"),
        ("water my fern with /garden-kit:plant-care", "garden-kit:plant-care"),
        ("لخّص هذا الملف بـ /pdf؟", "pdf"),
        # A pasted reply or document that names a skill runs it too, as in
        # Claude Code; the composer marks the word so the user sees it.
        ("Here is the doc:\n\n/pdf make a report", "pdf"),
        ("> /pdf make a report", "pdf"),
    ],
)
def test_a_command_later_in_the_message_keeps_the_whole_request(text, name):
    assert parse(text) == SkillCommand(name=name, request=text.strip())


def test_the_first_installed_skill_named_is_the_one_that_runs():
    command = parse("use /nope then /docx and /pdf for the memo")
    assert command == SkillCommand(
        name="docx", request="use /nope then /docx and /pdf for the memo"
    )


@pytest.mark.parametrize(
    "text",
    [
        "",
        "pdf",
        "/",
        "/tmp/x.txt",
        "see /etc/hosts and /tmp/x.txt",
        "/pdf.txt",
        "/-pdf",
        "/pdf--x",
        "/a:b:c",
        "/:pdf",
        "/pdf/../../etc go",
        "https://example.com/pdf",
        "and/pdf",
        "(/pdf)",
        "`/pdf`",
        # Right-to-left marks or other characters before the slash.
        "‏/pdf go",
        # A skill that is not installed is ordinary text.
        "/zzz do it",
        "/garden-kit:",
    ],
)
def test_rejects_text_that_names_no_installed_skill(text):
    assert parse(text) is None


def test_the_shape_check_ignores_paths_and_counts_any_name():
    assert names_a_skill("ask /anything at all")
    assert not names_a_skill("see /etc/hosts and https://x.test/pdf")


def test_keeps_host_notes_in_front():
    lead = "[This chat is working in the folder /w/[x]. Here.]\n\n"
    assert parse(lead + "/pdf go") == SkillCommand(name="pdf", request="go", lead=lead)
    assert parse(lead + "make it with /pdf") == SkillCommand(
        name="pdf", request="make it with /pdf", lead=lead
    )


def test_a_host_note_needs_its_blank_line_to_stay_a_note():
    # Without it the note is part of the user's text, so the command is mid-message.
    assert parse("[note]\n/pdf go") == SkillCommand(
        name="pdf", request="[note]\n/pdf go"
    )


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


def test_a_mid_sentence_command_is_sent_as_the_user_wrote_it():
    message = skill_command_message(
        parse("rewrite this with /humanizer"), {"status": "success"}
    )
    assert message.endswith(
        "Follow them for this request.]\n\nrewrite this with /humanizer"
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
    command = parse("/pdf لخّص هذا الملف في صفحة واحدة")
    assert command == SkillCommand(name="pdf", request="لخّص هذا الملف في صفحة واحدة")
