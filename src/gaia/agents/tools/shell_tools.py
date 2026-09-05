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
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, Optional

from gaia.agents.tools.command_timeouts import MAX_COMMAND_TIMEOUT, resolve_timeout

logger = logging.getLogger(__name__)

#: ``wait_for_condition`` bounds. The maximum wait is a ceiling, not a default:
#: a wait that could run unbounded is the spinning the primitive replaces.
WAIT_DEFAULT_TIMEOUT = 120
WAIT_MAX_TIMEOUT = 600
WAIT_DEFAULT_POLL_INTERVAL = 5
#: Poll floor. At 5s the probe rate stays near the 10-commands-per-minute shell
#: rate limit the wait is exempt from (it counts as one command, not one per poll).
WAIT_MIN_POLL_INTERVAL = 5
WAIT_MAX_POLL_INTERVAL = 60
#: A predicate is a quick check, not the work. Each probe is capped here, and by
#: whatever is left of the deadline.
WAIT_PROBE_TIMEOUT = 30

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


#: The one tool whose executor enforces the read-only binary policy, and so the
#: only one a ``shell:execute`` grant may exempt from confirmation.
_POLICY_GATED_SHELL_TOOL = "run_shell_command"

#: Runs its predicate through ``run_shell_command``, so the same guardrails
#: apply and a refused predicate is refused before anyone is prompted. It is
#: NOT grant-exempt: consent for a binary is not consent to poll with it.
_WAIT_TOOL = "wait_for_condition"


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


class ShellToolsMixin:
    """
    Mixin providing shell command execution tools with rate limiting.

    Tools provided:
    - run_shell_command: Execute terminal commands with timeout and safety checks
    - wait_for_condition: Poll a command until it succeeds, or hit a deadline

    Rate Limiting:
    - Max 10 commands per minute to prevent DOS
    - Max 3 commands per 10 seconds for burst prevention
    - A wait_for_condition call is one command; its probes are not metered again
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

        Pure and side-effect free, so it can run twice: once as a pre-flight
        before the confirmation prompt, once on the real execution path. Sharing
        one implementation is what keeps those two from ever disagreeing.

        Returns:
            ``(error, segments)`` — ``error`` is None when nothing here refuses
            the command. Refusals that need runtime context (rate limit, working
            directory, path traversal) stay with the caller, so a command this
            clears may still be refused later; one it rejects never runs.
        """
        if DANGEROUS_SHELL_OPERATORS.search(_operator_check_text(command)):
            return (
                {
                    "status": "error",
                    "error": "Shell operators (&, >, >>, <, &&, ||, ;, `, $()) are not allowed for security reasons.",
                    "has_errors": True,
                    "hint": "Pipe (|) is allowed. Use individual commands for other operations.",
                },
                [],
            )

        try:
            cmd_parts = shlex.split(command)
        except ValueError as exc:
            return (
                {
                    "status": "error",
                    "error": f"Invalid command syntax: {exc}",
                    "has_errors": True,
                },
                [],
            )

        segments = _split_pipeline(cmd_parts)
        if not segments:
            return (
                {"status": "error", "error": "Empty command", "has_errors": True},
                [],
            )

        granted = skill_granted_binaries(self)
        for segment in segments:
            error = self._validate_command(
                segment[0].lower(),
                segment,
                command if len(segments) == 1 else " ".join(segment),
                granted_binaries=granted,
            )
            if error:
                return error, segments

        return None, segments

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
        if tool_name not in (_POLICY_GATED_SHELL_TOOL, _WAIT_TOOL):
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

        granted = skill_granted_binaries(self)
        if not granted:
            return False

        command = (tool_args or {}).get("command")
        if not isinstance(command, str) or DANGEROUS_SHELL_OPERATORS.search(command):
            return False

        try:
            segments = _split_pipeline(shlex.split(command))
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

        # A wait's probes are one command's worth of budget, charged once when
        # the wait starts. Metering each poll separately would trip the burst
        # limit on the second probe and turn the primitive into an error.
        if getattr(self, "_shell_polling", False):
            return True, "", 0.0

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

    def _wait_interrupt_signal(self) -> threading.Event:
        """The event ``wait_for_condition`` sleeps on between probes.

        Waiting on the agent's cancel signal rather than sleeping blind is what
        makes Stop take effect during a wait; without it the user's click is
        honoured only once the deadline runs out, up to ten minutes later.
        Falls back to a never-set event for consumers that have no cancel
        channel (plain CLI runs), where the deadline is the only exit.
        """
        for source in (
            getattr(self, "_cancel_event", None),
            getattr(getattr(self, "console", None), "cancelled", None),
        ):
            if isinstance(source, threading.Event):
                return source
        return threading.Event()

    def _record_command_execution(self):
        """Record command execution timestamp for rate limiting."""
        if getattr(self, "_shell_polling", False):
            return  # already charged once, when the wait started
        self.shell_command_times.append(time.time())

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
        )

        binary = normalize_binary(cmd_base)
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
            decision = classify_invocation(policy, cmd_parts)
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
                git_subcmd = cmd_parts[1].lower()
                if git_subcmd not in SAFE_GIT_COMMANDS:
                    return {
                        "status": "error",
                        "error": f"Git command '{git_subcmd}' is not allowed. Only read-only git operations are permitted.",
                        "has_errors": True,
                        "allowed_git_commands": list(SAFE_GIT_COMMANDS),
                    }
        # Special handling for wmic - only allow read-only queries
        elif cmd_base == "wmic":
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
        elif cmd_base not in ALLOWED_COMMANDS:
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
            name="run_shell_command",
            # The agent-level guard must outlast the longest command class, or a
            # build would be abandoned by the loop while the subprocess is still
            # inside its own (correct) timeout.
            timeout=MAX_COMMAND_TIMEOUT + 60,
            description=(
                "Execute a shell/terminal command. Useful for listing directories (ls/dir), "
                "checking files (cat, stat), finding files (find), text processing (grep, head, tail), "
                "navigation (pwd), and system information. "
                'On Windows use: systeminfo, powershell -Command "Get-WmiObject Win32_Processor", '
                'powershell -Command "Get-CimInstance Win32_VideoController | Format-List Name,DriverVersion,AdapterRAM". '
                "On Linux use: lscpu, lspci, free -h. Pipes (|) are supported."
            ),
            parameters={
                "command": {
                    "type": "str",
                    "description": "The shell command to execute (e.g., 'ls -la', 'pwd', 'cat file.txt')",
                    "required": True,
                },
                "working_directory": {
                    "type": "str",
                    "description": "Directory to run the command in (defaults to current directory)",
                    "required": False,
                },
                "timeout": {
                    "type": "int",
                    "description": (
                        "Timeout in seconds. Omit it for the default that suits this "
                        f"kind of command. Maximum {MAX_COMMAND_TIMEOUT}."
                    ),
                    "required": False,
                },
            },
        )
        def run_shell_command(
            command: str,
            working_directory: Optional[str] = None,
            # Annotated int, not Optional[int]: the registry infers the JSON
            # schema type from this annotation and renders anything it cannot
            # read as a string, so Optional[int] would tell a tool-calling model
            # to send "60". None still means "use the class default".
            timeout: int = None,
        ) -> Dict[str, Any]:
            """Execute a shell command. Leave timeout unset: it defaults to what the command needs — 900s for test runners, 1800s for builds and installs, 300s for git/network calls, 30s for everything else.

            The class table leads because the prompt renders a tool by the FIRST
            LINE of its docstring; anything below is seen only by models using
            native tool calls. ``test_the_docstring_states_every_class`` keeps
            that line honest when the table changes.

            Args:
                command: Shell command to execute
                working_directory: Directory to run command in
                timeout: Maximum execution time in seconds. Omit it for the
                    class default above. Above the 3600s ceiling it is refused,
                    not clamped.

            Returns:
                Dictionary with status, output, and error information. The
                applied timeout and the class it came from are in ``timeout``
                and ``timeout_class``; a command killed at the limit carries
                ``timed_out`` plus whatever it printed first.
            """
            try:
                try:
                    timeout, timeout_class = resolve_timeout(command, timeout)
                except ValueError as exc:
                    return {
                        "status": "error",
                        "error": str(exc),
                        "command": command,
                        "has_errors": True,
                    }

                # Check rate limits first to prevent DOS
                allowed, reason, wait_time = self._check_rate_limit()
                if not allowed:
                    return {
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
                            "status": "error",
                            "error": f"Working directory not found: {working_directory}",
                            "has_errors": True,
                        }

                    if not os.path.isdir(working_directory):
                        return {
                            "status": "error",
                            "error": f"Path is not a directory: {working_directory}",
                            "has_errors": True,
                        }

                    # Validate path is allowed
                    if hasattr(self, "path_validator"):
                        if not self.path_validator.is_path_allowed(working_directory):
                            return {
                                "status": "error",
                                "error": f"Access denied: {working_directory} is not in allowed paths",
                                "has_errors": True,
                            }
                    elif hasattr(self, "_is_path_allowed"):
                        if not self._is_path_allowed(working_directory):
                            return {
                                "status": "error",
                                "error": f"Access denied: {working_directory} is not in allowed paths",
                                "has_errors": True,
                            }

                    cwd = str(Path(working_directory).resolve())
                else:
                    cwd = str(Path.cwd())

                # Operators, syntax, and the per-command whitelist. Shared with
                # the pre-flight that runs before the confirmation prompt, so a
                # command refused there is refused here for the same reason.
                error, segments = self._validate_shell_command(command)
                if error:
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
                for seg in segments:
                    error = self._validate_command(
                        seg[0].lower(),
                        seg,
                        command if len(segments) == 1 else " ".join(seg),
                        granted_binaries=granted,
                    )
                    if error:
                        return error

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
                lone_granted_segment = (
                    len(segments) == 1
                    and bool(granted)
                    and _is_granted_binary(segments[0][0], granted)
                )
                use_shell = os.name == "nt" and not lone_granted_segment

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
                    if cmd_base in _UNIX_TO_WIN:
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

                    # Record successful command execution for rate limiting
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
                        "error": (
                            f"Command timed out after {timeout} seconds: {command}. "
                            f"That is the '{timeout_class}' class default; the "
                            f"command was killed, so its work is incomplete."
                        ),
                        "command": command,
                        "stdout": stdout_str,
                        "stderr": stderr_str,
                        "has_errors": True,
                        "timed_out": True,
                        "timeout": timeout,
                        "timeout_class": timeout_class,
                        "duration_seconds": duration,
                        "cwd": cwd,
                        "hint": (
                            "Whatever the command printed before it was killed is in "
                            "'stdout'/'stderr' above. Either narrow the command (one "
                            "test file rather than the whole suite) or re-run it with a "
                            f"larger timeout, up to {MAX_COMMAND_TIMEOUT}s."
                        ),
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
                    "timeout_class": timeout_class,
                    "cwd": cwd,
                    "output_truncated": truncated,
                }

            except Exception as exc:
                logger.error(f"Error executing shell command: {exc}")
                return {"status": "error", "error": str(exc), "has_errors": True}

        @tool(
            atomic=True,
            name="wait_for_condition",
            # Outlast the longest wait the tool itself permits, so the agent
            # loop never abandons a wait that is still inside its deadline.
            timeout=WAIT_MAX_TIMEOUT + 60,
            description=(
                "Wait until a shell command succeeds (exit code 0), or give up at a "
                "deadline. Use this instead of sleeping and re-checking: waiting for a "
                "server to answer, a file to appear, a log line to be written, or a CI "
                "run to finish is ONE call, not one call per check. "
                f"Polls every {WAIT_DEFAULT_POLL_INTERVAL}s by default "
                f"({WAIT_MIN_POLL_INTERVAL}-{WAIT_MAX_POLL_INTERVAL}s), waits "
                f"{WAIT_DEFAULT_TIMEOUT}s by default and {WAIT_MAX_TIMEOUT}s at most. "
                "Examples: 'ls build/output.bin' (file exists), "
                "'grep -q \"Server started\" server.log' (log line written)."
            ),
            parameters={
                "command": {
                    "type": "str",
                    "description": (
                        "The predicate: a shell command that exits 0 once the condition "
                        "holds and non-zero until then. Same allowlist as run_shell_command."
                    ),
                    "required": True,
                },
                "working_directory": {
                    "type": "str",
                    "description": "Directory to run the predicate in (defaults to current directory)",
                    "required": False,
                },
                "timeout": {
                    "type": "int",
                    "description": (
                        f"Give up after this many seconds (default {WAIT_DEFAULT_TIMEOUT}, "
                        f"maximum {WAIT_MAX_TIMEOUT})."
                    ),
                    "required": False,
                },
                "poll_interval": {
                    "type": "int",
                    "description": (
                        f"Seconds between checks (default {WAIT_DEFAULT_POLL_INTERVAL}, "
                        f"{WAIT_MIN_POLL_INTERVAL}-{WAIT_MAX_POLL_INTERVAL})."
                    ),
                    "required": False,
                },
            },
        )
        def wait_for_condition(
            command: str,
            working_directory: Optional[str] = None,
            timeout: int = WAIT_DEFAULT_TIMEOUT,
            poll_interval: int = WAIT_DEFAULT_POLL_INTERVAL,
        ) -> Dict[str, Any]:
            """Wait until a shell command succeeds, instead of sleeping and re-checking: give it a command that exits 0 once the thing you are waiting for is ready (a file written, a server answering, a run finished) and it polls every 5s until then, giving up at 120s by default and 600s at most.

            One agent step covers the whole wait. The polling happens inside
            this call against a monotonic deadline, so the loop's step budget is
            spent on work rather than on re-asking whether the thing is ready.

            Args:
                command: The predicate — exits 0 once the condition holds,
                    non-zero until then. Same allowlist as run_shell_command.
                working_directory: Directory to run the predicate in
                timeout: Give up after this many seconds (max 600)
                poll_interval: Seconds between checks (5-60)

            Returns:
                A result dict carrying ``condition_met``, how many probes ran and
                the last probe's output. Deadline expiry is an error, not a
                quiet False.
            """
            try:
                timeout = int(timeout)
                poll_interval = int(poll_interval)
            except (TypeError, ValueError):
                return {
                    "status": "error",
                    "error": (
                        f"timeout and poll_interval must be whole seconds, got "
                        f"timeout={timeout!r}, poll_interval={poll_interval!r}."
                    ),
                    "has_errors": True,
                }

            if not 0 < timeout <= WAIT_MAX_TIMEOUT:
                return {
                    "status": "error",
                    "error": (
                        f"timeout must be between 1 and {WAIT_MAX_TIMEOUT} seconds, "
                        f"got {timeout}. A longer wait than {WAIT_MAX_TIMEOUT}s is the "
                        f"agent hanging, not waiting — report progress to the user and "
                        f"wait again if the condition is still worth waiting for."
                    ),
                    "has_errors": True,
                }
            if not WAIT_MIN_POLL_INTERVAL <= poll_interval <= WAIT_MAX_POLL_INTERVAL:
                return {
                    "status": "error",
                    "error": (
                        f"poll_interval must be between {WAIT_MIN_POLL_INTERVAL} and "
                        f"{WAIT_MAX_POLL_INTERVAL} seconds, got {poll_interval}. The "
                        f"floor keeps a wait from hammering the machine with probes."
                    ),
                    "has_errors": True,
                }

            # The wait is charged as one command up front; its probes are then
            # exempt (see _check_rate_limit) rather than each tripping the limit.
            allowed, reason, wait_time = self._check_rate_limit()
            if not allowed:
                return {
                    "status": "error",
                    "error": f"{reason}. Please wait {wait_time:.1f} seconds.",
                    "has_errors": True,
                    "rate_limited": True,
                    "wait_time_seconds": wait_time,
                }
            self._record_command_execution()

            interrupt = self._wait_interrupt_signal()
            start = time.monotonic()
            deadline = start + timeout
            polls = 0
            last: Dict[str, Any] = {}

            while True:
                remaining = deadline - time.monotonic()
                probe_timeout = max(1, min(int(remaining), WAIT_PROBE_TIMEOUT))
                self._shell_polling = True
                try:
                    last = run_shell_command(command, working_directory, probe_timeout)
                finally:
                    self._shell_polling = False
                polls += 1

                # No return_code means the predicate never ran — refused by the
                # guardrails, bad working directory, unparseable. Polling a
                # command that cannot run just burns the deadline, so stop now
                # and hand back the reason it was refused.
                if "return_code" not in last and not last.get("timed_out"):
                    return {
                        **last,
                        "condition_met": False,
                        "command": command,
                        "polls": polls,
                        "hint": (
                            "The predicate itself could not run, so the wait stopped "
                            "immediately. Fix the command above, then wait again."
                        ),
                    }

                if last.get("return_code") == 0:
                    elapsed = time.monotonic() - start
                    return {
                        "status": "success",
                        "condition_met": True,
                        "command": command,
                        "elapsed_seconds": elapsed,
                        "polls": polls,
                        "poll_interval_seconds": poll_interval,
                        "timeout": timeout,
                        "stdout": last.get("stdout", ""),
                        "stderr": last.get("stderr", ""),
                        "cwd": last.get("cwd"),
                        "has_errors": False,
                    }

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if interrupt.wait(min(poll_interval, remaining)):
                    return {
                        "status": "error",
                        "error": (
                            f"Wait for '{command}' was cancelled after {polls} "
                            f"check(s); the condition was never met."
                        ),
                        "condition_met": False,
                        "cancelled": True,
                        "command": command,
                        "polls": polls,
                        "has_errors": True,
                    }

            elapsed = time.monotonic() - start
            return {
                "status": "error",
                "error": (
                    f"Condition '{command}' was still not true {elapsed:.0f}s later "
                    f"(deadline {timeout}s, checked {polls} time(s) every "
                    f"{poll_interval}s). Last exit code: {last.get('return_code')}."
                ),
                "condition_met": False,
                "timed_out": True,
                "has_errors": True,
                "command": command,
                "elapsed_seconds": elapsed,
                "polls": polls,
                "poll_interval_seconds": poll_interval,
                "timeout": timeout,
                "last_return_code": last.get("return_code"),
                "stdout": last.get("stdout", ""),
                "stderr": last.get("stderr", ""),
                "cwd": last.get("cwd"),
                "hint": (
                    "The last check's output is above — read it before waiting again. "
                    "Whatever you are waiting for is slow, stuck, or never going to "
                    f"happen; tell the user which. A single wait is capped at "
                    f"{WAIT_MAX_TIMEOUT}s."
                ),
            }
