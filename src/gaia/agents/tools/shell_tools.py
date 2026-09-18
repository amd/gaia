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
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, Optional

from gaia.agents.base.verification import NOT_EXECUTED

logger = logging.getLogger(__name__)

# Commands that run WITHOUT ASKING — not the set of commands that exist.
#
# Membership here buys one thing: no confirmation prompt. A command that is not
# on this list is not refused; it is shown to the user, who approves it or does
# not (``TIER_CONFIRM``). That distinction is the whole permission model — an
# allowlist used as a refusal list makes the agent unable to do ordinary work
# its user is sitting right there to approve.
#
# So the bar for adding an entry is "safe to run unattended, every time, with
# arguments nobody reviewed", which in practice still means read-only.
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

# How a blocked command is blocked. Mirrors the three tiers in
# ``gaia.skills.binaries`` (ALLOW / CONFIRM / REFUSE), spelled locally so this
# module keeps its light import.
#
# REFUSE is for escalations a single yes/no prompt cannot honestly describe:
# base64-encoded PowerShell, a git option that runs a script behind a
# ``status``-looking subcommand, a granted CLI's credential-printing action.
# Asking about those trains a user to click yes on something the prompt text
# misrepresents.
#
# CONFIRM is everything else that is not a read: `git commit`, `npm test`,
# `rm file`. The user is shown the exact command and answers y / n / always.
# These MUST NOT be refused here — refusing a command that would run on
# approval is the dead end this tier exists to remove.
TIER_CONFIRM = "confirm"
TIER_REFUSE = "refuse"


def _blocked(tier: str, error: str, **extra) -> dict:
    """One blocked-command result, tagged with the tier that decided it."""
    return {
        "status": "error",
        "error": error,
        "has_errors": True,
        "tier": tier,
        **extra,
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
        return None, (
            f"Git global option '{name}' is not recognized, so the subcommand "
            "behind it cannot be identified."
        )

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

# Shell operators that could be used for command chaining or redirection
# Pipe (|) is allowed but validated separately
# SECURITY: Block command chaining and redirection operators.
# - && and & are command separators (Windows cmd.exe / bash)
# - > >> are output redirection, < is input redirection
# - || is OR chaining, ; is command separator
# - ` and $() are command substitution
# Note: bare & is matched as word-boundary to avoid false positives
# inside quoted PowerShell strings (e.g. @{N='...'}).
DANGEROUS_SHELL_OPERATORS = re.compile(
    r"(?:&&|&(?=\s|$)|>>|>(?:[^&>]|$)|<(?:[^<]|$)|\|\||;|`|\$\()"
)

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


def _is_granted_binary(token: str, granted: frozenset) -> bool:
    """True when *token* names a CLI this agent's skills granted."""
    if not granted:
        return False
    from gaia.skills.binaries import normalize_binary

    return normalize_binary(token) in granted


def _operator_check_text(command: str) -> str:
    """The part of *command* the operator blocklist applies to.

    A PowerShell ``-Command`` body is script, not outer shell, and is validated
    separately by ``_validate_command`` (DANGEROUS_PS_PATTERNS + the cmdlet
    prefix allowlist). Scanning it here would refuse legitimate cmdlets.
    """
    if not command.strip().lower().startswith(("powershell ", "powershell.exe ")):
        return command
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


#: Tokens that end one command and begin the next. Only ``|`` can appear
#: outside full access — ``DANGEROUS_SHELL_OPERATORS`` refuses the rest before
#: tokenisation — so extending the set leaves default behaviour untouched.
_SEGMENT_SEPARATORS = frozenset({"|", "||", "&&", ";", "&"})

#: Characters shlex is asked to lex as punctuation under full access. A newline is
#: one of them because the shell that ultimately runs the string treats it as a
#: command separator, so the segment walk has to as well.
_FULL_ACCESS_PUNCTUATION = ";&|<>\n"


def _heredoc_start(line: str) -> Optional[tuple]:
    """``(delimiter, strip_tabs)`` for the heredoc *line* opens, else None.

    Tokenised with the same lexer the gates use, so a ``<<`` inside quotes is
    data and ``<<<`` (a here-string, no body) never opens one.
    """
    try:
        tokens = _tokenize(line, full_access=True)
    except ValueError:
        return None
    for index, token in enumerate(tokens):
        if token not in ("<<", "<<-") or index + 1 >= len(tokens):
            continue
        word = tokens[index + 1]
        strip_tabs = token == "<<-"
        if word == "-" and index + 2 < len(tokens):
            word, strip_tabs = tokens[index + 2], True
        elif word.startswith("-") and len(word) > 1:
            word, strip_tabs = word[1:], True
        if word and all(ch not in _FULL_ACCESS_PUNCTUATION for ch in word):
            return word, strip_tabs
    return None


def _strip_heredoc_bodies(command: str) -> str:
    """*command* with every heredoc body removed.

    A body is input to the command that opened it, not a command, so the gates
    must not walk ``print(1+1)`` as a binary or choke on an apostrophe in it.
    Only the text that reaches the shell as commands is returned; the shell
    still runs the original. An unterminated body is left in place, so the
    gates see it and refuse what they cannot parse.
    """
    if "<<" not in command:
        return command
    kept: list = []
    lines = command.split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        kept.append(line)
        index += 1
        opened = _heredoc_start(line)
        if opened is None:
            continue
        word, strip_tabs = opened
        end = index
        while end < len(lines):
            body_line = lines[end].lstrip("\t") if strip_tabs else lines[end]
            if body_line == word:
                break
            end += 1
        if end == len(lines):
            kept.extend(lines[index:])
            break
        index = end + 1
    return "\n".join(kept)


def _tokenize(command: str, full_access: bool = False) -> list:
    """Split *command* into argv tokens.

    Default mode uses ``shlex.split``, unchanged. Full access asks shlex to
    treat ``;&|<>`` and the newline as punctuation instead, so ``cd build &&
    make`` yields a standalone ``&&`` for ``_split_pipeline`` to break on.
    Parentheses stay out of the punctuation set: making them tokens would
    mangle ordinary operands like ``find . -name "(draft)*"``.

    The newline is dropped from ``whitespace`` so it survives as a token rather
    than being eaten as a space. Inside quotes it is still ordinary data, so
    ``echo "a<newline>b"`` remains one operand.
    """
    if not full_access:
        return shlex.split(command)
    lexer = shlex.shlex(command, posix=True, punctuation_chars=_FULL_ACCESS_PUNCTUATION)
    lexer.whitespace_split = True
    lexer.commenters = ""  # same as shlex.split: '#' is data, not a comment
    lexer.whitespace = " \t\r"
    return list(lexer)


def _is_segment_separator(token: str) -> bool:
    """Whether *token* ends one command and begins the next.

    shlex emits a *run* of adjacent punctuation as a single token, so a newline
    reaches here fused to whatever preceded it — ``;\\n``, ``&&\\n``, ``\\n|\\n``.
    Any all-punctuation run containing a newline therefore separates, which is
    what stops ``ls\\nrm -rf /tmp/x`` from being walked as one ``ls`` segment.
    """
    if token in _SEGMENT_SEPARATORS:
        return True
    return (
        bool(token)
        and "\n" in token
        and all(char in _FULL_ACCESS_PUNCTUATION for char in token)
    )


def _split_pipeline(cmd_parts: list) -> list:
    """Split tokenised *cmd_parts* on shell separators into non-empty segments.

    Every segment is validated and audited on its own, which is what keeps a
    refused binary in ``a && b`` from riding in on an allowed one — and what
    keeps the audit record honest under full access, where the separators
    actually reach a shell.
    """
    segments: list = []
    current: list = []
    for part in cmd_parts:
        if _is_segment_separator(part):
            if current:
                segments.append(current)
            current = []
        else:
            current.append(part)
    if current:
        segments.append(current)
    return segments


class ShellToolsMixin:
    """
    Mixin providing shell command execution tools with rate limiting.

    Tools provided:
    - run_shell_command: Execute terminal commands with timeout and safety checks

    Rate Limiting (lifted under full access):
    - Max 10 commands per minute to prevent DOS
    - Max 3 commands per 10 seconds for burst prevention
    """

    def __init__(self, *args, **kwargs):
        """Initialize shell tools with rate limiting."""
        super().__init__(*args, **kwargs)

        # Rate limiting configuration
        self.shell_command_times = deque(maxlen=100)  # Track last 100 command times
        self.max_commands_per_minute = 10
        self.max_commands_per_10_seconds = 3

    def _validate_shell_command(self, command: str) -> tuple:
        """Every refusal ``command`` earns on its text alone, plus its segments.

        Each refusal is stamped ``executed: False`` — nothing here has launched
        anything, and downstream cannot tell a refused command from a failed one
        by the shape of the error alone (#3677).
        """
        error, segments = self._shell_command_refusal(command)
        if error is not None:
            error = {**error, **NOT_EXECUTED}
        return error, segments

    def _shell_command_refusal(self, command: str) -> tuple:
        """Every refusal ``command`` earns on its text alone, plus its segments.

        Pure and side-effect free, so it can run twice: once as a pre-flight
        before the confirmation prompt, once on the real execution path. Sharing
        one implementation is what keeps those two from ever disagreeing.

        Returns:
            ``(error, segments)`` — ``error`` is None when nothing here refuses
            the command. Refusals that need runtime context (rate limit, working
            directory, path traversal) stay with the caller, so a command this
            clears may still be refused later; one it rejects never runs.
        """
        full_access = self.full_access_active()

        if not full_access and DANGEROUS_SHELL_OPERATORS.search(
            _operator_check_text(command)
        ):
            return (
                {
                    "status": "error",
                    "tier": TIER_REFUSE,
                    "error": "Shell operators (&, >, >>, <, &&, ||, ;, `, $()) are not allowed for security reasons.",
                    "has_errors": True,
                    "hint": "Pipe (|) is allowed. Use individual commands for other operations.",
                },
                [],
            )

        try:
            cmd_parts = _tokenize(
                _strip_heredoc_bodies(command) if full_access else command,
                full_access=full_access,
            )
        except ValueError as exc:
            return (
                {
                    "status": "error",
                    "tier": TIER_REFUSE,
                    "error": f"Invalid command syntax: {exc}",
                    "has_errors": True,
                },
                [],
            )

        segments = _split_pipeline(cmd_parts)
        if not segments:
            return (
                {
                    "status": "error",
                    "tier": TIER_REFUSE,
                    "error": "Empty command",
                    "has_errors": True,
                },
                [],
            )

        granted = skill_granted_binaries(self)
        for segment in segments:
            error = self._validate_command(
                segment[0].lower(),
                segment,
                command if len(segments) == 1 else " ".join(segment),
                granted_binaries=granted,
                full_access=full_access,
            )
            if error:
                return error, segments

        return None, segments

    def full_access_active(self) -> bool:
        """Whether this session is running under full access (#3373).

        One source of truth, read live: the session's output handler carries
        ``full_access``, set only by the sidecar's ``PermissionState``
        from ``--full-access`` or the TUI's ``/full-access``. Every gate reads
        this — ``_validate_shell_command``, ``skill_grant_covers_call``, the
        tool description, the executor — so they cannot hold different opinions
        about whether a command is legal.

        Read live rather than resolved once because full access is toggleable
        mid-session over the control channel; caching it would leave `/full-access
        off` half-applied. Each ``run_shell_command`` reads it once and threads
        that value through its own pre-flight and execution, so a toggle landing
        mid-call cannot split the two.

        Answers False for any host that never set it — a plain console, the
        HTTP transport, a library embedding — which is what keeps the shipped
        default byte-identical.
        """
        return bool(getattr(getattr(self, "console", None), "full_access", False))

    def policy_refusal_for_call(
        self, tool_name: str, tool_args: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """The refusal this call has already earned, before anyone is asked.

        Read by ``Agent._policy_refusal``, which runs *before* the confirmation
        prompt. ``TIER_REFUSE`` always stops here: an escalation a yes/no cannot
        honestly describe (``gh auth token``, base64 PowerShell, ``git -c``
        behind ``status``), or a shape the runner cannot execute at all.

        ``TIER_CONFIRM`` (``git commit``, ``npm test``, ``rm notes.txt``) falls
        through to the prompt: refusing a command that would run on approval is
        the dead end this tier removes. The exception is a run where only
        ``GAIA_AUTO_APPROVE_TOOLS`` would approve it; see
        :meth:`_approval_is_environment_only`.

        Duck-typed rather than an override: ``Agent`` precedes this mixin in
        ``ChatAgent``'s MRO, so a same-named method here would never be reached.
        """
        if tool_name != _POLICY_GATED_SHELL_TOOL:
            return None
        command = (tool_args or {}).get("command")
        if not isinstance(command, str):
            return None
        error, _ = self._validate_shell_command(command)
        if error is None:
            return None
        if error.get("tier") != TIER_REFUSE:
            if not self._approval_is_environment_only():
                return None
            error = self._environment_only_refusal(error)
        logger.info(
            "Refusing %r before the confirmation prompt: %s",
            command,
            error.get("error"),
        )
        return error

    def _approval_is_environment_only(self) -> bool:
        """True when nothing but ``GAIA_AUTO_APPROVE_TOOLS`` would approve a prompt.

        A host that opted in explicitly (the TUI's full access sets
        ``auto_approve_gated_tools`` on the turn's handler) is a person deciding
        for this session. The environment variable is a blanket pre-approval for
        unattended runs, granted when commands outside the no-prompt list could
        not be approved at all; it never agreed to widen what those runs execute.
        """
        console = getattr(self, "console", None)
        if bool(getattr(console, "auto_approve_gated_tools", False)):
            return False
        if bool(getattr(console, "full_access", False)):
            return False
        # Deferred: the console module imports the package root.
        from gaia.agents.base import console as console_mod

        return console_mod.auto_approve_env_enabled()

    @staticmethod
    def _environment_only_refusal(error: Dict[str, Any]) -> Dict[str, Any]:
        """A confirmable command's block, re-explained for an unattended run."""
        return {
            **error,
            "tier": TIER_REFUSE,
            "hint": (
                "This run approves prompts through GAIA_AUTO_APPROVE_TOOLS, which "
                "does not extend to commands outside the no-prompt list. Run it "
                "interactively to approve it, or turn on full access in the TUI."
            ),
        }

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

        Deliberately narrow. It answers False unless **every** segment of the
        command is a granted binary running a read-only subcommand, so
        ``gh issue list | head`` still prompts even though ``head`` is
        whitelisted: consent was given for ``gh``, not for a pipeline.

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

        full_access = self.full_access_active()
        if full_access:
            # Every gated tool is pre-approved under full access; saying so here
            # keeps this answer aligned with the console's, rather than leaving
            # two predicates to disagree about whether the call was consented to.
            return True

        granted = skill_granted_binaries(self)
        if not granted:
            return False

        command = (tool_args or {}).get("command")
        if not isinstance(command, str) or DANGEROUS_SHELL_OPERATORS.search(command):
            return False

        try:
            segments = _split_pipeline(_tokenize(command))
        except ValueError:
            return False
        if not segments:
            return False

        from gaia.skills.binaries import (
            BINARY_POLICIES,
            normalize_binary,
            validate_invocation,
        )

        for segment in segments:
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

    @staticmethod
    def _validate_command(
        cmd_base: str,
        cmd_parts: list,
        command: str,
        granted_binaries: frozenset = frozenset(),
        full_access: bool = False,
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
            full_access: This session's ``--full-access`` state. When True
                every confirmation is already answered yes, so only a
                ``TIER_REFUSE`` result — something no approval can authorize —
                is returned; the default path below is unchanged.

        Returns None if the command is allowed, or an error dict if blocked.

        A skill-granted CLI's *confirmable write* returns None here too. This
        method answers "is this refused?", not "may it run unasked" — the
        second question belongs to ``skill_grant_covers_call``, which is what
        decides whether the confirmation prompt is skipped. Answering yes here
        would refuse a write before anyone could approve it, which is the dead
        end this tier removes.
        """
        if full_access:
            error = ShellToolsMixin._validate_command(
                cmd_base,
                cmd_parts,
                command,
                granted_binaries=granted_binaries,
            )
            if error is not None and error.get("tier") == TIER_REFUSE:
                return error
            return None

        # Skill-granted CLIs are gated by their own policy table instead of
        # ALLOWED_COMMANDS; anything ungranted still needs confirmation.
        # Imported here — gaia.skills pulls in the connector stack.
        from gaia.skills.binaries import (
            BINARY_POLICIES,
            REFUSE,
            classify_invocation,
            normalize_binary,
        )

        binary = normalize_binary(cmd_base)
        policy = BINARY_POLICIES.get(binary)
        if policy is not None:
            # Classify BEFORE the grant check. A REFUSE-tier invocation
            # (`gh auth token`) is refused on what it does, not on who may run
            # it — gating first would let an ungranted one out to the
            # confirmation prompt, which is weaker than the grant path.
            decision = classify_invocation(policy, cmd_parts)
            if decision.outcome == REFUSE:
                return {
                    "status": "error",
                    "tier": TIER_REFUSE,
                    "error": decision.message,
                    "has_errors": True,
                    "hint": (
                        f"This one is refused outright, not gated — the '{binary}' "
                        "grant will not run it even with the user's approval. "
                        "Use an allowed command, or tell the user what you would "
                        "have run and why it is blocked."
                    ),
                }
            if binary not in granted_binaries:
                return {
                    "status": "error",
                    "tier": TIER_CONFIRM,
                    "error": (
                        f"Command '{binary}' is not available to this agent. It is "
                        "granted only to a skill that declares "
                        f"'shell:execute:{binary}' in its SKILL.md — load that skill "
                        "first."
                    ),
                    "has_errors": True,
                    "hint": f"{policy.summary} {policy.install_hint}",
                }
            return None

        # Special handling for git - only allow read-only operations
        if cmd_base == "git":
            if len(cmd_parts) > 1:
                git_subcmd, resolve_error = _resolve_git_subcommand(cmd_parts)
                if resolve_error is not None:
                    return {
                        "status": "error",
                        "tier": TIER_REFUSE,
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
                        "tier": TIER_CONFIRM,
                        "error": f"Git command '{git_subcmd}' is not allowed. Only read-only git operations are permitted.",
                        "has_errors": True,
                        "allowed_git_commands": sorted(SAFE_GIT_COMMANDS),
                    }
        # Special handling for wmic - only allow read-only queries
        elif cmd_base == "wmic":
            cmd_lower = command.lower()
            dangerous_wmic_ops = {"call", "create", "delete", "set"}
            cmd_words = set(cmd_lower.split())
            if cmd_words & dangerous_wmic_ops:
                return {
                    "status": "error",
                    "tier": TIER_CONFIRM,
                    "error": "Only read-only wmic queries are allowed (get, list). Modifying operations (call, create, delete, set) are blocked.",
                    "has_errors": True,
                    "hint": "Use 'wmic <alias> get <properties>' for safe queries",
                    "examples": "wmic cpu get name, wmic os get caption, wmic path win32_videocontroller get name",
                }
        # Special handling for powershell - only allow read-only cmdlets
        elif cmd_base in ("powershell", "powershell.exe"):
            # Block dangerous execution flags that can bypass cmdlet filtering
            _BLOCKED_PS_FLAGS = {
                "-encodedcommand",
                "-enc",
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
            if any(part.lower() in _BLOCKED_PS_FLAGS for part in cmd_parts[1:]):
                return {
                    "status": "error",
                    "tier": TIER_REFUSE,
                    "error": "PowerShell execution flags like -EncodedCommand, -File, and -ExecutionPolicy are not allowed.",
                    "has_errors": True,
                    "hint": "Use -Command to pass a readable cmdlet string",
                    "examples": 'powershell -Command "Get-WmiObject Win32_Processor | Select-Object Name"',
                }
            # Extract the PowerShell command text
            ps_cmd = ""
            for i, part in enumerate(cmd_parts):
                if part.lower() in ("-command", "-c"):
                    ps_cmd = " ".join(cmd_parts[i + 1 :]).lower()
                    break
            if not ps_cmd:
                # Inline: powershell "Get-Process"
                ps_cmd = " ".join(cmd_parts[1:]).lower()

            if any(pat in ps_cmd for pat in DANGEROUS_PS_PATTERNS):
                return {
                    "status": "error",
                    "tier": TIER_CONFIRM,
                    "error": "Only read-only PowerShell cmdlets are allowed (Get-*, Select-Object, Format-*, Where-Object, etc.).",
                    "has_errors": True,
                    "hint": "Use Get-* cmdlets for safe queries",
                    "examples": (
                        'powershell -Command "Get-WmiObject Win32_Processor | Select-Object Name", '
                        'powershell -Command "Get-CimInstance Win32_VideoController | Format-List Name,DriverVersion"'
                    ),
                }

            # Verify each cmdlet is safe
            cmdlets = re.findall(r"[a-z]+-[a-z]+", ps_cmd)
            for cmdlet in cmdlets:
                if not any(
                    cmdlet.startswith(prefix) for prefix in SAFE_PS_CMDLET_PREFIXES
                ):
                    return {
                        "status": "error",
                        "tier": TIER_CONFIRM,
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
                        "tier": TIER_CONFIRM,
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
                        "tier": TIER_CONFIRM,
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
                    "tier": TIER_CONFIRM,
                    "error": "uniq with an output file is not allowed: it writes to disk, violating the read-only command policy.",
                    "has_errors": True,
                    "hint": "Use a single input (or stdin) and read stdout, e.g. 'uniq file' or 'sort file | uniq'.",
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
            return {
                "status": "error",
                "tier": TIER_CONFIRM,
                "error": f"Command '{cmd_base}' is not in the allowed list for security reasons",
                "has_errors": True,
                "hint": (
                    "Commands outside the no-prompt list run once the user "
                    "approves them; they are not refused."
                ),
                "examples": "ls, cat, grep, find, git status, systeminfo, powershell -Command 'Get-WmiObject ...'",
            }

        return None  # Command is allowed

    def _audit_shell_execution(self, command: str, cwd: str, segments: list) -> None:
        """Record a command run under full access, arguments and all.

        Skipping the confirmation *prompt* must not mean skipping the *record*:
        consent was granted in advance, which is exactly when the audit trail is
        the only evidence of what the agent did.
        """
        from gaia.security import audit_shell_command

        audit_shell_command(
            command=command, cwd=cwd, segments=segments, mode="full_access"
        )

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
            Execute a shell command and return the output.

            Args:
                command: Shell command to execute
                working_directory: Directory to run command in
                timeout: Maximum execution time in seconds

            Returns:
                Dictionary with status, output, and error information
            """
            try:
                full_access = self.full_access_active()

                # Check rate limits first to prevent DOS
                allowed, reason, wait_time = (
                    (True, "", 0.0) if full_access else self._check_rate_limit()
                )
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

                    # Validate path is allowed
                    if hasattr(self, "path_validator"):
                        if not self.path_validator.is_path_allowed(working_directory):
                            return {
                                **NOT_EXECUTED,
                                "status": "error",
                                "error": f"Access denied: {working_directory} is not in allowed paths",
                                "has_errors": True,
                            }
                    elif hasattr(self, "_is_path_allowed"):
                        if not self._is_path_allowed(working_directory):
                            return {
                                **NOT_EXECUTED,
                                "status": "error",
                                "error": f"Access denied: {working_directory} is not in allowed paths",
                                "has_errors": True,
                            }

                    cwd = str(Path(working_directory).resolve())
                else:
                    cwd = str(Path.cwd())

                # Operators, syntax, and the per-command whitelist. Shares one
                # implementation with the pre-flight that runs before the
                # confirmation prompt, so the two can never disagree.
                #
                # A CONFIRM-tier command has already been through
                # ``Agent._execute_tool``'s gate (the same single funnel that has
                # always gated ``write_file``), so it runs here unless the only
                # approval was the environment opt-in.
                env_only = self._approval_is_environment_only()
                error, segments = self._validate_shell_command(command)
                if error and error.get("tier") != TIER_REFUSE and env_only:
                    return self._environment_only_refusal(error)
                if error and error.get("tier") == TIER_REFUSE:
                    return error

                granted = skill_granted_binaries(self)
                cmd_parts = [part for segment in segments for part in segment]

                # Validate arguments for path traversal
                # This prevents "cat ../secret.txt" even if "cat" is allowed.
                # Exempt per SEGMENT, never per line: a granted CLI's operands are
                # remote ids, but 'gh … | cat ../secret' must still be checked.
                scanned = [
                    seg for seg in segments if not _is_granted_binary(seg[0], granted)
                ]
                if hasattr(self, "path_validator"):
                    for arg in [a for seg in scanned for a in seg[1:]]:
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
                                clean_path = candidate_path
                                resolved_path = str(
                                    Path(cwd).joinpath(clean_path).resolve()
                                )

                                if not self.path_validator.is_path_allowed(
                                    resolved_path
                                ):
                                    return {
                                        **NOT_EXECUTED,
                                        "status": "error",
                                        "error": f"Access denied: Argument '{arg}' resolves to forbidden path '{resolved_path}'",
                                        "has_errors": True,
                                    }
                            except (OSError, ValueError) as exc:
                                # Unresolvable is not a verdict — say so rather
                                # than letting the argument through unnoticed.
                                logger.warning(
                                    "Could not resolve '%s' against the allowed "
                                    "paths (%s); it was not path-checked.",
                                    arg,
                                    exc,
                                )

                cmd_base = cmd_parts[0].lower()

                # Validate every command in the pipeline, not just the first.
                # The walk stays intact under full access: it is what produces the
                # per-segment audit record below.
                for seg in segments:
                    error = self._validate_command(
                        seg[0].lower(),
                        seg,
                        command if len(segments) == 1 else " ".join(seg),
                        granted_binaries=granted,
                        full_access=full_access,
                    )
                    if error and (error.get("tier") == TIER_REFUSE or env_only):
                        return error

                if full_access:
                    self._audit_shell_execution(command, cwd, segments)

                # Log command execution (debug mode)
                if hasattr(self, "debug") and self.debug:
                    logger.info(f"Executing command: {command} in {cwd}")

                # On Windows, many commands are shell built-ins (dir, cd, type,
                # echo) and Unix commands (ls, pwd, cat) don't exist as .exe
                # files.  Since we have already validated the command against the
                # whitelist, we use shell=True on Windows so cmd.exe can resolve
                # both built-ins and commands on PATH (including those from Git
                # for Windows which provides ls, cat, grep, etc.).
                #
                # A skill-granted CLI is the exception, and must stay one. It is
                # a real executable — it needs no built-in resolution — and it
                # is the one path that can run without a confirmation prompt, on
                # arguments built from untrusted remote text (an issue body the
                # model just read). Handing cmd.exe the raw STRING there would
                # let that text act: `--search "x|whoami"` is one argv token to
                # every check above and two commands to cmd.exe, and `%VAR%`
                # expands into a value the approval prompt never showed. argv
                # goes to the process verbatim, so neither is possible.
                # One segment only: a pipeline needs a shell to be a pipeline,
                # and `cmd_parts` has already dropped the `|` tokens, so an argv
                # run of one would silently concatenate the two commands.
                #
                # Full access needs a shell everywhere, not just on Windows: the
                # operators it unblocks ARE the shell. argv-only execution would
                # drop the separators `_split_pipeline` stripped and silently
                # concatenate `a && b` into one bogus command line.
                lone_granted_segment = (
                    len(segments) == 1
                    and bool(granted)
                    and _is_granted_binary(segments[0][0], granted)
                )
                use_shell = (
                    os.name == "nt" or full_access
                ) and not lone_granted_segment

                # Build the command string for execution
                # On Windows with shell=True, use the ORIGINAL command string
                # to preserve quoting (critical for PowerShell pipe commands)
                exec_cmd = cmd_parts  # Default: list for subprocess

                if use_shell:
                    # Start with original command to preserve quoting
                    exec_cmd = command

                    # Map common Unix commands to Windows equivalents
                    # when Git-for-Windows tools aren't on PATH
                    _UNIX_TO_WIN = {
                        "ls": "dir",
                        "pwd": "cd",
                        "cat": "type",
                        "which": "where",
                        "cp": "copy",
                        "mv": "move",
                    }
                    if os.name == "nt" and cmd_base in _UNIX_TO_WIN:
                        import shutil

                        if not shutil.which(cmd_base):
                            win_cmd = _UNIX_TO_WIN[cmd_base]
                            logger.info(
                                f"Mapping Unix command '{cmd_base}' -> Windows '{win_cmd}'"
                            )
                            # Replace just the command name in the original string
                            exec_cmd = win_cmd + exec_cmd[len(cmd_base) :]

                # Execute command
                #
                # encoding/errors are explicit, and load-bearing. Bare
                # ``text=True`` decodes with the locale codec — cp1252 on a
                # default Windows box — and subprocess does that decode inside
                # its pipe reader THREAD. A byte that codec cannot map raises
                # UnicodeDecodeError in that thread, which dies, and
                # subprocess.run then returns returncode 0 with EMPTY stdout.
                # The command succeeded and its output was silently discarded.
                #
                # That is not an edge case: `gh issue list` on amd/gaia returns
                # an issue title containing "⚠️", so GitHub triage got back
                # nothing and the model reported an empty backlog it had never
                # actually read. Any tool emitting UTF-8 (git, gh, npm, docker)
                # hits it. errors="replace" keeps a stray undecodable byte from
                # costing the whole output.
                start_time = time.monotonic()
                try:
                    result = subprocess.run(
                        exec_cmd,
                        cwd=cwd,
                        capture_output=True,
                        # stdin is DEVNULL, never inherited. capture_output
                        # redirects stdout/stderr but leaves stdin alone, and
                        # this process's stdin is the agent transport's pipe —
                        # held open by the TUI and never written to. A child
                        # that reads it (directly, or by probing whether it is
                        # interactive) blocks forever on input that cannot
                        # arrive, because there is no human on that pipe.
                        #
                        # The hang was not theoretical: `gh` spawned from the
                        # agent never exited, while the identical command took
                        # 0.07s from a shell. Worse, subprocess.run's own
                        # timeout does not save it — on expiry it kills the
                        # cmd.exe it launched, then calls communicate() again
                        # with NO timeout, which waits on pipes the surviving
                        # grandchild still holds. That is the 180s tool timeout
                        # and the orphaned gh.exe left behind by every attempt.
                        #
                        # DEVNULL gives an immediate EOF, which is the honest
                        # answer here: an agent's shell command is
                        # non-interactive by construction.
                        stdin=subprocess.DEVNULL,
                        encoding="utf-8",
                        errors="replace",
                        timeout=timeout,
                        check=False,
                        env=os.environ.copy(),
                        shell=use_shell,  # nosec B602 - Windows-only; command whitelist-validated above, shell needed for cmd.exe built-ins/pipes
                    )
                    duration = time.monotonic() - start_time

                    # Record successful command execution for rate limiting.
                    # Skipped under full access: the limit is lifted, and the deque
                    # is created by _check_rate_limit, which never ran.
                    if not full_access:
                        self._record_command_execution()
                except subprocess.TimeoutExpired as exc:
                    duration = time.monotonic() - start_time

                    # Handle timeout gracefully
                    stdout_str = ""
                    stderr_str = ""
                    if exc.stdout:
                        stdout_str = (
                            exc.stdout
                            if isinstance(exc.stdout, str)
                            else exc.stdout.decode("utf-8", errors="replace")
                        )
                    if exc.stderr:
                        stderr_str = (
                            exc.stderr
                            if isinstance(exc.stderr, str)
                            else exc.stderr.decode("utf-8", errors="replace")
                        )

                    return {
                        "status": "error",
                        "error": f"Command timed out after {timeout} seconds",
                        "command": command,
                        "stdout": stdout_str,
                        "stderr": stderr_str,
                        "has_errors": True,
                        "timed_out": True,
                        "timeout": timeout,
                        "duration_seconds": duration,
                        "cwd": cwd,
                    }

                # Capture and truncate output if too long
                stdout = result.stdout or ""
                stderr = result.stderr or ""
                truncated = False
                max_output = 10_000

                if len(stdout) > max_output:
                    stdout = stdout[:max_output] + "\n...output truncated (stdout)..."
                    truncated = True

                if len(stderr) > max_output:
                    stderr = stderr[:max_output] + "\n...output truncated (stderr)..."
                    truncated = True

                # Debug logging
                if hasattr(self, "debug") and self.debug:
                    logger.info(
                        f"Command completed in {duration:.2f}s with return code {result.returncode}"
                    )

                return {
                    "status": "success",
                    "command": command,
                    "stdout": stdout,
                    "stderr": stderr,
                    "return_code": result.returncode,
                    "has_errors": result.returncode != 0,
                    "duration_seconds": duration,
                    "timeout": timeout,
                    "cwd": cwd,
                    "output_truncated": truncated,
                }

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
