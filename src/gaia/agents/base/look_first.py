# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An answer about the user's files that never looked at them.

A local model asked to review ``toybox/dates.py`` replied that the file does
not exist, and asked to explain it guessed — both without calling a tool. The
request named files in the workspace, so the answer could only have come from
the model's imagination. This finds those files so the loop can send the
agent to look once before it answers.
"""

from __future__ import annotations

import os
import re
from typing import List

#: A path-like token: has a separator, or ends in a short file extension.
_PATH_TOKEN = re.compile(
    r"(?<![\w@])(?:[A-Za-z]:[\\/]|/)?[\w.\-]+(?:[\\/][\w.\-]+)+|"
    r"(?<![\w@/\\])[\w\-]+\.[A-Za-z][A-Za-z0-9]{0,5}\b"
)

#: A request that rules out tool use this turn: "do NOT run anything", "plan only".
_NO_TOOLS = re.compile(
    r"\b(?:do\s+not|don['\u2019]?t|never)\s+(?:run|execute|call|use|open|read)\s+"
    r"(?:anything|any\s+(?:tools?|commands?|code|files?)|tools?|the\s+files?)\b"
    r"|\bwithout\s+(?:running|executing|calling|using|opening|reading)\s+"
    r"(?:anything|any\s+(?:tools?|commands?|code|files?)|tools?)\b"
    r"|\bno\s+tool(?:\s+call)?s\b"
    r"|\bplan\s+only\b",
    re.IGNORECASE,
)

LOOK_FIRST_PROMPT = (
    "You answered without opening anything, but the request is about {paths} "
    "in the workspace. Look with your tools first (search for it if that exact "
    "path isn't there), then answer from what you find."
)


def forbids_tools(request: str) -> bool:
    """Whether *request* tells the agent not to use tools this turn."""
    return bool(_NO_TOOLS.search(request or ""))


def named_workspace_paths(request: str, root: str) -> List[str]:
    """Paths *request* names that are, or sit in a folder that is, on disk.

    The working folder itself doesn't count: "You are working in X" says where,
    not what to look at.
    """
    root_real = os.path.realpath(root)
    found: List[str] = []
    for token in _PATH_TOKEN.findall(request or ""):
        token = token.rstrip(".")
        if not token or token in found:
            continue
        path = os.path.realpath(
            token if os.path.isabs(token) else os.path.join(root_real, token)
        )
        if os.path.normcase(path) == os.path.normcase(root_real):
            continue
        parent = os.path.dirname(path)
        if os.path.exists(path) or (
            os.path.normcase(parent) != os.path.normcase(root_real)
            and os.path.isdir(parent)
        ):
            found.append(token)
    return found
