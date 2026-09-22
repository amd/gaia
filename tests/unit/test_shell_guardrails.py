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
    TIER_CONFIRM,
    TIER_REFUSE,
    ShellToolsMixin,
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

    def __init__(self, full_access: bool):
        self.full_access = full_access


class _Shell(ShellToolsMixin):
    """A bare host for the mixin, wired to a console in a known full-access state."""

    def __init__(self, full_access: bool):
        self.console = _Console(full_access)


def check(command: str, *, full_access: bool):
    """Run the full text-level validation for one mode. None means allowed."""
    error, _segments = _Shell(full_access)._validate_shell_command(command)
    return error


def segments_for(command: str, *, full_access: bool):
    """Every segment the validator walked, flattened across the line's steps."""
    _error, steps = _Shell(full_access)._validate_shell_command(command)
    return [segment for step in steps for segment in step.segments]


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
    def test_git_push_needs_confirmation(self):
        result = validate("git push origin main")
        assert result is not None
        assert result["tier"] == TIER_CONFIRM
        assert (
            "push" in result["error"].lower()
            or "not allowed" in result["error"].lower()
        )

    def test_git_commit_needs_confirmation(self):
        result = validate("git commit -m 'msg'")
        assert result is not None
        assert result["tier"] == TIER_CONFIRM

    def test_git_diff_allowed(self):
        assert validate("git diff HEAD") is None

    def test_git_show_allowed(self):
        assert validate("git show HEAD") is None


# ---------------------------------------------------------------------------
# Git global options that precede the subcommand
# ---------------------------------------------------------------------------


class TestGitGlobalOptions:
    """A global flag must not be mistaken for the subcommand (#3624)."""

    def test_dash_c_repo_path_then_read_only_subcommand(self):
        assert validate("git -C /repo branch --list") is None

    def test_dash_c_repo_path_then_write_subcommand_still_blocked(self):
        result = validate("git -C /repo push origin main")
        assert result is not None
        assert "push" in result["error"]

    def test_git_dir_separate_value(self):
        assert validate("git --git-dir /repo/.git log --oneline") is None

    def test_git_dir_inline_value(self):
        assert validate("git --git-dir=/repo/.git status") is None

    def test_work_tree_and_no_pager_combined(self):
        assert validate("git --no-pager --work-tree /repo status") is None

    def test_namespace_value_is_not_read_as_subcommand(self):
        # Without value-consumption the walk would land on "reset".
        result = validate("git --namespace reset status")
        assert result is None

    def test_version_needs_no_subcommand(self):
        assert validate("git --version") is None

    def test_config_override_refused(self):
        result = validate("git -c core.pager=sh status")
        assert result is not None
        assert "-c" in result["error"]

    def test_config_env_refused(self):
        result = validate("git --config-env=core.pager=EVIL status")
        assert result is not None
        assert "--config-env" in result["error"]

    def test_exec_path_refused(self):
        result = validate("git --exec-path=/tmp/evil status")
        assert result is not None
        assert "--exec-path" in result["error"]

    def test_unknown_global_option_refused(self):
        result = validate("git --brand-new-flag status")
        assert result is not None
        assert "--brand-new-flag" in result["error"]

    def test_global_option_with_no_subcommand_refused(self):
        result = validate("git -C /repo")
        assert result is not None
        assert "No git subcommand" in result["error"]


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

    def test_chaining_is_split_off_before_this_scan(self):
        """`&&`, `||` and `;` pick which commands run; they do not change what
        a command IS, so each one goes through the whole allowlist on its own.

        `rm` is refused here for being `rm`, not for the operator in front of it.
        """
        for command in ("ls; rm -rf /", "ls && rm -rf /", "ls || rm -rf /"):
            error, _ = ShellToolsMixin()._validate_shell_command(command)
            assert error is not None, command
            assert "not in the allowed list" in error["error"]

    def test_a_lone_ampersand_is_not_a_chaining_operator(self):
        """`&` backgrounds a command, and cmd.exe runs `dir&whoami` as two."""
        assert DANGEROUS_SHELL_OPERATORS.search("dir&whoami")
        assert DANGEROUS_SHELL_OPERATORS.search("ls & rm -rf /")

    def test_newline(self):
        assert DANGEROUS_SHELL_OPERATORS.search("ls\nrm -rf /")

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
# Which tier a block lands in, and what full access lifts
# ---------------------------------------------------------------------------


class _Host(ShellToolsMixin):
    """A host whose console says how a prompt would be approved."""

    debug = False

    def __init__(self, host_opt_in=False):
        super().__init__()

        class _Console:
            auto_approve_gated_tools = host_opt_in

        self.console = _Console()


def refusal(command, host_opt_in=False):
    """What the pre-prompt gate returns -- None means the user gets asked."""
    return _Host(host_opt_in).policy_refusal_for_call(
        "run_shell_command", {"command": command}
    )


@pytest.fixture
def env_pre_approves(monkeypatch):
    """GAIA_AUTO_APPROVE_TOOLS=1, as an unattended run sets it."""
    monkeypatch.setattr(
        "gaia.agents.base.console.auto_approve_env_enabled", lambda: True
    )


class TestTiers:
    """A block is refused only when a yes/no prompt cannot honestly describe it."""

    @pytest.mark.parametrize(
        "command",
        [
            "git commit -m wip",
            "git push origin main",
            "npm test",
            "rm notes.txt",
            "find . -delete",
            "sort -o out.txt in.txt",
        ],
    )
    def test_a_describable_write_is_confirmable(self, command):
        assert validate(command)["tier"] == TIER_CONFIRM

    @pytest.mark.parametrize(
        "command",
        [
            "git -c core.pager=evil.sh status",
            "git --exec-path=/tmp/evil status",
            "powershell -EncodedCommand aQBlAHgA",
        ],
    )
    def test_an_undescribable_escalation_is_refused(self, command):
        assert validate(command)["tier"] == TIER_REFUSE

    @pytest.mark.parametrize("command", ["echo hi > f", "cat 'unterminated"])
    def test_what_the_runner_cannot_execute_is_refused(self, command):
        error, _ = _Host()._validate_shell_command(command)
        assert error["tier"] == TIER_REFUSE


class TestConfirmableCommandsReachThePrompt:
    """The regression this tier exists to prevent: refusing an approvable call."""

    @pytest.mark.parametrize(
        "command",
        ["git commit -m wip", "git push origin main", "npm test", "rm notes.txt"],
    )
    def test_not_refused_before_the_prompt(self, command):
        assert refusal(command) is None

    @pytest.mark.parametrize(
        "command", ["git -c core.pager=evil.sh status", "echo hi > f"]
    )
    def test_refused_escalations_stay_refused_even_with_a_host_opt_in(self, command):
        assert refusal(command, host_opt_in=True) is not None


class TestEnvironmentOnlyApproval:
    """GAIA_AUTO_APPROVE_TOOLS skips prompts; it never widened what a run executes."""

    def test_a_confirmable_command_is_refused_when_only_the_env_approves(
        self, env_pre_approves
    ):
        error = refusal("rm notes.txt")
        assert error is not None
        assert "GAIA_AUTO_APPROVE_TOOLS" in error["hint"]

    def test_the_no_prompt_list_still_runs_under_the_env(self, env_pre_approves):
        assert refusal("git status") is None

    def test_a_host_opt_in_is_a_person_deciding(self, env_pre_approves):
        """The TUI's full access sets the handler attribute, not the env var."""
        assert refusal("rm notes.txt", host_opt_in=True) is None

    def test_the_execution_path_refuses_too(self, env_pre_approves, tmp_path):
        """Defence in depth: a direct tool call never skips the same rule."""
        from gaia.agents.base.tools import get_tool_metadata

        host = _Host()
        host.register_shell_tools()
        run = get_tool_metadata("run_shell_command")["function"]

        result = run(command="touch made.txt", working_directory=str(tmp_path))
        assert result["status"] == "error"
        assert not (tmp_path / "made.txt").exists()

    def test_a_host_opt_in_runs_it(self, env_pre_approves, tmp_path):
        from gaia.agents.base.tools import get_tool_metadata

        host = _Host(host_opt_in=True)
        host.register_shell_tools()
        run = get_tool_metadata("run_shell_command")["function"]

        result = run(command="touch made.txt", working_directory=str(tmp_path))
        assert result["status"] == "success", result
        assert (tmp_path / "made.txt").exists()


# Full access: shell gates (#3373, #3374)
#
# The switch is the sidecar's --full-access / TUI /full-access, which answers
# every confirmation yes; these tests cover the shell gates it lifts with it.
# Inside the workspace nothing is allow-listed any more. What still refuses is
# the tier no approval can authorize, plus the path checks at execution.
#
# Every case asserts BOTH states. The default tier is what ships; full access
# is what the user turned on deliberately.
# ---------------------------------------------------------------------------

#: Binaries the default tier asks about and full access runs unasked.
CONFIRMABLE_BINARY_SAMPLES = [
    "npm",
    "node",
    "make",
    "cmake",
    "go",
    "cargo",
    "sed",
    "awk",
    "curl",
    "sleep",
    "cp",
    "mv",
    "rm",
    "mkdir",
    "touch",
    "evil_binary",
]


class TestConfirmableBinariesUnderFullAccess:
    @pytest.mark.parametrize("binary", CONFIRMABLE_BINARY_SAMPLES)
    def test_need_approval_with_full_access_off(self, binary):
        assert binary not in ALLOWED_COMMANDS
        result = check(f"{binary} --version", full_access=False)
        assert result is not None, f"{binary} leaked into the no-prompt tier"
        assert result["tier"] == TIER_CONFIRM

    @pytest.mark.parametrize("binary", CONFIRMABLE_BINARY_SAMPLES)
    def test_run_with_full_access_on(self, binary):
        assert check(f"{binary} --version", full_access=True) is None

    def test_deleting_a_file_is_not_refused(self):
        # All permissions means deleting works; the workspace boundary is
        # enforced at execution by the path checks, not by naming rm.
        assert check("rm notes.txt", full_access=True) is None
        assert check("rm -rf build", full_access=True) is None

    def test_default_refusal_text_unchanged(self):
        result = check("make build", full_access=False)
        assert "not in the allowed list for security reasons" in result["error"]

    def test_gh_default_refusal_still_points_at_the_skill_grant(self):
        result = check("gh issue list", full_access=False)
        assert "shell:execute:gh" in result["error"]


class TestFullAccessKeepsTheRefuseTier:
    """What a yes cannot authorize stays refused with full access on."""

    @pytest.mark.parametrize(
        "command",
        [
            "gh auth token",
            "git -c core.pager=evil.sh status",
            "git --exec-path=/tmp/evil status",
            "powershell -EncodedCommand aQBlAHgA",
            "cat 'unterminated",
        ],
    )
    def test_refused_in_both_modes(self, command):
        for full_access in (False, True):
            result = check(command, full_access=full_access)
            assert result is not None, (command, full_access)
            assert result["tier"] == TIER_REFUSE

    def test_read_only_commands_still_allowed(self):
        assert check("ls -la", full_access=True) is None
        assert check("grep -r foo src/", full_access=True) is None


class TestOperatorsUnderFullAccess:
    """Chaining (`&&`, `||`, `;`, `|`) is allowed in both modes — each segment
    goes through the allowlist on its own. What full access adds is the rest:
    redirections, backgrounding, substitution, newlines and heredocs."""

    def test_a_redirect_is_refused_by_default(self):
        result = check("cd . && ls > out.txt", full_access=False)
        assert result is not None
        assert "Shell operators" in result["error"]

    def test_compound_allowed_under_full_access(self):
        assert check("cd . && ls | head -3", full_access=True) is None

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
    def test_sequences_parse_under_full_access(self, command):
        assert check(command, full_access=True) is None

    @pytest.mark.parametrize(
        "command",
        [
            "make build > out.txt",
            "echo one & echo two",
            "echo `whoami`",
            "echo one\necho two",
        ],
    )
    def test_the_operators_full_access_adds_are_refused_by_default(self, command):
        result = check(command, full_access=False)
        assert result is not None
        assert "Shell operators" in result["error"]

    def test_a_pipe_is_refused_for_its_binary_not_for_the_pipe(self):
        # Pipes were never blocked. `pytest -q | tail -20` is refused with
        # bypass off because pytest is ungranted, NOT by the operator block —
        # #3373 cites it as an operator case and is wrong about that.
        result = check("pytest -q | tail -20", full_access=False)
        assert result is not None
        assert "Shell operators" not in result["error"]
        assert "shell:execute:pytest" in result["error"]


class TestHeredocsUnderFullAccess:
    """A heredoc body is input to its command, not a command to validate."""

    HEREDOC = "python3 - <<'EOF'\nprint(1+1)\nprint('it\\'s')\nEOF"

    def test_refused_by_default(self):
        assert check(self.HEREDOC, full_access=False)["tier"] == TIER_REFUSE

    def test_runs_under_full_access(self):
        assert check(self.HEREDOC, full_access=True) is None

    def test_the_body_is_not_walked_as_commands(self):
        assert segments_for(self.HEREDOC, full_access=True) == [
            ["python3", "-", "<<", "EOF"]
        ]

    def test_commands_after_the_terminator_are_still_walked(self):
        command = "cat <<EOF\nbody\nEOF\ngh auth token"
        assert check(command, full_access=True)["tier"] == TIER_REFUSE

    def test_a_quoted_marker_opens_no_body(self):
        # `<<` inside quotes is data, so the next line is a real command.
        command = 'echo "<< X"\ngh auth token\nX'
        assert check(command, full_access=True)["tier"] == TIER_REFUSE

    def test_a_here_string_has_no_body(self):
        command = 'cat <<< "hi"\ngh auth token'
        assert check(command, full_access=True)["tier"] == TIER_REFUSE

    def test_an_unterminated_body_is_still_checked(self):
        command = "cat <<EOF\ngh auth token"
        assert check(command, full_access=True)["tier"] == TIER_REFUSE


class TestPerSegmentWalkSurvivesFullAccess:
    """The per-segment walk is what produces the audit record; it must not be
    short-circuited just because the operators now parse."""

    def test_a_second_segment_is_refused_by_default_for_its_binary(self):
        result = check("ls && gh auth token", full_access=False)
        assert result is not None
        assert result["tier"] == TIER_REFUSE
        assert "Shell operators" not in result["error"]

    def test_a_refused_second_segment_refuses_the_whole_command(self):
        result = check("ls && gh auth token", full_access=True)
        assert result is not None
        assert result["tier"] == TIER_REFUSE

    def test_a_refused_pipe_segment_is_refused_in_both_modes(self):
        assert check("ls | gh auth token", full_access=False) is not None
        assert check("ls | gh auth token", full_access=True) is not None

    def test_every_segment_is_recorded_under_full_access(self):
        segments = segments_for("cd . && ls | head -3", full_access=True)
        assert [seg[0] for seg in segments] == ["cd", "ls", "head"]

    def test_pipe_segments_recorded_by_default(self):
        segments = segments_for("ls | grep foo | head -3", full_access=False)
        assert [seg[0] for seg in segments] == ["ls", "grep", "head"]


class TestNewlineSeparatesSegmentsUnderFullAccess:
    """A newline reaches the shell as a command separator, so the segment walk
    has to treat it as one, or `ls\\ngh auth token` is walked as a single `ls`."""

    def test_newline_does_not_smuggle_a_refused_invocation(self):
        result = check("ls\ngh auth token", full_access=True)
        assert result is not None
        assert result["tier"] == TIER_REFUSE

    def test_each_line_is_recorded_as_its_own_segment(self):
        segments = segments_for("ls\necho hi", full_access=True)
        assert [seg[0] for seg in segments] == ["ls", "echo"]

    @pytest.mark.parametrize(
        "command",
        [
            "ls;\ngh auth token",
            "ls &&\ngh auth token",
            "ls |\ngh auth token",
            "ls\n;gh auth token",
        ],
    )
    def test_newline_fused_to_another_operator_still_separates(self, command):
        # shlex emits a run of adjacent punctuation as ONE token, so these
        # arrive as ';\\n', '&&\\n', '|\\n', '\\n;' rather than two tokens.
        result = check(command, full_access=True)
        assert result is not None
        assert result["tier"] == TIER_REFUSE

    def test_blank_lines_do_not_create_empty_segments(self):
        segments = segments_for("ls\n\n\necho hi\n", full_access=True)
        assert [seg[0] for seg in segments] == ["ls", "echo"]

    def test_a_quoted_newline_is_data_not_a_separator(self):
        segments = segments_for('echo "a\nb"', full_access=True)
        assert segments == [["echo", "a\nb"]]
        assert check('echo "a\nb"', full_access=True) is None


class TestReadOnlySubGuardsLiftUnderFullAccessOnly:
    """The find/sort/uniq/git/PowerShell guards all encode "this binary may not
    write" — the assumption full access drops. Each must still hold with the
    flag off."""

    COMMANDS = [
        "git commit -m msg",
        "find /tmp -name x -delete",
        "sort -o /tmp/canary /etc/hostname",
        "uniq in.txt out.txt",
        "powershell -Command Remove-Item C:/important",
    ]

    @pytest.mark.parametrize("command", COMMANDS)
    def test_gated_by_default(self, command):
        assert check(command, full_access=False) is not None

    @pytest.mark.parametrize("command", COMMANDS)
    def test_allowed_under_full_access(self, command):
        assert check(command, full_access=True) is None


class TestFullAccessIsOffByDefault:
    def test_host_with_no_console_has_no_full_access(self):
        class Bare:
            full_access_active = ShellToolsMixin.full_access_active

        assert Bare().full_access_active() is False

    def test_stock_output_handler_has_no_full_access(self):
        from gaia.agents.base.console import OutputHandler

        assert OutputHandler.full_access is False

    def test_auto_approve_alone_does_not_lift_the_shell_gates(self):
        # An unattended harness that pre-approves prompts (GAIA_AUTO_APPROVE_TOOLS
        # or auto_approve_gated_tools) must NOT also inherit an unguarded shell.
        class ApproveOnly:
            auto_approve_gated_tools = True

        host = _Shell(full_access=False)
        host.console = ApproveOnly()
        assert host.full_access_active() is False
        assert host._validate_shell_command("make build")[0] is not None

    def test_full_access_is_a_host_opt_in_not_the_environment(self, monkeypatch):
        monkeypatch.setattr(
            "gaia.agents.base.console.auto_approve_env_enabled", lambda: True
        )
        assert _Shell(full_access=True)._approval_is_environment_only() is False

    def test_toggling_the_console_flips_the_gates_live(self):
        # /full-access off mid-session must take effect on the next command,
        # which is why the mode is read live rather than cached on the agent.
        host = _Shell(full_access=False)
        assert host._validate_shell_command("make build")[0] is not None
        host.console.full_access = True
        assert host._validate_shell_command("make build")[0] is None
        host.console.full_access = False
        assert host._validate_shell_command("make build")[0] is not None

    def test_validate_command_defaults_to_the_gated_tier(self):
        # The three-positional-argument call every existing test uses.
        assert ShellToolsMixin._validate_command("pytest", ["pytest"], "pytest")


# ---------------------------------------------------------------------------
# The executor, for real (#3373)
#
# Everything above stops at validation. These run the tool end to end and
# actually spawn a process, because validation passing is not the same as the
# command working: the operators only reach a shell if the executor asks for
# one, and the rate-limit deque is created lazily by a check full access skips.
# ---------------------------------------------------------------------------


class _ExecHost(ShellToolsMixin):
    """A host with a real tool registry, so run_shell_command can be called."""

    def __init__(self, full_access: bool):
        self.console = _Console(full_access)
        self.debug = False
        self.registered = {}

    def register(self, fn, name):
        self.registered[name] = fn


@pytest.fixture
def shell_tool(monkeypatch):
    """Return a factory for the real ``run_shell_command`` closure."""

    def build(full_access: bool, wait_cap=None):
        host = _ExecHost(full_access)
        if wait_cap is not None:
            # No patience for pacing, so the per-minute cap refuses on the spot
            # instead of sleeping the test out.
            host.max_rate_limit_wait_seconds = wait_cap
        captured = {}

        def fake_tool(**kwargs):
            def wrap(fn):
                captured[kwargs.get("name", fn.__name__)] = fn
                return fn

            return wrap

        import gaia.agents.base.tools as tools_mod

        monkeypatch.setattr(tools_mod, "tool", fake_tool)
        host.register_shell_tools()
        return captured["run_shell_command"]

    return build


class TestExecutorUnderFullAccess:
    def test_a_compound_command_actually_runs(self, shell_tool, tmp_path):
        """The whole point of #3373: `a && b` reaches a shell and succeeds."""
        run = shell_tool(full_access=True)

        result = run(
            "cd . && echo first && echo second", working_directory=str(tmp_path)
        )

        assert result["status"] == "success", result
        assert result["return_code"] == 0
        assert "first" in result["stdout"]
        assert "second" in result["stdout"]

    def test_the_line_never_reaches_a_shell_whole_by_default(
        self, shell_tool, tmp_path
    ):
        """Chaining runs step by step by default, so the text a shell would
        have to read whole — here a heredoc body — never gets there."""
        run = shell_tool(full_access=False)

        result = run(
            "python3 - <<'EOF'\nprint(6 * 7)\nEOF", working_directory=str(tmp_path)
        )

        assert result["status"] == "error"
        assert "Shell operators" in result["error"]

    def test_the_rate_limit_is_lifted(self, shell_tool, tmp_path):
        """More than max_commands_per_10_seconds back to back, no refusal.

        Also covers the deque: _check_rate_limit is what lazily creates it, and
        full access skips that call — recording into it anyway raised AttributeError
        on the very first command.
        """
        run = shell_tool(full_access=True)

        for i in range(5):
            result = run(f"echo run{i}", working_directory=str(tmp_path))
            assert result["status"] == "success", result
            assert not result.get("rate_limited")

    def test_the_rate_limit_still_applies_by_default(self, shell_tool, tmp_path):
        run = shell_tool(full_access=False, wait_cap=0)

        # echo is allowlisted, so it skips the burst limit (#3737) but not the
        # per-minute cap of 10.
        results = [run("echo hi", working_directory=str(tmp_path)) for _ in range(12)]

        assert any(r.get("rate_limited") for r in results)

    def test_every_execution_is_audited_with_its_arguments(
        self, shell_tool, tmp_path, monkeypatch
    ):
        records = []
        monkeypatch.setattr(
            "gaia.security.audit_shell_command",
            lambda **kw: records.append(kw),
        )
        run = shell_tool(full_access=True)

        run("cd . && echo audited", working_directory=str(tmp_path))

        assert len(records) == 1
        assert records[0]["command"] == "cd . && echo audited"
        assert records[0]["segments"] == [["cd", "."], ["echo", "audited"]]
        assert records[0]["mode"] == "full_access"

    def test_a_refused_binary_is_never_executed_or_audited(
        self, shell_tool, tmp_path, monkeypatch
    ):
        records = []
        monkeypatch.setattr(
            "gaia.security.audit_shell_command",
            lambda **kw: records.append(kw),
        )
        run = shell_tool(full_access=True)

        result = run("echo hi && gh auth token", working_directory=str(tmp_path))

        assert result["status"] == "error"
        assert records == [], "a refused command must not reach the audit trail"

    def test_a_heredoc_actually_runs(self, shell_tool, tmp_path):
        run = shell_tool(full_access=True)

        result = run(
            "python3 - <<'EOF'\nprint(6 * 7)\nEOF", working_directory=str(tmp_path)
        )

        assert result["status"] == "success", result
        assert result["stdout"].strip() == "42"

    def test_rm_deletes_a_file_in_the_workspace(self, shell_tool, tmp_path):
        target = tmp_path / "somefile.txt"
        target.write_text("x")
        run = shell_tool(full_access=True)

        result = run("rm somefile.txt", working_directory=str(tmp_path))

        assert result["status"] == "success", result
        assert not target.exists()


class TestFullAccessStaysInsideTheWorkspace:
    """No allowlist inside the workspace is not a way out of it."""

    @pytest.fixture
    def run(self, tmp_path, monkeypatch):
        from gaia.security import PathValidator

        workspace = tmp_path / "ws"
        workspace.mkdir()
        host = _ExecHost(True)
        host.path_validator = PathValidator([str(workspace)])
        captured = {}

        def fake_tool(**kwargs):
            def wrap(fn):
                captured[kwargs.get("name", fn.__name__)] = fn
                return fn

            return wrap

        import gaia.agents.base.tools as tools_mod

        monkeypatch.setattr(tools_mod, "tool", fake_tool)
        host.register_shell_tools()
        return workspace, captured["run_shell_command"]

    def test_an_argument_outside_is_refused(self, run, tmp_path):
        workspace, run_shell = run
        outside = tmp_path / "outside.txt"
        outside.write_text("keep")

        result = run_shell(f"rm {outside}", working_directory=str(workspace))

        assert result["status"] == "error"
        assert "Access denied" in result["error"]
        assert outside.exists()

    def test_a_working_directory_outside_is_refused(self, run, tmp_path):
        _workspace, run_shell = run

        result = run_shell("ls", working_directory=str(tmp_path))

        assert result["status"] == "error"
        assert "not in allowed paths" in result["error"]

    def test_a_redirect_outside_is_refused(self, run, tmp_path):
        workspace, run_shell = run
        target = tmp_path / "escaped.txt"

        result = run_shell(f"echo x > {target}", working_directory=str(workspace))

        assert result["status"] == "error"
        assert not target.exists()


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
