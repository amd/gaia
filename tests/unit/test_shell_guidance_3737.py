# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""``git -C <path> status`` is a read, not a ``-C`` subcommand (#3737).

Git's global options sit before the subcommand, so ``cmd_parts[1]`` is ``-C``,
not ``status`` — the read was refused as an unknown subcommand. The walk over
the global flags fixes that, and the paths those flags name get the same
allowed-paths check ``working_directory`` gets, so ``-C`` is not a way out of
the sandbox.
"""

import shlex
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

    def test_an_attached_short_option_says_how_to_rewrite_it(self):
        # Valid git, but the walk matches whole tokens — refusing it is fine,
        # refusing it without naming the fix is what burned a turn.
        error = _split("git -C/tmp status")["error"]
        assert "'-C /tmp'" in error
        assert "not recognized" not in error

    def test_an_attached_forbidden_option_gives_the_real_reason(self):
        error = _split("git -ccore.fsmonitor=evil status")["error"]
        assert "'-c' is not allowed" in error

    def test_a_genuinely_unknown_option_still_says_so(self):
        error = _split("git --frobnicate status")["error"]
        assert "'--frobnicate' is not recognized" in error

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
        result = run(command=f"git -C {shlex.quote(str(tmp_path))} status")
        # Not a repo, so git exits non-zero — but it ran, and was not refused.
        assert result["status"] == "success", result

    @pytest.mark.parametrize("host_cls", [_Host, _HostWithoutArgScan])
    def test_dash_c_outside_allowed_paths_is_refused(self, tmp_path, host_cls):
        allowed = tmp_path / "allowed"
        outside = tmp_path / "outside"
        allowed.mkdir()
        outside.mkdir()
        run = _run_tool(host_cls(allowed))
        result = run(command=f"git -C {shlex.quote(str(outside))} status")
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
        result = run(command=f"git --work-tree={shlex.quote(str(tmp_path))} status")
        assert result["status"] == "error"
        assert "--work-tree" in result["error"]
