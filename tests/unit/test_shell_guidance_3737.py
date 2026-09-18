# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The shell tool has to tell the model its rules, and not refuse ordinary reads.

342 of 2,001 tool calls in a benchmark sweep were shell refusals (#3737): the
operator ban was never stated up front, ``git -C <path> status`` was read as a
``-C`` subcommand, and a burst of read-only ``ls``/``cat`` hit the 3-per-10s
limit.
"""

import time
from pathlib import Path

import pytest

from gaia.agents.base.tools import get_tool_metadata
from gaia.agents.tools.shell_tools import ShellToolsMixin, _git_path_flag_values

validate = ShellToolsMixin._validate_command


class _Validator:
    def __init__(self, root: Path):
        self.root = str(root.resolve())

    def is_path_allowed(self, path: str, prompt_user: bool = True) -> bool:
        resolved = str(Path(path).resolve())
        return resolved == self.root or resolved.startswith(self.root + "/")


class _Host(ShellToolsMixin):
    debug = False

    def __init__(self, root: Path):
        super().__init__()
        self.path_validator = _Validator(root)


class _HostWithoutArgScan(ShellToolsMixin):
    """Only ``_is_path_allowed`` — the argument path scan does not run here."""

    debug = False

    def __init__(self, root: Path):
        super().__init__()
        self._validator = _Validator(root)

    def _is_path_allowed(self, path: str) -> bool:
        return self._validator.is_path_allowed(path)


def _run_tool(host):
    host.register_shell_tools()
    return get_tool_metadata("run_shell_command")["function"]


def _split(command: str):
    parts = command.split()
    return validate(parts[0], parts, command)


class TestTheDescriptionStatesTheRules:
    @pytest.fixture
    def description(self, tmp_path):
        _run_tool(_Host(tmp_path))
        return get_tool_metadata("run_shell_command")["description"]

    def test_it_names_the_operator_ban(self, description):
        for operator in ("&&", "||", ";", ">", "<", "$(", "heredoc"):
            assert operator in description

    def test_it_says_pipes_are_allowed(self, description):
        assert "Pipes (|) are allowed" in description

    def test_it_points_at_working_directory_instead_of_cd(self, description):
        assert "working_directory" in description
        assert "cd DIR && cmd" in description

    def test_it_says_read_only_and_where_python_goes(self, description):
        assert "read-only" in description
        assert "execute_python_file" in description

    def test_it_says_a_skill_grant_can_widen_the_list(self, description):
        # The allowlist reads as closed, but skills grant gh/pytest and
        # skill_grant_covers_call runs them unprompted; the description is what
        # the model reads every turn, so it has to say so.
        assert "skill" in description.lower()
        assert "gh" in description
        assert "pytest" in description

    def test_working_directory_argument_says_use_it_instead_of_cd(self, tmp_path):
        _run_tool(_Host(tmp_path))
        props = get_tool_metadata("run_shell_command")["parameters"]
        assert "instead of cd" in props["working_directory"]["description"]


class TestOperatorRefusalNamesWorkingDirectory:
    def test_cd_chain_hint_names_the_parameter_and_the_rest(self, tmp_path):
        error, _ = _Host(tmp_path)._validate_shell_command("cd /repo && git status")
        assert error["status"] == "error"
        assert error["executed"] is False
        assert "working_directory='/repo'" in error["hint"]
        assert "command='git status'" in error["hint"]

    def test_quoted_cd_path(self, tmp_path):
        error, _ = _Host(tmp_path)._validate_shell_command('cd "my dir" && ls -la')
        assert "working_directory='my dir'" in error["hint"]

    def test_pre_prompt_refusal_carries_the_hint_too(self, tmp_path):
        error = _Host(tmp_path).policy_refusal_for_call(
            "run_shell_command", {"command": "cd src && ls"}
        )
        assert "working_directory" in error["hint"]

    def test_other_operator_refusals_keep_the_generic_hint(self, tmp_path):
        error, _ = _Host(tmp_path)._validate_shell_command("ls && pwd")
        assert error is not None
        assert "working_directory" not in error.get("hint", "")

    def test_no_hint_when_the_tail_is_itself_refused(self, tmp_path):
        error, _ = _Host(tmp_path)._validate_shell_command("cd /repo && rm x")
        assert error is not None
        assert "rm x" not in error.get("hint", "")

    def test_no_hint_when_the_tail_still_chains(self, tmp_path):
        error, _ = _Host(tmp_path)._validate_shell_command("cd /a && ls && pwd")
        assert error is not None
        assert "&&" not in error.get("hint", "")

    def test_the_tool_returns_the_hint(self, tmp_path):
        run = _run_tool(_Host(tmp_path))
        result = run(command=f"cd {tmp_path} && ls")
        assert result["status"] == "error"
        assert "working_directory" in result["hint"]


class TestGitGlobalFlags:
    @pytest.mark.parametrize(
        "command",
        [
            "git -C /repo status",
            "git --no-pager log --oneline",
            "git --git-dir=/repo/.git --work-tree=/repo status",
            "git -C /repo --no-pager diff HEAD",
        ],
    )
    def test_read_only_subcommand_behind_global_flags_is_allowed(self, command):
        assert _split(command) is None

    def test_write_subcommand_behind_dash_c_is_still_refused(self):
        result = _split("git -C /repo push origin main")
        assert "push" in result["error"]

    @pytest.mark.parametrize(
        "command",
        ["git -c core.fsmonitor=evil status", "git --exec-path=/tmp status"],
    )
    def test_config_and_exec_path_overrides_are_refused(self, command):
        # -c can set core.fsmonitor / core.pager, which run a command on `status`.
        assert _split(command) is not None

    def test_path_flags_resolve_like_git(self, tmp_path):
        values = _git_path_flag_values(
            ["git", "-C", "a", "--git-dir=b", "-C", "c", "status"], str(tmp_path)
        )
        root = tmp_path.resolve()
        assert values == [
            ("-C", str(root / "a")),
            ("--git-dir", str(root / "a" / "b")),
            ("-C", str(root / "a" / "c")),
        ]

    def test_dash_c_inside_allowed_paths_runs(self, tmp_path):
        run = _run_tool(_Host(tmp_path))
        result = run(command=f"git -C {tmp_path} status")
        # Not a repo, so git exits non-zero — but it ran, and was not refused.
        assert result["status"] == "success", result

    @pytest.mark.parametrize("host_cls", [_Host, _HostWithoutArgScan])
    def test_dash_c_outside_allowed_paths_is_refused(self, tmp_path, host_cls):
        allowed = tmp_path / "allowed"
        outside = tmp_path / "outside"
        allowed.mkdir()
        outside.mkdir()
        run = _run_tool(host_cls(allowed))
        result = run(command=f"git -C {outside} status")
        assert result["status"] == "error"
        assert result["executed"] is False
        assert "Access denied" in result["error"]

    def test_relative_dash_c_escape_is_refused_without_the_arg_scan(self, tmp_path):
        allowed = tmp_path / "allowed"
        (tmp_path / "outside").mkdir()
        allowed.mkdir()
        run = _run_tool(_HostWithoutArgScan(allowed))
        result = run(command="git -C ../outside status", working_directory=str(allowed))
        assert result["status"] == "error"
        assert "git -C" in result["error"]

    def test_work_tree_outside_allowed_paths_is_refused(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        run = _run_tool(_HostWithoutArgScan(allowed))
        result = run(command=f"git --work-tree={tmp_path} status")
        assert result["status"] == "error"
        assert "--work-tree" in result["error"]


class TestAllowlistedCommandsSkipTheBurstLimit:
    @pytest.mark.parametrize(
        "command", ["ls -la", "cat a.txt | head -5", "git -C /repo log", "grep -rn x ."]
    )
    def test_allowlisted_classification(self, tmp_path, command):
        assert _Host(tmp_path)._is_allowlisted_command(command) is True

    @pytest.mark.parametrize(
        "command",
        ["git push origin main", "rm a.txt", "gh issue list", "ls && pwd", "ls > f"],
    )
    def test_not_allowlisted(self, tmp_path, command):
        assert _Host(tmp_path)._is_allowlisted_command(command) is False

    def test_a_burst_of_allowlisted_commands_is_not_throttled(self, tmp_path):
        run = _run_tool(_Host(tmp_path))
        for _ in range(6):
            result = run(command="ls", working_directory=str(tmp_path))
            assert result["status"] == "success", result

    def test_non_allowlisted_commands_are_still_burst_limited(self, tmp_path):
        host = _Host(tmp_path)
        run = _run_tool(host)
        now = time.time()
        host.shell_command_times.extend([now, now, now])
        result = run(command="rm a.txt", working_directory=str(tmp_path))
        assert result["rate_limited"] is True
        assert result["wait_time_seconds"] > 0
        assert result["executed"] is False
        assert "per 10 seconds" in result["error"]

    def test_allowlisted_commands_still_hit_the_per_minute_cap(self, tmp_path):
        host = _Host(tmp_path)
        run = _run_tool(host)
        now = time.time()
        host.shell_command_times.extend([now] * host.max_commands_per_minute)
        result = run(command="ls", working_directory=str(tmp_path))
        assert result["rate_limited"] is True
        assert result["wait_time_seconds"] > 0
        assert "per minute" in result["error"]

    def test_check_rate_limit_default_keeps_the_burst_limit(self, tmp_path):
        host = _Host(tmp_path)
        now = time.time()
        host.shell_command_times.extend([now, now, now])
        assert host._check_rate_limit()[0] is False
        assert host._check_rate_limit(allowlisted=True)[0] is True
