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
from typing import List, Optional, Sequence, Set

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
# These prefixes are understood by the denied-effect ledger only.  They are
# shell/platform syntax rather than safe policy launchers: the shell policy
# must not unwrap them and accidentally change an ordinary prompt.
_LEDGER_ONLY_WRAPPERS = frozenset({"call", "start", "&", "."})
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
_RUN_VALUE_FLAGS = {
    "conda": frozenset({"-n", "--name", "-p", "--prefix"}),
}
# Offline analysis handles xargs/parallel separately because their command is
# supplied through stdin; unwrapping xargs first would hide that uncertainty.
OFFLINE_WRAPPERS = (_POLICY_WRAPPERS - {"xargs"}) | _LEDGER_ONLY_WRAPPERS

# Flags whose next token is an operand for each launcher.  Keep these separate:
# ``sudo -n`` and ``sudo -E`` take no value, while ``nice -n 5`` does.
_VALUE_FLAGS = {
    "sudo": frozenset(
        {
            "-u",
            "--user",
            "-g",
            "--group",
            "-C",
            "--close-from",
            "-D",
            "--chdir",
            "-p",
            "--prompt",
            "-r",
            "--role",
            "-t",
            "--type",
            "-T",
            "--command-timeout",
            "-U",
            "--other-user",
            "-R",
            "--chroot",
        }
    ),
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
            "-I",
            "-L",
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

_ENV_VALUE_FLAGS = frozenset({"-u", "--unset", "-C", "--chdir", "-P", "-a", "--argv0"})
_ENV_NO_VALUE_FLAGS = frozenset(
    {
        "-i",
        "--ignore-environment",
        "-0",
        "--null",
        "-v",
        "--debug",
        "--block-signal",
        "--default-signal",
        "--ignore-signal",
        "--list-signal-handling",
    }
)

_NO_VALUE_FLAGS = {
    "sudo": frozenset({"-E", "-H", "-n", "-P", "-S", "-s", "-b", "-e"}),
    "doas": frozenset({"-C", "-L", "-n", "-s"}),
    "timeout": frozenset(
        {"--foreground", "--preserve-status", "-v", "--verbose", "--help", "--version"}
    ),
    "setsid": frozenset({"-f", "-w", "-c", "--wait", "--fork", "--ctty"}),
    "nohup": frozenset({"--help", "--version"}),
    "time": frozenset(
        {"-a", "--append", "-p", "--portability", "-q", "--quiet", "-v", "--verbose"}
    ),
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
            "--eof",
            "--max-lines",
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
        if token in _ENV_VALUE_FLAGS:
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
            index += 1
            break
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
            value_flags = _RUN_VALUE_FLAGS.get(program, frozenset())
            while index < len(values):
                token = values[index]
                if token == "--":
                    index += 1
                    break
                if token in value_flags:
                    index += 2
                    continue
                if any(token.startswith(flag + "=") for flag in value_flags):
                    index += 1
                    continue
                if token.startswith("-"):
                    index += 1
                    continue
                break
        else:
            return None
    return values[index:] if index is not None and index < len(values) else None


def _option_ends(program: str, argv: Sequence[str], index: int) -> Set[int]:
    """Every index where *program*'s own options could end.

    An option the table doesn't know may or may not take the next word, so
    both readings are followed rather than guessing one.
    """
    if program == "env":
        values = _ENV_VALUE_FLAGS
        no_values = _ENV_NO_VALUE_FLAGS
    else:
        values = _VALUE_FLAGS.get(program, frozenset())
        no_values = _NO_VALUE_FLAGS.get(program, frozenset())
    ends: Set[int] = set()
    seen: Set[int] = set()
    pending = [index]
    while pending:
        i = pending.pop()
        if i in seen or i >= len(argv):
            continue
        seen.add(i)
        token = argv[i]
        if program == "env" and _ASSIGNMENT_RE.fullmatch(token):
            pending.append(i + 1)
            continue
        if token == "--":
            ends.add(i + 1)
            continue
        if not token.startswith("-") or token == "-":
            ends.add(i)
            continue
        flag = token.split("=", 1)[0]
        if program == "env" and (flag.startswith("-S") or flag == "--split-string"):
            # The value is a second command line, not argv for this parser.
            continue
        if flag in values:
            pending.append(i + (1 if "=" in token else 2))
        elif flag in no_values:
            pending.append(i + 1)
        elif not flag.startswith("--") and token[:2] in values:
            pending.append(i + 1)  # a short flag with its value attached
        else:
            pending.extend((i + 1, i + 2))
    if program == "timeout":
        ends = {end + 1 for end in ends}  # the duration comes before the command
    return {end for end in ends if end < len(argv)}


def command_starts(argv: Sequence[str]) -> List[int]:
    """Every position after 0 where a command run by known launchers may start.

    For the shell policy: a refusal at any of these positions must hold,
    whichever reading of an unfamiliar launcher option is the real one.
    Each position is past the one before it, so the search always ends.
    """
    if not argv:
        return []
    found: Set[int] = set()
    pending = [0]
    while pending:
        start = pending.pop()
        program = _program(argv[start])
        if program == "env" or program in _POLICY_WRAPPERS:
            inner = {
                start + 1 + end for end in _option_ends(program, argv[start + 1 :], 0)
            }
        else:
            rest = wrapped_command_argv(argv[start:])
            inner = {len(argv) - len(rest)} if rest else set()
        for position in inner - found:
            found.add(position)
            pending.append(position)
    return sorted(found)


def _ledger_wrapper_argv(argv: Sequence[str]) -> Optional[List[str]]:
    """Return an inner command for a platform prefix used by effect tracking."""
    if not argv:
        return None
    program = _program(argv[0])
    if program in _LEDGER_ONLY_WRAPPERS:
        return list(argv[1:]) or None
    if program != "conda" or len(argv) < 2 or argv[1].lower() != "run":
        return None
    index = 2
    while index < len(argv):
        token = argv[index]
        if token == "--":
            return list(argv[index + 1 :]) or None
        if token in {"-n", "--name", "-p", "--prefix"}:
            index += 2
            continue
        if token.startswith("--name=") or token.startswith("--prefix="):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        return list(argv[index:])
    return None


def unwrap_command_argv(argv: Sequence[str], *, ledger: bool = False) -> List[str]:
    """Remove nested known launchers and return the innermost command argv."""
    current = list(argv)
    # A depth cap keeps malformed input from turning policy inspection into an
    # unbounded loop; an unresolved remainder is safer than guessing further.
    for _ in range(16):
        inner = wrapped_command_argv(current)
        if ledger and inner is None:
            inner = _ledger_wrapper_argv(current)
        if not inner:
            return current
        current = inner
    return current


__all__ = [
    "OFFLINE_WRAPPERS",
    "command_starts",
    "wrapped_command_argv",
    "unwrap_command_argv",
]
