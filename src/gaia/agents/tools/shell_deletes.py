# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The files a shell delete command names, read only where that is certain.

A turn's undo can copy a file before a shell command deletes it, but only when
GAIA knows exactly which file the command will remove. This module reads the
plain forms — ``del``/``erase``, ``rm``, ``unlink`` and PowerShell's
``Remove-Item`` — with literal operands. Anything the shell could expand or
reinterpret (wildcards, variables, escapes, recursion, several statements)
returns None, and the caller keeps the command as untracked shell work.
"""

from __future__ import annotations

import os
import re
import shlex
from typing import List, Optional

#: A file larger than this is not copied before a shell delete; the command
#: then stays untracked rather than filling the snapshot store.
MAX_SHELL_DELETE_COPY_BYTES = 64 * 1024 * 1024

#: Characters cmd.exe expands, splits or redirects on, quoted or not.
_CMD_UNSAFE = set("%^!&|<>*?\r\n")
#: Characters sh expands, escapes or splits on.
_POSIX_UNSAFE = set("$`\\*?[]{}!;&|<>()\r\n\"'")
#: Characters PowerShell expands, escapes, globs or splits on.
_PS_UNSAFE = set("$`;|&(){}@,<>*?[]#\r\n")

_RM_FLAGS = frozenset({"-f", "-v", "--force", "--verbose"})
_DEL_SWITCHES = frozenset({"/f", "/q"})
_PS_SHELLS = frozenset({"powershell", "pwsh"})
_PS_OUTER_FLAGS = frozenset({"-noprofile", "-noninteractive", "-nologo"})
_PS_COMMAND_FLAGS = frozenset({"-command", "-c"})
_PS_DELETES = frozenset({"remove-item", "ri", "rm", "del", "erase"})
_PS_PATH_PARAMS = frozenset({"-path", "-literalpath"})
_PS_SWITCHES = frozenset({"-force", "-verbose", "-confirm:$false"})

_DRIVE_RELATIVE_RE = re.compile(r"^[A-Za-z]:(?![\\/])")


def _program(token: str) -> str:
    return os.path.basename(token).lower().removesuffix(".exe")


def _split_windows(text: str) -> Optional[List[str]]:
    """*text* split the way cmd.exe hands it to a program, or None if unsure."""
    try:
        tokens = shlex.split(text, posix=False)
    except ValueError:
        return None
    argv = []
    for token in tokens:
        if any(ch in _CMD_UNSAFE for ch in token):
            return None
        if len(token) >= 2 and token[0] == token[-1] == '"':
            token = token[1:-1]
            if '"' in token:
                return None
        elif '"' in token or "'" in token:
            # cmd.exe does not quote with ', so shlex's reading would differ.
            return None
        argv.append(token)
    return argv


def _split_posix(text: str) -> Optional[List[str]]:
    """*text* split the way sh hands it to a program, or None if unsure."""
    if any(ch in _POSIX_UNSAFE for ch in text):
        return None
    argv = text.split()
    if any(token.startswith("~") for token in argv):
        return None
    return argv


def split_exact(text: str, windows: Optional[bool] = None) -> Optional[List[str]]:
    """The argv a shell would run for *text*, or None when it may expand."""
    if windows is None:
        windows = os.name == "nt"
    return _split_windows(text) if windows else _split_posix(text)


def _rm_operands(args: List[str]) -> Optional[List[str]]:
    operands: List[str] = []
    options_done = False
    for arg in args:
        if not options_done and arg == "--":
            options_done = True
        elif not options_done and arg.startswith("-") and arg != "-":
            if arg in _RM_FLAGS:
                continue
            # A short cluster like -fv; anything recursive or interactive is out.
            if arg.startswith("--") or any(f"-{ch}" not in _RM_FLAGS for ch in arg[1:]):
                return None
        else:
            operands.append(arg)
    return operands or None


def _del_operands(args: List[str]) -> Optional[List[str]]:
    operands: List[str] = []
    for arg in args:
        if arg.startswith("/"):
            if arg.lower() not in _DEL_SWITCHES:
                return None
            continue
        # cmd's del also splits names on , ; and =.
        if any(ch in arg for ch in ",;="):
            return None
        operands.append(arg)
    return operands or None


def _split_ps_body(body: str) -> Optional[List[str]]:
    """A PowerShell statement split into words, or None if it may expand."""
    probe = re.sub(r"(?i)-confirm:\$false\b", "", body)
    if any(ch in _PS_UNSAFE for ch in probe):
        return None
    if "''" in body or '""' in body:
        return None
    lexer = shlex.shlex(body, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    lexer.escape = ""  # a backslash is a path separator in PowerShell
    try:
        return list(lexer)
    except ValueError:
        return None


def _remove_item_operands(words: List[str]) -> Optional[List[str]]:
    if not words or words[0].lower() not in _PS_DELETES:
        return None
    operands: List[str] = []
    i = 1
    while i < len(words):
        word = words[i]
        lowered = word.lower()
        if lowered in _PS_SWITCHES:
            i += 1
            continue
        name, colon, _ = lowered.partition(":")
        if name in _PS_PATH_PARAMS:
            if colon:
                operands.append(word.split(":", 1)[1])
                i += 1
                continue
            if i + 1 >= len(words):
                return None
            operands.append(words[i + 1])
            i += 2
            continue
        if word.startswith("-"):
            return None
        operands.append(word)
        i += 1
    return operands or None


def _powershell_operands(args: List[str]) -> Optional[List[str]]:
    i = 0
    while i < len(args) and args[i].lower() in _PS_OUTER_FLAGS:
        i += 1
    if i < len(args) and args[i].lower() in _PS_COMMAND_FLAGS:
        i += 1
    rest = args[i:]
    if not rest or rest[0].startswith("-"):
        return None
    words = _split_ps_body(" ".join(rest))
    if words is None:
        return None
    return _remove_item_operands(words)


def delete_operands(argv: List[str]) -> Optional[List[str]]:
    """The literal paths a delete command removes, or None if it is not one GAIA reads.

    *argv* must be split exactly as the program receives it
    (:func:`split_exact`). A directory, recursive or wildcard delete is never
    read, so its effects stay unknown.
    """
    if not argv:
        return None
    program = _program(argv[0])
    args = argv[1:]
    if program == "rm":
        operands = _rm_operands(args)
    elif program in ("del", "erase"):
        operands = _del_operands(args)
    elif program == "unlink":
        operands = args[1:] if args[:1] == ["--"] else args
        operands = operands if len(operands) == 1 else None
        if operands and operands[0].startswith("-"):
            operands = None
    elif program in _PS_SHELLS:
        operands = _powershell_operands(args)
    else:
        return None
    if operands is None:
        return None
    for operand in operands:
        # '~' is a home folder to sh and PowerShell, never to the path join.
        if not operand or operand.startswith("~") or _DRIVE_RELATIVE_RE.match(operand):
            return None
        # A device path, an alternate data stream, or a name Windows trims.
        if os.name == "nt" and (
            operand.startswith("\\\\")
            or ":" in operand[2:]
            or operand.rstrip(" .") != operand
        ):
            return None
    return operands


__all__ = ["MAX_SHELL_DELETE_COPY_BYTES", "delete_operands", "split_exact"]
