# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``run_shell_command``'s ``cd`` persists to the NEXT separate call (#3380).

Every call used to start from the process cwd, so ``cd build`` in one call was
invisible to the next. Real host, real subprocesses, ``tmp_path`` — a mock
here would prove the code was called, not that the directory it changed to
actually carries over.
"""

import os

from gaia.agents.base.tools import get_tool_metadata
from gaia.agents.tools.shell_tools import ShellToolsMixin


class _Host(ShellToolsMixin):
    """Minimal host: the mixin only needs its own __init__ for rate limiting."""


def _tools(host):
    host.register_shell_tools()
    return (
        get_tool_metadata("run_shell_command")["function"],
        get_tool_metadata("get_shell_state")["function"],
        get_tool_metadata("reset_shell_session")["function"],
    )


class TestCwdPersistsAcrossCalls:
    def test_cd_survives_to_the_next_separate_call(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        run, state, _reset = _tools(_Host())

        result = run(f'cd "{sub}"')

        assert result["status"] == "success"
        assert os.path.realpath(state()["cwd"]) == os.path.realpath(str(sub))

    def test_a_later_call_with_no_working_directory_runs_where_cd_left_off(
        self, tmp_path
    ):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "marker.txt").write_text("here")
        run, _state, _reset = _tools(_Host())

        run(f'cd "{sub}"')
        # No working_directory override: must run in the session's cwd, not
        # the process cwd, i.e. it must see the file only "sub" contains.
        result = run("ls" if os.name != "nt" else "dir")

        assert "marker.txt" in result.get("stdout", "")

    def test_working_directory_argument_is_one_shot(self, tmp_path):
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        run, state, _reset = _tools(_Host())
        before = state()["cwd"]

        run("cd ." if os.name == "nt" else "pwd", working_directory=str(elsewhere))

        # An explicit working_directory never touches the session.
        assert state()["cwd"] == before

    def test_reset_shell_session_returns_to_the_starting_directory(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        run, state, reset = _tools(_Host())
        before = state()["cwd"]

        run(f'cd "{sub}"')
        assert os.path.realpath(state()["cwd"]) == os.path.realpath(str(sub))

        reset()

        assert state()["cwd"] == before

    def test_a_denied_cd_target_is_not_checkpointed(self, tmp_path):
        """The session cwd guard refuses what the path policy would refuse."""
        off_limits = tmp_path / "off-limits"
        off_limits.mkdir()

        class _Confined(ShellToolsMixin):
            def __init__(self, root):
                super().__init__()
                self.path_validator = _RootValidator(root)

        class _RootValidator:
            def __init__(self, root):
                self.root = str(root)

            def is_path_allowed(self, path: str) -> bool:
                root = os.path.realpath(self.root)
                real = os.path.realpath(path)
                return real == root or real.startswith(root + os.sep)

        allowed_root = tmp_path / "allowed"
        allowed_root.mkdir()
        run, state, _reset = _tools(_Confined(str(allowed_root)))
        before = state()["cwd"]

        run(f'cd "{off_limits}"')

        assert state()["cwd"] == before
