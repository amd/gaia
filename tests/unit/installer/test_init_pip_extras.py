# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Python-package installs on the GAIA installer's virtualenv.

The installer builds ``~/.gaia/venv`` with ``uv venv`` -- no pip -- and only
puts its ``bin`` on PATH, so nothing is activated. On that venv a bare
``uv pip install`` finds no environment and ``python -m pip`` does not exist.
``gaia init`` used to try both, swallow every failure and report success with
document Q&A broken. These tests pin the argv actually executed and printed,
and that a failed install fails init.
"""

import io
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents import install_hints
from gaia.installer import init_command
from gaia.installer.init_command import InitCommand

UV = "/home/u/.local/bin/uv"


@pytest.fixture
def installer_venv(monkeypatch):
    monkeypatch.setattr(install_hints, "_pip_available", lambda: False)
    monkeypatch.setattr(
        install_hints.shutil, "which", lambda name: UV if name == "uv" else None
    )
    monkeypatch.setattr(install_hints, "editable_gaia_root", lambda: None)


@pytest.fixture
def stock_venv(monkeypatch):
    monkeypatch.setattr(install_hints, "_pip_available", lambda: True)
    monkeypatch.setattr(install_hints.shutil, "which", lambda _name: None)
    monkeypatch.setattr(install_hints, "editable_gaia_root", lambda: None)


def _cmd(profile="rag"):
    cmd = InitCommand(profile=profile, yes=True)
    printed = []
    cmd._print = lambda msg, end="\n": printed.append(msg)
    return cmd, printed


def _run_extras(cmd, returncode=0, stderr=""):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)

    with (
        patch.object(init_command, "RICH_AVAILABLE", False),
        patch.object(init_command.subprocess, "run", side_effect=fake_run),
    ):
        ok = cmd._install_pip_extras()
    return ok, calls


class TestExtrasInstallArgv:
    def test_installer_venv_runs_uv_pinned_to_this_interpreter(self, installer_venv):
        cmd, _ = _cmd()
        ok, calls = _run_extras(cmd)
        assert ok
        assert calls == [
            [UV, "pip", "install", "--python", sys.executable, "amd-gaia[rag]"]
        ]

    def test_stock_venv_runs_its_own_pip(self, stock_venv):
        cmd, _ = _cmd()
        ok, calls = _run_extras(cmd)
        assert ok
        assert calls == [[sys.executable, "-m", "pip", "install", "amd-gaia[rag]"]]

    def test_editable_checkout_reinstalls_itself_with_the_extra(
        self, installer_venv, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(install_hints, "editable_gaia_root", lambda: str(tmp_path))
        cmd, _ = _cmd()
        _, calls = _run_extras(cmd)
        assert calls[0][-2:] == ["-e", f"{tmp_path}[rag]"]
        assert calls[0][3:5] == ["--python", sys.executable]

    def test_profile_without_extras_runs_nothing(self, installer_venv):
        cmd, _ = _cmd(profile="minimal")
        ok, calls = _run_extras(cmd)
        assert ok and calls == []


class TestExtrasInstallFailureIsLoud:
    def test_failed_install_returns_false_and_shows_stderr(self, installer_venv):
        cmd, printed = _cmd()
        ok, calls = _run_extras(
            cmd, returncode=2, stderr="error: Failed to fetch faiss-cpu\n"
        )
        out = "\n".join(printed)
        assert ok is False
        assert len(calls) == 1, "one installer, no silent retries through others"
        assert "Failed to fetch faiss-cpu" in out
        assert "exit 2" in out
        assert f'uv pip install --python {sys.executable} "amd-gaia[rag]"' in out

    def test_no_installer_at_all_fails_without_running_anything(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(install_hints, "_pip_available", lambda: False)
        monkeypatch.setattr(install_hints.shutil, "which", lambda _name: None)
        monkeypatch.setattr(install_hints.Path, "home", lambda: tmp_path)
        monkeypatch.setattr(install_hints, "editable_gaia_root", lambda: None)
        cmd, printed = _cmd()
        ok, calls = _run_extras(cmd)
        assert ok is False
        assert calls == []
        assert "docs.astral.sh/uv" in "\n".join(printed)

    def test_failed_extras_install_fails_init(self, monkeypatch):
        """`gaia init` must exit non-zero, not print "initialization complete"
        with document Q&A broken."""
        cmd = InitCommand(profile="rag", yes=True, skip_models=True)
        cmd._print = lambda msg, end="\n": None
        monkeypatch.setattr(cmd, "_ensure_lemonade_ready", lambda: True)
        monkeypatch.setattr(cmd, "_install_pip_extras", lambda: False)
        verify = MagicMock(return_value=True)
        completion = MagicMock()
        monkeypatch.setattr(cmd, "_verify_setup", verify)
        monkeypatch.setattr(cmd, "_print_completion", completion)
        monkeypatch.setattr(init_command, "stdin_is_tty", lambda: True)

        assert cmd.run() == 1
        verify.assert_not_called()
        completion.assert_not_called()


class TestAgentUiMissingDependencies:
    def test_prints_an_install_command_that_targets_this_interpreter(
        self, installer_venv, capsys
    ):
        import gaia.cli as gaia_cli

        with (
            patch("gaia.ui.build.ensure_webui_built"),
            patch(
                "gaia.ui.server.create_app",
                side_effect=ImportError("No module named 'fastapi'"),
            ),
            pytest.raises(SystemExit) as exc,
        ):
            gaia_cli._launch_agent_ui(port=0, log=MagicMock())

        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert f'uv pip install --python {sys.executable} "amd-gaia[ui]"' in out
        assert 'uv pip install "amd-gaia[ui]"' not in out


class TestCompletionTuiHintRich:
    def test_rich_output_keeps_the_installer_command_intact(self, monkeypatch):
        if not init_command.RICH_AVAILABLE:
            pytest.skip("rich not installed")
        cmd = InitCommand(profile="gaia", yes=True)
        cmd._is_hub_agent_available = lambda _id: True
        buf = io.StringIO()
        cmd.console = init_command.Console(file=buf, force_terminal=False, width=200)
        monkeypatch.setattr(init_command.shutil, "which", lambda _name: None)
        cmd._print_completion()
        out = buf.getvalue()
        assert init_command._INSTALLER_HINT in out
        assert "    gaia-tui " not in out
