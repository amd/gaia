# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Shell Tools Mixin for Chat Agent.

Provides shell command execution capabilities for file operations and system queries.
"""

import logging
import os
import re
import shlex
import signal
import subprocess
import tempfile
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from gaia.agents.base.checks import attach_check, check_from_command
from gaia.agents.base.verification import NOT_EXECUTED
from gaia.agents.tools.shell_session import ShellSession

logger = logging.getLogger(__name__)

#: Shell control flow. Commands run directly rather than through a shell, so
#: these are not binaries that could be allowed — they cannot run at all, and
#: exec reports them as a missing file, which sends the agent looking for a path
#: problem that does not exist.
SHELL_KEYWORDS = frozenset(
    {
        "for",
        "while",
        "until",
        "do",
        "done",
        "if",
        "then",
        "elif",
        "else",
        "fi",
        "case",
        "esac",
        "select",
        "function",
        "coproc",
        "{",
        "}",
        "[[",
    }
)


# Security: WHITELIST approach - only allow explicitly safe commands
# This is much safer than a blacklist which always misses dangerous commands
ALLOWED_COMMANDS = {
    # File listing and navigation (READ-ONLY)
    "ls",
    "dir",
    "pwd",
    "cd",
    # File content viewing (READ-ONLY)
    "cat",
    "head",
    "tail",
    "more",
    "less",
    # Text processing (READ-ONLY)
    "grep",
    "find",
    "wc",
    "sort",
    "uniq",
    "diff",
    "findstr",  # Windows grep equivalent
    # File information (READ-ONLY)
    "file",
    "stat",
    "du",
    "df",
    # System information (READ-ONLY) - cross-platform
    "whoami",
    "hostname",
    "uname",
    "date",
    "uptime",
    # Linux/macOS system information (READ-ONLY)
    "lscpu",  # CPU information
    "lspci",  # PCI devices (GPU, etc.)
    "lsblk",  # Block devices
    "lsusb",  # USB devices
    "free",  # Memory usage
    "nproc",  # Number of processors
    "arch",  # Architecture
    "sysctl",  # macOS system info
    "sw_vers",  # macOS version
    "system_profiler",  # macOS hardware info
    # Windows system information (READ-ONLY)
    "systeminfo",  # Comprehensive system/hardware info
    "wmic",  # WMI queries (subcommands checked separately)
    "powershell",  # PowerShell (cmdlets checked separately)
    "powershell.exe",  # PowerShell alias
    "tasklist",  # Process list (Windows equivalent of ps)
    "ipconfig",  # Network configuration
    "driverquery",  # Installed driver information
    "ver",  # Windows version
    # Path utilities
    "which",
    "whereis",
    "basename",
    "dirname",
    # Safe output
    "echo",
    "printf",
    # Process information (READ-ONLY)
    "ps",
    "top",
    "jobs",
    # Git commands (mostly safe, read-only operations)
    "git",  # Individual git subcommands checked separately
}

# Actions/predicates that turn otherwise read-only commands into a write,
# delete, or arbitrary-command-execution primitive. The whitelist only checks
# the command NAME, so these must be inspected explicitly or an allowed command
# (find/sort/uniq) becomes a bypass (CWE-184).
#
# find: -exec/-execdir/-ok/-okdir run any binary (incl. ones NOT in
# ALLOWED_COMMANDS); -delete removes files; -fprint/-fprintf/-fls write files.
# The read-only predicates (-print/-print0/-printf/-ls/-name/-type/…) are fine.
DANGEROUS_FIND_ACTIONS = {
    "-exec",
    "-execdir",
    "-ok",
    "-okdir",
    "-delete",
    "-fprint",
    "-fprint0",
    "-fprintf",
    "-fls",
}

# Safe read-only git subcommands
SAFE_GIT_COMMANDS = {
    "status",
    "log",
    "show",
    "diff",
    "branch",
    "remote",
    "ls-files",
    "ls-tree",
    "describe",
    "rev-parse",
    "help",
}

# Global git options that sit BEFORE the subcommand. They have to be stepped
# over to find what the command actually is, and each one is classified here —
# an unlisted option is refused rather than skipped, so a future git release
# cannot slip a value-taking flag past the walk and shift the subcommand index
# (CWE-184).

# Take a value, either as `--opt=value` or as the following token.
GIT_GLOBAL_FLAGS_WITH_VALUE = {
    "-C",
    "--git-dir",
    "--work-tree",
    "--namespace",
}

# Standalone switches that change nothing about what gets run.
GIT_GLOBAL_FLAGS_NO_VALUE = {
    "-P",
    "--no-pager",
    "--bare",
    "--no-replace-objects",
    "--literal-pathspecs",
    "--glob-pathspecs",
    "--noglob-pathspecs",
    "--icase-pathspecs",
    "--no-optional-locks",
}

# Options that ARE the whole command — there is no subcommand after them.
GIT_TERMINAL_FLAGS = {
    "--version",
    "--help",
    "-h",
    "--html-path",
    "--man-path",
    "--info-path",
}

# Global options that hand git arbitrary code or configuration, so they stay
# refused no matter how read-only the subcommand behind them looks.
GIT_FORBIDDEN_GLOBAL_FLAGS = {
    "-c": "it sets arbitrary git config for the run (e.g. core.pager, alias.*), which can execute a command",
    "--config-env": "it sets arbitrary git config from the environment, which can execute a command",
    "--exec-path": "it changes where git looks for its subcommands, which can execute an arbitrary binary",
}


def _unrecognized_git_option_error(name: str) -> str:
    """Why *name* stopped the walk, phrased so the caller can act on it.

    Git lets a short option carry its value attached (``-C/tmp``), but the walk
    matches whole tokens, so the plain "not recognized" text named a flag the
    caller never wrote and left nothing to change. The attached form stays
    refused — teaching the `-C` sandbox check a second way to split a token is
    how that sandbox springs a leak.
    """
    prefix = name[:2]
    if prefix in GIT_FORBIDDEN_GLOBAL_FLAGS:
        return (
            f"Git global option '{prefix}' is not allowed: "
            f"{GIT_FORBIDDEN_GLOBAL_FLAGS[prefix]}."
        )
    if prefix in GIT_GLOBAL_FLAGS_WITH_VALUE:
        return (
            f"Git global option '{prefix}' needs its value as a separate word: "
            f"write '{prefix} {name[2:]}', not '{name}'."
        )
    return (
        f"Git global option '{name}' is not recognized, so the subcommand "
        "behind it cannot be identified."
    )


def _resolve_git_subcommand(cmd_parts: list) -> tuple:
    """Step over git's global options to find the real subcommand.

    ``git -C <path> branch`` is a branch listing, not a ``-C`` command; reading
    ``cmd_parts[1]`` blindly refuses every invocation that carries a global flag.

    Returns:
        ``(subcommand, error_message)`` — exactly one is non-None. A terminal
        flag like ``--version`` comes back as the subcommand, since nothing
        follows it.
    """
    index = 1
    while index < len(cmd_parts):
        token = cmd_parts[index]
        if not token.startswith("-"):
            return token.lower(), None

        name = token.split("=", 1)[0]
        if name in GIT_TERMINAL_FLAGS:
            return name, None
        if name in GIT_FORBIDDEN_GLOBAL_FLAGS:
            return None, (
                f"Git global option '{name}' is not allowed: "
                f"{GIT_FORBIDDEN_GLOBAL_FLAGS[name]}."
            )
        if name in GIT_GLOBAL_FLAGS_WITH_VALUE:
            # `--opt=value` carries its value; `--opt value` consumes the next token.
            index += 1 if "=" in token else 2
            continue
        if name in GIT_GLOBAL_FLAGS_NO_VALUE:
            index += 1
            continue
        return None, _unrecognized_git_option_error(name)

    return None, "No git subcommand was given."


# Safe PowerShell cmdlet prefixes (read-only operations)
SAFE_PS_CMDLET_PREFIXES = (
    "get-",
    "select-object",
    "format-list",
    "format-table",
    "format-wide",
    "where-object",
    "sort-object",
    "measure-object",
    "group-object",
    "convertto-",
    "convertfrom-",
    "out-string",
    "out-null",
    "write-output",
    "test-path",
    "join-path",
    "split-path",
    "resolve-path",
)

# Dangerous PowerShell patterns to block
DANGEROUS_PS_PATTERNS = (
    "set-",
    "remove-",
    "new-",
    "stop-",
    "start-",
    "restart-",
    "invoke-",
    "clear-",
    "disable-",
    "enable-",
    "uninstall-",
    "install-",
    "register-",
    "unregister-",
    "add-",
    "move-",
    "copy-",
    "rename-",
    "update-",
    "send-",
    "import-",
    "export-",
    "iex",
    "invoke-expression",
    "invoke-command",
    "invoke-webrequest",
    "start-process",
    "net ",  # net user, net stop, etc.
    "cmd ",
    "& {",
    "& '",
    '& "',
)

# Shell operators that change what a command IS, rather than which commands run.
# Chaining (&&, ||, ;) and pipes are split off first and validated segment by
# segment, and the two stderr redirections that write nothing are taken out
# before the scan (_take_stderr_redirections); what is left here has no such
# reading.
# - > >> are output redirection, < is input redirection
# - ` and $() are command substitution
# - & backgrounds a command, and cmd.exe runs `dir . &where cmd` as two of them
#   — every `&` counts, spaced or not, so a lone one is never read as the `&&`
#   the splitter has already taken out.
# - a newline is a command separator to cmd.exe, and to every shell
DANGEROUS_SHELL_OPERATORS = re.compile(r"(?:&|>|<|`|\$\(|[\r\n])")

#: A leading ``NAME=value`` token, the way a shell reads one. The value may be
#: empty, and may hold anything shlex produced — it is handed to the subprocess
#: as an environment entry, never to a shell, so a metacharacter in it is data.
_ENV_ASSIGNMENT = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", re.DOTALL)

#: Variables that change what executes rather than how it behaves: the dynamic
#: linkers, the shells, and the hook/option variables of the binaries this tool
#: can actually run (git, gh, pytest, less, powershell). Families are denied
#: whole — a per-name list goes stale the moment one of those tools adds a hook.
DENIED_ENV_PREFIXES = (
    "LD_",  # ELF loader: LD_PRELOAD, LD_LIBRARY_PATH, LD_AUDIT
    "DYLD_",  # macOS loader: DYLD_INSERT_LIBRARIES, DYLD_LIBRARY_PATH
    "GCONV_",  # glibc loads a conversion module by path
    "BASH_",  # BASH_ENV sources a file before the shell runs
    "GIT_",  # GIT_SSH_COMMAND, GIT_EXTERNAL_DIFF, GIT_CONFIG_* inject commands
    "GH_",  # GH_PAGER, GH_EDITOR, GH_BROWSER run a command of their own
    "PYTEST_",  # PYTEST_ADDOPTS/PYTEST_PLUGINS re-inject flags and plugins
    "PERL",  # PERL5OPT/PERL5LIB, and PERL5DB
    "NODE_",  # NODE_OPTIONS carries --require
    "RUBY",  # RUBYOPT/RUBYLIB
    "LESS",  # LESSOPEN is an input filter, i.e. a command
    "JAVA",  # JAVA_TOOL_OPTIONS
    "_JAVA",  # _JAVA_OPTIONS
    "JDK_",  # JDK_JAVA_OPTIONS
    "PS",  # PSModulePath, PSExecutionPolicyPreference
)

#: The same rule for variables with no family to deny.
DENIED_ENV_NAMES = frozenset(
    {
        "PATH",
        "SHELL",
        "ENV",
        "IFS",
        "PAGER",
        "EDITOR",
        "VISUAL",
        "BROWSER",
        "PYTHONSTARTUP",
        "PYTHONHOME",
        "PYTHONEXECUTABLE",
        "PYTHONBREAKPOINT",
        "PYTHONUSERBASE",
        "PYTHONINSPECT",
        "GREP_OPTIONS",
        "LOCPATH",
        "NLSPATH",
        "TERMINFO",
    }
)


#: ``PYTHONPATH`` is deliberately absent: it is the case this exists for, and
#: its containment is the path check every value goes through in
#: ``_path_traversal_refusal`` rather than a name rule.


def _denied_env_name(name: str) -> bool:
    """True when *name* is a loader or hook variable.

    Case-insensitive, because Windows matches environment names that way: a
    lowercase ``path=`` overrides ``PATH`` there.
    """
    upper = name.upper()
    return upper in DENIED_ENV_NAMES or upper.startswith(DENIED_ENV_PREFIXES)


def _take_env_assignments(segment: list) -> tuple:
    """A segment's leading ``NAME=value`` tokens, and the argv left after them.

    Leading only, so ``grep a=b file`` is still a pattern. What is left is what
    the allowlist sees, which is why ``PYTHONPATH=. rm -rf /`` is refused
    exactly as ``rm -rf /`` is.
    """
    env: Dict[str, str] = {}
    index = 0
    for token in segment:
        match = _ENV_ASSIGNMENT.fullmatch(token)
        if match is None:
            break
        env[match[1]] = match[2]
        index += 1
    return env, segment[index:]


#: PowerShell execution flags that bypass cmdlet filtering outright.
BLOCKED_PS_FLAGS = frozenset(
    {
        "-encodedcommand",
        "-enc",
        # powershell.exe's own switch table carries these short aliases
        # alongside the prefix rule, so neither is reachable by prefix alone.
        "-ec",
        "-ea",
        "-file",
        "-f",
        "-executionpolicy",
        "-ex",
        "-ep",
        "-noprofile",
        "-nop",
        "-windowstyle",
        "-w",
        "-noninteractive",
        "-noni",
    }
)

#: PowerShell resolves a parameter from any unambiguous prefix of its name, so
#: an exact-match blocklist leaves ``-e``, ``-ec``, ``-fi``, ``-exec`` open onto
#: the very parameters it names.
PREFIX_BLOCKED_PS_PARAMS = (
    "encodedcommand",
    "encodedarguments",
    "file",
    "executionpolicy",
)

_SAFE_PS_LEADING_SWITCHES = frozenset({"nologo", "sta", "mta"})

#: Ways a ``-Command`` body reaches code the cmdlet allowlist never sees. The
#: outer operator scan skips this body by design (``_operator_check_text``), so
#: every escape it would have caught has to be caught here instead.
_PS_BODY_ESCAPES = (
    (re.compile(r"::"), "static .NET member access ([Type]::Member)"),
    (re.compile(r"\.\s*[a-z_][a-z0-9_]*\s*\("), "method invocation (.Method(...))"),
    (re.compile(r"(?:^|\|)\s*\.\s+"), "dot-sourcing (. script.ps1)"),
    # The two below fire in command position only — start of the body or just
    # after a pipe — so a path OPERAND (Get-Content C:/log.txt) is still a read.
    (
        re.compile(r"(?:^|\|)\s*(?:[.\\/]|[a-z]:)"),
        "running a file by path instead of a cmdlet",
    ),
    (
        re.compile(r"(?:^|\|)\s*\S*\.(?:ps1|psm1|exe|bat|cmd|vbs|js)\b"),
        "running a script or executable instead of a cmdlet",
    ),
    (re.compile(r"&"), "the call operator (& command)"),
    (re.compile(r"\$"), "variables and subexpressions ($var, $(...))"),
    (re.compile(r"[<>]"), "redirection (>, >>, <)"),
    (re.compile(r"[;\r\n]"), "statement separators (; or newline)"),
)

#: Long-flag names that make a command write a file. Matched on any prefix
#: because GNU-style long options accept unambiguous abbreviations.
_FILE_WRITE_FLAG_NAMES = ("output", "append")


def _is_blocked_ps_flag(token: str) -> bool:
    """True when *token* spells a PowerShell parameter that defeats the filter."""
    if not token or token[0] not in "-/":
        return False
    name = token[1:].split(":", 1)[0].split("=", 1)[0].lower()
    if not name:
        return False
    if f"-{name}" in BLOCKED_PS_FLAGS:
        return True
    return any(param.startswith(name) for param in PREFIX_BLOCKED_PS_PARAMS)


def _powershell_body_escape(ps_cmd: str) -> Optional[str]:
    """What *ps_cmd* uses to run code the cmdlet allowlist cannot see, or None."""
    for pattern, description in _PS_BODY_ESCAPES:
        if pattern.search(ps_cmd):
            return description
    return None


def _is_file_write_flag(token: str) -> bool:
    """True when *token* is a write-to-a-file flag in any of its spellings.

    Long and Windows-style names match on any prefix (``--o``, ``/out:``);
    the single-dash form matches only a cluster starting in ``o``, so
    ``git branch -a`` is not mistaken for ``--append``.
    """
    lowered = token.lower()
    head = lowered.split("=", 1)[0].split(":", 1)[0]
    if head.startswith("--") or head.startswith("/"):
        name = head.lstrip("-/")
        return bool(name) and any(
            full.startswith(name) for full in _FILE_WRITE_FLAG_NAMES
        )
    if head.startswith("-") and head != "-":
        return head[1] == "o"
    return False


#: Binaries an agent reaches for when it means "change this file". None are on
#: ALLOWED_COMMANDS, so they are refused either way — but the generic refusal
#: says "only read-only commands are allowed" and lists read-only examples,
#: which leaves no route to the thing the agent was trying to do. Naming these
#: lets the refusal point at edit_file instead of dead-ending (#3600).


FILE_REWRITE_BINARIES = frozenset(
    {"sed", "awk", "perl", "tee", "patch", "dd", "truncate", "ex", "ed"}
)

#: In-place flags for the binaries that can also be used read-only. ``sed -n
#: '10,20p' f`` prints a range and ``awk '{print $1}' f`` filters a stream;
#: neither is an edit, and answering them with "use edit_file" would push the
#: agent toward a write tool when it was trying to read.
_IN_PLACE_FLAGS = ("-i", "--in-place")

#: These write by definition — there is no read-only invocation to protect.
_ALWAYS_WRITES = frozenset({"tee", "patch", "dd", "truncate", "ed"})


def _rewrites_in_place(cmd_base: str, cmd_parts: list) -> bool:
    """Would this invocation change a file, as opposed to reading one?"""
    if cmd_base in _ALWAYS_WRITES:
        return True
    return any(
        part == flag
        or part.startswith(flag + "=")
        or (flag == "-i" and part.startswith("-i") and not part.startswith("--"))
        for part in cmd_parts[1:]
        for flag in _IN_PLACE_FLAGS
    )


#: The one tool whose executor enforces the read-only binary policy, and so the
#: only one a ``shell:execute`` grant may exempt from confirmation.
_POLICY_GATED_SHELL_TOOL = "run_shell_command"


def skill_granted_binaries(host: Any) -> frozenset:
    """CLIs *host*'s loaded skills granted via ``shell:execute:<binary>``.

    Read off the agent instance, never a module global: the grant belongs to one
    agent's session, so a skill loaded on one agent can never widen a sibling's
    shell. Empty for a host that has loaded no such skill — the common case,
    which leaves the whitelist behaviour byte-identical.
    """
    grants = getattr(host, "_granted_binaries", None)
    return grants.binaries() if grants is not None else frozenset()


def _is_granted_segment(segment: list, granted: frozenset) -> bool:
    """True when *segment* runs a CLI this agent's skills granted."""
    if not granted:
        return False
    from gaia.skills.binaries import normalize_binary, policy_argv

    return normalize_binary(policy_argv(segment)[0]) in granted


def _outside_double_quotes(text: str) -> str:
    """The spans of *text* the shell reads as syntax rather than as data.

    Both ``cmd.exe`` and ``sh`` honour double quotes and treat ``&``, ``|``,
    ``<`` and ``>`` between them as literals, so scanning a quoted argument for
    operators refuses reads like ``gh api "issues?a=1&b=2"``. An odd quote count
    means the quoting is broken, so nothing is stripped and the whole string is
    scanned.
    """
    if text.count('"') % 2:
        return text
    return " ".join(text.split('"')[::2])


#: The two stderr redirections that create nothing: one merges the stream into
#: stdout, the other discards it. Exact spellings only — ``2>`` to any other
#: path writes a file, and stays refused with every other redirection.
_MERGE_STDERR = "2>&1"
_DROP_STDERR = "2>/dev/null"

#: cmd.exe's null device. ``2>/dev/null`` there would write ``\dev\null``.
_WINDOWS_NULL = "2>nul"

#: A standalone token, whitespace or an end on either side, so ``2>&1x`` and
#: ``2>/dev/null.bak`` are not one of these.
_STDERR_REDIRECTION = re.compile(r"(?<![^\s])(?:2>&1|2>/dev/null)(?![^\s])")

#: A pipe the way ``_split_pipeline`` reads one: its own token, never ``a|b``.
_BARE_PIPE = re.compile(r"(?<![^\s])\|(?![^\s])")


def _double_quoted(text: str) -> list:
    """Per character: is it inside a double-quoted span, or a quote itself?"""
    inside = False
    mask = []
    for char in text:
        if char == '"':
            inside = not inside
            mask.append(True)
        else:
            mask.append(inside)
    return mask


def _stderr_redirections(text: str) -> list:
    """Every standalone stderr redirection in *text*, in order.

    Double quotes protect an operand here exactly as they do in
    ``_outside_double_quotes``, odd-count caveat included: broken quoting
    takes nothing out, which leaves the operator scan to refuse it. Single
    quotes do not protect, for the reason ``_split_connectors`` gives — but a
    ``'2>&1'`` glued to one is not a standalone token either way.
    """
    if text.count('"') % 2:
        return []
    quoted = _double_quoted(text)
    return [m for m in _STDERR_REDIRECTION.finditer(text) if not quoted[m.start()]]


def _take_stderr_redirections(text: str) -> tuple:
    """*text* without its stderr redirections, and the one each segment asked for.

    Neither form creates, truncates, or runs anything — they only say where
    that segment's stderr goes — so they are lifted out here rather than
    refused by the operator scan. Segments are counted the way
    ``_split_pipeline`` counts them, so a redirection lands on the command
    that wrote it. Only the surrounding whitespace survives a removal: a
    newline elsewhere on the line must still reach the operator scan.
    """
    matches = _stderr_redirections(text)
    if not matches:
        return text, {}
    quoted = _double_quoted(text)
    pipes = [m.start() for m in _BARE_PIPE.finditer(text) if not quoted[m.start()]]
    modes: Dict[int, str] = {}
    kept = []
    end = 0
    for match in matches:
        # sh's rule: a second redirection on one segment replaces the first.
        modes[sum(1 for pipe in pipes if pipe < match.start())] = match.group(0)
        kept.append(text[end : match.start()])
        end = match.end()
    kept.append(text[end:])
    return "".join(kept), modes


def _as_cmd_redirections(text: str) -> str:
    """*text* with its stderr redirections spelled the way cmd.exe spells them.

    A Windows step runs as a string through cmd.exe, which applies the
    redirection per segment itself — the one thing this process cannot do for
    a pipeline it does not own.
    """
    out = []
    end = 0
    for match in _stderr_redirections(text):
        out.append(text[end : match.start()])
        out.append(_MERGE_STDERR if match.group(0) == _MERGE_STDERR else _WINDOWS_NULL)
        end = match.end()
    out.append(text[end:])
    return "".join(out)


def _operator_check_text(command: str) -> str:
    """The part of *command* the operator blocklist applies to.

    A PowerShell ``-Command`` body is script, not outer shell, and is validated
    separately by ``_validate_command`` (DANGEROUS_PS_PATTERNS + the cmdlet
    prefix allowlist). Scanning it here would refuse legitimate cmdlets.
    """
    if not command.strip().lower().startswith(("powershell ", "powershell.exe ")):
        return _outside_double_quotes(command)
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    outer = []
    skip_next = False
    for part in parts:
        if skip_next:
            skip_next = False
            continue
        if part.lower() in ("-command", "-c"):
            skip_next = True
            continue
        outer.append(part)
    return " ".join(outer)


_ENV_ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")


def _split_pipeline(cmd_parts: list) -> list:
    """Split a shlex-split command on ``|`` into its non-empty segments."""
    segments: list = []
    current: list = []
    for part in cmd_parts:
        if part == "|":
            if current:
                segments.append(current)
            current = []
        else:
            current.append(part)
    if current:
        segments.append(current)
    return segments


#: Git global options whose value is a filesystem path git will operate in.
_GIT_PATH_FLAGS = ("-C", "--git-dir", "--work-tree")


def _git_path_flag_values(cmd_parts: list, cwd: str) -> list:
    """``(flag, resolved_path)`` for every path-taking git global option.

    Resolved the way git does: ``-C`` is relative to the directory before it,
    and ``--git-dir``/``--work-tree`` are relative to the last ``-C``. Without
    this check ``-C`` would be a way around the ``working_directory`` sandbox.
    """
    values: list = []
    base = Path(cwd)
    index = 1
    while index < len(cmd_parts):
        token = cmd_parts[index]
        if not token.startswith("-"):
            break
        name, has_inline, inline = token.partition("=")
        if name not in GIT_GLOBAL_FLAGS_WITH_VALUE:
            index += 1
            continue
        if has_inline:
            value = inline
            index += 1
        elif index + 1 < len(cmd_parts):
            value = cmd_parts[index + 1]
            index += 2
        else:
            break
        if name in _GIT_PATH_FLAGS:
            resolved = base.joinpath(value).resolve()
            values.append((name, str(resolved)))
            if name == "-C":
                base = resolved
    return values


#: The connectors that chain one line's pipelines. Longest first, so ``||`` is
#: never read as a pipe.
_CONNECTORS = ("&&", "||", ";")

#: A missing executable, the way a shell reports one.
_COMMAND_NOT_FOUND = 127


def _split_connectors(command: str) -> list:
    """``a && b; c`` as ``[("a", ""), (" b", "&&"), (" c", ";")]``.

    Each connector travels with the pipeline it gates, so the first is always
    "". A double-quoted span does not split — it is data to sh and to cmd.exe
    alike. That is the quote model ``_outside_double_quotes`` uses, odd-count
    caveat included: the splitter and the operator blocklist have to agree on
    where a segment ends or one of them is scanning the wrong text.

    Single quotes deliberately do not protect an operator, because cmd.exe does
    not honour them: ``grep 'a||b' f`` splits here and dies on the unbalanced
    quote, rather than reaching cmd.exe as two commands one of them never saw.
    """
    honour_quotes = command.count('"') % 2 == 0
    parts: list = []
    start = 0
    connector = ""
    quoted = False
    index = 0
    while index < len(command):
        if honour_quotes and command[index] == '"':
            quoted = not quoted
        elif not quoted:
            found = next((c for c in _CONNECTORS if command.startswith(c, index)), None)
            if found:
                parts.append((command[start:index], connector))
                connector = found
                index += len(found)
                start = index
                continue
        index += 1
    parts.append((command[start:], connector))
    return parts


@dataclass(frozen=True)
class _Step:
    """One pipeline of a command line, with the connector that gates it.

    ``text`` and ``segments`` have had the stderr redirections and the leading
    environment assignments lifted out; ``stderr_modes`` and ``envs`` hold what
    each segment asked for, and ``shell_text`` is the line cmd.exe gets, which
    keeps both — so it is only ever used for a step with no assignment.
    """

    text: str
    segments: list
    connector: str
    stderr_modes: tuple = ()
    shell_text: str = ""
    envs: tuple = ()

    @property
    def is_cd(self) -> bool:
        return len(self.segments) == 1 and self.segments[0][0].lower() == "cd"


def _connector_runs(connector: str, previous_code: int) -> bool:
    """sh's rule: ``&&`` needs the last status 0, ``||`` needs it non-zero.

    A skipped step leaves ``previous_code`` untouched, which is what makes
    ``false && a || b`` run ``b``.
    """
    if connector == "&&":
        return previous_code == 0
    if connector == "||":
        return previous_code != 0
    return True


def _cd_shape_refusal(step: _Step) -> Optional[Dict[str, Any]]:
    """``cd`` may move this line's later commands, and do nothing else.

    It is the one command whose effect outlives its own process, so its form is
    pinned here: a bare ``cd <dir>``, never a pipeline stage, never a flag.
    """
    if not any(segment[0].lower() == "cd" for segment in step.segments):
        return None
    if len(step.segments) > 1:
        return {
            "status": "error",
            "error": "cd cannot be part of a pipeline; it must stand alone.",
            "has_errors": True,
            "hint": "Write 'cd <dir> && <command>' instead.",
        }
    segment = step.segments[0]
    if len(segment) != 2 or segment[1].startswith("-"):
        return {
            "status": "error",
            "error": "cd takes exactly one directory and no flags.",
            "has_errors": True,
            "hint": "Write 'cd <dir> && <command>', or pass working_directory.",
        }
    if step.envs and step.envs[0]:
        return {
            "status": "error",
            "error": "cd takes no environment assignment: it starts no process.",
            "has_errors": True,
            "hint": "Put it on the command that needs it: 'cd <dir> && VAR=x <cmd>'.",
        }
    return None


def _env_refusal(name: str) -> Dict[str, Any]:
    """Why a loader or hook variable is refused, and what is not."""
    return {
        "status": "error",
        "error": (
            f"Setting '{name}' is not allowed: it changes which binary runs, or "
            "what code one loads, so it would carry the command back outside "
            "the allowlist that just cleared it."
        ),
        "has_errors": True,
        "hint": (
            "The loader and hook variables (PATH, LD_*, DYLD_*, GIT_*, GH_*, "
            "PYTEST_*, BASH_*, SHELL, PAGER, EDITOR, ...) are the only ones "
            "refused. Any other 'NAME=value' in front of a command is fine: "
            "'PYTHONPATH=. pytest -q'."
        ),
    }


def _no_command_refusal(segment: list) -> Dict[str, Any]:
    """An assignment with nothing after it sets a variable and runs nothing."""
    return {
        "status": "error",
        "error": f"'{' '.join(segment)}' sets a variable and runs nothing.",
        "has_errors": True,
        "hint": "Put the command after it: 'PYTHONPATH=. pytest -q'.",
    }


def _take_segment_envs(segments: list) -> tuple:
    """``(envs, segments, error)`` — each segment's assignments, split off it.

    One tuple entry per segment, so the count never changes: a segment that is
    nothing but assignments is a refusal, not a segment that disappears.
    """
    envs: list = []
    stripped: list = []
    for segment in segments:
        env, argv = _take_env_assignments(segment)
        denied = next((name for name in env if _denied_env_name(name)), None)
        if denied is not None:
            return (), [], _env_refusal(denied)
        if not argv:
            return (), [], _no_command_refusal(segment)
        envs.append(env)
        stripped.append(argv)
    return tuple(envs), stripped, None


def _parse_line(command: str) -> tuple:
    """*command* as steps, or the refusal its text alone earns.

    Shape only — operators, quoting, pipes, and ``cd``'s form. What each
    command may DO is the caller's question, so the refusal path and the
    skill-grant check share one answer to what a segment IS before they
    disagree about anything else.
    """
    steps: list = []
    parts = _split_connectors(command)
    for raw_text, connector in parts:
        text, modes = _take_stderr_redirections(raw_text)
        if DANGEROUS_SHELL_OPERATORS.search(_operator_check_text(text)):
            return [], {
                "status": "error",
                "error": (
                    "Shell operators (&, >, >>, <, `, $(), newline) are not "
                    "allowed for security reasons. The only redirections "
                    "allowed are 2>&1 and 2>/dev/null, which write nothing."
                ),
                "has_errors": True,
                "hint": (
                    "Pipes (|) and chaining (&&, ||, ;) are allowed. To keep "
                    "stderr, write '2>&1'; to drop it, write '2>/dev/null'. "
                    "To write a file, use write_file or edit_file."
                ),
            }
        try:
            cmd_parts = shlex.split(text)
        except ValueError as exc:
            return [], {
                "status": "error",
                "error": f"Invalid command syntax: {exc}",
                "has_errors": True,
            }
        segments = _split_pipeline(cmd_parts)
        if not segments:
            missing = f"after '{connector}'" if connector else "before an operator"
            return [], {
                "status": "error",
                "error": (
                    "Empty command" if len(parts) == 1 else f"Empty command {missing}"
                ),
                "has_errors": True,
            }
        if any(index >= len(segments) for index in modes):
            return [], {
                "status": "error",
                "error": "A stderr redirection here is not attached to a command.",
                "has_errors": True,
                "hint": "Write it after the command it belongs to: 'cmd 2>&1 | tail'.",
            }
        envs, segments, error = _take_segment_envs(segments)
        if error is not None:
            return [], error
        step = _Step(
            text=text.strip(),
            segments=segments,
            connector=connector,
            stderr_modes=tuple(modes.get(i, "") for i in range(len(segments))),
            shell_text=_as_cmd_redirections(raw_text).strip(),
            envs=envs,
        )
        error = _cd_shape_refusal(step)
        if error:
            return [], error
        steps.append(step)
    return steps, None


def _captured_stderr(mode: str, default: Any) -> Any:
    """Where *mode* sends this command's stderr; *default* when it asked for nothing.

    Both are destinations for a stream this process already captures — nothing
    is opened by name, so neither can create or truncate a file.
    """
    if mode == _MERGE_STDERR:
        return subprocess.STDOUT
    if mode == _DROP_STDERR:
        return subprocess.DEVNULL
    return default


def _segment_env(assignments: Dict[str, str]) -> Dict[str, str]:
    """This process's environment plus one segment's assignments.

    A copy every time: ``os.environ`` itself is never touched, so nothing a
    command sets outlives it or reaches the agent.
    """
    return {**os.environ, **assignments}


def _run_pipeline(
    segments: list, modes: tuple, envs: tuple, cwd: str, timeout: float
) -> subprocess.CompletedProcess:
    """Run validated ``a | b | c`` segments as chained processes, no shell.

    ``returncode`` is the rightmost failing stage (pipefail), so ``pytest |
    tail`` cannot turn a failing suite into a passing check. An upstream stage
    killed by SIGPIPE is not a failure: that is how ``| head`` ends a pipeline.

    *modes* is each segment's stderr redirection, if it asked for one. A merged
    segment writes stderr down its own stdout pipe, which is what puts it in
    front of the next stage's ``grep``; neither mode touches a return code.

    *envs* is each segment's own environment assignments, applied to that
    segment's process and to nothing else.
    """
    deadline = time.monotonic() + timeout
    procs: list = []
    errs: list = []
    upstream = None
    try:
        for index, argv in enumerate(segments):
            mode = modes[index] if index < len(modes) else ""
            err_target = _captured_stderr(mode, None)
            if err_target is None:
                errs.append(
                    tempfile.TemporaryFile()  # pylint: disable=consider-using-with
                )
                err_target = errs[-1]
            procs.append(
                subprocess.Popen(  # pylint: disable=consider-using-with
                    argv,
                    cwd=cwd,
                    stdin=subprocess.DEVNULL if upstream is None else upstream,
                    stdout=subprocess.PIPE,
                    stderr=err_target,
                    env=_segment_env(envs[index] if index < len(envs) else {}),
                )
            )
            if upstream is not None:
                # Only the child may hold the read end, or an early-exiting
                # reader never delivers SIGPIPE to the writer.
                upstream.close()
            upstream = procs[-1].stdout
        try:
            out, _ = procs[-1].communicate(timeout=max(deadline - time.monotonic(), 0))
            for proc in procs[:-1]:
                proc.wait(timeout=max(deadline - time.monotonic(), 0))
        except subprocess.TimeoutExpired as exc:
            for proc in procs:
                proc.kill()
            for proc in procs:
                proc.wait()
            raise subprocess.TimeoutExpired(
                exc.cmd, timeout, output=exc.output, stderr=_read_all(errs)
            ) from exc
    except BaseException:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            if proc.stdout and not proc.stdout.closed:
                proc.stdout.close()
        raise
    finally:
        stderr = _read_all(errs)
        for err in errs:
            err.close()

    sigpipe = -getattr(signal, "SIGPIPE", 0)
    codes = [proc.returncode for proc in procs]
    failed = [
        code
        for i, code in enumerate(codes)
        if code != 0 and not (i < len(codes) - 1 and sigpipe and code == sigpipe)
    ]
    return subprocess.CompletedProcess(
        args=segments,
        returncode=failed[-1] if failed else 0,
        stdout=(out or b"").decode("utf-8", errors="replace"),
        stderr=stderr,
    )


#: Unix commands cmd.exe spells differently, used only when Git-for-Windows
#: has not put the Unix one on PATH.
_UNIX_TO_WIN = {
    "ls": "dir",
    "pwd": "cd",
    "cat": "type",
    "which": "where",
    "cp": "copy",
    "mv": "move",
}


def _run_step(
    step: _Step, cwd: str, timeout: float, granted: frozenset
) -> subprocess.CompletedProcess:
    """Run one validated pipeline in *cwd*, and return what it produced.

    Raises ``subprocess.TimeoutExpired`` with whatever it had produced by then.

    On Windows a step goes through cmd.exe as its own string — never the whole
    line, whose connectors cmd.exe would act on without any of the per-segment
    validation the line has been through here.
    """
    segments = step.segments

    # On Windows, many commands are shell built-ins (dir, cd, type, echo) and
    # Unix commands (ls, pwd, cat) don't exist as .exe files. Since the command
    # has already been validated against the whitelist, we use shell=True on
    # Windows so cmd.exe can resolve both built-ins and commands on PATH
    # (including those from Git for Windows which provides ls, cat, grep, etc.).
    #
    # A skill-granted CLI is the exception, and must stay one. It is a real
    # executable — it needs no built-in resolution — and it is the one path that
    # can run without a confirmation prompt, on arguments built from untrusted
    # remote text (an issue body the model just read). Handing cmd.exe the raw
    # STRING there would let that text act: `--search "x|whoami"` is one argv
    # token to every check above and two commands to cmd.exe, and `%VAR%`
    # expands into a value the approval prompt never showed. argv goes to the
    # process verbatim, so neither is possible.
    # One segment only: the `|` tokens are already dropped, so an argv run of a
    # pipeline would concatenate its commands. Off Windows, _run_pipeline
    # chains the segments.
    lone_granted_segment = (
        len(segments) == 1
        and bool(granted)
        and _is_granted_segment(segments[0], granted)
    )
    # An environment assignment is scoped to its own segment, and cmd.exe owns
    # the whole string it is handed — so a step carrying one runs as argv here
    # too, and gives up cmd.exe's built-in resolution to keep that scope.
    use_shell = (
        os.name == "nt" and not lone_granted_segment and not any(step.envs or ())
    )

    exec_cmd = [part for segment in segments for part in segment]
    if use_shell:
        # The step's own text, to preserve quoting (critical for PowerShell).
        # It keeps the stderr redirections, spelled cmd.exe's way: cmd.exe owns
        # the pipeline here, so only it can route a middle segment's stderr.
        exec_cmd = step.shell_text or step.text
        cmd_base = segments[0][0].lower()
        if cmd_base in _UNIX_TO_WIN:
            import shutil

            if not shutil.which(cmd_base):
                win_cmd = _UNIX_TO_WIN[cmd_base]
                logger.info(
                    "Mapping Unix command '%s' -> Windows '%s'", cmd_base, win_cmd
                )
                exec_cmd = win_cmd + exec_cmd[len(cmd_base) :]

    if len(segments) > 1 and not use_shell:
        return _run_pipeline(segments, step.stderr_modes, step.envs, cwd, timeout)

    # A shell step's redirection is already in the string cmd.exe was handed.
    mode = "" if use_shell or not step.stderr_modes else step.stderr_modes[0]

    # encoding/errors are explicit, and load-bearing. Bare ``text=True``
    # decodes with the locale codec — cp1252 on a default Windows box — and
    # subprocess does that decode inside its pipe reader THREAD. A byte that
    # codec cannot map raises UnicodeDecodeError in that thread, which dies,
    # and subprocess.run then returns returncode 0 with EMPTY stdout. The
    # command succeeded and its output was silently discarded.
    #
    # That is not an edge case: `gh issue list` on amd/gaia returns an issue
    # title containing "⚠️", so GitHub triage got back nothing and the model
    # reported an empty backlog it had never actually read. Any tool emitting
    # UTF-8 (git, gh, npm, docker) hits it. errors="replace" keeps a stray
    # undecodable byte from costing the whole output.
    return subprocess.run(
        exec_cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=_captured_stderr(mode, subprocess.PIPE),
        # stdin is DEVNULL, never inherited. Capturing redirects
        # stdout/stderr but leaves stdin alone, and this process's stdin is the
        # agent transport's pipe — held open by the TUI and never written to. A
        # child that reads it (directly, or by probing whether it is
        # interactive) blocks forever on input that cannot arrive, because
        # there is no human on that pipe.
        #
        # The hang was not theoretical: `gh` spawned from the agent never
        # exited, while the identical command took 0.07s from a shell. Worse,
        # subprocess.run's own timeout does not save it — on expiry it kills
        # the cmd.exe it launched, then calls communicate() again with NO
        # timeout, which waits on pipes the surviving grandchild still holds.
        # That is the 180s tool timeout and the orphaned gh.exe left behind by
        # every attempt.
        #
        # DEVNULL gives an immediate EOF, which is the honest answer here: an
        # agent's shell command is non-interactive by construction.
        stdin=subprocess.DEVNULL,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        env=_segment_env(step.envs[0] if step.envs else {}),
        shell=use_shell,  # nosec B602 - Windows-only; command whitelist-validated above, shell needed for cmd.exe built-ins/pipes
    )


def _as_text(raw: Any) -> str:
    """Partial output from a timeout, whichever way the runner captured it."""
    if not raw:
        return ""
    return raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")


def _read_all(files: list) -> str:
    """Every stage's captured stderr, in pipeline order."""
    chunks = []
    for handle in files:
        if handle.closed:
            continue
        handle.seek(0)
        chunks.append(handle.read())
    return b"".join(chunks).decode("utf-8", errors="replace")


class ShellToolsMixin:
    """
    Mixin providing shell command execution tools with rate limiting.

    Tools provided:
    - run_shell_command: Execute terminal commands with timeout and safety checks

    Rate Limiting:
    - Max 10 commands per minute to prevent DOS
    - Max 3 commands per 10 seconds for burst prevention
    - A command over either limit waits for the window (up to
      ``max_rate_limit_wait_seconds``) instead of being refused
    """

    def __init__(self, *args, **kwargs):
        """Initialize shell tools with rate limiting."""
        super().__init__(*args, **kwargs)

        # Rate limiting configuration
        self.shell_command_times = deque(maxlen=100)  # Track last 100 command times
        self.max_commands_per_minute = 10
        self.max_commands_per_10_seconds = 3

        # Created on first use: an agent that never runs a command should not
        # pay for tracking a session it never needed.
        self._shell_session: Optional[ShellSession] = None

    def _session_cwd_guard(self) -> Callable[[str], bool]:
        """The predicate the session asks before checkpointing a ``cd``.

        Without it, persistence would be a way around the path policy: ``cd`` to
        a forbidden directory, then read a file by bare name, and the per-argument
        check never sees a path to reject. Reuses ``_path_allowed`` -- the same
        check ``working_directory`` and every step's own path already go
        through, so a directory the session remembers and one a fresh call
        would accept can never disagree.
        """
        return self._path_allowed

    @property
    def shell_session(self) -> ShellSession:
        """This agent's shell session, created on first use."""
        session = getattr(self, "_shell_session", None)
        if session is None or session.closed:
            session = ShellSession(cwd_guard=self._session_cwd_guard())
            self._shell_session = session
        return session

    def reset_shell_session(self) -> ShellSession:
        """Replace the session with a clean one and return it."""
        previous = getattr(self, "_shell_session", None)
        self._shell_session = ShellSession(cwd_guard=self._session_cwd_guard())
        if previous is not None:
            previous.close()
        return self._shell_session

    def close_shell_session(self) -> None:
        """Tear the session down at task end. Safe to call more than once."""
        session = getattr(self, "_shell_session", None)
        if session is not None:
            session.close()
            self._shell_session = None

    def _validate_shell_command(self, command: str) -> tuple:
        """Every refusal ``command`` earns on its text alone, plus its steps.

        Each refusal is stamped ``executed: False`` — nothing here has launched
        anything, and downstream cannot tell a refused command from a failed one
        by the shape of the error alone (#3677).
        """
        error, steps = self._shell_command_refusal(command)
        if error is not None:
            error = {**error, **NOT_EXECUTED}
        return error, steps

    def _shell_command_refusal(self, command: str) -> tuple:
        """Every refusal ``command`` earns on its text alone, plus its steps.

        Pure and side-effect free, so it can run twice: once as a pre-flight
        before the confirmation prompt, once on the real execution path. Sharing
        one implementation is what keeps those two from ever disagreeing.

        A chained line is refused whole: one REFUSE-tier segment anywhere means
        no part of it runs, so the model cannot learn to smuggle a refused
        command in behind a permitted one.

        Returns:
            ``(error, steps)`` — ``error`` is None when nothing here refuses
            the command. Refusals that need runtime context (rate limit, working
            directory, path traversal) stay with the caller, so a command this
            clears may still be refused later; one it rejects never runs.
        """
        steps, error = _parse_line(command)
        if error is not None:
            return error, []

        granted = skill_granted_binaries(self)
        for step in steps:
            for segment in step.segments:
                error = self._validate_command(
                    segment[0].lower(),
                    segment,
                    step.text if len(step.segments) == 1 else " ".join(segment),
                    granted_binaries=granted,
                )
                if error:
                    return error, []

        return None, steps

    def policy_refusal_for_call(
        self, tool_name: str, tool_args: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """The refusal this call has already earned, before anyone is asked.

        Read by ``Agent._policy_refusal``. A command the guardrails will refuse
        must never raise a confirmation prompt: asking someone to approve
        ``gh auth token`` when the answer is already no trains them to click
        through, and frames a blocked action as merely risky. Refuse it first
        and say why.

        The mirror of that rule is what makes writes work: a command that WOULD
        run on approval must not be refused here. ``_validate_shell_command``
        returns None for a granted binary's confirmable write, so it falls
        through to the prompt instead of dying in front of it.

        Duck-typed rather than an override — ``Agent`` precedes this mixin in
        ``ChatAgent``'s MRO, so a same-named method here would never be reached.
        """
        if tool_name != _POLICY_GATED_SHELL_TOOL:
            return None
        command = (tool_args or {}).get("command")
        if not isinstance(command, str):
            return None
        error, _ = self._validate_shell_command(command)
        if error is not None:
            logger.info(
                "Refusing %r before the confirmation prompt: %s",
                command,
                error.get("error"),
            )
        return error

    def skill_grant_covers_call(
        self, tool_name: str, tool_args: Dict[str, Any]
    ) -> bool:
        """True when an active ``shell:execute:<binary>`` grant covers this call.

        Read by ``Agent._call_is_pre_authorized`` to skip the per-call
        confirmation modal. The grant *is* the consent: the user declared one
        named binary, restricted to a read-only table, in a skill they chose to
        load. Re-asking on every call is not a second safeguard — a triage runs
        five to ten ``gh`` reads, so it is five to ten modals attended and a
        100% failure rate unattended.

        Deliberately narrow. It answers False unless **every** segment of every
        pipeline on the line is a granted binary running a read-only
        subcommand, so ``gh issue list | head`` still prompts even though
        ``head`` is whitelisted: consent was given for ``gh``, not for a
        pipeline. ``gh issue list && gh issue view 1`` is covered — two reads
        of the granted CLI are what the grant is for, chained or not.

        **The ALLOW tier only.** A confirmable write (``gh issue comment``)
        answers False here on purpose: the grant declares which writes MAY be
        offered, and the user still approves each one. This is the single place
        that decides a gh call runs with nobody asked, which is why it uses
        ``validate_invocation`` — the function whose contract is exactly that
        question, and which answers no for CONFIRM as well as REFUSE.

        Duck-typed rather than an override — ``Agent`` precedes this mixin in
        ``ChatAgent``'s MRO, so a same-named method here would never be reached.
        """
        if tool_name != _POLICY_GATED_SHELL_TOOL:
            # Only the tool that actually runs _validate_command may be exempt;
            # anything else would skip the modal without enforcing the policy.
            return False

        granted = skill_granted_binaries(self)
        if not granted:
            return False

        command = (tool_args or {}).get("command")
        if not isinstance(command, str):
            return False

        # The same parse as the refusal path, or the two tiers disagree about
        # what a segment is.
        steps, error = _parse_line(command)
        if error is not None or not steps:
            return False

        from gaia.skills.binaries import (
            BINARY_POLICIES,
            normalize_binary,
            policy_argv,
            validate_invocation,
        )

        for segment in map(policy_argv, (s for step in steps for s in step.segments)):
            binary = normalize_binary(segment[0])
            if binary not in granted:
                return False
            policy = BINARY_POLICIES.get(binary)
            if policy is None or validate_invocation(policy, segment) is not None:
                return False

        logger.info(
            "Skipping the confirmation prompt for '%s': %r is covered by an active "
            "skill grant (read-only %s).",
            tool_name,
            command,
            ", ".join(sorted(granted)),
        )
        return True

    def _pace_rate_limit(self) -> tuple:
        """Wait out the rate limit rather than refuse, up to a cap.

        A refusal only makes the model send the same command again after the
        same wait, at the cost of a step. Returns ``(allowed, reason,
        wait_time, waited)``; a wait past ``max_rate_limit_wait_seconds`` is
        still refused.
        """
        cap = getattr(self, "max_rate_limit_wait_seconds", 60.0)
        waited = 0.0
        while True:
            allowed, reason, wait_time = self._check_rate_limit()
            if allowed or waited + wait_time > cap:
                return allowed, reason, wait_time, waited
            time.sleep(wait_time)
            waited += wait_time

    def _path_allowed(self, path: str) -> bool:
        """Whether *path* is inside this agent's allowed paths.

        A host with neither validator is unconstrained, which is what a bare
        mixin and every pre-validator agent already were.
        """
        if hasattr(self, "path_validator"):
            return bool(self.path_validator.is_path_allowed(path))
        if hasattr(self, "_is_path_allowed"):
            return bool(self._is_path_allowed(path))
        return True

    def _resolve_cd_target(self, target: str, cwd: str) -> tuple:
        """``(directory, None)`` for ``cd <target>``, or ``(cwd, refusal)``.

        Held to the same bar as ``working_directory``: it is the same thing,
        chosen mid-line instead of up front.
        """
        try:
            resolved = str(Path(cwd).joinpath(target).resolve())
        except (OSError, ValueError) as exc:
            return cwd, {
                **NOT_EXECUTED,
                "status": "error",
                "error": f"cd {target}: could not resolve the directory ({exc})",
                "has_errors": True,
            }
        if not os.path.isdir(resolved):
            return cwd, {
                **NOT_EXECUTED,
                "status": "error",
                "error": f"Directory not found: {target}",
                "has_errors": True,
                "hint": f"Nothing on this line ran. '{target}' is relative to {cwd}.",
            }
        if not self._path_allowed(resolved):
            return cwd, {
                **NOT_EXECUTED,
                "status": "error",
                "error": f"Access denied: {resolved} is not in allowed paths",
                "has_errors": True,
            }
        return resolved, None

    def _path_traversal_refusal(
        self, step: _Step, cwd: str, granted: frozenset
    ) -> Optional[Dict[str, Any]]:
        """Refuse an argument that resolves outside the allowed paths.

        This prevents "cat ../secret.txt" even if "cat" is allowed. Exempt per
        SEGMENT, never per line: a granted CLI's operands are remote ids, but
        'gh … | cat ../secret' must still be checked.

        An environment assignment's value is held to the same rule on EVERY
        segment, granted or not — it is never a remote id, and a value like
        ``PYTHONPATH`` decides which code the command imports.
        """
        if not hasattr(self, "path_validator"):
            return None

        segments = step.segments
        scanned = [seg for seg in segments if not _is_granted_segment(seg, granted)]
        candidates = [("Argument", a) for seg in scanned for a in seg[1:]]
        candidates += [
            (f"'{name}='", entry)
            for env in step.envs or ()
            for name, value in env.items()
            for entry in value.split(os.pathsep)
            if entry
        ]
        for label, arg in candidates:
            candidate_path = arg
            if arg.startswith("-"):
                if "=" in arg:
                    _, candidate_path = arg.split("=", 1)
                else:
                    if os.sep not in arg and "/" not in arg:
                        continue

            # On Windows, skip flags starting with / (e.g., /i, /n, /c:)
            # These are Windows command switches, not Unix paths
            if os.name == "nt" and candidate_path.startswith("/"):
                # Only treat as a real path if it has multiple segments
                # (e.g., /proc/cpuinfo) not single flags (/i, /format:list)
                if "/" not in candidate_path[1:]:
                    continue

            # Check if it looks like a path
            if (
                os.sep in candidate_path
                or "/" in candidate_path
                or ".." in candidate_path
            ):
                # Ignore URLs
                if candidate_path.startswith(
                    ("http://", "https://", "git://", "ssh://")
                ):
                    continue

                # Resolve path relative to CWD
                try:
                    resolved_path = str(Path(cwd).joinpath(candidate_path).resolve())

                    if not self.path_validator.is_path_allowed(resolved_path):
                        return {
                            **NOT_EXECUTED,
                            "status": "error",
                            "error": f"Access denied: {label} '{arg}' resolves to forbidden path '{resolved_path}'",
                            "has_errors": True,
                        }
                except (OSError, ValueError) as exc:
                    # Unresolvable is not a verdict — say so rather than
                    # letting the argument through unnoticed.
                    logger.warning(
                        "Could not resolve '%s' against the allowed "
                        "paths (%s); it was not path-checked.",
                        arg,
                        exc,
                    )
        return None

    def _check_rate_limit(self) -> tuple:
        """
        Check if rate limit allows another command.

        Returns:
            (allowed: bool, reason: str, wait_time: float)
        """
        # Initialize if not already done (defensive programming)
        if not hasattr(self, "shell_command_times"):
            self.shell_command_times = deque(maxlen=100)
            self.max_commands_per_minute = 10
            self.max_commands_per_10_seconds = 3

        current_time = time.time()

        # Remove old timestamps outside the window
        minute_ago = current_time - 60
        ten_sec_ago = current_time - 10

        # Count recent commands
        recent_minute = sum(1 for t in self.shell_command_times if t > minute_ago)
        recent_10_sec = sum(1 for t in self.shell_command_times if t > ten_sec_ago)

        # Check 10-second burst limit
        if recent_10_sec >= self.max_commands_per_10_seconds:
            recent_times = [t for t in self.shell_command_times if t > ten_sec_ago]
            if recent_times:
                oldest_in_window = min(recent_times)
                wait_time = 10 - (current_time - oldest_in_window)
            else:
                wait_time = 10.0
            return (
                False,
                f"Rate limit: max {self.max_commands_per_10_seconds} commands per 10 seconds. Wait {wait_time:.1f}s",
                wait_time,
            )

        # Check 1-minute limit
        if recent_minute >= self.max_commands_per_minute:
            recent_times = [t for t in self.shell_command_times if t > minute_ago]
            if recent_times:
                oldest_in_window = min(recent_times)
                wait_time = 60 - (current_time - oldest_in_window)
            else:
                wait_time = 60.0
            return (
                False,
                f"Rate limit: max {self.max_commands_per_minute} commands per minute. Wait {wait_time:.1f}s",
                wait_time,
            )

        return True, "", 0.0

    def _record_command_execution(self):
        """Record command execution timestamp for rate limiting."""
        self.shell_command_times.append(time.time())

    def _git_path_refusal(self, segments: list, cwd: str) -> Optional[Dict[str, Any]]:
        """Refuse a git ``-C``/``--git-dir``/``--work-tree`` outside allowed paths.

        The same allowed-paths check ``working_directory`` gets.
        """
        for segment in segments:
            if segment[0].lower() != "git":
                continue
            for flag, path in _git_path_flag_values(segment, cwd):
                if hasattr(self, "path_validator"):
                    allowed = self.path_validator.is_path_allowed(path)
                elif hasattr(self, "_is_path_allowed"):
                    allowed = self._is_path_allowed(path)
                else:
                    continue
                if not allowed:
                    return {
                        **NOT_EXECUTED,
                        "status": "error",
                        "error": f"Access denied: git {flag} {path} is not in allowed paths",
                        "has_errors": True,
                    }
        return None

    @staticmethod
    def _validate_command(
        cmd_base: str,
        cmd_parts: list,
        command: str,
        granted_binaries: frozenset = frozenset(),
    ) -> Optional[Dict[str, Any]]:
        """
        Validate a command against the whitelist and subcommand rules.

        Args:
            cmd_base: The lowercased command name.
            cmd_parts: The shlex-split command.
            command: The raw command string.
            granted_binaries: Skill-granted CLIs for *this* agent instance. Passed
                in rather than read from module state so the grant can never be
                global.

        Returns None if the command is allowed, or an error dict if blocked.

        A skill-granted CLI's *confirmable write* returns None here too. This
        method answers "is this refused?", not "may it run unasked" — the
        second question belongs to ``skill_grant_covers_call``, which is what
        decides whether the confirmation prompt is skipped. Answering yes here
        would refuse a write before anyone could approve it, which is the dead
        end this tier removes.
        """
        # Skill-granted CLIs are gated by their own policy table instead of
        # ALLOWED_COMMANDS; anything ungranted is still refused.
        # Imported here — gaia.skills pulls in the connector stack.
        from gaia.skills.binaries import (
            BINARY_POLICIES,
            REFUSE,
            classify_invocation,
            normalize_binary,
            policy_argv,
        )

        policy_parts = policy_argv(cmd_parts)
        binary = normalize_binary(policy_parts[0])
        policy = BINARY_POLICIES.get(binary)
        if policy is not None:
            if binary not in granted_binaries:
                return {
                    "status": "error",
                    "error": (
                        f"Command '{binary}' is not available to this agent. It is "
                        "granted only to a skill that declares "
                        f"'shell:execute:{binary}' in its SKILL.md — load that skill "
                        "first."
                    ),
                    "has_errors": True,
                    "hint": f"{policy.summary} {policy.install_hint}",
                }
            decision = classify_invocation(policy, policy_parts)
            if decision.outcome == REFUSE:
                return {
                    "status": "error",
                    "error": decision.message,
                    "has_errors": True,
                    "hint": (
                        f"This one is refused outright, not gated — the '{binary}' "
                        "grant will not run it even with the user's approval. "
                        "Use an allowed command, or tell the user what you would "
                        "have run and why it is blocked."
                    ),
                }
            return None

        # Special handling for git - only allow read-only operations
        if cmd_base == "git":
            if len(cmd_parts) > 1:
                git_subcmd, resolve_error = _resolve_git_subcommand(cmd_parts)
                if resolve_error is not None:
                    return {
                        "status": "error",
                        "error": resolve_error,
                        "has_errors": True,
                        "allowed_git_commands": sorted(SAFE_GIT_COMMANDS),
                    }
                if (
                    git_subcmd not in SAFE_GIT_COMMANDS
                    and git_subcmd not in GIT_TERMINAL_FLAGS
                ):
                    return {
                        "status": "error",
                        "error": f"Git command '{git_subcmd}' is not allowed. Only read-only git operations are permitted.",
                        "has_errors": True,
                        "allowed_git_commands": sorted(SAFE_GIT_COMMANDS),
                    }
            # A read-only subcommand still writes a caller-chosen path when it
            # is handed an output flag, and the subcommand check never sees it.
            for part in cmd_parts[1:]:
                if (
                    len(cmd_parts) > 1
                    and cmd_parts[1].lower() == "ls-files"
                    and part == "-o"
                ):
                    continue
                if _is_file_write_flag(part):
                    return {
                        "status": "error",
                        "error": (
                            f"git '{part}' writes to a file, which is not allowed "
                            "under the read-only command policy."
                        ),
                        "has_errors": True,
                        "hint": "Drop the output flag and read git's result from stdout.",
                    }
        # Special handling for wmic - only allow read-only queries
        elif cmd_base == "wmic":
            for part in cmd_parts[1:]:
                if _is_file_write_flag(part):
                    return {
                        "status": "error",
                        "error": (
                            f"wmic '{part}' writes to a file, which is not allowed "
                            "under the read-only command policy."
                        ),
                        "has_errors": True,
                        "hint": "Drop /output: and /append: and read the query result from stdout.",
                    }
            cmd_lower = command.lower()
            dangerous_wmic_ops = {"call", "create", "delete", "set"}
            cmd_words = set(cmd_lower.split())
            if cmd_words & dangerous_wmic_ops:
                return {
                    "status": "error",
                    "error": "Only read-only wmic queries are allowed (get, list). Modifying operations (call, create, delete, set) are blocked.",
                    "has_errors": True,
                    "hint": "Use 'wmic <alias> get <properties>' for safe queries",
                    "examples": "wmic cpu get name, wmic os get caption, wmic path win32_videocontroller get name",
                }
        # Special handling for powershell - only allow read-only cmdlets
        elif cmd_base in ("powershell", "powershell.exe"):
            if any(_is_blocked_ps_flag(part) for part in cmd_parts[1:]):
                return {
                    "status": "error",
                    "error": "PowerShell execution flags like -EncodedCommand, -File, and -ExecutionPolicy are not allowed.",
                    "has_errors": True,
                    "hint": "Use -Command to pass a readable cmdlet string",
                    "examples": 'powershell -Command "Get-WmiObject Win32_Processor | Select-Object Name"',
                }
            body_start = 1
            while body_start < len(cmd_parts):
                part = cmd_parts[body_start]
                if not part.startswith(("-", "/")):
                    break
                name = part[1:].lower()
                if name and "command".startswith(name):
                    body_start += 1
                    break
                if name not in _SAFE_PS_LEADING_SWITCHES:
                    return {
                        "status": "error",
                        "error": f"PowerShell switch '{part}' has not been reviewed and is not allowed.",
                        "has_errors": True,
                        "hint": "Use -Command with plain read-only cmdlets.",
                    }
                body_start += 1
            ps_cmd = " ".join(cmd_parts[body_start:]).lower()
            if not ps_cmd:
                return {
                    "status": "error",
                    "error": "PowerShell requires an explicit read-only command.",
                    "has_errors": True,
                }

            escape = _powershell_body_escape(ps_cmd)
            if escape is not None:
                return {
                    "status": "error",
                    "error": (
                        f"PowerShell {escape} is not allowed: it runs code the "
                        "read-only cmdlet allowlist cannot inspect."
                    ),
                    "has_errors": True,
                    "hint": (
                        "Use plain Get-* cmdlets and parameters only — no $variables, "
                        "no [Type]::Member, no .Method(), no &, no redirection, no ';'."
                    ),
                    "examples": (
                        'powershell -Command "Get-CimInstance Win32_Processor | Select-Object Name", '
                        'powershell -Command "Get-Process | Sort-Object WS -Descending | Select-Object -First 15 Name, Id, WS"'
                    ),
                }

            if any(pat in ps_cmd for pat in DANGEROUS_PS_PATTERNS):
                return {
                    "status": "error",
                    "error": "Only read-only PowerShell cmdlets are allowed (Get-*, Select-Object, Format-*, Where-Object, etc.).",
                    "has_errors": True,
                    "hint": "Use Get-* cmdlets for safe queries",
                    "examples": (
                        'powershell -Command "Get-WmiObject Win32_Processor | Select-Object Name", '
                        'powershell -Command "Get-CimInstance Win32_VideoController | Format-List Name,DriverVersion"'
                    ),
                }

            # Verify each cmdlet is safe
            for segment in ps_cmd.split("|"):
                head = re.match(r"\s*([a-z]+-[a-z]+)\b", segment)
                if not head or not head[1].startswith(SAFE_PS_CMDLET_PREFIXES):
                    return {
                        "status": "error",
                        "error": "Each PowerShell pipeline command must be a read-only cmdlet.",
                        "has_errors": True,
                    }
            cmdlets = re.findall(r"[a-z]+-[a-z]+", ps_cmd)
            for cmdlet in cmdlets:
                if not any(
                    cmdlet.startswith(prefix) for prefix in SAFE_PS_CMDLET_PREFIXES
                ):
                    return {
                        "status": "error",
                        "error": f"PowerShell cmdlet '{cmdlet}' is not allowed. Only read-only cmdlets are permitted.",
                        "has_errors": True,
                        "hint": "Allowed: Get-*, Select-Object, Format-List, Format-Table, Where-Object, Sort-Object",
                    }
        # Special handling for find - block predicates that run, delete, or
        # write files. Without this, `find ... -exec touch {} +` executes a
        # binary that is NOT in ALLOWED_COMMANDS, bypassing the whitelist.
        elif cmd_base == "find":
            for part in cmd_parts[1:]:
                if part.lower() in DANGEROUS_FIND_ACTIONS:
                    return {
                        "status": "error",
                        "error": (
                            f"find action '{part}' is not allowed: it can run "
                            "arbitrary commands, delete, or write files, "
                            "bypassing the read-only command whitelist."
                        ),
                        "has_errors": True,
                        "hint": "Use read-only find predicates only: -name, -type, -path, -print, -ls.",
                    }
        # Special handling for sort - -o/--output writes to a file. Cover every
        # spelling: -o FILE, -oFILE, -o=FILE, bundled short clusters (-ro), and
        # every GNU long-option abbreviation of --output (--o, --out, --output=).
        # Any short cluster containing 'o' is treated as -o; this over-blocks a
        # few exotic clusters (e.g. -to, separator 'o'), which is acceptable for
        # a read-only security guard.
        elif cmd_base == "sort":
            for part in cmd_parts[1:]:
                part_lower = part.lower()
                flag = part_lower.split("=", 1)[0]
                is_output = False
                if flag.startswith("--"):
                    # --output and any unambiguous abbreviation (--o, --out, ...).
                    if len(flag) > 2 and "--output".startswith(flag):
                        is_output = True
                elif part_lower.startswith("-") and part_lower != "-":
                    # The leading run of letters is the short-flag cluster;
                    # anything after it is an attached value (-oFILE, -ro/tmp/x).
                    cluster = re.match(r"[a-z]*", flag[1:]).group(0)
                    if "o" in cluster:
                        is_output = True
                if is_output:
                    return {
                        "status": "error",
                        "error": "sort -o/--output writes to a file, which is not allowed under the read-only command policy.",
                        "has_errors": True,
                        "hint": "Drop -o/--output and read sort's result from stdout (e.g. 'sort file' or 'sort file | head').",
                    }
        # Special handling for uniq - a second file operand is an output file.
        elif cmd_base == "uniq":
            # Flags that consume the following token as their value; the operand
            # counter must skip that value so it isn't mistaken for a file.
            _uniq_value_flags = {
                "-f",
                "--skip-fields",
                "-s",
                "--skip-chars",
                "-w",
                "--check-chars",
            }
            operands = []
            skip_next = False
            for part in cmd_parts[1:]:
                if skip_next:
                    skip_next = False
                    continue
                if part in _uniq_value_flags:
                    skip_next = True
                    continue
                if part.startswith("-") and part != "-":
                    continue  # flag (incl. --flag=value and bundled short flags)
                operands.append(part)
            # operands = [input, output]; a second operand is a write target.
            if len(operands) >= 2:
                return {
                    "status": "error",
                    "error": "uniq with an output file is not allowed: it writes to disk, violating the read-only command policy.",
                    "has_errors": True,
                    "hint": "Use a single input (or stdin) and read stdout, e.g. 'uniq file' or 'sort file | uniq'.",
                }
        elif cmd_base in SHELL_KEYWORDS:
            return {
                "status": "error",
                "error": (
                    f"'{cmd_base}' is shell control flow, and commands run "
                    "directly rather than through a shell, so it cannot run."
                ),
                "has_errors": True,
                "hint": (
                    "Use run_python for a loop or a condition, or issue the "
                    "commands one per call and decide between them yourself."
                ),
            }
        elif cmd_base not in ALLOWED_COMMANDS:
            # Refusing a file rewrite with "only read-only commands are allowed"
            # is a dead end: the agent wanted to change a file and the message
            # names nothing that can. Point at the tool that does the job.
            #
            # Only when the invocation actually writes. `sed -n '10,20p' f`
            # prints a line range; answering that with "use edit_file" sends the
            # agent to a write tool when it was trying to read.
            if cmd_base in FILE_REWRITE_BINARIES and _rewrites_in_place(
                cmd_base, cmd_parts
            ):
                return {
                    "status": "error",
                    "error": (
                        f"'{cmd_base}' rewrites files and is not available. Use the "
                        f"edit tools instead — they are not blocked."
                    ),
                    "has_errors": True,
                    "hint": (
                        "Call edit_file with the exact existing text as old_content, "
                        "or edit_python_file for .py to have the edit syntax-checked. "
                        "Use write_file to create a file that does not exist yet."
                    ),
                    "examples": "edit_file(file_path=..., old_content=..., new_content=...)",
                }
            if _ENV_ASSIGNMENT_RE.match(cmd_parts[0]):
                return {
                    "status": "error",
                    "error": (
                        f"'{cmd_parts[0]}' sets an environment variable for the "
                        "command, and inline assignments (VAR=value command) "
                        "are not supported."
                    ),
                    "has_errors": True,
                    "hint": "Run the command without the assignment.",
                }
            return {
                "status": "error",
                "error": f"Command '{cmd_base}' is not in the allowed list for security reasons",
                "has_errors": True,
                "hint": "Only read-only, informational commands are allowed",
                "examples": "ls, cat, grep, find, git status, systeminfo, powershell -Command 'Get-WmiObject ...'",
            }

        return None  # Command is allowed

    def register_shell_tools(self) -> None:
        """Register shell command execution tools."""
        from gaia.agents.base.tools import tool

        @tool(
            atomic=True,
        )
        def run_shell_command(
            command: str, working_directory: Optional[str] = None, timeout: int = 30
        ) -> Dict[str, Any]:
            """
            Execute a shell command and return its output.

            Chain on one line: 'a && b' on success, 'a || b' on failure,
            'a; b' always, 'a | b' pipes, 'cd <dir> && b' runs b there. Each
            is allowlist-checked; one approval covers the line. '2>&1' keeps
            stderr and '2>/dev/null' drops it; 'PYTHONPATH=. pytest -q' scopes
            a variable to one command. Other redirections and ` $() &
            newline are refused.

            Args:
                command: Shell command to execute
                working_directory: Directory to run command in
                timeout: Max execution time in seconds, for the whole line

            Returns:
                Dictionary with status, combined output, the last command's
                exit code, and 'steps' (each command with its own code)
            """
            try:
                # Check rate limits first to prevent DOS
                allowed, reason, wait_time, waited = self._pace_rate_limit()
                if not allowed:
                    return {
                        **NOT_EXECUTED,
                        "status": "error",
                        "error": f"{reason}. Please wait {wait_time:.1f} seconds.",
                        "has_errors": True,
                        "rate_limited": True,
                        "wait_time_seconds": wait_time,
                        "hint": "Rate limiting prevents excessive command execution",
                    }

                # Validate working directory if specified
                if working_directory:
                    if not os.path.exists(working_directory):
                        return {
                            **NOT_EXECUTED,
                            "status": "error",
                            "error": f"Working directory not found: {working_directory}",
                            "has_errors": True,
                        }

                    if not os.path.isdir(working_directory):
                        return {
                            **NOT_EXECUTED,
                            "status": "error",
                            "error": f"Path is not a directory: {working_directory}",
                            "has_errors": True,
                        }

                    if not self._path_allowed(working_directory):
                        hint = (
                            self.path_validator.scratch_hint(working_directory)
                            if hasattr(self, "path_validator")
                            else ""
                        )
                        return {
                            **NOT_EXECUTED,
                            "status": "error",
                            "error": f"Access denied: {working_directory} is not in allowed paths.{hint}",
                            "has_errors": True,
                        }

                    cwd = str(Path(working_directory).resolve())
                    session_scoped = False
                else:
                    # An explicit working_directory is a one-shot override that
                    # never touches the session; with none, resume where the
                    # last call's cd left off.
                    cwd = self.shell_session.cwd
                    session_scoped = True

                # Operators, syntax, and the per-command whitelist, for every
                # segment of every pipeline on the line. Shared with the
                # pre-flight that runs before the confirmation prompt, so a
                # command refused there is refused here for the same reason.
                error, steps = self._validate_shell_command(command)
                if error:
                    return error

                granted = skill_granted_binaries(self)

                # A `cd` step moves the steps after it, so each step's directory
                # — and what its arguments resolve against — is settled here,
                # before anything runs.
                step_cwds: list = []
                walk_cwd = cwd
                for step in steps:
                    step_cwds.append(walk_cwd)
                    if step.is_cd:
                        walk_cwd, error = self._resolve_cd_target(
                            step.segments[0][1], walk_cwd
                        )
                        if error:
                            return error

                for step, step_cwd in zip(steps, step_cwds):
                    error = self._path_traversal_refusal(step, step_cwd, granted)
                    if error:
                        return error
                    error = self._git_path_refusal(step.segments, step_cwd)
                    if error:
                        return error

                # Log command execution (debug mode)
                if hasattr(self, "debug") and self.debug:
                    logger.info(f"Executing command: {command} in {cwd}")

                start_time = time.monotonic()
                deadline = start_time + timeout
                stdout_parts: list = []
                stderr_parts: list = []
                ran: list = []
                last_code = 0
                unhandled_failure = False
                spawned = False
                ran_cwd = cwd

                for index, (step, step_cwd) in enumerate(zip(steps, step_cwds)):
                    if not _connector_runs(step.connector, last_code):
                        continue
                    # `||` is the line saying it expects the failure before it;
                    # anything else leaves it standing, so `pytest -q; ls`
                    # cannot report a failing suite as a passing check.
                    if step.connector == "||":
                        unhandled_failure = False
                    if step.is_cd:
                        # Its whole effect is the directory the walk above
                        # already applied to the steps that follow it.
                        last_code = 0
                        ran.append({"command": step.text, "return_code": 0})
                        # Only a cd that actually ran moves the session.
                        ran_cwd = (
                            step_cwds[index + 1]
                            if index + 1 < len(step_cwds)
                            else walk_cwd
                        )
                        continue
                    try:
                        result = _run_step(
                            step,
                            step_cwd,
                            max(deadline - time.monotonic(), 0),
                            granted,
                        )
                    except subprocess.TimeoutExpired as exc:
                        stdout_parts.append(_as_text(exc.stdout))
                        stderr_parts.append(_as_text(exc.stderr))
                        return attach_check(
                            {
                                "status": "error",
                                "error": f"Command timed out after {timeout} seconds",
                                "command": command,
                                "stdout": "".join(stdout_parts),
                                "stderr": "".join(stderr_parts),
                                "has_errors": True,
                                "timed_out": True,
                                "timeout": timeout,
                                "duration_seconds": time.monotonic() - start_time,
                                "cwd": cwd,
                                "steps": ran,
                            },
                            check_from_command(
                                command,
                                [seg for st in steps for seg in st.segments],
                                None,
                                "".join(stdout_parts),
                                "".join(stderr_parts),
                            ),
                        )
                    except FileNotFoundError as exc:
                        # Mid-line, the outer handler's "nothing ran" answer
                        # would disown the commands that did; report it the way
                        # a shell does and let the connectors decide the rest.
                        if not spawned:
                            raise
                        logger.error("Command executable not found: %s", exc)
                        stderr_parts.append(f"{step.text}: {exc.strerror or exc}\n")
                        last_code = _COMMAND_NOT_FOUND
                        unhandled_failure = True
                        ran.append({"command": step.text, "return_code": last_code})
                        continue

                    spawned = True
                    stdout_parts.append(result.stdout or "")
                    stderr_parts.append(result.stderr or "")
                    last_code = result.returncode
                    unhandled_failure = unhandled_failure or last_code != 0
                    ran.append({"command": step.text, "return_code": last_code})

                duration = time.monotonic() - start_time

                # Checkpoint where the cd's that actually ran left off, so the
                # NEXT separate call starts there. The pre-flight walk applies
                # every cd on the line unconditionally, so it is not the answer
                # here: a cd that `&&`/`||` skipped, or that a timeout cut the
                # line before, never happened and must not move the session.
                # A one-shot working_directory never touches it either way.
                if session_scoped:
                    self.shell_session.set_cwd(ran_cwd)

                # One line is one model step, so it costs one slot however many
                # commands it chains.
                self._record_command_execution()

                stdout = "".join(stdout_parts)
                stderr = "".join(stderr_parts)
                max_output = 10_000

                from gaia.agents.base.artifacts import retain_excerpt

                truncated = len(stdout) > max_output or len(stderr) > max_output
                stdout = retain_excerpt(self, stdout, max_output)
                stderr = retain_excerpt(self, stderr, max_output)

                # Debug logging
                if hasattr(self, "debug") and self.debug:
                    logger.info(
                        f"Command completed in {duration:.2f}s with return code {last_code}"
                    )

                outcome = {
                    "status": "success",
                    "command": command,
                    "stdout": stdout,
                    "stderr": stderr,
                    "return_code": last_code,
                    "has_errors": last_code != 0 or unhandled_failure,
                    "duration_seconds": duration,
                    "timeout": timeout,
                    "cwd": cwd,
                    "output_truncated": truncated,
                    "steps": ran,
                }
                if waited:
                    outcome["waited_seconds"] = round(waited, 1)
                check = check_from_command(
                    command,
                    [seg for st in steps for seg in st.segments],
                    last_code,
                    "".join(stdout_parts),
                    "".join(stderr_parts),
                )
                return attach_check(outcome, check)

            except FileNotFoundError as exc:
                # The executable is not there, so nothing started. Said out loud
                # or the footer reports a missing pytest as a failing one.
                logger.error(f"Command executable not found: {exc}")
                return {
                    **NOT_EXECUTED,
                    "status": "error",
                    "error": str(exc),
                    "has_errors": True,
                }
            except Exception as exc:
                logger.error(f"Error executing shell command: {exc}")
                return {"status": "error", "error": str(exc), "has_errors": True}

        @tool(
            atomic=True,
            display_label="Shell state",
        )
        def get_shell_state() -> Dict[str, Any]:
            """Report the shell session's current working directory.

            Shell commands share one session, so a `cd` from an earlier command
            (when it was not a one-shot `working_directory` override) is still
            in effect. Call this to read that directory instead of guessing it.
            """
            return {
                "status": "success",
                "cwd": self.shell_session.cwd,
                "has_errors": False,
            }

        @tool(
            atomic=True,
            display_label="Reset shell",
        )
        def reset_shell_session() -> Dict[str, Any]:
            """Return the shell session to the directory it started in.

            Use this when the session is in the wrong directory, or as a clean
            slate at the start of an unrelated task.
            """
            session = self.reset_shell_session()
            return {
                "status": "success",
                "message": "Shell session reset.",
                "cwd": session.cwd,
                "has_errors": False,
            }
