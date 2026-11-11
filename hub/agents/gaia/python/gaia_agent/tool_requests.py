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
from typing import Iterable, List

SHELL_TOOLS = ("run_shell_command", "get_shell_state")

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


# A request about GAIA's own voice: "change your voice", "speak slower".
_VOICE_SETTING_NAMED = re.compile(
    r"\byour\s+(?:own\s+)?(?:voice|volume|speed|accent)\b"
    r"|\b(?:speak|talk)\s+(?:a\s+(?:bit|little)\s+)?(?:slower|faster|louder|quieter|softer|more\s+(?:slowly|quietly|loudly))\b"
    r"|\b(?:different|another|male|female|british|american)\s+voice\b"
    r"|\bchange\s+(?:the\s+)?voice\b"
    r"|\bwake\s+word\b"
    r"|\b(?:the|that|your)\s+(?:beep|chime|ding|tone)\b"
    r"|\b(?:turn|switch)\s+(?:the\s+)?captions\s+(?:on|off)\b"
    r"|\b(?:turn|switch)\s+(?:on|off)\s+(?:the\s+)?captions\b"
    r"|\b(?:be|a\s+(?:bit|little))\s+(?:louder|quieter)\b"
    r"|\b(?:turn|bring)\s+(?:the\s+)?volume\s+(?:up|down)\b",
    re.IGNORECASE,
)


def requested_tools(user_input: str, registry: Iterable[str]) -> List[str]:
    """Return the registry tools *user_input* explicitly asks for, in order.

    Three signals: a registry tool name written out (``run_shell_command``), a
    request that names the shell or can only be answered by it, and a request to
    speak. Only names in *registry* are returned, so a profile without shell
    tools gets none.
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
    if (
        _VOICE_SETTING_NAMED.search(user_input)
        and "set_voice_setting" in names
        and "set_voice_setting" not in found
    ):
        found.append("set_voice_setting")
    return found
