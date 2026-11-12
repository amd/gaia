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
    r"\b(?:aloud|out\s+loud|text[- ]to[- ]speech|into\s+speech|narrate)\b",
    re.IGNORECASE,
)
SPEECH_TOOLS = ("text_to_speech", "dictate_document")

# A document turned into audio: "dictate this PDF", "make an audiobook of it".
_DICTATION_NAMED = re.compile(
    r"\bdictate\b|\baudio\s?books?\b"
    r"|\b(?:in)?to\s+(?:an?\s+)?(?:audio|mp3)\b"
    r"|\bas\s+(?:an?\s+)?(?:audio|mp3)\b"
    r"|\b(?:audio|spoken)\s+version\b"
    r"|\bread\s+\S+(?:\s+\S+){0,3}\s+to\s+me\b",
    re.IGNORECASE,
)
# Audio that already exists is for the transcription tools, not for speech.
_EXISTING_AUDIO = re.compile(
    r"\b(?:video|recording|transcri\w+|podcast|song|music|meeting|voice\s?memo)\b",
    re.IGNORECASE,
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


def requested_tools(user_input: str, registry: Iterable[str]) -> List[str]:
    """Return the registry tools *user_input* explicitly asks for, in order.

    Signals: a registry tool name written out (``run_shell_command``), a
    request that names the shell or can only be answered by it, a request to
    speak, a request to turn a document into audio, and a named picture or
    recording. Only names in *registry* are returned, so a profile without
    shell tools gets none.
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
    if _DICTATION_NAMED.search(user_input) and not _EXISTING_AUDIO.search(user_input):
        wanted = SPEECH_TOOLS[::-1]
    elif _SPEECH_NAMED.search(user_input):
        wanted = SPEECH_TOOLS[:1]
    else:
        wanted = ()
    for name in wanted:
        if name in names and name not in found:
            found.append(name)
    for pattern, name in _MEDIA_NAMED:
        # "dictate notes.pdf to notes.mp3" names the output, not a recording to transcribe.
        if name == "transcribe_media" and "dictate_document" in wanted:
            continue
        if pattern.search(user_input) and name in names and name not in found:
            found.append(name)
    return found
