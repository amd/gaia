# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for shell command guardrails in ShellToolsMixin._validate_command."""

import pytest


@pytest.mark.parametrize(
    "command",
    [
        "powershell -NoLogo calc.exe",
        "powershell -Mta calc.exe",
        "powershell -Sta -NoLogo -com calc.exe",
        'powershell -comm "calc.exe"',
        "powershell -InputFormat Text calc.exe",
        "powershell -ConfigurationName x calc.exe",
        "powershell -Unknown Get-Process",
        "powershell -NoLogo",
        "powershell -NoLogo calc",
        'powershell -Command "Get-Process | calc"',
        'powershell -Command "Get-Process\ncalc.exe"',
    ],
)
def test_powershell_switches_cannot_hide_executable_body(command):
    assert ShellToolsMixin()._validate_shell_command(command)[0] is not None


@pytest.mark.parametrize(
    "command",
    [
        "powershell -NoLogo Get-Process",
        "powershell -Sta -NoLogo -com Get-Process",
        'powershell -Command "Get-Content ./a.txt"',
        'powershell -Command "Get-ChildItem . -Recurse"',
        "git ls-files -o",
        "git ls-files --others",
    ],
)
def test_reviewed_switches_and_relative_path_reads_remain_allowed(command):
    assert ShellToolsMixin()._validate_shell_command(command)[0] is None


from gaia.agents.tools.shell_tools import (
    ALLOWED_COMMANDS,
    DANGEROUS_SHELL_OPERATORS,
    DEVELOPER_COMMANDS,
    ShellToolsMixin,
    _is_lone_granted_segment,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def validate(command: str):
    """Return the validation error dict, or None if allowed."""
    parts = command.split()
    return ShellToolsMixin._validate_command(parts[0], parts, command)


class _Console:
    """The one attribute the shell gates read off a session's output handler."""

    def __init__(self, bypass: bool):
        self.bypass_permissions = bypass


class _Shell(ShellToolsMixin):
    """A bare host for the mixin, wired to a console in a known bypass state."""

    def __init__(self, bypass: bool):
        self.console = _Console(bypass)


class _Grants:
    """Stand-in for BinaryGrants — the one method the shell gates call."""

    def __init__(self, *binaries: str):
        self._binaries = frozenset(binaries)

    def binaries(self) -> frozenset:
        return self._binaries


class _GrantedShell(_Shell):
    """A host whose loaded skills granted ``shell:execute:<binary>``."""

    def __init__(self, bypass: bool, *binaries: str):
        super().__init__(bypass)
        self._granted_binaries = _Grants(*binaries)


def check(command: str, *, bypass: bool):
    """Run the full text-level validation for one mode. None means allowed."""
    error, _segments = _Shell(bypass)._validate_shell_command(command)
    return error


def segments_for(command: str, *, bypass: bool):
    _error, segments = _Shell(bypass)._validate_shell_command(command)
    return segments


# ---------------------------------------------------------------------------
# Allowed commands
# ---------------------------------------------------------------------------


class TestAllowedCommands:
    def test_ls(self):
        assert validate("ls -la") is None

    def test_cat(self):
        assert validate("cat file.txt") is None

    def test_grep(self):
        assert validate("grep -r foo src/") is None

    def test_git_status(self):
        assert validate("git status") is None

    def test_git_log(self):
        assert validate("git log --oneline -10") is None

    def test_systeminfo(self):
        assert validate("systeminfo") is None

    def test_powershell_get_process(self):
        assert validate("powershell -Command Get-Process") is None

    def test_powershell_get_wmiobject(self):
        assert validate("powershell -Command Get-WmiObject Win32_Processor") is None

    def test_powershell_select_object(self):
        assert validate("powershell -Command Get-Process | Select-Object Name") is None


# ---------------------------------------------------------------------------
# Blocked commands (not in ALLOWED_COMMANDS)
# ---------------------------------------------------------------------------


class TestBlockedCommands:
    def test_curl(self):
        result = validate("curl http://example.com")
        assert result is not None
        assert result["status"] == "error"

    def test_wget(self):
        result = validate("wget http://example.com")
        assert result is not None

    def test_rm(self):
        result = validate("rm -rf /tmp/foo")
        assert result is not None

    def test_arbitrary_binary(self):
        result = validate("evil_binary --flag")
        assert result is not None


# ---------------------------------------------------------------------------
# Git subcommand restrictions
# ---------------------------------------------------------------------------


class TestGitSubcommands:
    def test_git_push_blocked(self):
        result = validate("git push origin main")
        assert result is not None
        assert (
            "push" in result["error"].lower()
            or "not allowed" in result["error"].lower()
        )

    def test_git_commit_blocked(self):
        result = validate("git commit -m 'msg'")
        assert result is not None

    def test_git_diff_allowed(self):
        assert validate("git diff HEAD") is None

    def test_git_show_allowed(self):
        assert validate("git show HEAD") is None


# ---------------------------------------------------------------------------
# Dangerous shell operator detection
# ---------------------------------------------------------------------------


class TestDangerousOperators:
    def test_redirect_output(self):
        assert DANGEROUS_SHELL_OPERATORS.search("echo hello > file.txt")

    def test_redirect_output_no_space(self):
        # Bare > at end of string — edge case fixed in this PR
        assert DANGEROUS_SHELL_OPERATORS.search("echo hello>")

    def test_redirect_input(self):
        assert DANGEROUS_SHELL_OPERATORS.search("cat < file.txt")

    def test_append_redirect(self):
        assert DANGEROUS_SHELL_OPERATORS.search("echo hello >> file.txt")

    def test_command_substitution_backtick(self):
        assert DANGEROUS_SHELL_OPERATORS.search("echo `whoami`")

    def test_command_substitution_dollar(self):
        assert DANGEROUS_SHELL_OPERATORS.search("echo $(whoami)")

    def test_semicolon(self):
        assert DANGEROUS_SHELL_OPERATORS.search("ls; rm -rf /")

    def test_logical_and(self):
        assert DANGEROUS_SHELL_OPERATORS.search("ls && rm -rf /")

    def test_logical_or(self):
        assert DANGEROUS_SHELL_OPERATORS.search("ls || rm -rf /")

    def test_pipe_is_safe(self):
        # Single pipe is allowed (handled by pipe logic, not this regex)
        assert not DANGEROUS_SHELL_OPERATORS.search("ls | grep foo")

    def test_ampersand_word_boundary(self):
        # Background process & at end of word — should be caught
        assert DANGEROUS_SHELL_OPERATORS.search("sleep 10 &")

    def test_clean_command_not_flagged(self):
        assert not DANGEROUS_SHELL_OPERATORS.search("ls -la /tmp")
        assert not DANGEROUS_SHELL_OPERATORS.search("git status")
        assert not DANGEROUS_SHELL_OPERATORS.search("cat file.txt")


# ---------------------------------------------------------------------------
# find / sort / uniq write & exec side-doors (CWE-184: find -exec bypass)
# ---------------------------------------------------------------------------


class TestFindActionGuards:
    """find is whitelisted as read-only, but several predicates run, delete,
    or write files. These must be blocked or find becomes a whitelist bypass.
    """

    def test_find_exec_blocked(self):
        result = validate("find /tmp -maxdepth 0 -exec touch /tmp/canary {} +")
        assert result is not None
        assert result["status"] == "error"
        assert "find" in result["error"].lower()

    def test_find_execdir_blocked(self):
        result = validate("find /tmp -execdir touch {} +")
        assert result is not None

    def test_find_ok_blocked(self):
        assert validate("find /tmp -name x -ok rm {} ;") is not None

    def test_find_okdir_blocked(self):
        assert validate("find /tmp -okdir rm {} ;") is not None

    def test_find_delete_blocked(self):
        assert validate("find /tmp -name x -delete") is not None

    def test_find_fprint_blocked(self):
        assert validate("find . -fprint /tmp/canary") is not None

    def test_find_fprintf_blocked(self):
        assert validate("find . -fprintf /tmp/canary hi") is not None

    def test_find_fls_blocked(self):
        assert validate("find . -fls /tmp/canary") is not None

    def test_find_fprint0_blocked(self):
        # -fprint0 writes null-separated results to FILE, same as -fprint.
        assert validate("find . -fprint0 /tmp/canary") is not None

    def test_find_exec_uppercase_blocked(self):
        # Token is lowercased before matching, so case tricks don't help.
        assert validate("find /tmp -EXEC touch {} +") is not None

    # Read-only predicates must still be allowed
    def test_find_print_allowed(self):
        assert validate("find /tmp -maxdepth 2 -print") is None

    def test_find_printf_allowed(self):
        # -printf writes to STDOUT (read-only); must not be confused with -fprintf.
        assert validate("find . -printf %p") is None

    def test_find_ls_allowed(self):
        assert validate("find . -ls") is None

    def test_find_name_type_allowed(self):
        assert validate("find . -name foo.py -type f") is None


class TestSortOutputGuard:
    def test_sort_output_short_blocked(self):
        result = validate("sort -o /tmp/canary /etc/hostname")
        assert result is not None
        assert result["status"] == "error"

    def test_sort_output_long_blocked(self):
        assert validate("sort --output=/tmp/canary /etc/hostname") is not None

    def test_sort_output_attached_blocked(self):
        # -oFILE attached form must not slip past.
        assert validate("sort -o/tmp/canary /etc/hostname") is not None

    def test_sort_output_bundled_attached_blocked(self):
        # -ro/tmp/x == -r -o /tmp/x: cluster + attached value in one token.
        assert validate("sort -ro/tmp/canary /etc/hostname") is not None

    def test_sort_output_bundled_blocked(self):
        # Bundled short cluster -ro == -r -o.
        assert validate("sort -ro /tmp/canary /etc/hostname") is not None

    def test_sort_output_abbreviation_blocked(self):
        # GNU sort accepts unambiguous long-option abbreviations of --output.
        assert validate("sort --out=/tmp/canary /etc/hostname") is not None
        assert validate("sort --o /tmp/canary /etc/hostname") is not None

    def test_sort_plain_allowed(self):
        assert validate("sort file.txt") is None

    def test_sort_flags_allowed(self):
        assert validate("sort -r -u file.txt") is None


class TestUniqOutputGuard:
    def test_uniq_output_file_blocked(self):
        result = validate("uniq in.txt out.txt")
        assert result is not None
        assert result["status"] == "error"

    def test_uniq_single_input_allowed(self):
        assert validate("uniq file.txt") is None

    def test_uniq_count_flag_allowed(self):
        assert validate("uniq -c file.txt") is None

    def test_uniq_value_flag_not_counted_as_operand(self):
        # -f consumes '2'; only one operand (file.txt) remains -> allowed.
        assert validate("uniq -f 2 file.txt") is None


# ---------------------------------------------------------------------------
# PowerShell cmdlet filtering
# ---------------------------------------------------------------------------


class TestPowerShellFiltering:
    def test_get_cmdlet_allowed(self):
        assert validate("powershell -Command Get-WmiObject Win32_Processor") is None

    def test_set_cmdlet_blocked(self):
        result = validate("powershell -Command Set-ExecutionPolicy Unrestricted")
        assert result is not None
        assert result["status"] == "error"

    def test_remove_cmdlet_blocked(self):
        result = validate("powershell -Command Remove-Item C:/important")
        assert result is not None

    def test_invoke_expression_blocked(self):
        result = validate("powershell -Command Invoke-Expression $cmd")
        assert result is not None

    def test_encoded_command_blocked(self):
        result = validate("powershell -EncodedCommand dQBzAGUA")
        assert result is not None
        assert result["status"] == "error"

    def test_file_flag_blocked(self):
        result = validate("powershell -File C:/malicious.ps1")
        assert result is not None

    def test_execution_policy_flag_blocked(self):
        result = validate("powershell -ExecutionPolicy Bypass -Command Get-Process")
        assert result is not None

    def test_short_enc_flag_blocked(self):
        result = validate("powershell -enc dQBzAGUA")
        assert result is not None

    def test_format_list_allowed(self):
        assert validate("powershell -Command Get-Process | Format-List Name") is None

    def test_where_object_allowed(self):
        assert (
            validate("powershell -Command Get-Process | Where-Object Name -eq svchost")
            is None
        )


# ---------------------------------------------------------------------------
# Read-only allowlist bypasses (C4)
#
# Probe strings from a security review of the read-only whitelist: the refused
# ones were answered "allowed" before, the allowed ones pin behaviour the fix
# must not cost. They go through the WHOLE validator rather than one regex,
# because each bypass reached the shell by a different door — the operator
# scan, the PowerShell flag list, the `-Command` body, or a git/wmic flag the
# subcommand check never looked at.
# ---------------------------------------------------------------------------


def refused(command: str) -> bool:
    """True when the full validator refuses *command*."""
    error, _ = ShellToolsMixin()._validate_shell_command(command)
    return error is not None


class TestUnspacedAmpersandIsAnOperator:
    """cmd.exe splits on `&` with or without whitespace around it."""

    def test_unspaced_ampersand_chains_a_second_command(self):
        assert refused("dir . &where cmd")

    @pytest.mark.parametrize(
        "command",
        ["dir&whoami", "dir .&where cmd", "ls >& out", "ls <& in", "sleep 10 &"],
    )
    def test_every_ampersand_spelling_is_refused(self, command):
        assert refused(command)

    @pytest.mark.parametrize(
        "command", ["ls -la /tmp", "git status", "cat file.txt", "ls | grep foo"]
    )
    def test_ordinary_commands_still_run(self, command):
        assert not refused(command)


class TestPowerShellFlagPrefixes:
    """PowerShell resolves a parameter from a prefix, so exact matching leaks."""

    @pytest.mark.parametrize(
        "command",
        [
            "powershell -e ZQBjAGgAbwA=",
            "powershell -ec ZQBjAGgAbwA=",
            "powershell -enc ZQBjAGgAbwA=",
            "powershell -encod ZQBjAGgAbwA=",
            "powershell -EncodedCommand ZQBjAGgAbwA=",
            "powershell -fi C:/evil.ps1",
            "powershell -File C:/evil.ps1",
            "powershell -exec bypass -Command Get-Process",
            "powershell -ExecutionPolicy Bypass -Command Get-Process",
        ],
    )
    def test_any_prefix_of_a_blocked_parameter_is_refused(self, command):
        assert refused(command)

    @pytest.mark.parametrize(
        "command",
        [
            "powershell -Command Get-Process",
            "powershell -c Get-Process",
            'powershell -Command "Get-WmiObject Win32_Processor | Select-Object Name"',
        ],
    )
    def test_command_is_not_a_prefix_of_anything_blocked(self, command):
        assert not refused(command)


class TestPowerShellCommandBodyEscapes:
    """The outer operator scan skips the `-Command` body, so it is checked here."""

    @pytest.mark.parametrize(
        "command",
        [
            'powershell -Command "Get-Content x > C:/out.txt"',
            "powershell -Command \"[System.Diagnostics.Process]::Start('calc')\"",
            "powershell -Command \"[System.IO.File]::WriteAllText('a','b')\"",
            "powershell -Command \"(Get-WmiObject Win32_Process).Create('calc')\"",
            'powershell -Command "& calc.exe"',
            'powershell -Command "&$var"',
            'powershell -Command ". ./evil.ps1"',
            'powershell -Command "Get-Process; Get-Service"',
            'powershell -Command "Get-Process $env:USERNAME"',
        ],
    )
    def test_code_the_cmdlet_allowlist_cannot_see_is_refused(self, command):
        assert refused(command)

    @pytest.mark.parametrize(
        "command",
        [
            'powershell -Command "Get-CimInstance Win32_Processor | Select-Object Name"',
            'powershell -Command "Get-Process | Sort-Object WS -Descending | '
            'Select-Object -First 15 Name, Id, WS"',
            'powershell -Command "Get-ChildItem -Filter *.log"',
        ],
    )
    def test_plain_read_only_cmdlets_still_run(self, command):
        assert not refused(command)


class TestGitAndWmicFileWrites:
    """The allowlisted read-only binaries that can still write a chosen path."""

    @pytest.mark.parametrize(
        "command",
        [
            "git log --output=C:/out.txt --format=pwned",
            "git log --o C:/out.txt",
            "git log -o C:/out.txt",
            "git log -oC:/out.txt",
            "wmic /output:C:/out.txt cpu get name",
            "wmic /append:C:/out.txt os get caption",
        ],
    )
    def test_an_output_flag_is_refused(self, command):
        assert refused(command)

    @pytest.mark.parametrize(
        "command",
        [
            "git log --oneline -10",
            "git branch -a",
            "git diff --stat",
            "git status",
            "wmic cpu get name",
            "wmic os get caption",
        ],
    )
    def test_read_only_spellings_are_untouched(self, command):
        assert not refused(command)


class TestQuotedOperatorsAreData:
    """cmd.exe and sh both read `&` between double quotes as a literal.

    Scanning the quoted span too would refuse ordinary reads whose argument
    happens to contain a URL query string.
    """

    @pytest.mark.parametrize(
        "command",
        ['grep "a&b" file.txt', 'grep "a>b" file.txt', 'cat "a|b.txt"'],
    )
    def test_an_operator_inside_double_quotes_is_an_argument(self, command):
        assert not refused(command)

    @pytest.mark.parametrize(
        "command",
        ['dir "a" &calc', 'dir "a&b" & calc', 'echo "a" & calc', 'cat "a.txt" ; id'],
    )
    def test_an_operator_outside_the_quotes_is_still_an_operator(self, command):
        assert refused(command)

    def test_unbalanced_quotes_are_scanned_whole(self):
        """Broken quoting means the shell's parse is anyone's guess — refuse."""
        assert refused('dir "a &calc')


class TestPowerShellRunsAFileInsteadOfACmdlet:
    """A body naming a path or an executable never matches the cmdlet regex.

    Every probe here passed the `verb-noun` allowlist by containing no cmdlet
    at all, which made the whole PowerShell filter a no-op for that call.
    """

    @pytest.mark.parametrize(
        "command",
        [
            r'powershell -Command ".\evil.ps1"',
            r'powershell -Command "C:\evil.ps1"',
            r'powershell -Command "\\host\share\evil.ps1"',
            r'powershell -Command ". .\evil.ps1"',
            r'powershell -Command "Get-Process | .\evil.ps1"',
            'powershell -Command "calc.exe"',
            'powershell -Command "payload.bat"',
        ],
    )
    def test_running_a_file_by_path_is_refused(self, command):
        assert refused(command)

    @pytest.mark.parametrize(
        "command",
        [
            r'powershell -Command "Get-Content C:\temp\a.txt"',
            'powershell -Command "Get-Content C:/temp/a.txt"',
            'powershell -Command "Get-ChildItem -Filter *.log"',
        ],
    )
    def test_a_path_operand_is_still_a_read(self, command):
        """The rule is command position only, or every file argument breaks."""
        assert not refused(command)


# ---------------------------------------------------------------------------
# Bypass permissions: shell gates (#3373, #3374)
#
# The switch is the sidecar's existing --bypass-permissions / TUI /bypass, which
# already turned confirmation prompts off; these tests cover the shell gates it
# now lifts with them.
#
# Every case asserts BOTH states. The default tier is what ships; the bypass
# tier is what the user turned on deliberately. A test that only covered the
# bypass side could not catch a new binary leaking into the default set.
# ---------------------------------------------------------------------------

#: The developer set's headline entries, named individually so a future edit
#: cannot quietly move one into ALLOWED_COMMANDS unnoticed (#3374).
DEVELOPER_BINARY_SAMPLES = [
    "python",
    "python3",
    "pytest",
    "npm",
    "node",
    "make",
    "cmake",
    "go",
    "cargo",
    "gh",
    "sed",
    "awk",
    "curl",
    "sleep",
    "timeout",
    "export",
    "cp",
    "mv",
]


class TestDeveloperSetIsSeparate:
    def test_allowed_commands_carries_no_developer_binary(self):
        # #2768 hardens ALLOWED_COMMANDS as a read-only tier; the developer set
        # must stay beside it, never merged into it.
        assert ALLOWED_COMMANDS.isdisjoint(DEVELOPER_COMMANDS)

    def test_rm_is_in_neither_set(self):
        # Deliberate: not a security boundary (python can delete), an accident
        # tripwire. Adding it later is cheaper than taking it back.
        assert "rm" not in ALLOWED_COMMANDS
        assert "rm" not in DEVELOPER_COMMANDS

    @pytest.mark.parametrize("binary", DEVELOPER_BINARY_SAMPLES)
    def test_developer_binary_declared(self, binary):
        assert binary in DEVELOPER_COMMANDS


class TestDeveloperBinariesRefusedByDefault:
    """With the flag OFF every developer binary is still refused, as today."""

    @pytest.mark.parametrize("binary", DEVELOPER_BINARY_SAMPLES)
    def test_refused_with_bypass_off(self, binary):
        result = check(f"{binary} --version", bypass=False)
        assert result is not None, f"{binary} leaked into the default tier"
        assert result["status"] == "error"

    @pytest.mark.parametrize("binary", DEVELOPER_BINARY_SAMPLES)
    def test_allowed_with_bypass_on(self, binary):
        assert check(f"{binary} --version", bypass=True) is None

    def test_default_refusal_text_unchanged(self):
        # The existing wording, asserted so bypass mode cannot alter the
        # message a normal user sees.
        result = check("make build", bypass=False)
        assert "not in the allowed list for security reasons" in result["error"]

    def test_gh_default_refusal_still_points_at_the_skill_grant(self):
        # gh has a BINARY_POLICIES entry, so its refusal is the grant message,
        # not the allowlist one. Bypass is an additional path to gh, not a
        # replacement for skill_granted_binaries.
        result = check("gh issue list", bypass=False)
        assert "shell:execute:gh" in result["error"]


class TestBypassIsStillASet:
    @pytest.mark.parametrize("command", ["rm -rf /tmp/foo", "evil_binary --flag"])
    def test_refused_in_both_modes(self, command):
        assert check(command, bypass=False) is not None
        assert check(command, bypass=True) is not None

    def test_bypass_refusal_names_the_developer_set(self):
        result = check("rm -rf /tmp/foo", bypass=True)
        assert "developer command set" in result["error"]

    def test_read_only_commands_still_allowed_under_bypass(self):
        assert check("ls -la", bypass=True) is None
        assert check("grep -r foo src/", bypass=True) is None


class TestOperatorsUnderBypass:
    def test_compound_refused_by_default(self):
        result = check("cd . && ls", bypass=False)
        assert result is not None
        assert "Shell operators" in result["error"]

    def test_compound_allowed_under_bypass(self):
        assert check("cd . && ls | head -3", bypass=True) is None

    @pytest.mark.parametrize(
        "command",
        [
            "cd build && cmake ..",
            "pytest -q || echo failed",
            "echo one ; echo two",
            "pytest -q | tail -20",
            "make build > out.txt",
        ],
    )
    def test_sequences_parse_under_bypass(self, command):
        assert check(command, bypass=True) is None

    @pytest.mark.parametrize(
        "command",
        [
            "cd build && cmake ..",
            "pytest -q || echo failed",
            "echo one ; echo two",
        ],
    )
    def test_same_sequences_refused_by_default(self, command):
        result = check(command, bypass=False)
        assert result is not None
        assert "Shell operators" in result["error"]

    def test_a_pipe_is_refused_for_its_binary_not_for_the_pipe(self):
        # Pipes were never blocked. `pytest -q | tail -20` is refused with
        # bypass off because pytest is ungranted, NOT by the operator block —
        # #3373 cites it as an operator case and is wrong about that.
        result = check("pytest -q | tail -20", bypass=False)
        assert result is not None
        assert "Shell operators" not in result["error"]
        assert "shell:execute:pytest" in result["error"]


class TestPerSegmentWalkSurvivesBypass:
    """The per-segment walk is what produces the audit record; it must not be
    short-circuited just because the operators now parse."""

    def test_denied_binary_in_segment_two_refuses_the_whole_command_by_default(self):
        # Refused for the operator, before the binary is even reached — the
        # ordering documented in #3373.
        result = check("ls && rm -rf /tmp/foo", bypass=False)
        assert result is not None
        assert "Shell operators" in result["error"]

    def test_denied_binary_in_segment_two_refuses_the_whole_command_under_bypass(self):
        result = check("ls && rm -rf /tmp/foo", bypass=True)
        assert result is not None
        assert "rm" in result["error"]

    def test_denied_binary_in_pipe_segment_two_refused_in_both_modes(self):
        # No operator involved, so both modes reach the per-segment walk.
        assert check("ls | rm -rf /tmp/foo", bypass=False) is not None
        assert check("ls | rm -rf /tmp/foo", bypass=True) is not None

    def test_every_segment_is_recorded_under_bypass(self):
        segments = segments_for("cd . && ls | head -3", bypass=True)
        assert [seg[0] for seg in segments] == ["cd", "ls", "head"]

    def test_pipe_segments_recorded_by_default(self):
        segments = segments_for("ls | grep foo | head -3", bypass=False)
        assert [seg[0] for seg in segments] == ["ls", "grep", "head"]


class TestNewlineSeparatesSegmentsUnderBypass:
    """A newline reaches the shell as a command separator, so the segment walk
    has to treat it as one. While it did not, `ls\\nrm -rf /tmp/x` was walked as
    a single `ls` — the refused binary never checked, and the audit record
    showing one invocation where two ran."""

    def test_newline_does_not_smuggle_a_refused_binary(self):
        result = check("ls\nrm -rf /tmp/x", bypass=True)
        assert result is not None
        assert "rm" in result["error"]

    def test_each_line_is_recorded_as_its_own_segment(self):
        segments = segments_for("ls\necho hi", bypass=True)
        assert [seg[0] for seg in segments] == ["ls", "echo"]

    @pytest.mark.parametrize(
        "command",
        [
            "ls;\nrm -rf /tmp/x",
            "ls &&\nrm -rf /tmp/x",
            "ls |\nrm -rf /tmp/x",
            "ls\n;rm -rf /tmp/x",
        ],
    )
    def test_newline_fused_to_another_operator_still_separates(self, command):
        # shlex emits a run of adjacent punctuation as ONE token, so these
        # arrive as ';\n', '&&\n', '|\n', '\n;' rather than two tokens.
        result = check(command, bypass=True)
        assert result is not None
        assert "rm" in result["error"]

    def test_blank_lines_do_not_create_empty_segments(self):
        segments = segments_for("ls\n\n\necho hi\n", bypass=True)
        assert [seg[0] for seg in segments] == ["ls", "echo"]

    def test_a_quoted_newline_is_data_not_a_separator(self):
        # The regression guard for the fix: quoting must still work, or
        # `echo "a<newline>b"` would be walked as a bogus `b` command.
        segments = segments_for('echo "a\nb"', bypass=True)
        assert segments == [["echo", "a\nb"]]
        assert check('echo "a\nb"', bypass=True) is None


class TestReadOnlySubGuardsLiftUnderBypassOnly:
    """The find/sort/uniq/git/PowerShell guards all encode "this binary may not
    write" — the exact assumption bypass mode drops. Each must still hold with
    the flag off."""

    @pytest.mark.parametrize(
        "command",
        [
            "git commit -m msg",
            "find /tmp -name x -delete",
            "sort -o /tmp/canary /etc/hostname",
            "uniq in.txt out.txt",
            "powershell -Command Remove-Item C:/important",
        ],
    )
    def test_refused_by_default(self, command):
        assert check(command, bypass=False) is not None

    @pytest.mark.parametrize(
        "command",
        [
            "git commit -m msg",
            "find /tmp -name x -delete",
            "sort -o /tmp/canary /etc/hostname",
            "uniq in.txt out.txt",
            "powershell -Command Remove-Item C:/important",
        ],
    )
    def test_allowed_under_bypass(self, command):
        assert check(command, bypass=True) is None


class TestBypassIsOffByDefault:
    def test_host_with_no_console_is_not_bypassed(self):
        class Bare:
            bypass_gates_active = ShellToolsMixin.bypass_gates_active

        assert Bare().bypass_gates_active() is False

    def test_stock_output_handler_is_not_bypassed(self):
        from gaia.agents.base.console import OutputHandler

        assert OutputHandler.bypass_permissions is False

    def test_auto_approve_alone_does_not_lift_the_shell_gates(self):
        # An unattended harness that pre-approves prompts (GAIA_AUTO_APPROVE_TOOLS
        # or auto_approve_gated_tools) must NOT also inherit an unguarded shell.
        class ApproveOnly:
            auto_approve_gated_tools = True

        host = _Shell(bypass=False)
        host.console = ApproveOnly()
        assert host.bypass_gates_active() is False
        assert host._validate_shell_command("make build")[0] is not None

    def test_toggling_the_console_flips_the_gates_live(self):
        # /bypass off mid-session must take effect on the next command, which is
        # why the mode is read live rather than cached on the agent.
        host = _Shell(bypass=False)
        assert host._validate_shell_command("make build")[0] is not None
        host.console.bypass_permissions = True
        assert host._validate_shell_command("make build")[0] is None
        host.console.bypass_permissions = False
        assert host._validate_shell_command("make build")[0] is not None

    def test_validate_command_defaults_to_the_read_only_tier(self):
        # The three-positional-argument call every existing test uses.
        assert ShellToolsMixin._validate_command("pytest", ["pytest"], "pytest")


# ---------------------------------------------------------------------------
# The executor, for real (#3373)
#
# Everything above stops at validation. These run the tool end to end and
# actually spawn a process, because validation passing is not the same as the
# command working: the operators only reach a shell if the executor asks for
# one, and the rate-limit deque is created lazily by a check bypass skips.
# ---------------------------------------------------------------------------


class _ExecHost(ShellToolsMixin):
    """A host with a real tool registry, so run_shell_command can be called."""

    def __init__(self, bypass: bool):
        self.console = _Console(bypass)
        self.debug = False
        self.registered = {}

    def register(self, fn, name):
        self.registered[name] = fn


@pytest.fixture
def shell_tool(monkeypatch):
    """Return a factory for the real ``run_shell_command`` closure."""

    def build(bypass: bool, granted: tuple = ()):
        host = _ExecHost(bypass)
        if granted:
            host._granted_binaries = _Grants(*granted)
        captured = {}

        def fake_tool(**kwargs):
            def wrap(fn):
                # The real decorator defaults the tool name to the function's.
                captured[kwargs.get("name", fn.__name__)] = fn
                return fn

            return wrap

        import gaia.agents.base.tools as tools_mod

        monkeypatch.setattr(tools_mod, "tool", fake_tool)
        host.register_shell_tools()
        return captured["run_shell_command"]

    return build


class TestExecutorUnderBypass:
    def test_a_compound_command_actually_runs(self, shell_tool, tmp_path):
        """The whole point of #3373: `a && b` reaches a shell and succeeds."""
        run = shell_tool(bypass=True)

        result = run(
            "cd . && echo first && echo second", working_directory=str(tmp_path)
        )

        assert result["status"] == "success", result
        assert result["return_code"] == 0
        assert "first" in result["stdout"]
        assert "second" in result["stdout"]

    def test_the_same_command_never_reaches_a_shell_by_default(
        self, shell_tool, tmp_path
    ):
        run = shell_tool(bypass=False)

        result = run(
            "cd . && echo first && echo second", working_directory=str(tmp_path)
        )

        assert result["status"] == "error"
        assert "Shell operators" in result["error"]

    def test_the_rate_limit_is_lifted(self, shell_tool, tmp_path):
        """More than max_commands_per_10_seconds back to back, no refusal.

        Also covers the deque: _check_rate_limit is what lazily creates it, and
        bypass skips that call — recording into it anyway raised AttributeError
        on the very first command.
        """
        run = shell_tool(bypass=True)

        for i in range(5):
            result = run(f"echo run{i}", working_directory=str(tmp_path))
            assert result["status"] == "success", result
            assert not result.get("rate_limited")

    def test_the_rate_limit_still_applies_by_default(self, shell_tool, tmp_path):
        run = shell_tool(bypass=False)

        results = [run("echo hi", working_directory=str(tmp_path)) for _ in range(5)]

        assert any(r.get("rate_limited") for r in results)

    def test_every_execution_is_audited_with_its_arguments(
        self, shell_tool, tmp_path, monkeypatch
    ):
        records = []
        monkeypatch.setattr(
            "gaia.security.audit_shell_command",
            lambda **kw: records.append(kw),
        )
        run = shell_tool(bypass=True)

        run("cd . && echo audited", working_directory=str(tmp_path))

        assert len(records) == 1
        assert records[0]["command"] == "cd . && echo audited"
        assert records[0]["segments"] == [["cd", "."], ["echo", "audited"]]
        assert records[0]["mode"] == "bypass"

    def test_a_refused_binary_is_never_executed_or_audited(
        self, shell_tool, tmp_path, monkeypatch
    ):
        records = []
        monkeypatch.setattr(
            "gaia.security.audit_shell_command",
            lambda **kw: records.append(kw),
        )
        run = shell_tool(bypass=True)

        result = run("echo hi && rm -rf nope", working_directory=str(tmp_path))

        assert result["status"] == "error"
        assert records == [], "a refused command must not reach the audit trail"


# ---------------------------------------------------------------------------
# Redirection on the skill-granted path
#
# A lone granted CLI is handed argv, never a shell. A `>` would arrive as one
# more literal argument, so the command would appear to succeed while writing
# nothing. That is the one outcome worse than a refusal.
# ---------------------------------------------------------------------------


def check_granted(command: str, *, bypass: bool = True, binaries=("gh",)):
    error, _segments = _GrantedShell(bypass, *binaries)._validate_shell_command(command)
    return error


class TestRedirectionOnTheGrantedPath:
    def test_a_redirect_is_refused_rather_than_passed_as_text(self):
        error = check_granted("gh issue list > out.txt")
        assert error is not None
        assert "Redirection" in error["error"]
        assert "gh" in error["error"]

    def test_the_refusal_names_a_way_to_get_the_file_written(self):
        assert "file tools" in check_granted("gh issue list > out.txt")["hint"]

    def test_append_and_input_redirection_are_refused_too(self):
        assert check_granted("gh issue list >> out.txt") is not None
        assert check_granted("gh api graphql < query.txt") is not None

    def test_the_same_command_without_a_redirect_still_runs(self):
        assert check_granted("gh issue list --state open") is None

    def test_a_quoted_angle_bracket_is_data_not_a_redirect(self):
        # Tokenisation drops the quotes, so only a quote-aware scan of the raw
        # text can tell a search operand from a redirect.
        assert check_granted('gh issue list --search "updated:>2026-01-01"') is None

    def test_a_chained_command_keeps_its_shell_and_so_its_redirect(self):
        # More than one segment is not the argv-only path: a real shell performs
        # the redirect, so there is nothing to refuse.
        assert check_granted("gh issue list > out.txt && echo done") is None

    def test_the_refusal_is_scoped_to_granted_binaries(self):
        # Same text, no grant: the ordinary bypass path, where a shell redirects.
        assert check("gh issue list > out.txt", bypass=True) is None
        assert check("make build > out.txt", bypass=True) is None

    def test_without_bypass_the_operator_blocklist_gets_there_first(self):
        # The refusal only has to exist under bypass; by default `>` never
        # reaches tokenisation at all.
        error = check_granted("gh issue list > out.txt", bypass=False)
        assert error is not None
        assert "Shell operators" in error["error"]

    def test_the_pre_flight_and_the_executor_share_one_answer(self):
        # Extracted precisely so the refusal above and the executor's `use_shell`
        # cannot disagree about which commands run argv-only.
        granted = frozenset({"gh"})
        assert _is_lone_granted_segment([["gh", "issue", "list"]], granted)
        assert not _is_lone_granted_segment(
            [["gh", "issue", "list"], ["echo", "done"]], granted
        )
        assert not _is_lone_granted_segment([["make", "build"]], granted)
        assert not _is_lone_granted_segment([["gh", "issue", "list"]], frozenset())


class TestGrantedRedirectNeverReachesAProcess:
    def test_the_tool_refuses_and_audits_nothing(
        self, shell_tool, tmp_path, monkeypatch
    ):
        records = []
        monkeypatch.setattr(
            "gaia.security.audit_shell_command",
            lambda **kw: records.append(kw),
        )
        run = shell_tool(bypass=True, granted=("gh",))

        result = run("gh issue list > out.txt", working_directory=str(tmp_path))

        assert result["status"] == "error"
        assert "Redirection" in result["error"]
        assert records == [], "a refused command must not reach the audit trail"
        assert not (tmp_path / "out.txt").exists()
