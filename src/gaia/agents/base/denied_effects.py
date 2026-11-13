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
import functools
import ipaddress
import logging
import os
import re
import shlex
import socket
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from gaia.agents.base.command_wrappers import unwrap_command_argv

logger = logging.getLogger(__name__)

COMMAND = "command"
NETWORK = "network"
PATH = "path"
SCRIPT = "script"

#: A file change whose target the code computes: it could be any denied path.
COMPUTED_PATH = "a path the code works out when it runs"

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


_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "cmd", "powershell", "pwsh"})
_SHELL_COMMAND_FLAGS = frozenset({"-c", "/c", "/k", "-command", "-commandwithargs"})
_POWERSHELLS = frozenset({"powershell", "pwsh"})
#: PowerShell's script switch; bash and zsh use ``-f`` for something else.
_SHELL_FILE_FLAGS = frozenset({"-file", "-f"})
#: A script run by naming it: what it does is in its source.
_SCRIPT_FILE_RE = re.compile(r"\.(?:ps1|bat|cmd|sh)$", re.IGNORECASE)
#: PowerShell switches whose next word is their value, not the script to run.
_SHELL_VALUE_FLAGS = frozenset(
    {
        "-executionpolicy",
        "-ep",
        "-ex",
        "-windowstyle",
        "-w",
        "-version",
        "-workingdirectory",
        "-wd",
        "-inputformat",
        "-outputformat",
        "-configurationname",
        "-settingsfile",
        "-custompipename",
    }
)
#: PowerShell's process launcher. Not ``start``: cmd's own takes a title first.
_START_PROCESS = frozenset({"start-process", "saps"})
_START_PROCESS_VALUE_FLAGS = frozenset(
    {
        "-verb",
        "-workingdirectory",
        "-windowstyle",
        "-redirectstandardoutput",
        "-redirectstandarderror",
        "-redirectstandardinput",
        "-credential",
    }
)

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
        "mkdir",
        "md",
        "touch",
        "new-item",
        "ni",
        "cp",
        "copy",
        "copy-item",
        "cpi",
        "tee",
        "tee-object",
        "add-content",
        "ac",
        "xcopy",
        "robocopy",
    }
)
#: Switches of those commands whose value is content or a setting, not a path.
_NON_PATH_VALUE_FLAGS = frozenset(
    {
        "-value",
        "-encoding",
        "-stream",
        "-delimiter",
        "-itemtype",
        "-type",
        "-inputobject",
        "-filter",
        "-include",
        "-exclude",
        "-credential",
    }
)
#: cmd programs whose ``/X`` arguments are switches, not paths.
_SLASH_SWITCH_PROGRAMS = frozenset({"xcopy", "robocopy", "copy", "move", "del", "rd"})
_SLASH_SWITCH_RE = re.compile(r"^/[A-Za-z]{1,8}(?::\S*)?$")

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
            if COMPUTED_PATH in (self.target, other.target):
                return True
            return _path_within(self.target, other.target) or _path_within(
                other.target, self.target
            )
        return self.target == other.target


def _path_within(child: str, parent: str) -> bool:
    if child == parent:
        return True
    parent = parent.rstrip("\\/")
    return child.startswith(parent + os.sep) or child.startswith(parent + "/")


_UNC_DRIVE_RE = re.compile(r"^\\\\([^\\]+)\\([A-Za-z])\$(\\.*)?$")


@functools.lru_cache(maxsize=1)
def _local_hosts() -> frozenset:
    r"""Host names an administrative share (``\\host\c$``) means this PC by."""
    return frozenset({"localhost", "127.0.0.1", "::1", socket.gethostname().lower()})


def _windows_spelling(path: str) -> str:
    r"""One spelling for the many Windows has for a file.

    ``\\?\C:\x``, ``\\localhost\c$\x``, ``C:\x::$DATA``, ``C:\x:stream`` and
    ``C:\x.`` all open ``C:\x``.
    """
    path = path.replace("/", "\\")
    lowered = path.lower()
    if lowered.startswith("\\\\?\\unc\\"):
        path = "\\\\" + path[8:]
    elif lowered.startswith(("\\\\?\\", "\\\\.\\")):
        path = path[4:]
    share = _UNC_DRIVE_RE.match(path)
    if share and share.group(1).lower() in _local_hosts():
        path = f"{share.group(2)}:{share.group(3) or chr(92)}"
    drive, rest = os.path.splitdrive(path)
    parts = [
        part if part in (".", "..") else part.split(":", 1)[0].rstrip(" .")
        for part in rest.split("\\")
    ]
    return drive + "\\".join(parts)


def _canonical(path: str) -> str:
    """The form every target is compared in, wherever it was spelled."""
    if os.name == "nt":
        path = _windows_spelling(path)
    try:
        # Expands 8.3 short names and follows links, as opening the file would.
        path = os.path.realpath(path)
    except (OSError, ValueError) as e:
        logger.debug(
            "compared a target as written; resolving it failed (%s)", type(e).__name__
        )
    return os.path.normcase(os.path.abspath(path))


def _lexical_path(raw: str, cwd: str) -> Optional[str]:
    """*raw* made absolute against *cwd*, without touching the filesystem."""
    raw = raw.strip().strip("'\"")
    if not raw or "\x00" in raw or raw.startswith("-") or "://" in raw:
        return None
    try:
        expanded = os.path.expanduser(os.path.expandvars(raw))
        return os.path.normcase(os.path.abspath(os.path.join(cwd, expanded)))
    except (TypeError, ValueError):
        return None


def _norm_path(raw: str, cwd: str) -> Optional[str]:
    lexical = _lexical_path(raw, cwd)
    return _canonical(lexical) if lexical else None


def _norm_path_abs(raw: str) -> Optional[str]:
    """An already-resolved absolute path, normalised like every other target."""
    if not isinstance(raw, str) or "\x00" in raw or not os.path.isabs(raw):
        return None
    return _canonical(raw)


def _legacy_ipv4(host: str) -> Optional[str]:
    """*host* as dotted IPv4 when it is one in the forms URL clients accept.

    ``2852039166``, ``0xA9FEA9FE``, ``0251.0376.0251.0376`` and ``169.254.43518``
    all reach 169.254.169.254: each part is decimal, ``0x`` hex or ``0`` octal,
    and the last part fills the bytes the others leave.
    """
    parts = host.split(".")
    if not 1 <= len(parts) <= 4:
        return None
    numbers = []
    for part in parts:
        try:
            if part.lower().startswith("0x"):
                numbers.append(int(part[2:] or "0", 16))
            elif len(part) > 1 and part.startswith("0"):
                numbers.append(int(part, 8))
            else:
                numbers.append(int(part, 10))
        except ValueError:
            return None
    *head, last = numbers
    if any(n > 255 for n in head) or last >= 256 ** (4 - len(head)):
        return None
    value = last
    for index, number in enumerate(head):
        value |= number << (24 - 8 * index)
    return str(ipaddress.IPv4Address(value))


def canonical_host(host: str) -> str:
    """One spelling per network target, so every way of writing an IP matches.

    ``[::ffff:169.254.169.254]``, ``2852039166`` and ``0xa9fea9fe`` are all
    ``169.254.169.254``; a name is lowercased without its trailing dot.
    """
    host = host.lower().strip("[]").rstrip(".")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return _legacy_ipv4(host) or host
    mapped = getattr(address, "ipv4_mapped", None)
    return str(mapped or address)


#: Names cloud providers serve instance metadata and credentials under.
_METADATA_NAMES = frozenset(
    {
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "metadata.azure.com",
        "metadata.azure.internal",
        "instance-data",
        "instance-data.ec2.internal",
    }
)
#: Metadata addresses outside the link-local ranges: AWS over IPv6, Alibaba.
_METADATA_ADDRESSES = frozenset(
    {ipaddress.ip_address("fd00:ec2::254"), ipaddress.ip_address("100.100.100.200")}
)
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def is_cloud_metadata_host(host: str) -> bool:
    """Whether *host*, in any spelling, is a cloud-metadata or link-local target.

    169.254.0.0/16 and fe80::/10 are link-local and hold every major cloud's
    metadata service; a private LAN address (a NAS, a dev box) is not one.
    """
    host = canonical_host(host or "")
    if host in _METADATA_NAMES:
        return True
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    if address.version == 6 and address in _NAT64:
        address = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return address.is_link_local or address in _METADATA_ADDRESSES


def _hosts(text: str) -> Set[Effect]:
    return {
        Effect(NETWORK, canonical_host(m.group(1)))
        for m in _URL_RE.finditer(text or "")
    }


#: A request target written without a scheme: ``curl 169.254.169.254/x``.
_BARE_HOST_RE = re.compile(
    r"^(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*)(?::\d+)?(?:/|$)"
)


def _bare_hosts(args: Sequence[str]) -> Set[Effect]:
    """Hosts a request client is given without ``http://`` in front of them."""
    found: Set[Effect] = set()
    for arg in args:
        if arg.startswith("-") or "://" in arg:
            continue
        match = _BARE_HOST_RE.match(arg)
        if match is None:
            continue
        written = match.group(1)
        host = canonical_host(written)
        # '--retry 5' is a count and 'out.json' a file: an address must look
        # like one, and a name counts only with a path or port after it.
        if _is_address(host) and (len(written) >= 7 or "." in written):
            found.add(Effect(NETWORK, host))
        elif arg[len(written) : len(written) + 1] in ("/", ":"):
            found.add(Effect(NETWORK, host))
    return found


def _is_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


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


def _quoted_tokens(segment: str) -> List[Tuple[str, bool]]:
    """Each word, unquoted, and whether it was one quoted string."""
    try:
        parts = shlex.split(segment, posix=False)
    except ValueError:
        parts = segment.split()
    tokens = []
    for p in parts:
        quoted = len(p) >= 2 and p[0] == p[-1] and p[0] in "'\""
        tokens.append((p[1:-1] if quoted else p, quoted))
    return tokens


def _tokens(segment: str) -> List[str]:
    return [token for token, _ in _quoted_tokens(segment)]


#: ``>f``, ``>>f``, ``2>f``, ``x>f``: a redirect, with or without spaces.
_REDIRECT_RE = re.compile(r"^(?P<arg>[^>]*?)(?:\d|\*)?>>?(?P<target>[^>]*)$")

#: ``[IO.File]::WriteAllText(path, …)`` and friends in PowerShell.
_DOTNET_IO_RE = re.compile(
    r"\[(?:System\.)?IO\.(?P<type>File|Directory)\]::(?P<method>\w+)\s*\((?P<args>[^)]*)\)",
    re.IGNORECASE,
)
#: Which arguments of a .NET file method name a path it changes.
_DOTNET_WRITE_ARGS = {
    "copy": (1,),
    "move": (0, 1),
    "replace": (0, 1),
}
_DOTNET_WRITERS = re.compile(
    r"^(?:write|append|create|openwrite|delete|move|replace|copy|set|encrypt|decrypt)",
    re.IGNORECASE,
)


def _dotnet_args(text: str) -> List[str]:
    """Top-level comma-separated arguments, quotes kept."""
    args, buf, quote = [], [], None
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch == ",":
            args.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    args.append("".join(buf).strip())
    return args


def _dotnet_file_writes(segment: str, cwd: str) -> Set[Effect]:
    effects: Set[Effect] = set()
    for m in _DOTNET_IO_RE.finditer(segment):
        method = m.group("method").lower()
        if not _DOTNET_WRITERS.match(method):
            continue
        args = _dotnet_args(m.group("args"))
        for index in _DOTNET_WRITE_ARGS.get(method, (0,)):
            if index >= len(args):
                continue
            arg = args[index]
            literal = len(arg) >= 2 and arg[0] == arg[-1] and arg[0] in "'\""
            path = _norm_path(arg[1:-1], cwd) if literal else None
            effects.add(Effect(PATH, path or COMPUTED_PATH))
    return effects


def _sed_in_place_files(rest: Sequence[str]) -> List[str]:
    """The files ``sed -i`` rewrites; none without ``-i``."""
    in_place = any(
        a.startswith("-i") or a.startswith("--in-place") for a in rest
    ) or any(
        a.startswith("-") and not a.startswith("--") and "i" in a[1:] for a in rest
    )
    if not in_place:
        return []
    files: List[str] = []
    script_given = False
    skip = False
    for arg in rest:
        if skip:
            skip = False
        elif arg in ("-e", "-f", "--expression", "--file"):
            script_given, skip = True, True
        elif arg.startswith(("--expression=", "--file=")):
            script_given = True
        elif arg.startswith("-"):
            continue
        elif not script_given:
            script_given = True
        else:
            files.append(arg)
    return files


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
    effects = _effects_of_line(command, cwd, depth)
    if '`"' in command:
        # PowerShell's `" nests a quote, but in sh the backtick ends a
        # substitution, so read the line both ways.
        effects |= _effects_of_line(command.replace('`"', "'"), cwd, depth)
    return effects


def _effects_of_line(command: str, cwd: str, depth: int) -> Set[Effect]:
    """Effects of one reading of a shell command line."""
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
        pending = False
        # A quoted word is an argument (``cmd /c "echo x > f"``): the shell it
        # is handed to reads its redirects.
        for tok, quoted in _quoted_tokens(segment):
            if pending:
                redirect_targets.append(tok)
                pending = False
                continue
            redirect = None if quoted else _REDIRECT_RE.match(tok)
            if redirect is None:
                kept.append(tok)
                continue
            if redirect.group("arg"):
                kept.append(redirect.group("arg"))
            target = redirect.group("target").strip("'\"")
            if not target:
                pending = True
            elif not target.startswith("&"):
                redirect_targets.append(target)
        for target in redirect_targets:
            if target.lower() not in ("/dev/null", "nul", "$null"):
                path = _norm_path(target, cwd)
                if path:
                    effects.add(Effect(PATH, path))
        effects |= _dotnet_file_writes(segment, cwd)
        effects |= _effects_of_argv(kept, cwd, depth)
    return effects


def _positionals(args: Sequence[str], value_flags: frozenset) -> List[str]:
    """The words of *args* that are neither switches nor a switch's value."""
    found: List[str] = []
    skip = False
    for arg in args:
        if skip:
            skip = False
        elif arg.startswith("-"):
            skip = arg.lower() in value_flags
        else:
            found.append(arg)
    return found


def _started_process(rest: Sequence[str], cwd: str, depth: int) -> Set[Effect]:
    """What ``Start-Process <program> -ArgumentList '...'`` would run."""
    program: Optional[str] = None
    arguments: List[str] = []
    written: List[str] = []
    i = 0
    while i < len(rest):
        arg, value = rest[i].lower(), rest[i + 1] if i + 1 < len(rest) else None
        # A switch never holds a space: "-File x.ps1" is a quoted argument list.
        switch = arg.startswith("-") and " " not in arg
        if switch and _abbreviates(arg, "-filepath", "-path") and value is not None:
            program, i = value, i + 2
        elif switch and _abbreviates(arg, "-argumentlist", "-args") and value:
            end = _array_end(rest, i + 1)
            arguments = _argument_list(rest[i + 1 : end])
            i = end
        elif switch and _abbreviates(arg, "-workingdirectory") and value is not None:
            cwd, i = _norm_path(value, cwd) or cwd, i + 2
        elif switch and arg.startswith("-redirectstandard") and value is not None:
            if not arg.endswith("input"):
                written.append(value)
            i += 2
        elif switch and arg in _START_PROCESS_VALUE_FLAGS:
            i += 2
        elif switch:
            i += 1
        elif program is None:
            program, i = rest[i], i + 1
        else:
            end = _array_end(rest, i)
            arguments = arguments or _argument_list(rest[i:end])
            i = end
    redirected = {
        Effect(PATH, path) for path in (_norm_path(w, cwd) for w in written) if path
    }
    if program is None:
        return redirected
    return redirected | _effects_of_argv([program, *arguments], cwd, depth + 1)


def _abbreviates(arg: str, *names: str) -> bool:
    """PowerShell accepts any unambiguous prefix of a parameter (``-F``, ``-Arg``)."""
    return len(arg) >= 2 and any(name.startswith(arg) for name in names)


def _array_end(words: Sequence[str], start: int) -> int:
    """One past an array value: ``'-File', "x.ps1"`` or ``@('-File', 'x.ps1')``
    arrives split at each comma."""
    end = start + 1
    in_parens = words[start].startswith("@(") and not words[start].endswith(")")
    while end < len(words) and (
        in_parens or words[end - 1].endswith(",") or words[end].startswith(",")
    ):
        if words[end].endswith(")"):
            in_parens = False
        end += 1
    return end


def _argument_list(words: Sequence[str]) -> List[str]:
    """The arguments Start-Process passes on. One string (``'-File x.ps1'``) is
    split into words; an array's elements are each one argument, spaces and all."""
    if len(words) == 1 and not words[0].startswith("@("):
        return [w.strip("'\",") for w in _tokens(words[0]) if w.strip("'\",")]
    elements = []
    for word in words:
        for piece in re.split(r"""(?<=['"]),|,(?=['"])""", word):
            piece = piece.strip().removeprefix("@(").removesuffix(")")
            piece = piece.strip(", ").strip("'\"")
            if piece:
                elements.append(piece)
    return elements


def _past_launcher(tokens: List[str]) -> List[str]:
    """*tokens* without cmd's ``start ["title"] /switches`` or ``gsudo -flags``."""
    head = _program(tokens[0]) if tokens else ""
    if head == "start":
        words, titled = list(tokens[1:]), False
        # Only start's own title and switches lead: ``start cmd /c x`` keeps /c.
        while words:
            # The title is the quoted first word: empty, or more than one word.
            if not titled and (not words[0].strip("'\"") or " " in words[0]):
                titled = True
                words.pop(0)
            elif _SLASH_SWITCH_RE.match(words[0]):
                if words.pop(0).lower() == "/d" and words:
                    words.pop(0)
            else:
                break
        return words or tokens
    if head == "gsudo":
        words = list(tokens[1:])
        while words and words[0].startswith("-"):
            if words.pop(0).lower() in ("-u", "--user", "-i", "--integrity") and words:
                words.pop(0)
        return words or tokens
    return tokens


def _effects_of_argv(argv: Sequence[str], cwd: str, depth: int) -> Set[Effect]:
    """Effects of one already-tokenised command, unwrapping launchers."""
    tokens = list(argv)
    while tokens and _ASSIGNMENT_RE.match(tokens[0]):
        tokens.pop(0)
    tokens = unwrap_command_argv(_past_launcher(tokens), ledger=True)
    if not tokens:
        return set()
    first = tokens[0].strip("'\"")
    program, rest = _program(tokens[0]), tokens[1:]
    hosts: Set[Effect] = set()
    # ``.\fix.bat`` or a script here: what it does, besides the command it is.
    if _SCRIPT_FILE_RE.search(first) and (
        re.search(r"[\\/]", first) or os.path.isfile(os.path.join(cwd, first))
    ):
        hosts = effects_of_script(first, cwd, depth + 1)
    subcommand = next((w.lower() for w in rest if not w.startswith("-")), "")
    if program in _NETWORK_PROGRAMS and (
        program != "git" or subcommand in _GIT_NETWORK_SUBCOMMANDS
    ):
        hosts = _hosts(" ".join(rest)) | _bare_hosts(rest)
    if _PYTHON_RE.match(program):
        return _python_invocation(rest, cwd, depth)
    if program in _SHELLS:
        for idx, arg in enumerate(rest):
            if arg.lower() in _SHELL_COMMAND_FLAGS and idx + 1 < len(rest):
                return effects_of_shell_command(
                    " ".join(rest[idx + 1 :]), cwd, depth + 1
                )
            if program in _POWERSHELLS:
                flag, _, attached = arg.lower().partition(":")
                if flag in _SHELL_FILE_FLAGS and attached:
                    return effects_of_script(arg.split(":", 1)[1], cwd, depth + 1)
                if flag in _SHELL_FILE_FLAGS and idx + 1 < len(rest):
                    return effects_of_script(rest[idx + 1], cwd, depth + 1)
        if program in _POWERSHELLS:
            scripts = _positionals(rest, _SHELL_VALUE_FLAGS)
        else:
            scripts = [a for a in rest if not a.startswith("-")]
        return effects_of_script(scripts[0], cwd, depth + 1) if scripts else set()
    if program in _START_PROCESS:
        started = _started_process(rest, cwd, depth)
        return hosts | (started or {Effect(COMMAND, "start-process")})
    effects: Set[Effect] = set(hosts)
    family = _canonical_family(program, rest)
    if family:
        effects.add(Effect(COMMAND, family))
    changed: List[str] = []
    if program in _PATH_MUTATORS:
        changed = [
            arg
            for arg in _positionals(rest, _NON_PATH_VALUE_FLAGS)
            # ``xcopy a b /Y``: a cmd switch, not a folder at the drive root.
            if not (program in _SLASH_SWITCH_PROGRAMS and _SLASH_SWITCH_RE.match(arg))
        ]
    elif program == "dd":
        changed = [arg[3:] for arg in rest if arg.startswith("of=")]
    elif program == "sed":
        changed = _sed_in_place_files(rest)
    for arg in changed:
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


#: Module functions that change a path, and the arguments naming it: each is
#: ``(position, keyword)``. A copy changes only its destination.
_FILE_CHANGERS: Dict[str, Dict[str, Tuple[Tuple[int, str], ...]]] = {
    "shutil": {
        "rmtree": ((0, "path"),),
        "move": ((0, "src"), (1, "dst")),
        "copy": ((1, "dst"),),
        "copy2": ((1, "dst"),),
        "copyfile": ((1, "dst"),),
        "copytree": ((1, "dst"),),
    },
    "os": {
        "remove": ((0, "path"),),
        "unlink": ((0, "path"),),
        "rmdir": ((0, "path"),),
        "removedirs": ((0, "name"),),
        "rename": ((0, "src"), (1, "dst")),
        "replace": ((0, "src"), (1, "dst")),
        "renames": ((0, "old"), (1, "new")),
        "makedirs": ((0, "name"),),
        "mkdir": ((0, "path"),),
        "truncate": ((0, "path"),),
        "symlink": ((1, "dst"),),
        "link": ((1, "dst"),),
    },
}
#: Modules whose ``open(file, mode)`` writes when the mode says so.
_OPENERS = frozenset({"io", "codecs", "builtins", "gzip", "bz2", "lzma"})
_TRACKED_MODULES = (
    frozenset({"os", "shutil", "pathlib", "tempfile", "subprocess", "importlib"})
    | _OPENERS
)
#: ``os.open`` flags that write; a flag it can't name is read as a write.
_WRITE_FLAGS = frozenset({"O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND"})
#: ``pathlib.Path`` methods that delete or overwrite the path, on any receiver.
_PATH_CHANGERS = frozenset({"unlink", "rmdir", "write_text", "write_bytes"})
#: Only on a receiver known to be a Path: ``str.replace``, ``tarfile.open``.
_PATH_ONLY_CHANGERS = frozenset({"mkdir", "touch", "symlink_to", "hardlink_to"})
_PATH_MOVERS = frozenset({"rename", "replace"})
_PATH_TYPES = frozenset({"Path", "PurePath", "WindowsPath", "PosixPath"})
#: Path methods returning a path derived from the receiver.
_PATH_DERIVERS = frozenset(
    {"joinpath", "with_name", "with_suffix", "resolve", "absolute", "expanduser"}
)
#: ``os.path`` functions that return their argument's path, reshaped.
_SAME_PATH_FUNCS = frozenset({"abspath", "normpath", "realpath", "expanduser"})


def _arg(call: ast.Call, position: int, keyword: str) -> Optional[ast.AST]:
    """The argument at *position*, or passed as *keyword*; None if unseen."""
    if any(isinstance(arg, ast.Starred) for arg in call.args[: position + 1]):
        return None
    if len(call.args) > position:
        return call.args[position]
    return next((k.value for k in call.keywords if k.arg == keyword), None)


def _spreads(call: ast.Call) -> bool:
    """``f(*paths)`` / ``f(**kw)``: an argument may be there unseen."""
    return any(isinstance(a, ast.Starred) for a in call.args) or any(
        k.arg is None for k in call.keywords
    )


class _PyFiles:
    """Which paths a snippet deletes, moves, writes or creates, as far as it shows.

    Tracks literal strings, names bound to them, f-strings, ``os.path.join``,
    ``Path`` construction, ``/`` and ``.parent``. A target it cannot read is
    :data:`COMPUTED_PATH`.
    """

    def __init__(self, tree: ast.AST, cwd: str):
        self.cwd = cwd
        self.modules: Dict[str, str] = {}
        self.funcs: Dict[str, Tuple[str, str]] = {}
        self.values: Dict[str, str] = {}
        self.lists: Dict[str, ast.AST] = {}
        self.paths: Set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root in _TRACKED_MODULES:
                        # ``import os.path as osp`` binds os.path, not os.
                        bound = alias.name if alias.asname else root
                        self.modules[alias.asname or root] = bound
            elif isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    self.funcs[alias.asname or alias.name] = (node.module, alias.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                self._bind(node.targets[0], node.value)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                self._bind(node.target, node.value)
            elif isinstance(node, ast.For) and isinstance(node.target, ast.Name):
                iterated = node.iter
                if (
                    isinstance(iterated, ast.Call)
                    and isinstance(iterated.func, ast.Attribute)
                    and iterated.func.attr in ("iterdir", "glob", "rglob")
                ):
                    self.paths.add(node.target.id)

    def _bind(self, target: ast.AST, value: ast.AST) -> None:
        if (
            isinstance(target, ast.Tuple)
            and isinstance(value, ast.Tuple)
            and len(target.elts) == len(value.elts)
        ):
            for name, item in zip(target.elts, value.elts):
                self._bind(name, item)
            return
        if not isinstance(target, ast.Name):
            return
        text = self.text(value)
        if text is not None:
            self.values[target.id] = text
        if self.is_path(value):
            self.paths.add(target.id)
        if isinstance(value, (ast.List, ast.Tuple)):
            self.lists[target.id] = value

    def argv(self, node: ast.AST) -> Tuple[List[str], bool]:
        """A command list as far as the code spells it, and whether all of it is.

        ``sys.executable`` reads as python.
        """
        if isinstance(node, ast.Name) and node.id in self.lists:
            node = self.lists[node.id]
        if not isinstance(node, (ast.List, ast.Tuple)):
            return [], False
        argv: List[str] = []
        complete = True
        for elt in node.elts:
            text = self.text(elt)
            if (
                text is None
                and isinstance(elt, ast.Attribute)
                and elt.attr == "executable"
            ):
                text = "python"
            if text is None:
                complete = False
                continue
            argv.append(text)
        return argv, complete

    def dynamic(self, call: ast.Call, depth: int) -> Set[Effect]:
        """Code that picks what it calls at run time: it could reach any path."""
        func = call.func
        module, name = self._callee(call)
        if isinstance(func, ast.Name):
            builtin = func.id not in self.funcs or module == "builtins"
        else:
            builtin = module == "builtins"
        computed = {Effect(PATH, COMPUTED_PATH)}
        if builtin and name in ("exec", "eval"):
            source = _arg(call, 0, "source")
            text = self.text(source) if source is not None else None
            if text is None:
                return computed
            return effects_of_python_code(text, self.cwd, depth + 1)
        if builtin and name == "__import__":
            return computed
        if module == "importlib" and name == "import_module":
            return computed
        if builtin and name == "getattr" and call.args:
            owner = call.args[0]
            if (isinstance(owner, ast.Name) and owner.id in self.modules) or (
                isinstance(owner, ast.Call)
                and isinstance(owner.func, ast.Name)
                and owner.func.id == "__import__"
            ):
                return computed
        return set()

    def _module_of(self, node: ast.AST) -> Optional[str]:
        if isinstance(node, ast.Name):
            return self.modules.get(node.id)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            base = self.modules.get(node.value.id)
            return f"{base}.{node.attr}" if base else None
        return None

    def _callee(self, call: ast.Call) -> Tuple[Optional[str], Optional[str]]:
        func = call.func
        if isinstance(func, ast.Name):
            return self.funcs.get(func.id, (None, func.id))
        if isinstance(func, ast.Attribute):
            return self._module_of(func.value), func.attr
        return None, None

    def _is_path_type(self, node: ast.AST) -> bool:
        """``Path`` / ``pathlib.Path`` / an alias of it, uncalled."""
        if isinstance(node, ast.Name):
            module, name = self.funcs.get(node.id, (None, node.id))
            return name in _PATH_TYPES and module in (None, "pathlib")
        if isinstance(node, ast.Attribute):
            return node.attr in _PATH_TYPES and self._module_of(node.value) == "pathlib"
        return False

    def is_path(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in self.paths
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return self.is_path(node.left)
        if isinstance(node, ast.Attribute) and node.attr == "parent":
            return self.is_path(node.value)
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                if func.attr in _PATH_DERIVERS and self.is_path(func.value):
                    return True
                if func.attr in ("home", "cwd") and self._is_path_type(func.value):
                    return True
            module, name = self._callee(node)
            return name in _PATH_TYPES and module in (None, "pathlib")
        return False

    def text(self, node: ast.AST) -> Optional[str]:
        """The string *node* evaluates to, when the code spells it out."""
        literal = _const_str(node)
        if literal is not None:
            return literal
        if isinstance(node, ast.Name):
            return self.values.get(node.id)
        if isinstance(node, ast.JoinedStr):
            parts = [self._fstring_part(value) for value in node.values]
            return None if any(p is None for p in parts) else "".join(parts)
        if isinstance(node, ast.BinOp):
            left, right = self.text(node.left), self.text(node.right)
            if left is None or right is None:
                return None
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Div):
                return os.path.join(left, right)
            return None
        if isinstance(node, ast.Attribute) and node.attr == "parent":
            inner = self.text(node.value)
            return os.path.dirname(inner.rstrip("\\/")) if inner else None
        if isinstance(node, ast.Call):
            return self._call_text(node)
        return None

    def _fstring_part(self, node: ast.AST) -> Optional[str]:
        literal = _const_str(node)
        if literal is not None:
            return literal
        if (
            isinstance(node, ast.FormattedValue)
            and node.format_spec is None
            and node.conversion in (-1, ord("s"))
        ):
            return self.text(node.value)
        return None

    def _call_text(self, node: ast.Call) -> Optional[str]:
        func = node.func
        if isinstance(func, ast.Attribute) and self.is_path(func.value):
            base = self.text(func.value)
            if func.attr in ("resolve", "absolute", "expanduser"):
                return base
            args = [self.text(arg) for arg in node.args]
            if base is None or any(arg is None for arg in args):
                return None
            if func.attr == "joinpath":
                return os.path.join(base, *args)
            if func.attr == "with_name" and len(args) == 1:
                return os.path.join(os.path.dirname(base), args[0])
            if func.attr == "with_suffix" and len(args) == 1:
                return os.path.splitext(base)[0] + args[0]
            return None
        if isinstance(func, ast.Attribute) and self._is_path_type(func.value):
            return {"home": "~", "cwd": self.cwd}.get(func.attr)
        module, name = self._callee(node)
        # Where a snippet writes when it is steered somewhere allowed instead.
        if (module, name) == ("os", "getcwd"):
            return self.cwd
        if (module, name) == ("tempfile", "gettempdir"):
            return tempfile.gettempdir()
        if name in _PATH_TYPES or (name == "str" and module is None):
            return self.text(node.args[0]) if len(node.args) == 1 else None
        if module == "os.path" and len(node.args) >= 1:
            if name == "join":
                parts = [self.text(arg) for arg in node.args]
                if any(part is None for part in parts):
                    return None
                return os.path.join(*parts)
            if name in _SAME_PATH_FUNCS and len(node.args) == 1:
                return self.text(node.args[0])
            if name == "dirname" and len(node.args) == 1:
                inner = self.text(node.args[0])
                return os.path.dirname(inner) if inner is not None else None
        return None

    def _known_prefix(self, node: ast.AST) -> Optional[str]:
        """The folder a partly computed path is known to be under."""
        if isinstance(node, ast.Call):
            module, name = self._callee(node)
            if module == "os.path" and name == "join" and node.args:
                return self.text(node.args[0])
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "joinpath"
                and self.is_path(func.value)
            ):
                return self.text(func.value)
        if isinstance(node, ast.BinOp):
            return self.text(node.left)
        if isinstance(node, ast.JoinedStr):
            known = ""
            for value in node.values:
                part = self._fstring_part(value)
                if part is None:
                    break
                known += part
            cut = max(known.rfind("/"), known.rfind("\\"))
            return known[:cut] if cut > 0 else None
        return None

    def _target(self, node: ast.AST) -> Effect:
        # ``join(folder, name)`` / ``folder / name``: the folder is reached.
        value = self.text(node) or self._known_prefix(node)
        path = _norm_path(value, self.cwd) if value else None
        return Effect(PATH, path or COMPUTED_PATH)

    def _targets(self, call: ast.Call, spec: Sequence[Tuple[int, str]]) -> Set[Effect]:
        found = {
            self._target(node)
            for node in (_arg(call, pos, kw) for pos, kw in spec)
            if node is not None
        }
        if len(found) < len(spec) and _spreads(call):
            found.add(Effect(PATH, COMPUTED_PATH))
        return found

    def _writes(self, mode: Optional[ast.AST]) -> bool:
        """An ``open`` mode that writes; a mode the code computes may."""
        if mode is None:
            return False
        text = self.text(mode)
        return text is None or any(flag in text for flag in "wax+")

    def _os_open_writes(self, flags: Optional[ast.AST]) -> bool:
        if flags is None:
            return False
        if isinstance(flags, ast.Constant):
            return flags.value != 0
        names = {
            n.attr if isinstance(n, ast.Attribute) else n.id
            for n in ast.walk(flags)
            if isinstance(n, (ast.Attribute, ast.Name))
        } - set(self.modules)
        return bool(names & _WRITE_FLAGS) or any(
            not name.startswith("O_") for name in names
        )

    def changed(self, call: ast.Call) -> Set[Effect]:
        module, name = self._callee(call)
        spec = _FILE_CHANGERS.get(module or "", {}).get(name or "")
        if spec:
            return self._targets(call, spec)
        if module == "os" and name == "open":
            if self._os_open_writes(_arg(call, 1, "flags")):
                return self._targets(call, ((0, "path"),))
            return set()
        if name == "open" and (
            module in _OPENERS or (module is None and isinstance(call.func, ast.Name))
        ):
            if self._writes(_arg(call, 1, "mode")):
                return self._targets(call, ((0, "file"),))
            return set()
        func = call.func
        if not isinstance(func, ast.Attribute):
            return set()
        if func.attr in _PATH_CHANGERS:
            return {self._target(func.value)}
        if not self.is_path(func.value):
            return set()
        if func.attr in _PATH_ONLY_CHANGERS:
            return {self._target(func.value)}
        if func.attr == "open":
            if self._writes(_arg(call, 0, "mode")):
                return {self._target(func.value)}
            return set()
        if func.attr in _PATH_MOVERS:
            return {self._target(func.value)} | self._targets(call, ((0, "target"),))
        return set()


def effects_of_python_code(code: str, cwd: str, depth: int = 0) -> Set[Effect]:
    """What a Python snippet would do: tests, subprocesses, hosts, file changes."""
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
    files = _PyFiles(tree, cwd)
    for call in calls:
        effects |= _effects_of_python_call(
            call, subprocess_names, "unittest" in imported, files, depth
        )
        effects |= files.changed(call)
        effects |= files.dynamic(call, depth)
    return effects


#: Methods that read the path they are called on: ``Path('x').read_text()``.
_PATH_READERS = frozenset({"read_text", "read_bytes", "open"})
#: ``shutil`` functions whose first argument is a file they read.
_SHUTIL_READERS = frozenset({"copy", "copy2", "copyfile", "copytree", "move"})


#: Every call name :func:`python_file_reads` can report a read through.
PYTHON_READ_CALLS = frozenset(
    {"open", "system", "popen"} | _PATH_READERS | _SHUTIL_READERS | _SUBPROCESS_FUNCS
)


def _mode_writes_only(mode: Optional[ast.AST]) -> bool:
    """Whether an ``open`` *mode* leaves nothing already in the file to read.

    ``w`` and ``x`` start the file empty; ``a`` writes after what is there
    without reading it. ``r+`` and ``a+`` read, and so does a mode not
    written out as a literal.
    """
    if not (isinstance(mode, ast.Constant) and isinstance(mode.value, str)):
        return False
    text = mode.value
    return "w" in text or "x" in text or ("a" in text and "+" not in text)


def _os_open_writes_only(flags: Optional[ast.AST]) -> bool:
    """Whether ``os.open`` *flags* open the file for writing only."""
    if flags is None:
        return False
    named = {
        node.attr if isinstance(node, ast.Attribute) else node.id
        for node in ast.walk(flags)
        if isinstance(node, (ast.Attribute, ast.Name))
    }
    return "O_WRONLY" in named and not named & {"O_RDWR", "O_RDONLY"}


def python_file_reads(code: str, cwd: str) -> Tuple[List[str], List[List[str]]]:
    """The files a snippet opens, and the commands it hands to a shell.

    Only paths a read is made from: ``open``/``io.open``/``os.open``, a
    ``Path`` that is read or opened, and the source of a ``shutil`` copy or
    move. A string that is never opened — a message naming ``app/.env`` — is
    not in the list, nor is a file opened only to be written (``'w'``,
    ``'a'``, ``'x'``, ``os.O_WRONLY``). Paths are read the way
    :class:`_PyFiles` reads a file change: literals, names bound to them, f-strings, ``os.path.join`` and
    ``Path(...) / ...``. A path the code only works out when it runs is not.

    Returns:
        ``(paths, commands)``: *commands* are argv lists, one per command
        given to ``subprocess`` or ``os.system``.
    """
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        # Python refuses to run it too, so it opens nothing.
        return [], []
    files = _PyFiles(tree, cwd)
    paths: List[str] = []
    commands: List[List[str]] = []

    def add(node: Optional[ast.AST]) -> None:
        text = files.text(node) if node is not None else None
        if text:
            paths.append(text)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        module, name = files._callee(node)
        func = node.func
        if name == "open" and (
            module in _OPENERS or module == "os" or isinstance(func, ast.Name)
        ):
            if module == "os":
                if not _os_open_writes_only(_arg(node, 1, "flags")):
                    add(_arg(node, 0, "path"))
            elif not _mode_writes_only(_arg(node, 1, "mode")):
                add(_arg(node, 0, "file"))
        elif (
            isinstance(func, ast.Attribute)
            and func.attr in _PATH_READERS
            and files.is_path(func.value)
        ):
            if func.attr != "open" or not _mode_writes_only(_arg(node, 0, "mode")):
                add(func.value)
        elif module == "shutil" and name in _SHUTIL_READERS:
            add(_arg(node, 0, "src"))
        elif name in _SUBPROCESS_FUNCS or (
            module == "os" and name in ("system", "popen")
        ):
            first = _arg(node, 0, "args")
            if first is None:
                continue
            command = files.text(first)
            if command:
                commands.extend(_tokens(seg) for seg in _split_segments(command))
            else:
                argv, _ = files.argv(first)
                if argv:
                    commands.append(argv)
    return paths, commands


def _effects_of_python_call(
    node: ast.Call,
    subprocess_names: Set[str],
    uses_unittest: bool,
    files: "_PyFiles",
    depth: int,
) -> Set[Effect]:
    cwd = files.cwd
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
    module = files._module_of(func.value) if isinstance(func, ast.Attribute) else None
    is_subprocess = ("subprocess" in (owner, module) and name in _SUBPROCESS_FUNCS) or (
        owner is None and name in subprocess_names
    )
    is_os = "os" in (owner, module)
    if is_os and name.startswith(("exec", "spawn")):
        return {Effect(PATH, COMPUTED_PATH)}
    if is_subprocess or (is_os and name in ("system", "popen")):
        keyword = (
            "args" if is_subprocess else ("command" if name == "system" else "cmd")
        )
        first = _arg(node, 0, keyword)
        if first is None:
            return {Effect(PATH, COMPUTED_PATH)} if _spreads(node) else set()
        text = files.text(first)
        if text is not None:
            return effects_of_shell_command(text, cwd, depth + 1)
        argv, complete = files.argv(first)
        effects = _effects_of_argv(argv, cwd, depth + 1) if argv else set()
        # A command the code builds at run time could name any path.
        if not complete:
            effects.add(Effect(PATH, COMPUTED_PATH))
        return effects
    return set()


def effects_of_script(script: str, cwd: str, depth: int = 0) -> Set[Effect]:
    """The script itself, plus whatever its source says it does."""
    path = _norm_path(script, cwd)
    if not path:
        return set()
    # Named as written: the file changes it makes are compared canonically.
    effects = {Effect(SCRIPT, _lexical_path(script, cwd) or path)}
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
    inner = effects_of_shell_command(source, here, depth)
    if path.endswith((".bat", ".cmd")):
        # Batch syntax (set, if, goto) parses as commands; keep what it changes.
        inner = {e for e in inner if e.kind != COMMAND or e.target == TEST_SUITE}
    return effects | inner


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


_FILE_VERBS = {
    "write": "write",
    "create_file": "write",
    "save": "write",
    "edit": "edit",
    "replace": "edit",
    "append": "append to",
    "delete": "delete",
    "remove": "delete",
    "move": "move",
    "rename": "rename",
    "download": "download to",
}


def _file_in_words(path: str) -> str:
    """``check_ties.py in a temp folder`` rather than a three-line temp path."""
    path = path.strip()
    name = os.path.basename(path.rstrip("/\\")) or path
    folder = os.path.dirname(path.rstrip("/\\"))
    if not folder:
        return name
    try:
        temp = os.path.realpath(tempfile.gettempdir())
        in_temp = os.path.commonpath(
            [os.path.normcase(temp), os.path.normcase(os.path.realpath(folder))]
        ) == os.path.normcase(temp)
    except (OSError, ValueError):
        in_temp = False
    return f"{name} in a temp folder" if in_temp else f"{name} in {folder}"


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
    verb = _MUTATING_TOOL_RE.search(tool_name or "")
    if verb:
        for key in _PATH_ARG_NAMES:
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                friendly = (
                    f"{_FILE_VERBS[verb.group(0).lower()]} {_file_in_words(value)}"
                )
                extra = ", ".join(
                    f"{k}={v!r}" for k, v in args.items() if k not in (key, "content")
                )
                if not extra:
                    return friendly
                extra = extra if len(extra) <= 300 else extra[:300] + "…"
                # The path alone isn't the whole call: a user approving this
                # exact string must see the other args too (e.g. the URL a
                # download fetches, or the destination a move writes to).
                return f"{friendly} ({extra})"
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
    #: GAIA's policy said no, not a person, so no person can lift it.
    refused: bool = False


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
        refused: bool = False,
        paths: Optional[Sequence[str]] = None,
        hosts: Optional[Sequence[str]] = None,
    ) -> Optional[Denial]:
        """Remember what this declined call would have done.

        *kinds* narrows what is kept: a policy refusal of one command shape
        records the host or path it targeted, not the whole command family.
        *refused* marks a policy refusal rather than a person's no. *paths*,
        when given, are the call's targets as the tool itself resolved them,
        recorded instead of reading them off the arguments. *hosts* likewise:
        the host a fetch was blocked at and the address it resolved to, which
        after a redirect is not the host the URL argument names.
        """
        if paths is not None:
            found = {Effect(PATH, p) for p in map(_norm_path_abs, paths) if p}
        elif hosts is not None:
            found = {Effect(NETWORK, canonical_host(h)) for h in hosts if h}
        else:
            found = effects_of_call(tool_name, tool_args, self.cwd)
        effects = {
            e
            for e in found
            if e.target != COMPUTED_PATH and (kinds is None or e.kind in kinds)
        }
        if not effects:
            return None
        denial = Denial(
            tool_name, render_call(tool_name, tool_args), reason, effects, refused
        )
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
