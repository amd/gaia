# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Small, conservative parsers for commands hidden behind launchers.

The shell policy and denied-effect ledger both need to find the command a
launcher will execute.  They must use the same parser: a second, weaker
implementation can let a refused operation become an ordinary prompt or fail
to cover a delete that is retried through a wrapper.

This module intentionally does not interpret shell syntax.  It only skips
well-known launcher options and their operands.  Unknown options are skipped
as options, never guessed to consume the following word; that keeps the parser
on the safe side for finding the inner command.
"""

from __future__ import annotations

import os
import re
from typing import List, Optional, Sequence

_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")

_POLICY_WRAPPERS = frozenset(
    {
        "sudo",
        "doas",
        "env",
        "timeout",
        "setsid",
        "nohup",
        "time",
        "nice",
        "exec",
        "command",
        "xargs",
    }
)
_DIRECT_WRAPPERS = frozenset({"npx", "uvx", "pipx", "bunx"})
_RUN_WRAPPERS = {
    "uv": {"run"},
    "poetry": {"run"},
    "pipenv": {"run"},
    "pdm": {"run"},
    "hatch": {"run"},
    "conda": {"run"},
    "pnpm": {"exec", "dlx"},
    "yarn": {"dlx", "exec"},
}

# Flags whose next token is an operand for each launcher.  Keep these separate:
# ``sudo -n`` and ``sudo -E`` take no value, while ``nice -n 5`` does.
_VALUE_FLAGS = {
    "sudo": frozenset({"-u", "--user", "-g", "--group", "-C", "--chdir"}),
    "doas": frozenset({"-u", "--user", "-a", "--persist"}),
    "timeout": frozenset({"-k", "--kill-after", "-s", "--signal"}),
    "setsid": frozenset(),
    "nohup": frozenset(),
    "time": frozenset({"-f", "--format", "-o", "--output"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "exec": frozenset({"-a", "--argv0"}),
    "command": frozenset({"-a", "--argv0"}),
    "xargs": frozenset(
        {
            "-a",
            "--arg-file",
            "-E",
            "--eof",
            "-I",
            "--replace",
            "-L",
            "--max-lines",
            "-n",
            "--max-args",
            "-P",
            "--max-procs",
            "-s",
            "--max-chars",
            "-d",
            "--delimiter",
        }
    ),
}

_NO_VALUE_FLAGS = {
    "sudo": frozenset({"-E", "-H", "-n", "-P", "-S", "-s", "-b", "-e"}),
    "doas": frozenset({"-C", "-L", "-n", "-s"}),
    "timeout": frozenset(
        {"--foreground", "--preserve-status", "--verbose", "--help", "--version"}
    ),
    "setsid": frozenset({"-f", "-w", "-c", "--wait", "--fork", "--ctty"}),
    "nohup": frozenset({"--help", "--version"}),
    "time": frozenset({"-a", "--append", "-v", "--verbose", "--help", "--version"}),
    "nice": frozenset({"--help", "--version"}),
    "exec": frozenset({"-p", "-c"}),
    "command": frozenset({"-p", "-v", "-V"}),
    "xargs": frozenset(
        {
            "-0",
            "--null",
            "-r",
            "--no-run-if-empty",
            "--replace",
            "--verbose",
            "--show-limits",
            "--max-procs=0",
            "--help",
            "--version",
        }
    ),
}


def _program(token: str) -> str:
    name = os.path.basename(token).lower()
    return name.removesuffix(".exe")


def _matches_flag(token: str, flags: frozenset[str]) -> Optional[str]:
    """Return the matching flag, supporting ``--flag=value`` spellings."""
    name = token.split("=", 1)[0]
    if name in flags:
        return name
    return None


def _skip_env(argv: Sequence[str], index: int) -> Optional[int]:
    while index < len(argv):
        token = argv[index]
        if _ASSIGNMENT_RE.fullmatch(token):
            index += 1
            continue
        if token in ("-u", "--unset", "-C", "--chdir"):
            index += 2
            continue
        if token == "--":
            return index + 1
        if (
            token == "-S"
            or token.startswith("-S")
            or token.startswith("--split-string")
        ):
            # The value is a second command line, not argv for this parser.
            return None
        if token.startswith("-"):
            index += 1
            continue
        return index
    return None


def _skip_launcher_options(
    program: str, argv: Sequence[str], index: int
) -> Optional[int]:
    values = _VALUE_FLAGS.get(program, frozenset())
    no_values = _NO_VALUE_FLAGS.get(program, frozenset())
    while index < len(argv):
        token = argv[index]
        if token == "--":
            return index + 1
        if not token.startswith("-") or token == "-":
            break
        flag = token.split("=", 1)[0]
        if flag in values and "=" not in token:
            index += 2
            continue
        if flag in values:
            index += 1
            continue
        if flag in no_values:
            index += 1
            continue
        # A short cluster can contain only no-value flags (for example
        # ``sudo -EH``).  Do not consume the next word for an unknown option.
        index += 1
    if program == "timeout":
        # timeout's first non-option operand is the duration, then the command.
        if index < len(argv):
            index += 1
    return index if index < len(argv) else None


def wrapped_command_argv(argv: Sequence[str]) -> Optional[List[str]]:
    """Return the command executed by one known launcher, or ``None``.

    The result is deliberately conservative: an unknown grammar returns the
    first plausible command instead of consuming its following operand.  The
    caller can then apply the normal policy to that command.
    """
    if not argv:
        return None
    values = list(argv)
    program = _program(values[0])
    if program == "env":
        index = _skip_env(values, 1)
    elif program in _POLICY_WRAPPERS:
        index = _skip_launcher_options(program, values, 1)
    elif program in _DIRECT_WRAPPERS:
        index = 1
        while index < len(values) and values[index].startswith("-"):
            index += 1
    else:
        verbs = _RUN_WRAPPERS.get(program)
        if verbs and len(values) > 1 and values[1].lower() in verbs:
            index = 2
            while index < len(values) and values[index].startswith("-"):
                index += 1
        else:
            return None
    return values[index:] if index is not None and index < len(values) else None


def unwrap_command_argv(argv: Sequence[str]) -> List[str]:
    """Remove nested known launchers and return the innermost command argv."""
    current = list(argv)
    # A depth cap keeps malformed input from turning policy inspection into an
    # unbounded loop; an unresolved remainder is safer than guessing further.
    for _ in range(16):
        inner = wrapped_command_argv(current)
        if not inner:
            return current
        current = inner
    return current


__all__ = ["wrapped_command_argv", "unwrap_command_argv"]
