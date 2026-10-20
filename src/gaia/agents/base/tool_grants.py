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

import glob
import os
import posixpath
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from gaia.agents.base.call_risk import DESTRUCTIVE, RISK_NOTE_ARG, shell_command_risk
from gaia.agents.base.command_facts import (
    PYTHON_BINARIES,
    binary_name,
    effective_argv,
    line_words,
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

#: Families that run code the command itself does not show — test files, a
#: Makefile, a package's setup script. Their grant label says so, because the
#: code behind the same family can change between one prompt and the next.
#: Matched against the family's leading words.
_RUNS_CODE = {
    "python -m": "runs that Python module",
    "pytest": "runs the test code it finds",
    "unittest": "runs the test code it finds",
    "jest": "runs the test code it finds",
    "vitest": "runs the test code it finds",
    "mocha": "runs the test code it finds",
    "tox": "runs the project's configured commands",
    "nox": "runs the project's configured commands",
    "make": "runs the Makefile's commands",
    "gmake": "runs the Makefile's commands",
    "nmake": "runs the Makefile's commands",
    "ninja": "runs the build file's commands",
    "cmake": "runs the project's build scripts",
    "mvn": "runs the project's build scripts",
    "gradle": "runs the project's build scripts",
    "gradlew": "runs the project's build scripts",
    "rake": "runs the Rakefile's tasks",
    "go run": "builds and runs Go code",
    "go test": "runs Go test code",
    "go generate": "runs commands named in Go files",
    "cargo run": "builds and runs Rust code",
    "cargo test": "runs Rust test code",
    "cargo build": "runs the crates' build scripts",
    "dotnet run": "builds and runs .NET code",
    "dotnet test": "runs .NET test code",
    "npm run": "runs the package's scripts",
    "npm test": "runs the package's scripts",
    "npm start": "runs the package's scripts",
    "npm install": "runs the package's scripts",
    "npm i": "runs the package's scripts",
    "npm ci": "runs the package's scripts",
    "npm add": "runs the package's scripts",
    "npm rebuild": "runs the package's scripts",
    "pnpm run": "runs the package's scripts",
    "pnpm test": "runs the package's scripts",
    "pnpm start": "runs the package's scripts",
    "pnpm install": "runs the package's scripts",
    "pnpm i": "runs the package's scripts",
    "pnpm ci": "runs the package's scripts",
    "pnpm add": "runs the package's scripts",
    "pnpm rebuild": "runs the package's scripts",
    "yarn": "runs the package's scripts",
    "pip install": "runs the package's setup code",
    "pip3 install": "runs the package's setup code",
    "uv pip": "runs the package's setup code",
    "uv sync": "runs the project's setup code",
    "uv build": "runs the project's setup code",
    "poetry install": "runs the project's setup code",
    "pipenv install": "runs the package's setup code",
    "pdm install": "runs the project's setup code",
}

#: ``<tool> <verb>`` that runs whatever follows it, like a shell would. The
#: family is the wrapper plus what it runs: `uv run pytest`, never `uv run`.
_RUN_WRAPPERS = frozenset(
    {
        ("uv", "run"),
        ("poetry", "run"),
        ("pipenv", "run"),
        ("pdm", "run"),
        ("hatch", "run"),
        ("rye", "run"),
        ("conda", "run"),
        ("bundle", "exec"),
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

#: Prompt argument set by the agent (never the model) when a script it runs sits
#: in its own scratch folder. Such a call is never offered "always".
SCRATCH_ARG = "_scratch_dir"

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
    #: The file or skill the grant names, for a front-end to phrase in its own words.
    subject: str = ""

    @property
    def key(self) -> str:
        """The grant as one string — the single key of a one-family call."""
        return " + ".join(self.keys)


def grant_scope(tool_name: str, tool_args: Any) -> Optional[GrantScope]:
    """The grant an "always" answer to this call would create, or None.

    ``None`` means the UI must not offer "always" for this call.
    """
    args = tool_args if isinstance(tool_args, dict) else {}
    if tool_name in _UNGRANTABLE_TOOLS or args.get(RISK_NOTE_ARG):
        return None
    # Scratch writes never ask, so no prompt shows what a later run there does.
    if args.get(SCRATCH_ARG):
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


def is_inside_folder(path: str, folder: str) -> bool:
    """True when *path* resolves to something strictly inside *folder*.

    Both sides go through ``realpath``, so a symlink, junction, 8.3 name or
    ``..`` is judged by where it really lands, never by how it is spelled. A
    spelling ``realpath`` leaves distinct (``\\\\?\\``, a UNC share, a case
    variant on macOS) is caught by file identity. A path that cannot be
    resolved counts as inside: the caller then asks instead of trusting it.
    """
    if not path or not folder:
        return False
    try:
        real_path = os.path.realpath(path)
        real_folder = os.path.realpath(folder)
    except (OSError, ValueError):
        return True
    case_path, case_folder = os.path.normcase(real_path), os.path.normcase(real_folder)
    try:
        if case_path != case_folder and (
            os.path.commonpath([case_path, case_folder]) == case_folder
        ):
            return True
    except ValueError:
        pass  # different drives or roots: fall through to file identity
    parent = os.path.dirname(real_path)
    while True:
        try:
            if os.path.samefile(parent, real_folder):
                return True
        except OSError:
            pass
        up = os.path.dirname(parent)
        if up == parent:
            return False
        parent = up


#: Programs that move the rest of a line into another folder.
_CHDIR = frozenset({"cd", "pushd", "chdir", "set-location", "sl"})

#: Programs that return to a folder the line does not name.
_CHDIR_BACK = frozenset({"popd", "pop-location"})

#: A variable the shell may fill in that GAIA's own environment does not hold.
_UNSET_VARIABLE = re.compile(r"\$(\{|[A-Za-z_])")
_WHOLE_VARIABLE = re.compile(r"\$(\{\w+\}|\w+)")

#: Wildcard matches checked before a word is counted as reaching the folder.
_MAX_GLOB_MATCHES = 200


def command_reaches_folder(command: str, cwds: Sequence[str], folder: str) -> bool:
    """True when a shell line runs in *folder*, or any word of it lands there.

    Every word — arguments, ``--opt=value`` and ``NAME=value`` values, a
    ``PATH``-style list — is read as a path against each folder the line may
    run in: the given ones and every ``cd`` target, so relative and ``..``
    spellings count. Each is judged by :func:`is_inside_folder`, so links and
    other spellings of *folder* do too. Anything that cannot be pinned down
    counts as reaching it: an unreadable line, a variable the line sets
    itself, ``cd -``, a network path.
    """
    if not folder or not isinstance(command, str) or not command.strip():
        return False
    lines = line_words(command)
    if lines is None:
        return True
    lands = _Lands(folder)
    bases = [c for c in cwds if c] or [os.getcwd()]
    if any(lands(base) for base in bases):
        return True
    for words in lines:
        program = binary_name(words[0])
        if program in _CHDIR_BACK or (program in _CHDIR and "-" in words[1:]):
            return True
        if program in _CHDIR:
            # Every word may be the target: `cd /d X` on cmd, `-Path X` on pwsh.
            targets = [w for w in words[1:] if not w.startswith("-")] or ["~"]
            if any(_UNSET_VARIABLE.search(_spellings(t)[-1]) for t in targets):
                return True
            moved = [
                os.path.join(base, spelling)
                for base in bases
                for target in targets
                for spelling in _spellings(target)
            ]
            if any(lands(path) for path in moved):
                return True
            bases.extend(moved)
        for word in words:
            for value in _path_candidates(word):
                spellings = _spellings(value)
                if _names_an_unknown_path(spellings[-1]):
                    return True
                for spelling in spellings:
                    if any(lands(os.path.join(base, spelling)) for base in bases):
                        return True
    return False


def _names_an_unknown_path(text: str) -> bool:
    """True when the shell, not GAIA, decides which path *text* names.

    `$A` or `$A/x` does; `awk '{print $NF}'` and `jq '$x'` name no path.
    """
    if not _UNSET_VARIABLE.search(text):
        return False
    return bool(_WHOLE_VARIABLE.fullmatch(text)) or "/" in text or "\\" in text


def _path_candidates(word: str) -> List[str]:
    """Every reading of one shell word that could name a path."""
    found = [word]
    if "=" in word:
        found.append(word.split("=", 1)[1])
    if word.startswith("-") and not word.startswith("--") and len(word) > 2:
        found.append(word[2:])  # -Cdir, -rreq.txt
    for value in list(found):
        # A URL's `//host` is not a path list's second entry.
        seps = (";", ",") if "://" in value else (os.pathsep, ";", ":", ",")
        for sep in seps:
            if sep in value:
                found.extend(part for part in value.split(sep) if len(part) > 1)
    return [value for value in found if value]


def _spellings(word: str) -> List[str]:
    """*word* as written, and with ``~`` and environment variables expanded."""
    expanded = os.path.expandvars(os.path.expanduser(word))
    return [word] if expanded == word else [word, expanded]


def _is_network_path(path: str) -> bool:
    """A Windows path on another machine: looking at it would contact that host."""
    if os.name != "nt":
        return False
    path = path.replace("/", "\\")
    if path.startswith(("\\\\?\\", "\\\\.\\")):
        rest = path[4:]
        return rest[:4].upper() == "UNC\\" or not (len(rest) > 1 and rest[1] == ":")
    return path.startswith("\\\\")


class _Lands:
    """:func:`is_inside_folder` for one folder, or the folder itself.

    One shell line asks about many paths under the same few folders, so the
    folder's identity and each parent's are looked up once. A network path is
    never looked at, and counts as landing there.
    """

    def __init__(self, folder: str) -> None:
        self.folder = folder
        try:
            self.real = os.path.normcase(os.path.realpath(folder))
            self.stat = os.stat(folder)
        except (OSError, ValueError):
            self.real, self.stat = "", None
        self.seen: Dict[str, bool] = {}
        self.asked: Dict[str, bool] = {}

    def __call__(self, path: str) -> bool:
        if path not in self.asked:
            self.asked[path] = self._lands_any(path)
        return self.asked[path]

    def _lands_any(self, path: str) -> bool:
        if _is_network_path(path):
            return True
        if not glob.has_magic(path):
            return self._lands(path)
        if self._lands(path):
            return True
        # `scratch-?/new.py` matches nothing yet, but its folder part does.
        pattern = path
        while glob.has_magic(pattern):
            for count, match in enumerate(glob.iglob(pattern)):
                if count >= _MAX_GLOB_MATCHES or self._lands(match):
                    return True
            pattern = os.path.dirname(pattern)
        return False

    def _lands(self, path: str) -> bool:
        try:
            real = os.path.normcase(os.path.realpath(path))
        except (OSError, ValueError):
            return True
        if _is_network_path(real):
            return True
        if not self.real:
            return is_inside_folder(path, self.folder)
        try:
            if os.path.commonpath([real, self.real]) == self.real:
                return True
        except ValueError:
            pass  # different drives or roots: fall through to file identity
        while True:
            if real not in self.seen:
                self.seen[real] = self._same(real)
            if self.seen[real]:
                return True
            up = os.path.dirname(real)
            if up == real:
                return False
            real = up

    def _same(self, path: str) -> bool:
        try:
            return os.path.samestat(os.stat(path), self.stat)
        except (OSError, ValueError):
            return False


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

    families: List[str] = []
    labels: List[str] = []
    for segment in chosen:
        found = _family(segment)
        if found is None:
            return None
        family, label = found
        if family not in families:
            families.append(family)
            labels.append(label)
    keys = tuple(f"{tool_name}:{family}" for family in families)
    return GrantScope(keys=keys, label=", ".join(labels))


def _family(argv: Tuple[str, ...]) -> Optional[Tuple[str, str]]:
    """``(family, label)`` for one simple command, or None if it has none.

    The family is what the grant records; the label is what the prompt shows,
    and it says when the family runs code the command line does not.
    """
    family = _family_name(argv)
    if family is None:
        return None
    words = family.split()
    if len(words) > 2 and (words[0], words[1]) in _RUN_WRAPPERS:
        words = words[2:]
    for n in (2, 1):
        what = _RUNS_CODE.get(" ".join(words[:n]))
        if what:
            return family, f"{family} ({what})"
    return family, family


def _family_name(argv: Tuple[str, ...]) -> Optional[str]:
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
        # A module is found in the working folder first, so `python -m explore`
        # runs whatever explore.py is there: it is never just "explore".
        if binary not in _RUNS_CODE and binary not in ("pip", "pip3", "uv"):
            if not all(_is_subcommand_word(part) for part in binary.split(".")):
                return None
            return f"python -m {binary}"
    elif binary in _INTERPRETERS:
        return f"{binary} (any script)"
    if not binary or binary in _UNBOUNDED_BINARIES:
        return None

    rest = argv[1:]
    if rest and (binary, rest[0]) in _RUN_WRAPPERS:
        inner = rest[1:]
        # Flags here may take a value, so where the command starts is unknown.
        if not inner or inner[0].startswith("-"):
            return None
        if not _is_subcommand_word(inner[0]):
            return f"{binary} {rest[0]} (any script)"
        inner_family = _family_name(tuple(inner))
        if inner_family is None:
            return None
        return f"{binary} {rest[0]} {inner_family}"

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
        keys=(f"{tool_name}:{normalised}",),
        label=f"{tool_name} on {display}",
        subject=display,
    )


def _named_scope(
    tool_name: str, args: Dict[str, Any], names: Sequence[str]
) -> Optional[GrantScope]:
    value = _first_str(args, names)
    if not value:
        return None
    return GrantScope(
        keys=(f"{tool_name}:{value}",), label=f"{tool_name} {value}", subject=value
    )


__all__ = [
    "GrantScope",
    "PATH_ACCESS_PROMPT_TOOL",
    "PATH_TOOLS",
    "RISK_NOTE_ARG",
    "SCRATCH_ARG",
    "command_reaches_folder",
    "grant_scope",
    "is_inside_folder",
    "path_argument",
]
