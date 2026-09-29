# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What a tool call would *do*, so a "no" covers the effect, not the tool name.

A user who declines ``run_shell_command("pytest")`` has declined running the
test suite. Writing ``run_tests.py`` and running it with ``execute_python_file``
reaches the same effect, and treating that as a fresh, unrelated call is how a
denial gets quietly routed around (#4447).

:func:`effects_of_call` reads a call's arguments into :class:`Effect` values —
a command family, a network host, a file path, or a script — and
:class:`DeniedEffects` keeps the ones a denial covered for the rest of the turn
so the loop can stop an overlapping call, whichever tool it arrives through.

Extraction is deliberately conservative about what it records from a denial:
text utilities in a pipeline (``| tail``) and ``cd`` are never recorded, so
declining ``cd repo && pytest | tail`` blocks the test run and nothing else.
"""

from __future__ import annotations

import ast
import logging
import os
import re
import shlex
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

COMMAND = "command"
NETWORK = "network"
PATH = "path"
SCRIPT = "script"

#: One effect however it is spelled: every runner here executes the test suite.
TEST_SUITE = "the test suite"
_TEST_RUNNERS = frozenset(
    {
        "pytest",
        "py.test",
        "pytest-3",
        "tox",
        "nox",
        "unittest",
        "nose2",
        "jest",
        "vitest",
        "mocha",
    }
)
#: ``npm test`` and friends: package-manager spellings of the same effect.
_PACKAGE_TEST_SCRIPTS = frozenset({"npm", "yarn", "pnpm", "bun"})

#: CLIs whose subcommand is part of the effect: declining ``git push`` must not
#: block ``git status``. Value = how many leading subcommand words count.
_SUBCOMMAND_DEPTH = {
    "gh": 2,
    "az": 2,
    "aws": 2,
    "gcloud": 2,
    "kubectl": 1,
    "git": 1,
    "npm": 1,
    "pnpm": 1,
    "yarn": 1,
    "bun": 1,
    "pip": 1,
    "uv": 1,
    "cargo": 1,
    "go": 1,
    "docker": 1,
    "dotnet": 1,
    "conda": 1,
    "poetry": 1,
    "brew": 1,
    "winget": 1,
    "choco": 1,
    "apt": 1,
    "apt-get": 1,
    "systemctl": 1,
}

#: Global flags that take a value, so the value is not read as a subcommand
#: (``kubectl -n prod delete`` is ``kubectl delete``, not ``kubectl prod``).
_VALUE_FLAGS = frozenset(
    {
        "-n",
        "--namespace",
        "-r",
        "--repo",
        "-c",
        "--context",
        "--kubeconfig",
        "--profile",
        "--region",
        "--project",
        "--subscription",
        "-g",
        "--resource-group",
        "-f",
        "--file",
        "--git-dir",
        "--work-tree",
        "--prefix",
    }
)

#: Programs whose URL arguments are requests. A URL in a commit message or an
#: echo is text, not a target.
_NETWORK_PROGRAMS = frozenset(
    {
        "curl",
        "wget",
        "invoke-webrequest",
        "invoke-restmethod",
        "iwr",
        "irm",
        "http",
        "https",
        "httpie",
        "xh",
        "aria2c",
        "git",
        "ssh",
        "scp",
        "sftp",
        "ftp",
        "nc",
        "ncat",
        "telnet",
    }
)
#: ``git commit -m "see https://…"`` mentions a URL; these contact one.
_GIT_NETWORK_SUBCOMMANDS = frozenset(
    {"clone", "fetch", "pull", "push", "ls-remote", "remote", "submodule"}
)
#: Python modules that make requests; a snippet importing none of them is not
#: treated as contacting the URLs it merely mentions.
_NETWORK_MODULES = frozenset(
    {"urllib", "urllib3", "requests", "httpx", "http", "aiohttp", "socket", "ftplib"}
)

#: Never recorded as denied: they shape another command's output or move the
#: shell, and blocking them for the rest of the turn would block everything.
_NEUTRAL_PROGRAMS = frozenset(
    {
        "cd",
        "chdir",
        "pushd",
        "popd",
        "set-location",
        "echo",
        "true",
        "false",
        "exit",
        "cat",
        "type",
        "head",
        "tail",
        "grep",
        "egrep",
        "findstr",
        "sort",
        "uniq",
        "wc",
        "tee",
        "more",
        "less",
        "select-string",
        "select-object",
        "where-object",
        "out-string",
        "out-null",
        "format-table",
        "format-list",
    }
)

#: Prefixes that run the next word as the program.
_WRAPPERS = frozenset(
    {"sudo", "time", "nice", "nohup", "exec", "command", "call", "start", "&", "."}
)
#: ``<runner> run <program>`` / ``<runner> exec <program>`` wrappers.
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
_DIRECT_WRAPPERS = frozenset({"npx", "uvx", "pipx", "bunx"})

_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "cmd", "powershell", "pwsh"})
_SHELL_COMMAND_FLAGS = frozenset({"-c", "/c", "/k", "-command", "-commandwithargs"})

_PYTHON_RE = re.compile(r"^(?:python|pythonw|py)(?:\d+(?:\.\d+)*)?$")
_EXE_SUFFIX_RE = re.compile(r"\.(?:exe|cmd|bat|ps1|com)$", re.IGNORECASE)
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

#: File-changing shell commands whose operands are the paths they change.
_PATH_MUTATORS = frozenset(
    {
        "rm",
        "rmdir",
        "del",
        "erase",
        "rd",
        "unlink",
        "truncate",
        "mv",
        "move",
        "ren",
        "rename",
        "remove-item",
        "ri",
        "move-item",
        "rename-item",
        "set-content",
        "clear-content",
        "out-file",
    }
)

#: Running or importing a test module is running (part of) the suite.
_TEST_MODULE_RE = re.compile(r"^(?:test_\w*|\w+_tests?)$")
#: A script by one of these names runs tests; importing ``tests.helpers`` does not.
_TEST_SCRIPT_RE = re.compile(r"^(?:test_\w*|\w+_tests?|run_?tests?\w*|tests?)$")

_URL_RE = re.compile(r"\bhttps?://([A-Za-z0-9.\-]+|\[[0-9A-Fa-f:.]+\])", re.IGNORECASE)

#: Argument names that name the file a mutating tool changes.
_PATH_ARG_NAMES = (
    "file_path",
    "path",
    "filepath",
    "filename",
    "target",
    "target_path",
    "destination",
    "dest",
    "save_to",
    "output_path",
    "directory",
)
_MUTATING_TOOL_RE = re.compile(
    r"(?:write|edit|delete|remove|move|rename|replace|append|create_file|save|download)",
    re.IGNORECASE,
)
_SHELL_ARG_NAMES = ("command", "cmd")

#: A script bigger than this is not read to find what it runs.
_MAX_SCRIPT_BYTES = 512 * 1024


@dataclass(frozen=True)
class Effect:
    """One thing a call does to the world that a user may say no to."""

    kind: str
    target: str

    def describe(self) -> str:
        """How the question to the user names this effect."""
        if self.kind == COMMAND:
            if self.target == TEST_SUITE:
                return "runs the test suite"
            return f"runs `{self.target}`"
        if self.kind == NETWORK:
            return f"contacts {self.target}"
        if self.kind == PATH:
            return f"changes {self.target}"
        return f"runs the script {self.target}"

    def overlaps(self, other: "Effect") -> bool:
        """True when doing *other* would do this effect too."""
        if self.kind != other.kind:
            return False
        if self.kind == COMMAND:
            mine, theirs = self.target.split(" "), other.target.split(" ")
            short = min(len(mine), len(theirs))
            return mine[:short] == theirs[:short]
        if self.kind == NETWORK:
            return (
                self.target == other.target
                or other.target.endswith("." + self.target)
                or self.target.endswith("." + other.target)
            )
        if self.kind == PATH:
            return _path_within(self.target, other.target) or _path_within(
                other.target, self.target
            )
        return self.target == other.target


def _path_within(child: str, parent: str) -> bool:
    if child == parent:
        return True
    parent = parent.rstrip("\\/")
    return child.startswith(parent + os.sep) or child.startswith(parent + "/")


def _norm_path(raw: str, cwd: str) -> Optional[str]:
    raw = raw.strip().strip("'\"")
    if not raw or "\x00" in raw or raw.startswith("-") or "://" in raw:
        return None
    try:
        expanded = os.path.expanduser(os.path.expandvars(raw))
        return os.path.normcase(os.path.abspath(os.path.join(cwd, expanded)))
    except (TypeError, ValueError):
        return None


def _hosts(text: str) -> Set[Effect]:
    return {
        Effect(NETWORK, m.group(1).lower().strip("[]").rstrip("."))
        for m in _URL_RE.finditer(text or "")
    }


# --------------------------------------------------------------------------- shell


def _split_segments(command: str) -> List[str]:
    """Split on ``&& || ; | & newline`` outside quotes."""
    segments: List[str] = []
    buf: List[str] = []
    quote: Optional[str] = None
    i = 0
    while i < len(command):
        ch = command[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch in ";|&\n":
            # ``2>&1`` / ``&>`` are redirects; PowerShell's ``& 'C:\x.exe'``
            # call operator starts a segment. Neither separates commands.
            redirect = ch == "&" and (
                command[i - 1 : i] == ">" or command[i + 1 : i + 2] == ">"
            )
            call_op = (
                ch == "&" and not "".join(buf).strip() and command[i + 1 : i + 2] != "&"
            )
            if redirect or call_op:
                buf.append(ch)
            else:
                segments.append("".join(buf))
                buf = []
                if command[i : i + 2] in ("&&", "||"):
                    i += 1
        else:
            buf.append(ch)
        i += 1
    segments.append("".join(buf))
    return [s.strip() for s in segments if s.strip()]


def _tokens(segment: str) -> List[str]:
    try:
        parts = shlex.split(segment, posix=False)
    except ValueError:
        parts = segment.split()
    return [
        p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "'\"" else p for p in parts
    ]


def _program(token: str) -> str:
    base = re.split(r"[\\/]", token)[-1].lower()
    return _EXE_SUFFIX_RE.sub("", base)


def _canonical_family(program: str, rest: Sequence[str]) -> Optional[str]:
    """``pytest`` → the test suite; ``gh issue close 1`` → ``gh issue close``."""
    if program in _NEUTRAL_PROGRAMS or not program:
        return None
    if program in _TEST_RUNNERS:
        return TEST_SUITE
    words: List[str] = []
    skip = False
    for w in rest:
        if skip:
            skip = False
        elif w.lower() in _VALUE_FLAGS:
            skip = True
        elif not w.startswith("-") and not w.startswith("/"):
            words.append(w.lower())
    if program in _PACKAGE_TEST_SCRIPTS and words[:1] == ["test"]:
        return TEST_SUITE
    if program in _PACKAGE_TEST_SCRIPTS and words[:2] == ["run", "test"]:
        return TEST_SUITE
    depth = _SUBCOMMAND_DEPTH.get(program, 0)
    return " ".join([program, *words[:depth]])


def _python_invocation(args: Sequence[str], cwd: str, depth: int) -> Set[Effect]:
    """``python -m pytest`` / ``python -c "..."`` / ``python run_tests.py``."""
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "-m" and i + 1 < len(args):
            module = args[i + 1].lower()
            family = _canonical_family(module.split(".")[0], args[i + 2 :])
            return {Effect(COMMAND, family)} if family else set()
        if arg == "-c" and i + 1 < len(args):
            return effects_of_python_code(args[i + 1], cwd, depth + 1)
        if arg in ("-W", "-X", "-Q"):
            i += 2
            continue
        if arg.startswith("-"):
            i += 1
            continue
        return effects_of_script(arg, cwd, depth + 1)
    return set()


def effects_of_shell_command(command: str, cwd: str, depth: int = 0) -> Set[Effect]:
    """Every effect a shell command line has, segment by segment."""
    if depth > 3 or not isinstance(command, str):
        return set()
    effects: Set[Effect] = set()
    for segment in _split_segments(command):
        tokens = _tokens(segment)
        # ``cd repo && python run_tests.py`` runs repo/run_tests.py.
        if (
            len(tokens) == 2
            and _program(tokens[0]) in ("cd", "chdir", "pushd", "set-location")
            and not tokens[1].startswith("-")
        ):
            cwd = _norm_path(tokens[1], cwd) or cwd
            continue
        redirect_targets: List[str] = []
        kept: List[str] = []
        for idx, tok in enumerate(tokens):
            if tok in (">", ">>", "1>", "2>", "*>") and idx + 1 < len(tokens):
                redirect_targets.append(tokens[idx + 1])
            elif re.match(r"^\d?>>?[^>&]", tok):
                redirect_targets.append(re.sub(r"^\d?>>?", "", tok))
            elif (idx > 0 and tokens[idx - 1] in (">", ">>", "1>", "2>", "*>")) or (
                ">" in tok
            ):
                continue
            else:
                kept.append(tok)
        for target in redirect_targets:
            if target.lower() not in ("/dev/null", "nul", "$null"):
                path = _norm_path(target, cwd)
                if path:
                    effects.add(Effect(PATH, path))
        effects |= _effects_of_argv(kept, cwd, depth)
    return effects


def _effects_of_argv(argv: Sequence[str], cwd: str, depth: int) -> Set[Effect]:
    """Effects of one already-tokenised command, unwrapping launchers."""
    tokens = list(argv)
    while tokens and _ASSIGNMENT_RE.match(tokens[0]):
        tokens.pop(0)
    while tokens:
        program = _program(tokens[0])
        if program in _WRAPPERS or program == "env":
            tokens.pop(0)
            while tokens and (
                _ASSIGNMENT_RE.match(tokens[0]) or tokens[0].startswith("-")
            ):
                tokens.pop(0)
            continue
        if program in _DIRECT_WRAPPERS:
            tokens.pop(0)
            while tokens and tokens[0].startswith("-"):
                tokens.pop(0)
            continue
        verbs = _RUN_WRAPPERS.get(program)
        if verbs and len(tokens) > 1 and tokens[1].lower() in verbs:
            tokens = tokens[2:]
            while tokens and tokens[0].startswith("-"):
                # ``conda run -n env`` takes a value.
                tokens = (
                    tokens[2:] if tokens[0] in ("-n", "--name", "-p") else tokens[1:]
                )
            continue
        break
    if not tokens:
        return set()
    program, rest = _program(tokens[0]), tokens[1:]
    hosts: Set[Effect] = set()
    subcommand = next((w.lower() for w in rest if not w.startswith("-")), "")
    if program in _NETWORK_PROGRAMS and (
        program != "git" or subcommand in _GIT_NETWORK_SUBCOMMANDS
    ):
        hosts = _hosts(" ".join(rest))
    if _PYTHON_RE.match(program):
        return _python_invocation(rest, cwd, depth)
    if program in _SHELLS:
        for idx, arg in enumerate(rest):
            if arg.lower() in _SHELL_COMMAND_FLAGS and idx + 1 < len(rest):
                return effects_of_shell_command(
                    " ".join(rest[idx + 1 :]), cwd, depth + 1
                )
        scripts = [a for a in rest if not a.startswith("-")]
        return effects_of_script(scripts[0], cwd, depth + 1) if scripts else set()
    effects: Set[Effect] = set(hosts)
    family = _canonical_family(program, rest)
    if family:
        effects.add(Effect(COMMAND, family))
    if program in _PATH_MUTATORS:
        for arg in rest:
            path = _norm_path(arg, cwd)
            if path:
                effects.add(Effect(PATH, path))
    return effects


# -------------------------------------------------------------------------- python

_SUBPROCESS_FUNCS = frozenset(
    {
        "run",
        "call",
        "check_call",
        "check_output",
        "Popen",
        "getoutput",
        "getstatusoutput",
    }
)


def _const_str(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _argv_from_node(node: ast.AST) -> Optional[List[str]]:
    """A literal ``["pytest", "-q"]`` (``sys.executable`` reads as python)."""
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    argv: List[str] = []
    for elt in node.elts:
        text = _const_str(elt)
        if text is None and isinstance(elt, ast.Attribute) and elt.attr == "executable":
            text = "python"
        if text is None:
            if not argv:
                return None
            continue
        argv.append(text)
    return argv or None


def effects_of_python_code(code: str, cwd: str, depth: int = 0) -> Set[Effect]:
    """What a Python snippet would do: tests, subprocesses, hosts, writes."""
    if depth > 3 or not isinstance(code, str):
        return set()
    effects: Set[Effect] = set()
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return effects
    subprocess_names: Set[str] = set()
    imported: Set[str] = set()
    calls: List[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            imported.add(root)
            if any(_TEST_MODULE_RE.match(part) for part in node.module.split(".")):
                effects.add(Effect(COMMAND, TEST_SUITE))
            if root == "subprocess":
                subprocess_names |= {a.asname or a.name for a in node.names}
        elif isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
            if any(
                _TEST_MODULE_RE.match(part)
                for alias in node.names
                for part in alias.name.split(".")
            ):
                effects.add(Effect(COMMAND, TEST_SUITE))
        elif isinstance(node, ast.Call):
            calls.append(node)
    if imported & _NETWORK_MODULES:
        effects |= _hosts(code)
    # ``from unittest import mock`` runs nothing; importing a runner does.
    if imported & {"pytest", "nose2"}:
        effects.add(Effect(COMMAND, TEST_SUITE))
    for call in calls:
        effects |= _effects_of_python_call(
            call, subprocess_names, "unittest" in imported, cwd, depth
        )
    return effects


def _effects_of_python_call(
    node: ast.Call,
    subprocess_names: Set[str],
    uses_unittest: bool,
    cwd: str,
    depth: int,
) -> Set[Effect]:
    func = node.func
    owner = (
        func.value.id
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
        else None
    )
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if not name:
        return set()
    if (owner == "unittest" and name in ("main", "TextTestRunner")) or (
        uses_unittest and name in ("TextTestRunner", "discover")
    ):
        return {Effect(COMMAND, TEST_SUITE)}
    if owner == "pytest" and name == "main":
        return {Effect(COMMAND, TEST_SUITE)}
    if owner == "runpy" and name in ("run_module", "run_path") and node.args:
        target = _const_str(node.args[0])
        if target and name == "run_module":
            family = _canonical_family(target.split(".")[0].lower(), [])
            return {Effect(COMMAND, family)} if family else set()
        return effects_of_script(target, cwd, depth + 1) if target else set()
    is_subprocess = (owner == "subprocess" and name in _SUBPROCESS_FUNCS) or (
        owner is None and name in subprocess_names
    )
    is_os_shell = owner == "os" and name in ("system", "popen")
    if (is_subprocess or is_os_shell) and node.args:
        first = node.args[0]
        text = _const_str(first)
        if text is not None:
            return effects_of_shell_command(text, cwd, depth + 1)
        argv = _argv_from_node(first)
        if argv:
            return _effects_of_argv(argv, cwd, depth + 1)
        return set()
    if name == "open" and owner is None and len(node.args) >= 2:
        mode = _const_str(node.args[1]) or ""
        target = _const_str(node.args[0])
        if target and any(flag in mode for flag in "wax+"):
            path = _norm_path(target, cwd)
            return {Effect(PATH, path)} if path else set()
    return set()


def effects_of_script(script: str, cwd: str, depth: int = 0) -> Set[Effect]:
    """The script itself, plus whatever its source says it does."""
    path = _norm_path(script, cwd)
    if not path:
        return set()
    effects = {Effect(SCRIPT, path)}
    stem, ext = os.path.splitext(os.path.basename(path))
    if ext in (".py", ".pyw") and _TEST_SCRIPT_RE.match(stem):
        effects.add(Effect(COMMAND, TEST_SUITE))
    # Not written yet: the script path alone still identifies it.
    if not os.path.isfile(path):
        return effects
    try:
        if os.path.getsize(path) > _MAX_SCRIPT_BYTES:
            return effects
        with open(path, encoding="utf-8", errors="replace") as fh:
            source = fh.read()
    except OSError as e:
        logger.warning("Could not read %s to see what it runs: %s", path, e)
        return effects
    here = os.path.dirname(path)
    if path.endswith((".py", ".pyw")):
        return effects | effects_of_python_code(source, here, depth)
    return effects | effects_of_shell_command(source, here, depth)


# ---------------------------------------------------------------------------- calls


def _is_url_arg(key: str) -> bool:
    key = key.lower()
    return key.endswith(("url", "uri", "urls")) or key in ("endpoint", "href", "link")


def effects_of_call(
    tool_name: str, tool_args: Optional[Dict[str, Any]], cwd: Optional[str] = None
) -> Set[Effect]:
    """Every effect a tool call would have, whichever tool carries it."""
    args = tool_args if isinstance(tool_args, dict) else {}
    name = (tool_name or "").lower()
    cwd = cwd or os.getcwd()
    working_dir = args.get("working_directory") or args.get("cwd")
    if isinstance(working_dir, str) and working_dir.strip():
        cwd = _norm_path(working_dir, cwd) or cwd

    effects: Set[Effect] = set()
    # URL-shaped arguments only: a URL inside file content is text, not a request.
    for key, value in args.items():
        if isinstance(key, str) and _is_url_arg(key):
            for item in value if isinstance(value, (list, tuple)) else [value]:
                if isinstance(item, str):
                    effects |= _hosts(item)

    for key in _SHELL_ARG_NAMES:
        if isinstance(args.get(key), str):
            effects |= effects_of_shell_command(args[key], cwd)
    if "python" in name and re.search(r"run|exec", name):
        if isinstance(args.get("code"), str):
            effects |= effects_of_python_code(args["code"], cwd)
        script = args.get("file_path") or args.get("script") or args.get("path")
        if isinstance(script, str) and "code" not in args:
            effects |= effects_of_script(script, cwd)
    elif _MUTATING_TOOL_RE.search(name):
        for key in _PATH_ARG_NAMES:
            value = args.get(key)
            if isinstance(value, str):
                path = _norm_path(value, cwd)
                if path:
                    effects.add(Effect(PATH, path))
    return effects


def render_call(tool_name: str, tool_args: Optional[Dict[str, Any]]) -> str:
    """The exact thing about to run, as a person would read it."""
    args = tool_args if isinstance(tool_args, dict) else {}
    for key in _SHELL_ARG_NAMES:
        if isinstance(args.get(key), str):
            return args[key].strip()
    if isinstance(args.get("code"), str):
        code = args["code"].strip()
        return code if len(code) <= 400 else code[:400] + "\n…"
    runs_python = re.search(r"python", tool_name or "", re.I) and re.search(
        r"run|exec", tool_name or "", re.I
    )
    if runs_python and isinstance(args.get("file_path"), str):
        extra = args.get("args") or ""
        return f"python {args['file_path']} {extra}".strip()
    shown = ", ".join(f"{k}={v!r}" for k, v in args.items() if k != "content")
    shown = shown if len(shown) <= 300 else shown[:300] + "…"
    return f"{tool_name}({shown})"


@dataclass
class Denial:
    """One declined call and the effects it covered."""

    tool_name: str
    rendered: str
    reason: str
    effects: Set[Effect] = field(default_factory=set)


class DeniedEffects:
    """The effects declined so far this turn. One per turn; never persisted."""

    def __init__(self, cwd: Optional[str] = None):
        self.cwd = cwd or os.getcwd()
        self.denials: List[Denial] = []

    def __bool__(self) -> bool:
        return any(d.effects for d in self.denials)

    def record(
        self,
        tool_name: str,
        tool_args: Optional[Dict[str, Any]],
        reason: str,
        kinds: Optional[Set[str]] = None,
    ) -> Optional[Denial]:
        """Remember what this declined call would have done.

        *kinds* narrows what is kept: a policy refusal of one command shape
        records the host or path it targeted, not the whole command family.
        """
        effects = effects_of_call(tool_name, tool_args, self.cwd)
        if kinds is not None:
            effects = {e for e in effects if e.kind in kinds}
        if not effects:
            return None
        denial = Denial(tool_name, render_call(tool_name, tool_args), reason, effects)
        self.denials.append(denial)
        return denial

    def conflict(
        self, tool_name: str, tool_args: Optional[Dict[str, Any]]
    ) -> Optional[Tuple[Denial, Effect]]:
        """The earlier denial this call would route around, if any."""
        if not self:
            return None
        wanted = effects_of_call(tool_name, tool_args, self.cwd)
        for denial in self.denials:
            for denied in denial.effects:
                for effect in wanted:
                    if denied.overlaps(effect):
                        return denial, effect
        return None
