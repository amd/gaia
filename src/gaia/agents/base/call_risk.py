# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The label a confirmation prompt shows: what this call does, not what its tool could do.

A tool name does not bound a shell command — ``run_shell_command`` is ``pytest``
on one call and ``rm -rf`` on the next — so labelling by name called both
DESTRUCTIVE. A label that is wrong on the safe calls is the one people learn to
ignore on the dangerous ones.

Four risks, ordered; a line gets the strictest any of its commands earns:

* ``read`` — looks, changes nothing.
* ``write`` — creates or changes files or repository state.
* ``execute`` — runs code GAIA cannot see into (tests, scripts, unknown programs).
* ``destructive`` — deletes or discards, and may not be reversible.

This only picks the words on the prompt. Whether a call may run at all is
decided before the prompt, by the tool's own validator.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from gaia.agents.base.command_facts import effective_argv, read_command

READ = "read"
WRITE = "write"
EXECUTE = "execute"
DESTRUCTIVE = "destructive"

_ORDER = {READ: 0, WRITE: 1, EXECUTE: 2, DESTRUCTIVE: 3}

#: Display-only prompt argument carrying the risk note of a call GAIA's own
#: checks would otherwise refuse. Such a call is approved once or not at all,
#: so no "always" is offered for it and none can cover it.
RISK_NOTE_ARG = "_risk_note"

#: Prompt argument marking a call only a person may approve: a console that
#: pre-approves tools still asks about it, and never shows the marker itself.
NEEDS_PERSON_ARG = "_needs_person"

SHELL_TOOLS = frozenset({"run_shell_command", "run_cli_command", "wait_for_condition"})

_FILE_WRITE_TOOLS = frozenset(
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

_CODE_TOOLS = frozenset({"run_python", "execute_python_file", "dev_server"})

_READ_PROGRAMS = frozenset(
    {
        "ls",
        "dir",
        "pwd",
        "cd",
        "pushd",
        "popd",
        "cat",
        "type",
        "head",
        "tail",
        "less",
        "more",
        "grep",
        "rg",
        "findstr",
        "wc",
        "sort",
        "uniq",
        "cut",
        "tr",
        "column",
        "echo",
        "printf",
        "true",
        "which",
        "where",
        "whoami",
        "hostname",
        "date",
        "file",
        "stat",
        "du",
        "df",
        "tree",
        "diff",
        "jq",
        "printenv",
        "ps",
        "get-childitem",
        "get-content",
        "get-location",
        "select-string",
    }
)

_DESTRUCTIVE_PROGRAMS = frozenset(
    {
        "rm",
        "rmdir",
        "del",
        "erase",
        "rd",
        "shred",
        "dd",
        "mkfs",
        "format",
        "truncate",
        "remove-item",
        "kill",
        "pkill",
        "killall",
        "taskkill",
        "stop-process",
    }
)

_WRITE_PROGRAMS = frozenset(
    {
        "mv",
        "cp",
        "mkdir",
        "touch",
        "ln",
        "chmod",
        "chown",
        "tee",
        "copy",
        "move",
        "ren",
        "rename",
        "md",
        "copy-item",
        "move-item",
        "new-item",
        "set-content",
        "add-content",
        "out-file",
    }
)

#: git subcommands that only look.
_GIT_READS = frozenset(
    {
        "status",
        "log",
        "diff",
        "show",
        "blame",
        "grep",
        "ls-files",
        "rev-parse",
        "describe",
        "shortlog",
        "reflog",
        "fetch",
    }
)

#: Package managers: these words install or change dependencies; anything else
#: they are asked to do (`npm test`, `npm run build`) runs project code.
_PACKAGE_MANAGERS = frozenset(
    {"npm", "pnpm", "yarn", "pip", "pip3", "uv", "cargo", "go"}
)
_INSTALL_WORDS = frozenset(
    {
        "install",
        "i",
        "add",
        "uninstall",
        "remove",
        "update",
        "upgrade",
        "ci",
        "sync",
        "get",
    }
)

#: `gh <group> <verb>`: these verbs only read.
_GH_READ_VERBS = frozenset({"list", "view", "status", "diff", "checks", "search"})


def _stricter(a: str, b: str) -> str:
    return a if _ORDER[a] >= _ORDER[b] else b


#: git options before the subcommand that take a value: `git -C . clean` is `clean`.
_GIT_VALUE_OPTIONS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})


def _git_risk(args: Tuple[str, ...]) -> str:
    rest = list(args)
    while rest and rest[0].startswith("-"):
        option = rest.pop(0)
        if option in _GIT_VALUE_OPTIONS and rest:
            rest.pop(0)
    args = tuple(rest)
    words = [a for a in args if not a.startswith("-")]
    sub = words[0].lower() if words else ""
    flags = {a for a in args if a.startswith("-")}
    if sub == "remote":
        return READ if len(words) == 1 or words[1] in ("show", "get-url") else WRITE
    if sub == "config":
        reads = {"--get", "--get-all", "--get-regexp", "--list", "-l"}
        return READ if flags & reads else WRITE
    if sub in ("clean",):
        return DESTRUCTIVE
    if sub == "reset" and "--hard" in flags:
        return DESTRUCTIVE
    if sub == "push" and flags & {
        "-f",
        "--force",
        "--force-with-lease",
        "--delete",
        "-d",
    }:
        return DESTRUCTIVE
    if sub == "branch" and flags & {"-D", "-d", "--delete"}:
        return DESTRUCTIVE
    if sub == "stash" and len(words) > 1 and words[1] in ("drop", "clear"):
        return DESTRUCTIVE
    if sub == "restore" or (sub == "checkout" and ("--" in args or "." in words[1:])):
        return DESTRUCTIVE
    if sub == "branch" and len(words) == 1:
        return READ
    if sub in _GIT_READS:
        return READ
    return WRITE


#: Programs that run the command they are handed; the risk is that command's.
_WRAPPERS = frozenset(
    {"env", "sudo", "doas", "nohup", "timeout", "xargs", "time", "nice"}
)


def _unwrap(argv: Tuple[str, ...]) -> Tuple[str, ...]:
    """Drop a wrapper and its own flags, values and assignments."""
    rest = list(argv[1:])
    while rest and (
        rest[0].startswith("-") or "=" in rest[0] or rest[0].replace(".", "").isdigit()
    ):
        rest.pop(0)
    return tuple(rest)


def _segment_risk(argv: Tuple[str, ...]) -> str:
    argv = effective_argv(argv)
    if argv[0] in _WRAPPERS:
        inner = _unwrap(argv)
        return _segment_risk(inner) if inner else READ
    program = argv[0]
    args = argv[1:]
    if program in _DESTRUCTIVE_PROGRAMS:
        return DESTRUCTIVE
    if program == "find":
        if any(a in ("-delete",) for a in args):
            return DESTRUCTIVE
        if any(a in ("-exec", "-execdir", "-ok", "-okdir") for a in args):
            return EXECUTE
        return READ
    if program == "rg" and any(a.startswith("--pre") for a in args):
        return EXECUTE
    if program == "sed":
        return WRITE if any(a.startswith("-i") for a in args) else READ
    if program == "git":
        return _git_risk(args)
    if program == "gh":
        words = [a for a in args if not a.startswith("-")]
        if len(words) >= 2 and words[1] in _GH_READ_VERBS:
            return READ
        return WRITE
    if program in _PACKAGE_MANAGERS:
        words = [a for a in args if not a.startswith("-")]
        if words and words[0] in _INSTALL_WORDS:
            return WRITE
        return EXECUTE
    if program in _READ_PROGRAMS:
        return READ
    if program in _WRITE_PROGRAMS:
        return WRITE
    # Test runners, interpreters, build tools and every program this table has
    # never heard of: GAIA cannot see what they touch, so it says so — without
    # claiming they destroy anything.
    return EXECUTE


def shell_command_risk(command: str) -> str:
    """The risk of one shell command line."""
    facts = read_command(command)
    if facts.opaque:
        return EXECUTE
    risk = WRITE if facts.writes_file else READ
    for segment in facts.segments:
        risk = _stricter(risk, _segment_risk(segment))
    return risk


def call_risk(tool_name: str, tool_args: Any) -> Optional[str]:
    """The risk label for one gated call, or None when this module has no opinion.

    None leaves the label to the client's own per-tool table (email sends,
    calendar actions), which is what it was before this existed.
    """
    args: Dict[str, Any] = tool_args if isinstance(tool_args, dict) else {}
    if tool_name in SHELL_TOOLS:
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            return EXECUTE
        risk = shell_command_risk(command)
        # A call GAIA's checks would refuse is never labelled a mere read.
        return _stricter(risk, WRITE) if args.get(RISK_NOTE_ARG) else risk
    if tool_name in _CODE_TOOLS:
        return EXECUTE
    if tool_name in _FILE_WRITE_TOOLS:
        return WRITE
    return None


#: The plain line a command prompt shows under the command. A read needs no
#: warning, and front-ends already print their own sentence for a delete.
_PLAIN_NOTES = {
    WRITE: "Changes files or repository state where it runs.",
    EXECUTE: "Can run any code, with your permissions.",
}


def plain_risk_note(risk: Optional[str]) -> str:
    """One plain sentence for *risk* on a command prompt, or "" when none is due."""
    return _PLAIN_NOTES.get(risk or "", "")


__all__ = [
    "DESTRUCTIVE",
    "EXECUTE",
    "NEEDS_PERSON_ARG",
    "READ",
    "RISK_NOTE_ARG",
    "SHELL_TOOLS",
    "WRITE",
    "call_risk",
    "plain_risk_note",
    "shell_command_risk",
]
