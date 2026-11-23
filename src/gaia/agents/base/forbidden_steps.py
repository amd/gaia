# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A step the user's request ruled out that ran anyway, and an answer silent on it.

The permission card is the safety net for a forbidden step, but a person who
approves a card mid-turn may not see it was the step they had ruled out. The
answer is where they would learn it, so an answer that leaves it out gets sent
back once to say so (#8803).

Deliberately narrow: only an explicit "don't use the shell" or "don't change
permissions" in the request counts, and only a shell command that actually ran.
English phrasing only; a ban in another language is left to the prompt rule.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Iterable, List

SHELL_TOOL = "run_shell_command"

_NOT = r"(?:do\s+not|don['’]?t|never|without)"

_NO_SHELL = re.compile(
    rf"\b(?:{_NOT}\s+(?:use|using|run|running|call|calling|touch|touching|open|opening)"
    r"|no)\s+(?:(?:the|a|any)\s+)?(?:shell|terminal|command[\s-]line|bash|zsh|powershell)\b",
    re.IGNORECASE,
)

_PERMISSION_PROGRAMS = r"chmod|chown|chgrp|chattr|attrib|icacls|cacls|takeown|set-acl"

_NO_PERMISSION_CHANGE = re.compile(
    rf"\b{_NOT}\s+(?:"
    r"(?:change|changing|modify|modifying|alter|altering|touch|touching|edit|editing)"
    r"\s+(?:(?:the|its|any|my|this|that|file|file['’]s|files['’]?)\s+){0,3}"
    r"(?:permissions?|perms|mode|ownership|owner|attributes?|read-only\s+flag)"
    rf"|(?:use\s+|using\s+|run\s+|running\s+)?(?:{_PERMISSION_PROGRAMS})"
    r"|(?:make|making)\s+(?:\S+\s+){0,2}writable"
    r")\b",
    re.IGNORECASE,
)

_PERMISSION_COMMAND = re.compile(
    rf"(?:^|[\s;&|(`])(?:sudo\s+)?(?:{_PERMISSION_PROGRAMS})(?:\.exe)?\b"
    r"|\bIsReadOnly\b",
    re.IGNORECASE,
)

_MENTIONS_STEP = re.compile(
    rf"\b(?:shell|terminal|command|{_PERMISSION_PROGRAMS}|permissions?)\b",
    re.IGNORECASE,
)

#: How every message from :func:`undisclosed_forbidden_steps` ends.
FORBIDDEN_STEP_UNSAID = "the answer does not say so."

_MAX_SHOWN = 3
_MAX_COMMAND_CHARS = 80


#: A word before the phrase that makes it a statement, not an instruction:
#: "I don't use the shell much", "why is there no shell?".
_STATEMENT_LEAD = frozenset(
    "i we they he she there there's theres is was are were has have had got "
    "who which".split()
)


_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'"})


def _normalised(text: str) -> str:
    """*text* in NFKC, apostrophes plain, invisible format characters dropped."""
    text = unicodedata.normalize("NFKC", text or "").translate(_APOSTROPHES)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def _instructs(pattern: re.Pattern, text: str) -> bool:
    for match in pattern.finditer(text):
        before = re.search(r"([\w']+)\s+$", text[: match.start()])
        if not before or before.group(1).lower() not in _STATEMENT_LEAD:
            return True
    return False


#: "I didn't use the shell", "no command was run": a denial, not owning up.
_DENIED_BEFORE = re.compile(
    r"(?:\b(?:not|never|no|without)|n't)\s+"
    r"(?:(?:use|used|using|run|ran|running|touch|touched|touching|change|changed|"
    r"changing|modify|modified|call|called|open|opened|need|needed|any|the|a|an|"
    r"your|my|its|to|have|has|been|was|were|it|file|file's)\s+){0,3}$",
    re.IGNORECASE,
)


def _owns_up(pattern: re.Pattern, answer: str) -> bool:
    return any(
        not _DENIED_BEFORE.search(answer[: m.start()]) for m in pattern.finditer(answer)
    )


def ruled_out(request: str) -> List[str]:
    """What *request* explicitly forbids, as phrases for a check message."""
    text = _normalised(request)
    found = []
    if _instructs(_NO_SHELL, text):
        found.append("not to use the shell")
    if _instructs(_NO_PERMISSION_CHANGE, text):
        found.append("not to change permissions")
    return found


def _commands_that_ran(records: Iterable[Dict[str, Any]]) -> List[str]:
    commands = []
    for record in records:
        if record.get("tool") != SHELL_TOOL or not record.get("ran"):
            continue
        command = (record.get("args") or {}).get("command")
        if isinstance(command, str) and command.strip():
            commands.append(command.strip())
    return commands


def undisclosed_forbidden_steps(
    request: str, records: Iterable[Dict[str, Any]], answer: str
) -> str:
    """A check message when a forbidden step ran and *answer* never says so, else ``""``."""
    forbidden = ruled_out(request)
    if not forbidden:
        return ""
    commands = _commands_that_ran(records)
    if "not to use the shell" not in forbidden:
        commands = [c for c in commands if _PERMISSION_COMMAND.search(c)]
    if not commands:
        return ""
    words = {c.split()[0].lower() for c in commands}
    said = _normalised(answer)
    if _owns_up(_MENTIONS_STEP, said) or any(
        _owns_up(re.compile(rf"(?<![\w-]){re.escape(w)}(?![\w-])", re.I), said)
        for w in words
    ):
        return ""
    shown = ", ".join(
        f"`{c if len(c) <= _MAX_COMMAND_CHARS else c[:_MAX_COMMAND_CHARS] + '…'}`"
        for c in commands[:_MAX_SHOWN]
    )
    if len(commands) > _MAX_SHOWN:
        shown += f" and {len(commands) - _MAX_SHOWN} more"
    return (
        f"The request said {' and '.join(forbidden)}, and this turn ran {shown}; "
        + FORBIDDEN_STEP_UNSAID
    )
