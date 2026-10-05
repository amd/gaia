# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""What an "always allow" answer is allowed to grant.

A confirmation prompt shows one **call**. The grant it offers must not exceed
what that prompt described — otherwise a single keypress on ``gh auth token``
hands over unrestricted shell for the rest of the session, including commands
from a different skill and commands a prompt injection talks the model into.

So a grant is keyed on the *invocation*, not the tool name:

    run_shell_command  command="gh issue list"   ->  run_shell_command:gh issue list
    run_shell_command  command="cd p && python -m pytest -q 2>&1"
                                                 ->  run_shell_command:pytest
    write_file         file_path="notes.md"      ->  write_file:notes.md

A shell grant covers a command *family*: the program and its subcommand,
wherever it sits in a line. A ``cd``, a harmless redirection (``2>&1``) and a
read-only filter (``| tail``) do not change what was approved, so they do not
split the grant. A line running two families grants both, and a later line is
covered only when every family in it was granted. Deleting is never granted.

A tool with no scope rule returns ``None``, which means **"always" is not
offered at all** for that call. That is the safe default and it is honest: the
key is either narrow enough to describe in the prompt, or the user answers
y/n each time. Blanket session-wide trust has one home, and it is
full-access mode — explicit, indicated on every frame, and opted into
deliberately.

This replaces an earlier blanket ban on "always" for the shell tools. The ban's
reasoning was right — a tool name says nothing about what the next call will do
— but the remedy was too blunt: it removed the affordance instead of narrowing
it. Scoping keeps the affordance and takes away the blast radius.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from gaia.agents.base.call_risk import DESTRUCTIVE, shell_command_risk
from gaia.agents.base.command_facts import (
    PYTHON_BINARIES,
    binary_name,
    effective_argv,
    read_command,
)

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

#: Interpreters. Their name bounds nothing either, but running scripts is the
#: everyday work of a coding session, so they get a grant whose label SAYS it
#: covers any script — `python (any script)`, never a bare `python`.
_INTERPRETERS = frozenset({"node", "deno", "bun", "perl", "ruby", "php"})

#: Programs that only move the session or read what another command printed.
#: They never decide what a line may do, so they do not split a grant:
#: `cd proj && pytest | tail -5` is the same decision as `pytest`. Only programs
#: with no flag that writes a file or runs another program belong here — `sort -o`,
#: `uniq in out` and `rg --pre` do both, so they are families of their own.
_TRANSPARENT = frozenset({"cd", "pushd", "popd", "set-location"})
_READ_FILTERS = frozenset(
    {
        "head",
        "tail",
        "grep",
        "findstr",
        "wc",
        "cut",
        "tr",
        "column",
        "cat",
        "more",
        "echo",
        "printf",
        "true",
    }
)

#: Programs that take flags first as a matter of course and have no subcommand
#: a flag could redirect: `pytest -q tests/` is still `pytest`.
_FLAG_FIRST_BINARIES = frozenset({"pytest", "ls", "grep", "unittest"})

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
PATH_TOOLS = frozenset(
    {
        "write_file",
        "write_python_file",
        "write_markdown_file",
        "edit_file",
        "edit_python_file",
        "replace_function",
        "update_gaia_md",
        "save_extracted_items",
    }
)

_PATH_ARG_NAMES = ("file_path", "path", "filename", "file", "target_file")
_COMMAND_ARG_NAMES = ("command", "cmd", "script", "command_line")
_SKILL_ARG_NAMES = ("skill", "skill_name", "skill_id", "name")

# Each takes the skill as its first argument, so an "always" answer scopes to
# that one skill rather than to the tool at large.
_SKILL_TOOLS = frozenset({"install_skill", "remove_skill"})

#: `capture_skill` is deliberately NOT grantable — every capture prompts.
#: These scopes key on the skill NAME, but for capture the operative argument
#: is `source`: an "always allow capture_skill notes" would silently approve any
#: future source under that name, and the label would not describe what was
#: granted. Keying on `source` would not fix it either — a URL is not a stable
#: identity, since the bytes behind it can change between captures.
#: The prompt ``PathValidator`` raises when a tool reaches outside the session's
#: scope. Not a registered tool, and ungrantable: approving one path must never
#: become "always allow any path" — each new path is asked about on its own.
PATH_ACCESS_PROMPT_TOOL = "allow_path_access"

_UNGRANTABLE_TOOLS = frozenset({"capture_skill", PATH_ACCESS_PROMPT_TOOL})

#: Tools whose every call is the same kind of action, so the family IS the
#: tool. The label says how wide that is.
_FAMILY_TOOLS = {
    "run_python": "Python snippets (run_python)",
    "execute_python_file": "running Python files",
    "notify_desktop": "desktop notifications",
}


@dataclass(frozen=True)
class GrantScope:
    """One "always allow" grant: what gets recorded, and what to call it.

    ``keys`` are the families the grant records; a later call is covered only
    when every one of its keys was granted. ``label`` is what the prompt
    promises the user — the two must describe the same thing, because the
    label is the only account of the grant anyone ever reads.
    """

    keys: Tuple[str, ...]
    label: str

    @property
    def key(self) -> str:
        """The grant as one string — the single key of a one-family call."""
        return " + ".join(self.keys)


def grant_scope(tool_name: str, tool_args: Any) -> Optional[GrantScope]:
    """The grant an "always" answer to this call would create, or None.

    ``None`` means the UI must not offer "always" for this call.
    """
    args = tool_args if isinstance(tool_args, dict) else {}
    if tool_name in _UNGRANTABLE_TOOLS:
        return None
    if tool_name in _SHELL_TOOLS:
        return _shell_scope(tool_name, args)
    if tool_name in PATH_TOOLS:
        return _path_scope(tool_name, args)
    if tool_name in _SKILL_TOOLS:
        return _named_scope(tool_name, args, _SKILL_ARG_NAMES)
    if tool_name in _FAMILY_TOOLS:
        return GrantScope(keys=(tool_name,), label=_FAMILY_TOOLS[tool_name])
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
    facts = read_command(command)
    # Unreadable, or a redirect into a file the family does not name.
    if facts.opaque or facts.writes_file:
        return None
    # A delete is never a standing grant: each one is answered on its own.
    if shell_command_risk(command) == DESTRUCTIVE:
        return None

    segments = [s for s in facts.segments if binary_name(s[0]) not in _TRANSPARENT]
    significant = [s for s in segments if binary_name(s[0]) not in _READ_FILTERS]
    # A line that only reads (`echo hello`, `cat x | head`) is granted as itself.
    chosen = significant or segments[:1] or facts.segments[:1]

    labels: List[str] = []
    for segment in chosen:
        label = _family(segment)
        if label is None:
            return None
        if label not in labels:
            labels.append(label)
    keys = tuple(f"{tool_name}:{label}" for label in labels)
    return GrantScope(keys=keys, label=", ".join(labels))


def _family(argv: Tuple[str, ...]) -> Optional[str]:
    """The family one simple command belongs to, or None if it has none."""
    binary = binary_name(argv[0])
    if binary in PYTHON_BINARIES:
        # Only the interpreter's own options: `python -m pytest -c pytest.ini`
        # passes -c to pytest, not to python.
        own = []
        for token in argv[1:]:
            if not token.startswith("-") or token == "-m":
                break
            own.append(token)
        if "-c" in own:
            return "python -c (any inline Python)"
        argv = effective_argv(argv)
        binary = argv[0]
        if binary in PYTHON_BINARIES:
            return "python (any script)"
    elif binary in _INTERPRETERS:
        return f"{binary} (any script)"
    if not binary or binary in _UNBOUNDED_BINARIES:
        return None

    rest = argv[1:]
    # A flag before any subcommand redirects what the command acts on —
    # `git -C /elsewhere commit` is not `git commit`. Neither scope is honest:
    # `git commit` hides the redirection, and a bare `git` covers every
    # subcommand there is. So this call simply cannot be granted.
    if rest and rest[0].startswith("-") and binary not in _FLAG_FIRST_BINARIES:
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
    return " ".join(words)


def _is_subcommand_word(token: str) -> bool:
    """True for a bare subcommand word — not a flag, path, or argument value."""
    if not token or not token[0].isalpha():
        return False
    return all(ch.isalnum() or ch in "-_" for ch in token)


def path_argument(tool_args: Any) -> str:
    """The path a file-writing call targets, or "" when it names none."""
    args = tool_args if isinstance(tool_args, dict) else {}
    return _first_str(args, _PATH_ARG_NAMES)


def _path_scope(tool_name: str, args: Dict[str, Any]) -> Optional[GrantScope]:
    raw = path_argument(args)
    if not raw:
        return None
    # Normalised for matching, never resolved: this must stay a pure function of
    # the arguments, so it cannot depend on the filesystem or the process's cwd.
    normalised = posixpath.normpath(raw.replace("\\", "/"))
    display = posixpath.basename(normalised) or normalised
    return GrantScope(
        keys=(f"{tool_name}:{normalised}",), label=f"{tool_name} on {display}"
    )


def _named_scope(
    tool_name: str, args: Dict[str, Any], names: Sequence[str]
) -> Optional[GrantScope]:
    value = _first_str(args, names)
    if not value:
        return None
    return GrantScope(keys=(f"{tool_name}:{value}",), label=f"{tool_name} {value}")


__all__ = [
    "GrantScope",
    "PATH_ACCESS_PROMPT_TOOL",
    "PATH_TOOLS",
    "grant_scope",
    "path_argument",
]
