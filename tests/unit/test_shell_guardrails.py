# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for shell command guardrails in ShellToolsMixin._validate_command."""

import pytest

from gaia.agents.tools.shell_tools import (
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

    @pytest.mark.parametrize(
        "command", ["cat a && rm b", "echo hi > f", "cat 'unterminated"]
    )
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
        "command", ["git -c core.pager=evil.sh status", "cat a && rm b"]
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
