# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for shell command guardrails in ShellToolsMixin._validate_command."""

import pytest

from gaia.agents.tools.shell_tools import (
    ALLOWED_COMMANDS,
    DANGEROUS_SHELL_OPERATORS,
    DEVELOPER_COMMANDS,
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

    def __init__(self, bypass: bool):
        self.bypass_permissions = bypass


class _Shell(ShellToolsMixin):
    """A bare host for the mixin, wired to a console in a known bypass state."""

    def __init__(self, bypass: bool):
        self.console = _Console(bypass)


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

    def build(bypass: bool):
        host = _ExecHost(bypass)
        captured = {}

        def fake_tool(**kwargs):
            def wrap(fn):
                captured[kwargs["name"]] = fn
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
