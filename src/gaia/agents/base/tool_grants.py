# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""What an "always allow" answer is allowed to grant.

A confirmation prompt shows one **call**. The grant it offers must not exceed
what that prompt described — otherwise a single keypress on ``gh auth token``
hands over unrestricted shell for the rest of the session, including commands
from a different skill and commands a prompt injection talks the model into.

So a grant is keyed on the *invocation*, not the tool name:

    run_shell_command  command="gh issue list"   ->  run_shell_command:gh issue list
    write_file         file_path="notes.md"      ->  write_file:notes.md

A tool with no scope rule returns ``None``, which means **"always" is not
offered at all** for that call. That is the safe default and it is honest: the
key is either narrow enough to describe in the prompt, or the user answers
y/n each time. Blanket session-wide trust has one home, and it is
bypass-permissions mode — explicit, indicated on every frame, and opted into
deliberately.

This replaces an earlier blanket ban on "always" for the shell tools. The ban's
reasoning was right — a tool name says nothing about what the next call will do
— but the remedy was too blunt: it removed the affordance instead of narrowing
it. Scoping keeps the affordance and takes away the blast radius.
"""

from __future__ import annotations

import ntpath
import posixpath
import shlex
from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence

#: Shell metacharacters. A command containing any of them runs more than one
#: thing, so no single scope describes it — the grant is refused outright
#: rather than keyed on whichever binary happens to come first.
_SHELL_METACHARACTERS = ("|", "&", ";", ">", "<", "`", "$(", "\n", "\r")

#: Binaries whose name bounds nothing: they run whatever they are handed, so
#: "allow `bash` this session" is "allow everything this session" wearing a
#: narrower label. These never produce a grant.
_UNBOUNDED_BINARIES = frozenset(
    {
        "bash",
        "sh",
        "zsh",
        "fish",
        "dash",
        "csh",
        "ksh",
        "cmd",
        "command",
        "powershell",
        "pwsh",
        "python",
        "python3",
        "py",
        "node",
        "deno",
        "bun",
        "perl",
        "ruby",
        "php",
        "osascript",
        "env",
        "nohup",
        "xargs",
        "eval",
        "exec",
        "sudo",
        "doas",
        "start",
        "npx",
        "pnpx",
        "uvx",
    }
)

#: How many words after the binary a shell grant may cover. Two is enough for
#: the command-group/subcommand shape most CLIs use (``gh issue list``,
#: ``git remote add``) and stops well short of the arguments.
_MAX_SHELL_SCOPE_WORDS = 2

#: ``wait_for_condition`` belongs here because its ``command`` is a shell
#: command like any other — scoping the grant to it keeps "always" from becoming
#: "any command, as long as you poll with it".
_SHELL_TOOLS = frozenset({"run_shell_command", "run_cli_command", "wait_for_condition"})

#: Tools whose blast radius is one path. The grant is that exact path — not its
#: directory: the prompt named a file, so the grant covers a file.
_PATH_TOOLS = frozenset(
    {
        "write_file",
        "write_python_file",
        "write_markdown_file",
        "edit_file",
        "edit_python_file",
        "replace_function",
        "update_gaia_md",
    }
)

_PATH_ARG_NAMES = ("file_path", "path", "filename", "file", "target_file")
_COMMAND_ARG_NAMES = ("command", "cmd", "script", "command_line")
_SKILL_ARG_NAMES = ("skill", "skill_name", "skill_id", "name")

_SKILL_TOOLS = frozenset({"install_skill", "remove_skill"})


@dataclass(frozen=True)
class GrantScope:
    """One "always allow" grant: what gets recorded, and what to call it.

    ``key`` is matched exactly on later calls. ``label`` is what the prompt
    promises the user — the two must describe the same thing, because the
    label is the only account of the grant anyone ever reads.
    """

    key: str
    label: str


def grant_scope(tool_name: str, tool_args: Any) -> Optional[GrantScope]:
    """The grant an "always" answer to this call would create, or None.

    ``None`` means the UI must not offer "always" for this call.
    """
    args = tool_args if isinstance(tool_args, dict) else {}
    if tool_name in _SHELL_TOOLS:
        return _shell_scope(tool_name, args)
    if tool_name in _PATH_TOOLS:
        return _path_scope(tool_name, args)
    if tool_name in _SKILL_TOOLS:
        return _named_scope(tool_name, args, _SKILL_ARG_NAMES)
    return None


def _first_str(args: Dict[str, Any], names: Sequence[str]) -> str:
    for name in names:
        value = args.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _shell_scope(tool_name: str, args: Dict[str, Any]) -> Optional[GrantScope]:
    command = _first_str(args, _COMMAND_ARG_NAMES)
    if not command:
        return None
    if any(meta in command for meta in _SHELL_METACHARACTERS):
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        # Unbalanced quotes: the command cannot be read, so it cannot be scoped.
        return None
    if not tokens:
        return None

    binary = _binary_name(tokens[0])
    if not binary or binary in _UNBOUNDED_BINARIES:
        return None

    rest = tokens[1:]
    # A flag before any subcommand redirects what the command acts on —
    # `git -C /elsewhere commit` is not `git commit`. Neither scope is honest:
    # `git commit` hides the redirection, and a bare `git` covers every
    # subcommand there is. So this call simply cannot be granted.
    if rest and rest[0].startswith("-"):
        return None

    words = [binary]
    for token in rest:
        if len(words) > _MAX_SHELL_SCOPE_WORDS:
            break
        # Stop at the first token that is not a plain subcommand word — flags,
        # paths, and argument values all end the scope rather than joining it.
        if not _is_subcommand_word(token):
            break
        words.append(token)

    label = " ".join(words)
    return GrantScope(key=f"{tool_name}:{label}", label=label)


def _binary_name(token: str) -> str:
    """The bare program name, with any directory and .exe suffix removed."""
    name = ntpath.basename(posixpath.basename(token)).lower()
    for suffix in (".exe", ".cmd", ".bat", ".com", ".ps1"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name


def _is_subcommand_word(token: str) -> bool:
    """True for a bare subcommand word — not a flag, path, or argument value."""
    if not token or not token[0].isalpha():
        return False
    return all(ch.isalnum() or ch in "-_" for ch in token)


def _path_scope(tool_name: str, args: Dict[str, Any]) -> Optional[GrantScope]:
    raw = _first_str(args, _PATH_ARG_NAMES)
    if not raw:
        return None
    # Normalised for matching, never resolved: this must stay a pure function of
    # the arguments, so it cannot depend on the filesystem or the process's cwd.
    normalised = posixpath.normpath(raw.replace("\\", "/"))
    display = posixpath.basename(normalised) or normalised
    return GrantScope(
        key=f"{tool_name}:{normalised}", label=f"{tool_name} on {display}"
    )


def _named_scope(
    tool_name: str, args: Dict[str, Any], names: Sequence[str]
) -> Optional[GrantScope]:
    value = _first_str(args, names)
    if not value:
        return None
    return GrantScope(key=f"{tool_name}:{value}", label=f"{tool_name} {value}")


__all__ = ["GrantScope", "grant_scope"]
