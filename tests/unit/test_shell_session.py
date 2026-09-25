# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for the persistent shell session's cwd tracking (issue #3380).

Scope: this session tracks cwd only -- it holds no execution method and no
environment state. ``run_shell_command``'s existing subprocess-per-step engine
(``shell_tools.py``) is unchanged and already covered elsewhere; these tests
only cover what ``ShellSession`` itself adds.
"""

import pytest

from gaia.agents.tools.shell_session import ShellSession, ShellSessionClosed


@pytest.fixture
def session(tmp_path):
    shell = ShellSession(start_cwd=str(tmp_path))
    yield shell
    shell.close()


class TestSetCwd:
    def test_set_cwd_moves_the_session(self, session, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()

        assert session.set_cwd(str(sub)) is True
        assert session.cwd == str(sub.resolve())

    def test_set_cwd_rejects_a_non_directory(self, session, tmp_path):
        before = session.cwd

        assert session.set_cwd(str(tmp_path / "does-not-exist")) is False
        assert session.cwd == before

    def test_cwd_guard_refuses_a_directory_change(self, tmp_path):
        off_limits = tmp_path / "off-limits"
        off_limits.mkdir()
        allowed = str(tmp_path)
        shell = ShellSession(
            start_cwd=allowed,
            cwd_guard=lambda path: "off-limits" not in path.replace("\\", "/"),
        )
        try:
            assert shell.set_cwd(str(off_limits)) is False
            assert shell.cwd == allowed
        finally:
            shell.close()

    def test_cwd_guard_allows_a_permitted_directory(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        shell = ShellSession(start_cwd=str(tmp_path), cwd_guard=lambda path: True)
        try:
            assert shell.set_cwd(str(sub)) is True
            assert shell.cwd == str(sub.resolve())
        finally:
            shell.close()


class TestReset:
    def test_reset_restores_the_starting_directory(self, session, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        session.set_cwd(str(sub))

        session.reset()

        assert session.cwd == str(tmp_path.resolve())


class TestTeardown:
    def test_close_makes_set_cwd_an_error(self, tmp_path):
        shell = ShellSession(start_cwd=str(tmp_path))

        shell.close()

        assert shell.closed is True
        with pytest.raises(ShellSessionClosed):
            shell.set_cwd(str(tmp_path))
