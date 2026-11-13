# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Which tools a user's message explicitly asks for.

Semantic selection ranks tools by how similar their descriptions are to the
message. On the flagship's ~90-tool registry most tools clear the threshold, so
the cap decides — and "use your shell tool to run pwd" lost ``run_shell_command``
to a dozen file-browsing tools. A request that names a tool, or asks for a fact
only a tool can read (the working directory), is not a ranking question: the
named tool has to be offered.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Tuple

# Core only: naming a tool off the short list forces the session's one jump.
SHELL_TOOLS = ("run_shell_command",)

# The user names the shell. "Terminal" and "bash" are ordinary English too, so
# they count only as the place something is run ("in the terminal").
_SHELL_NAMED = re.compile(
    r"\b(?:shell|powershell|command[- ]line)\b"
    r"|\b(?:in|open|use|from|via|with|using)\s+(?:an?\s+|the\s+|your\s+|my\s+)?"
    r"(?:terminal|bash|cmd(?:\.exe)?|zsh)\b",
    re.IGNORECASE,
)

# The user asks to run a command, or for the directory the session is in, which
# only the shell knows.
_SHELL_ONLY_ANSWER = re.compile(
    r"\b(?:pwd|cwd)\b"
    r"|\b(?:current|present)\s+(?:working\s+)?(?:directory|dir)\b"
    r"|\bworking\s+(?:directory|folder|dir)\b"
    r"|\b(?:what|which)\s+(?:directory|folder)\s+(?:am\s+i|are\s+(?:we|you))\s+in\b"
    r"|\b(?:run|execute)\s+(?:the\s+|this\s+|these\s+|that\s+|a\s+|some\s+)?"
    r"(?:shell\s+)?commands?\b"
    r"|\b(?:run|execute)\s+`?(?:ls|dir|pwd|cd|git|whoami|hostname|ipconfig|ifconfig"
    r"|ps|echo|cat|type|where|which|npm|pip|uv|make)\b",
    re.IGNORECASE,
)


# Spoken output: "read this aloud" shares no words with the tool's description, so
# retrieval scored it under small talk.
_SPEECH_NAMED = re.compile(
    r"\b(?:aloud|out\s+loud|text[- ]to[- ]speech|into\s+speech)\b", re.IGNORECASE
)

# A file only an off-list tool can open. The prompt names these tools, and a
# model cannot call what its list does not hold.
_MEDIA_NAMED = (
    (
        re.compile(r"\.(?:png|jpe?g|gif|bmp|webp|tiff?)\b", re.IGNORECASE),
        "analyze_image",
    ),
    (
        re.compile(r"\.(?:mp3|wav|m4a|flac|ogg|mp4|mkv|mov|webm)\b", re.IGNORECASE),
        "transcribe_media",
    ),
)

# Skill-library tools outside core. A pasted SKILL.md scores capture_skill only a
# little above the short list's skill tools, so retrieval alone never jumps.
CAPTURE_TOOL = "capture_skill"
INSTALL_TOOL = "install_skill"
LESSON_TOOL = "remember_skill_lesson"
SKILL_REQUEST_TOOLS = (CAPTURE_TOOL, INSTALL_TOOL, LESSON_TOOL)

# Phrasing is English only; a pasted SKILL.md is recognised in any language.
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_FRONTMATTER_KEY = re.compile(r"^(name|description):\s*\S")
_SKILL_WORD = re.compile(r"\bskills?\b", re.IGNORECASE)

# Phrasing is read from the message's ends: a paste sits between them.
_PHRASING_SPAN = 4096

# "skill level", "skills section": the word as part of another noun.
_NOT_A_SKILL = (
    r"(?![\s-]*(?:levels?|sets?|trees?|points?|gaps?|sections?|lists?|matrix"
    r"|ratings?|scores?|checks?|issues?|bars?|badges?|bugs?|errors?|columns?"
    r"|fields?|categor(?:y|ies)|tags?|requirements?)\b)"
)
_DETERMINERS = r"(?:(?:this|that|the|these|those|a|an|my|your|new|following)\s+)+"

# Resume and profile talk: "add this skill to my CV" is not about the library.
_CAREER_TARGET = re.compile(
    r"\b(?:to|in|on|into|onto|for)\s+(?:my|the|your|our|a|his|her|their)\s+"
    r"(?:[\w-]+\s+)?(?:r[eé]sum[eé]s?|cvs?|profiles?|linkedin|portfolios?)\b",
    re.IGNORECASE,
)

# "save this skill", "add the skill at ~/x", "install the rss-digest skill". A
# determiner is required, so "add skills to my resume" is not a request, and
# only installs take the plural: "add the skills you listed" is about a list.
_SKILL_VERB_OBJECT = re.compile(
    r"\b(?:(?P<save>save|store|keep|add|capture|import|grab|register)\s+"
    + _DETERMINERS
    + r"(?:[\w.-]+\s+){0,2}?skill\b"
    + r"|(?P<install>install|download)\s+"
    + _DETERMINERS
    + r"(?:[\w.-]+\s+){0,2}?skills?\b)"
    + _NOT_A_SKILL,
    re.IGNORECASE,
)
# "save this as a skill", "turn these notes into a skill".
_AS_A_SKILL = re.compile(
    r"\b(?:save|store|keep|add|turn|make|convert|capture|import)\b[^.?!\n]{0,60}?"
    r"\b(?:as|into)\s+(?:an?\s+|my\s+|a\s+new\s+|new\s+)?skill\b" + _NOT_A_SKILL,
    re.IGNORECASE,
)
# "install tampered-notes from the hub": the hub serves only skills.
_INSTALL_FROM_HUB = re.compile(
    r"\binstall\b[^.?!\n]{0,80}?\b(?:from|on|off)\s+the\s+(?:agent\s+)?hub\b"
    r"|\bskills?\b[^\n]{0,120}?\bhub\b[^\n]{0,120}?\binstall\b",
    re.IGNORECASE,
)

# A correction to a skill's own instructions: "this skill should end with X".
_SKILL_REF_PATTERN = (
    r"\b(?:this|that|the|my|your)\s+(?:[\w.-]+\s+)?skill(?:'s)?\b" + _NOT_A_SKILL
)
_SKILL_REF = re.compile(_SKILL_REF_PATTERN, re.IGNORECASE)
# As a subject, only a skill's own slug names it: "my team skill should" doesn't.
_SKILL_SUBJECT = (
    r"\b(?:this|that|the)\s+(?:[\w.]+-[\w.-]+\s+)?skill(?:'s)?\b" + _NOT_A_SKILL
)
_SKILL_LESSON = re.compile(
    _SKILL_SUBJECT + r"\s+(?:[\w-]+\s+){0,2}?should\b"
    r"|\b(?:change|update|fix|correct|edit|rewrite)\s+" + _SKILL_REF_PATTERN,
    re.IGNORECASE,
)
_STANDING_CUE = re.compile(
    r"\b(?:from\s+now\s+on|going\s+forward|make\s+(?:that|it|this)\s+stick)\b",
    re.IGNORECASE,
)


def _skill_frontmatter(text: str) -> Tuple[List[bool], str]:
    """Return each pasted SKILL.md frontmatter's in-a-fence flag, and the prose.

    A frontmatter block is a ``---`` pair holding top-level ``name:`` and
    ``description:`` keys. The prose is every line outside fences and blocks.
    """
    lines = text.splitlines()
    blocks: List[bool] = []
    prose: List[str] = []
    fence = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        marker = _FENCE.match(line)
        if marker:
            mark = marker.group(1)
            if not fence:
                fence = mark
            elif mark[0] == fence[0] and len(mark) >= len(fence):
                fence = ""
            i += 1
            continue
        if line.strip() == "---":
            end = next(
                (
                    j
                    for j in range(i + 1, len(lines))
                    if lines[j].strip() == "---" or _FENCE.match(lines[j])
                ),
                None,
            )
            if end is not None and lines[end].strip() == "---":
                keys = {
                    m.group(1)
                    for m in map(_FRONTMATTER_KEY.match, lines[i + 1 : end])
                    if m
                }
                if keys == {"name", "description"}:
                    blocks.append(bool(fence))
                    i = end + 1
                    continue
        if not fence:
            prose.append(line)
        i += 1
    return blocks, "\n".join(prose)


def _skill_tools(user_input: str) -> List[str]:
    """Return the skill-library tools *user_input* asks for, capture first."""
    wanted: List[str] = []
    blocks, prose = _skill_frontmatter(user_input)
    # A fenced block is often an example of something else; the prose decides.
    if any(not fenced for fenced in blocks) or (blocks and _SKILL_WORD.search(prose)):
        wanted.append(CAPTURE_TOOL)
    if len(user_input) > 2 * _PHRASING_SPAN:
        text = user_input[:_PHRASING_SPAN] + "\n" + user_input[-_PHRASING_SPAN:]
    else:
        text = user_input
    career = bool(_CAREER_TARGET.search(text))
    install = False
    for match in _SKILL_VERB_OBJECT.finditer(text):
        if match.group("install"):
            install = True
        elif not career and CAPTURE_TOOL not in wanted:
            wanted.append(CAPTURE_TOOL)
    if not career and _AS_A_SKILL.search(text) and CAPTURE_TOOL not in wanted:
        wanted.append(CAPTURE_TOOL)
    if install or _INSTALL_FROM_HUB.search(text):
        # "Install" covers the hub and a URL or path, which capture handles.
        wanted.extend(t for t in (INSTALL_TOOL, CAPTURE_TOOL) if t not in wanted)
    if _SKILL_LESSON.search(text) or (
        _STANDING_CUE.search(text) and _SKILL_REF.search(text)
    ):
        wanted.append(LESSON_TOOL)
    return wanted


def requested_tools(user_input: str, registry: Iterable[str]) -> List[str]:
    """Return the registry tools *user_input* explicitly asks for, in order.

    Five signals: a registry tool name written out (``run_shell_command``), a
    request that names the shell or can only be answered by it, a request to
    speak, a named picture or recording, and a pasted skill or a request to
    save, install, or correct one. Only names in *registry* are returned, so a
    profile without shell tools gets none.
    """
    names = set(registry)
    found: List[str] = []

    # Underscored names only: single words like "tree" or "recall" are ordinary
    # English and would fire on every sentence that uses them.
    for token in re.findall(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", user_input.lower()):
        if token in names and token not in found:
            found.append(token)

    if _SHELL_NAMED.search(user_input) or _SHELL_ONLY_ANSWER.search(user_input):
        for name in SHELL_TOOLS:
            if name in names and name not in found:
                found.append(name)
    if (
        _SPEECH_NAMED.search(user_input)
        and "text_to_speech" in names
        and "text_to_speech" not in found
    ):
        found.append("text_to_speech")
    for pattern, name in _MEDIA_NAMED:
        if pattern.search(user_input) and name in names and name not in found:
            found.append(name)
    for name in _skill_tools(user_input):
        if name in names and name not in found:
            found.append(name)
    return found
