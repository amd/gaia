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


def requested_tools(user_input: str, registry: Iterable[str]) -> List[str]:
    """Return the registry tools *user_input* explicitly asks for, in order.

    Two signals: a registry tool name written out (``run_shell_command``), and a
    request that names the shell or can only be answered by it. Only names in
    *registry* are returned, so a profile without shell tools gets none.
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
    return found
