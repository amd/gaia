# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""What a shell command line does, read from its structure rather than its text.

Two consumers need the same reading of a command, and they must never disagree:

* :mod:`gaia.agents.base.tool_grants` — which command *family* an "always"
  answer covers (``cd proj && python -m pytest -q 2>&1`` is ``pytest``).
* :mod:`gaia.agents.base.call_risk` — which label the prompt shows (running the
  tests is not DESTRUCTIVE; ``rm -rf build`` is).

This is deliberately small. It is not the enforcement boundary — the shell
tool's own validator decides what may run at all, before any prompt. It only
describes a command that already reached the prompt, and anything it cannot
read is reported as ``opaque`` so callers fall back to the cautious answer.
"""

from __future__ import annotations

import ntpath
import posixpath
import shlex
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

#: Operators that end one simple command and start the next.
_SEPARATORS = frozenset({"&&", "||", ";", "|"})

#: Redirection operators shlex hands back as their own tokens.
_REDIRECTS = frozenset({">", ">>", ">&", "&>", "<", "<<", "<<<", ">|"})

#: Targets a redirect may name without writing anything the user owns.
_NULL_DEVICES = frozenset({"/dev/null", "nul", "$null"})

#: Constructs whose effect is chosen only when the command runs — no reading of
#: the text can say what they do.
_OPAQUE_MARKERS = ("`", "$(", "\n", "\r", "<(", ">(")


@dataclass(frozen=True)
class CommandFacts:
    """One command line, split into the simple commands it runs.

    ``segments`` holds each simple command's argv with leading ``NAME=value``
    assignments and harmless redirections (``2>&1``, ``>/dev/null``) removed.
    ``writes_file`` is set by a redirect into a real file. ``opaque`` means the
    line could not be read with confidence; ``segments`` is then empty.
    """

    segments: Tuple[Tuple[str, ...], ...] = field(default_factory=tuple)
    writes_file: bool = False
    opaque: bool = False


def binary_name(token: str) -> str:
    """The bare program name, with any directory and .exe-style suffix removed."""
    name = ntpath.basename(posixpath.basename(token)).lower()
    for suffix in (".exe", ".cmd", ".bat", ".com", ".ps1"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name


def _lex(command: str, escape: str) -> Optional[List[str]]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars="|&;<>")
    lexer.whitespace_split = True
    lexer.escape = escape
    try:
        return list(lexer)
    except ValueError:
        return None


def _operators(tokens: List[str]) -> List[str]:
    return [t for t in tokens if t in _SEPARATORS or t in _REDIRECTS or t == "&"]


def _tokenize(command: str) -> Optional[List[str]]:
    """The tokens, or None when the two shells this runs under would disagree.

    Backslash is a path separator on Windows (`cd C:\\Users\\me`) and an escape
    on POSIX, where `pytest \\" ; curl x ; \\"` runs `curl`. The Windows reading
    is kept for the words, but the line is only readable when both readings put
    the command boundaries in the same places.
    """
    windows = _lex(command, escape="")
    posix = _lex(command, escape="\\")
    if windows is None or posix is None or _operators(windows) != _operators(posix):
        return None
    return windows


def _is_assignment(token: str) -> bool:
    name, eq, _ = token.partition("=")
    return bool(eq) and name.isidentifier()


def read_command(command: str) -> CommandFacts:
    """Split *command* into simple commands, or report it as opaque."""
    if not isinstance(command, str) or not command.strip():
        return CommandFacts(opaque=True)
    if any(marker in command for marker in _OPAQUE_MARKERS):
        return CommandFacts(opaque=True)
    tokens = _tokenize(command)
    if not tokens:
        return CommandFacts(opaque=True)

    segments: List[Tuple[str, ...]] = []
    current: List[str] = []
    writes_file = False
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in _SEPARATORS:
            if not current:
                return CommandFacts(opaque=True)
            segments.append(tuple(current))
            current = []
            i += 1
            continue
        if token == "&":
            # A trailing `&` leaves a process running after the call returns.
            return CommandFacts(opaque=True)
        if token in _REDIRECTS:
            if token in ("<<", "<<<"):
                return CommandFacts(opaque=True)
            target = tokens[i + 1] if i + 1 < len(tokens) else ""
            if not target or target in _SEPARATORS or target in _REDIRECTS:
                return CommandFacts(opaque=True)
            # `2>&1`: the fd number was already appended as an argument.
            if current and current[-1].isdigit():
                current.pop()
            if token == "<":
                pass  # reading a file changes nothing
            elif token == ">&" and target.isdigit():
                pass  # fd duplication
            elif target.lower() not in _NULL_DEVICES:
                writes_file = True
            i += 2
            continue
        if not current and _is_assignment(token):
            i += 1
            continue
        current.append(token)
        i += 1
    if not current:
        return CommandFacts(opaque=True)
    segments.append(tuple(current))
    return CommandFacts(segments=tuple(segments), writes_file=writes_file)


#: Interpreters whose `-m <module>` form runs a named tool.
PYTHON_BINARIES = frozenset({"python", "python3", "py"})


def effective_argv(argv: Tuple[str, ...]) -> Tuple[str, ...]:
    """The argv as the tool it runs: ``python -m pytest -q`` is ``pytest -q``.

    Anything else is returned unchanged, with the binary normalised.
    """
    if not argv:
        return argv
    binary = binary_name(argv[0])
    if binary in PYTHON_BINARIES:
        rest = list(argv[1:])
        while rest and rest[0].startswith("-") and rest[0] != "-m":
            # Only flags that take no value are skipped; `-W x` stops here.
            if rest[0] in ("-X", "-W"):
                break
            rest.pop(0)
        if len(rest) >= 2 and rest[0] == "-m":
            return (rest[1].lower(),) + tuple(rest[2:])
    return (binary,) + tuple(argv[1:])


__all__ = [
    "CommandFacts",
    "PYTHON_BINARIES",
    "binary_name",
    "effective_argv",
    "read_command",
]
