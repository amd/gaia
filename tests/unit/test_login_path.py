# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""An app launched from Finder has no Homebrew on PATH; GAIA reads the user's
login PATH once so the agent's shell sees the same tools their terminal does."""

import os
import shutil
import stat
import subprocess
import sys
import types

import pytest

from gaia import env
from gaia.env import (
    LoginPathError,
    apply_login_path,
    child_env,
    merge_paths,
    read_login_path,
)

BEGIN, END = env._PATH_BEGIN, env._PATH_END
posix_only = pytest.mark.skipif(os.name == "nt", reason="needs a POSIX shell")
SEP = os.pathsep


def paths(*entries):
    return SEP.join(entries)


def completed(stdout="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    monkeypatch.setattr(env, "_login_path_applied", False)


class TestReadLoginPath:
    def test_reads_the_value_between_the_markers(self, monkeypatch):
        out = f"{BEGIN}/opt/homebrew/bin:/usr/bin{END}"
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: completed(out))
        assert read_login_path(shell="/bin/zsh") == "/opt/homebrew/bin:/usr/bin"

    def test_rc_file_noise_before_and_after_is_ignored(self, monkeypatch):
        out = f"Welcome back!\nnvm loaded\n{BEGIN}/a:/b{END}\nbye\n"
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: completed(out))
        assert read_login_path(shell="/bin/zsh") == "/a:/b"

    def test_an_rc_file_that_echoes_the_markers_cannot_win(self, monkeypatch):
        out = f"{BEGIN}/evil{END}\n{BEGIN}/real{END}"
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: completed(out))
        assert read_login_path(shell="/bin/zsh") == "/real"

    def test_missing_markers_is_an_error_naming_the_exit_code(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: completed("", 3))
        with pytest.raises(LoginPathError, match="exited 3 without printing PATH"):
            read_login_path(shell="/bin/zsh")

    def test_an_empty_path_is_an_error(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **k: completed(f"{BEGIN}{END}")
        )
        with pytest.raises(LoginPathError, match="empty PATH"):
            read_login_path(shell="/bin/zsh")

    def test_a_hung_shell_is_an_error_naming_the_timeout(self, monkeypatch):
        def hang(*a, **k):
            raise subprocess.TimeoutExpired("zsh", k["timeout"])

        monkeypatch.setattr(subprocess, "run", hang)
        with pytest.raises(LoginPathError, match="did not finish within 2s"):
            read_login_path(shell="/bin/zsh", timeout=2)

    def test_a_shell_that_cannot_start_is_an_error(self, monkeypatch):
        def boom(*a, **k):
            raise FileNotFoundError("no such shell")

        monkeypatch.setattr(subprocess, "run", boom)
        with pytest.raises(LoginPathError, match="could not run"):
            read_login_path(shell="/nope")

    def test_runs_an_interactive_login_shell_with_no_stdin(self, monkeypatch):
        seen = {}

        def spy(cmd, **kw):
            seen.update(cmd=cmd, **kw)
            return completed(f"{BEGIN}/a{END}")

        monkeypatch.setattr(subprocess, "run", spy)
        read_login_path(shell="/bin/zsh")
        assert seen["cmd"][:2] == ["/bin/zsh", "-ilc"]
        assert seen["stdin"] == subprocess.DEVNULL
        assert seen["timeout"] == env.LOGIN_PATH_TIMEOUT_S

    @posix_only
    def test_a_real_shell_with_a_noisy_startup_file(self, tmp_path):
        script = tmp_path / "fakeshell"
        script.write_text(
            "#!/bin/sh\n"
            'echo "banner from rc file"\n'
            "PATH=/opt/homebrew/bin:/usr/bin\n"
            f'printf "%s" "{BEGIN}${{PATH}}{END}"\n'
        )
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        assert read_login_path(shell=str(script)) == "/opt/homebrew/bin:/usr/bin"

    @posix_only
    def test_a_real_shell_that_never_exits_is_cut_off(self, tmp_path):
        script = tmp_path / "hangshell"
        script.write_text("#!/bin/sh\nsleep 30\n")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        with pytest.raises(LoginPathError, match="did not finish"):
            read_login_path(shell=str(script), timeout=0.5)


class TestAccountShell:
    def test_never_trusts_the_shell_environment_variable(self, monkeypatch):
        """A .env file in the working directory can set SHELL; it must not
        choose what GAIA runs at startup."""
        fake_pwd = types.SimpleNamespace(
            getpwuid=lambda uid: types.SimpleNamespace(pw_shell=sys.executable)
        )
        monkeypatch.setitem(sys.modules, "pwd", fake_pwd)
        monkeypatch.setattr(os, "getuid", lambda: 501, raising=False)
        monkeypatch.setenv("SHELL", "/tmp/evil-shell")
        assert env._account_login_shell() == sys.executable

    @pytest.mark.parametrize("shell", ["", "zsh", "/does/not/exist"])
    def test_an_unusable_recorded_shell_is_an_error(self, monkeypatch, shell):
        fake_pwd = types.SimpleNamespace(
            getpwuid=lambda uid: types.SimpleNamespace(pw_shell=shell)
        )
        monkeypatch.setitem(sys.modules, "pwd", fake_pwd)
        monkeypatch.setattr(os, "getuid", lambda: 501, raising=False)
        with pytest.raises(LoginPathError, match="not runnable"):
            env._account_login_shell()


class TestMergePaths:
    def test_login_order_wins_and_inherited_only_entries_survive(self):
        merged = merge_paths(
            paths("/opt/homebrew/bin", "/usr/bin"),
            paths("/Users/me/.gaia/uv", "/usr/bin", "/bin"),
        )
        assert merged.split(os.pathsep) == [
            "/opt/homebrew/bin",
            "/usr/bin",
            "/Users/me/.gaia/uv",
            "/bin",
        ]

    def test_duplicates_and_empty_entries_are_dropped(self):
        merged = merge_paths(paths("/a", "", "/a", "/b"), paths("/b", "", "/c"))
        assert merged.split(os.pathsep) == ["/a", "/b", "/c"]

    def test_an_empty_inherited_path_gives_the_login_path(self):
        assert merge_paths(paths("/a", "/b"), "") == paths("/a", "/b")


class TestApplyLoginPath:
    def test_does_nothing_off_macos(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(
            env, "read_login_path", lambda: pytest.fail("must not run a shell")
        )
        assert apply_login_path() is False

    def test_applies_the_merged_path_to_the_process_once(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setenv("PATH", paths("/usr/bin", "/bin"))
        calls = []
        monkeypatch.setattr(
            env,
            "read_login_path",
            lambda: calls.append(1) or paths("/opt/homebrew/bin", "/usr/bin"),
        )
        assert apply_login_path() is True
        assert os.environ["PATH"].split(os.pathsep)[0] == "/opt/homebrew/bin"
        assert apply_login_path() is True
        assert calls == [1]

    def test_children_get_the_repaired_path(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setenv("PATH", paths("/usr/bin", "/bin"))
        monkeypatch.setattr(env, "read_login_path", lambda: "/opt/homebrew/bin")
        apply_login_path()
        assert "/opt/homebrew/bin" in child_env()["PATH"].split(os.pathsep)

    def test_a_failure_warns_on_stderr_keeps_the_path_and_never_touches_stdout(
        self, monkeypatch, capsys
    ):
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setenv("PATH", paths("/usr/bin", "/bin"))

        def fail():
            raise LoginPathError("zsh -ilc did not finish within 5s")

        monkeypatch.setattr(env, "read_login_path", fail)
        assert apply_login_path() is False
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "could not read your login PATH" in captured.err
        assert "did not finish within 5s" in captured.err
        assert os.environ["PATH"] == paths("/usr/bin", "/bin")

    def test_a_model_cannot_set_path_through_a_command_assignment(self):
        """PATH stays denied for per-command env; only this trusted repair sets it."""
        from gaia.agents.tools.shell_tools import _denied_env_name

        assert _denied_env_name("PATH")

    @posix_only
    def test_a_tool_only_on_the_login_path_is_found_after_the_repair(
        self, monkeypatch, tmp_path
    ):
        from gaia.agents.base.system_context import probe_binaries

        tool = tmp_path / "zz-brew-only-tool"
        tool.write_text("#!/bin/sh\necho ok\n")
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setenv("PATH", paths("/usr/bin", "/bin"))
        assert shutil.which("zz-brew-only-tool") is None
        assert probe_binaries(["zz-brew-only-tool"]) == {"zz-brew-only-tool": False}
        monkeypatch.setattr(env, "read_login_path", lambda: str(tmp_path))
        apply_login_path()
        assert shutil.which("zz-brew-only-tool")
        assert probe_binaries(["zz-brew-only-tool"]) == {"zz-brew-only-tool": True}


def test_cli_main_repairs_the_path_before_anything_else(monkeypatch):
    import gaia.cli as cli

    order = []
    monkeypatch.setattr(cli, "use_os_trust_store", lambda: order.append("tls"))
    monkeypatch.setattr(cli, "apply_login_path", lambda: order.append("path"))

    def stop():
        order.append("parser")
        raise SystemExit(0)

    monkeypatch.setattr(cli, "build_parser", stop)
    with pytest.raises(SystemExit):
        cli.main()
    assert order == ["tls", "path", "parser"]
