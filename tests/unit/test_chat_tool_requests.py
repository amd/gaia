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


SKILL_REGISTRY = REGISTRY | {
    "load_skill",
    "list_skills",
    "capture_skill",
    "install_skill",
    "remember_skill_lesson",
}
CAPTURE = ["capture_skill"]
INSTALL = ["install_skill", "capture_skill"]
LESSON = ["remember_skill_lesson"]

PASTED_SKILL = """save this skill and load it:

---
name: standup-format
description: Format a daily standup update when the user says what they did yesterday.
version: "1.0.0"
---

# Standup format

## Output shape
- Yesterday, Today, Blockers bullets, then `-- END STANDUP --`.
"""


def test_a_pasted_skill_asks_to_be_captured():
    """The paste-and-load request that never reached the capture tool."""
    assert requested_tools(PASTED_SKILL, SKILL_REGISTRY) == CAPTURE


@pytest.mark.parametrize(
    "message",
    [
        # Bare paste, no instruction at all.
        PASTED_SKILL.split("\n", 2)[2],
        # CRLF line endings from a Windows clipboard.
        PASTED_SKILL.replace("\n", "\r\n"),
        # Arabic: "save this skill and load it". Phrasing is English-only, but
        # the pasted frontmatter is the signal in any language.
        "احفظ هذه المهارة وحمّلها:\n\n" + PASTED_SKILL.split("\n", 2)[2],
        # Fenced, but the prose says it is a skill.
        "Here's my SKILL.md, add it:\n```markdown\n"
        + PASTED_SKILL.split("\n", 2)[2]
        + "```",
        "save this skill for me:\n\n---\nname: hostile-notes\n"
        "description: A note taking skill.\npermissions: [network]\n---\n",
    ],
)
def test_pasted_frontmatter_asks_for_capture(message):
    assert requested_tools(message, SKILL_REGISTRY) == CAPTURE


@pytest.mark.parametrize(
    "message",
    [
        "add the skill at ~/gaia-eval/capture/word-count to your library and load it",
        "grab this skill and add it to your library: http://x.test/a/SKILL.md",
        "add this skill and load it: http://x.test/b/SKILL.md",
        "Save this as a skill: always answer in haiku.",
        "turn these notes into a skill",
        "capture the following skill from ~/skills/receipts",
        "Import my new skill from C:/skills/foo",
    ],
)
def test_save_phrasing_asks_for_capture(message):
    assert requested_tools(message, SKILL_REGISTRY) == CAPTURE


@pytest.mark.parametrize(
    "message",
    [
        "Install the experimental-notes skill from the hub for me.",
        "Install tampered-notes from the hub.",
        "just install pdf-forms from the hub then",
        "Is there a skill on the hub for converting units? If there is, install "
        "it and load it.",
        "download the rss-digest skill",
    ],
)
def test_install_phrasing_asks_for_install(message):
    assert requested_tools(message, SKILL_REGISTRY) == INSTALL


@pytest.mark.parametrize(
    "message",
    [
        "That closing line is wrong for my team. From now on this skill should "
        "end with == DONE == instead. Make that stick.",
        "the standup-format skill should always list blockers first",
        "Fix the price-watch skill's report format: drop the emoji.",
        "Going forward, the rss-digest skill must show dates in ISO format.",
    ],
)
def test_a_correction_to_a_skill_asks_for_the_lesson_tool(message):
    assert requested_tools(message, SKILL_REGISTRY) == LESSON


@pytest.mark.parametrize(
    "message",
    [
        # The word alone is not a request.
        "What skills do you have installed right now?",
        "Load the github-triage skill.",
        "Which skills do you currently have loaded? Name them.",
        "I want to improve my cooking skills.",
        "What new capability does that skill actually give you?",
        "Then get rid of that skill.",
        # Resume and HR senses of the word.
        "add skills to my resume",
        "Add Python to the skills section of my CV.",
        "add a skill level column to this table",
        "save my skills list as a csv",
        "install the skill tree mod for this game",
        "add the following skills to my CV: Python, SQL",
        "add the skills you listed to my resume",
        "add a new skills column",
        "save these skills to my profile",
        "add this skill to my LinkedIn profile: negotiation",
        "add a new skill column to the sheet",
        # Fixing code or a document that mentions skills is not a correction.
        "fix the skill issue in my code",
        "can you fix that skill bug",
        "update the skill section of my resume",
        "my team skill should improve",
        "update your skill requirements list",
        # Installing something that is not a skill.
        "install numpy",
        "Install the GitHub CLI for me.",
        # Using a skill's format is not correcting it.
        "Check that lantern page again and report it in your usual price-watch "
        "format.",
        "should I use the pdf skill instead?",
        "from now on, answer in French",
        # Arabic mention of "skill" with no paste.
        "ما هي المهارات المتوفرة لديك؟",
    ],
)
def test_mentioning_skills_asks_for_nothing(message):
    assert requested_tools(message, SKILL_REGISTRY) == []


@pytest.mark.parametrize(
    "message",
    [
        # A coding-assistant subagent definition in a fence: same keys, not a skill.
        "Why does my subagent not show up?\n```markdown\n---\nname: reviewer\n"
        "description: Reviews code\ntools: Read, Grep\n---\nYou review code.\n```",
        # YAML in a fence that merely has name and description keys.
        "Why is this invalid?\n```yaml\n---\nname: build\ndescription: CI\n---\n```",
        # A blog post's frontmatter: title, not name.
        "Proofread this post:\n\n---\ntitle: Hello\ndescription: First post\n---\n"
        "Welcome to my blog.",
        # Indented keys are nested, not the frontmatter's own.
        "---\nmeta:\n  name: x\n  description: y\n---\n",
        # Unclosed: a horizontal rule followed by prose.
        "Notes\n---\nname: Alice\ndescription: tall\n",
        # The block closes inside a fence, so it never closed outside one.
        "---\nname: a\n```\ndescription: b\n---\n```",
        # Horizontal rules around prose with only one of the keys.
        "---\nname: Bob\n---\n",
    ],
)
def test_frontmatter_that_is_not_a_pasted_skill_asks_for_nothing(message):
    assert requested_tools(message, SKILL_REGISTRY) == []


def test_skill_tools_absent_from_the_registry_are_not_requested():
    """A profile without the skill library gets no skill request."""
    assert requested_tools(PASTED_SKILL, REGISTRY) == []
    assert requested_tools(
        "Install tampered-notes from the hub.", {"install_skill"}
    ) == ["install_skill"]


def test_unfenced_name_and_description_frontmatter_asks_for_capture():
    """Intended: an unfenced block with both keys is a pasted skill, any prose."""
    message = "---\nname: x\ndescription: y\n---\nWhat does this do?"
    assert requested_tools(message, SKILL_REGISTRY) == CAPTURE


def test_save_phrasing_after_a_long_paste_still_counts():
    """The instruction may follow the paste; the middle is skipped, not the end."""
    message = "filler line\n" * 2000 + "save this skill"
    assert requested_tools(message, SKILL_REGISTRY) == CAPTURE


def test_a_skill_named_after_a_career_site_still_installs():
    message = "install the linkedin-post skill from the hub"
    assert requested_tools(message, SKILL_REGISTRY) == INSTALL


@pytest.mark.parametrize("prefix", ["", "install ", "fix "])
@pytest.mark.parametrize("unit", ["skill hub ", "the skill ", "this skill should "])
def test_a_megabyte_of_skill_words_is_classified_quickly(prefix, unit):
    import time

    message = prefix + unit * 100000
    start = time.perf_counter()
    requested_tools(message, SKILL_REGISTRY)
    assert time.perf_counter() - start < 1.0


def test_a_huge_paste_is_classified_quickly():
    """Many unclosed rules must not turn the frontmatter scan quadratic in regex."""
    import time

    message = "save this skill\n" + "---\nname: x\n" * 5000
    start = time.perf_counter()
    requested_tools(message, SKILL_REGISTRY)
    assert time.perf_counter() - start < 5.0
