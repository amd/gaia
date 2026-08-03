# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for shell command guardrails in ShellToolsMixin._validate_command."""

from gaia.agents.tools.shell_tools import (
    DANGEROUS_SHELL_OPERATORS,
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
