# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An agent must not change the user's system packages to get around a tool.

After ``run_python`` broke, an agent with full access ran ``python -m pip
install openpyxl`` into the user's system Python. Full access swaps the
per-binary shell policy for the developer set, which names ``python``, so the
pip policy (and its refusal of installs outside the project) never fired.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gaia.agents.tools import package_installs
from gaia.agents.tools.package_installs import system_install_target
from gaia.agents.tools.shell_tools import (
    TIER_CONFIRM_ONCE,
    TIER_REFUSE,
    ShellToolsMixin,
)


def _interpreter(root: Path, venv: bool) -> Path:
    folder = root / ("Scripts" if os.name == "nt" else "bin")
    folder.mkdir(parents=True)
    exe = folder / ("python.exe" if os.name == "nt" else "python")
    exe.write_text("", encoding="utf-8")
    if venv:
        (root / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
    return exe


@pytest.fixture
def system_python(tmp_path, monkeypatch):
    """``python`` on PATH is an installation with no pyvenv.cfg."""
    exe = _interpreter(tmp_path / "Python312", venv=False)
    real = package_installs._locate

    def locate(program, cwd):
        if package_installs._name(program) in ("python", "pip", "python3"):
            return exe
        return real(program, cwd)

    monkeypatch.setattr(package_installs, "_locate", locate)
    return exe


class TestWhatCountsAsASystemInstall:
    @pytest.mark.parametrize(
        "argv",
        [
            ["python", "-m", "pip", "install", "openpyxl"],
            ["pip", "install", "openpyxl"],
            ["pip", "uninstall", "-y", "openpyxl"],
            ["python", "-m", "pip", "install", "--user", "openpyxl"],
            ["timeout", "60", "pip", "install", "openpyxl"],
            ["cmd", "/c", "pip install openpyxl"],
            ["powershell", "-Command", "python -m pip install openpyxl"],
        ],
    )
    def test_pip_into_a_python_outside_any_venv(self, system_python, argv, tmp_path):
        assert system_install_target(argv, str(tmp_path)) is not None

    @pytest.mark.parametrize(
        "argv",
        [
            ["npm", "install", "-g", "typescript"],
            ["npm", "i", "--global", "typescript"],
            ["pnpm", "add", "-g", "typescript"],
            ["yarn", "global", "add", "typescript"],
            ["uv", "pip", "install", "--system", "openpyxl"],
            ["uv", "tool", "install", "ruff"],
            ["pipx", "install", "ruff"],
            ["conda", "install", "numpy"],
            ["winget", "install", "Python.Python.3.12"],
        ],
    )
    def test_other_machine_wide_installs(self, argv, tmp_path):
        assert system_install_target(argv, str(tmp_path)) is not None

    @pytest.mark.parametrize(
        "argv",
        [
            ["pip", "list"],
            ["pip", "show", "openpyxl"],
            ["python", "-m", "pip", "install", "--target", "deps", "openpyxl"],
            ["pip", "install", "--dry-run", "openpyxl"],
            ["pip", "install", "--help"],
            ["python", "script.py", "install"],
            ["python", "-c", "print('pip install')"],
            ["npm", "install"],
            ["npm", "install", "left-pad"],
            ["uv", "pip", "install", "openpyxl"],
            ["uv", "add", "openpyxl"],
            ["uv", "sync"],
            ["conda", "install", "-p", "./env", "numpy"],
            ["conda", "list"],
        ],
    )
    def test_project_scoped_and_read_only_commands_pass(
        self, system_python, argv, tmp_path
    ):
        assert system_install_target(argv, str(tmp_path)) is None

    def test_a_project_venv_is_not_the_system(self, tmp_path):
        exe = _interpreter(tmp_path / ".venv", venv=True)
        relative = os.path.relpath(exe, tmp_path)
        assert (
            system_install_target(
                [relative, "-m", "pip", "install", "x"], str(tmp_path)
            )
            is None
        )
        assert (
            system_install_target([str(exe), "-m", "pip", "install", "x"], "/") is None
        )

    def test_an_installation_without_pyvenv_cfg_is_the_system(self, tmp_path):
        exe = _interpreter(tmp_path / "Python312", venv=False)
        target = system_install_target(
            [str(exe), "-m", "pip", "install", "x"], str(tmp_path)
        )
        assert target is not None
        assert str(tmp_path / "Python312") in target


class _Host(ShellToolsMixin):
    debug = False

    def __init__(self, full_access: bool):
        super().__init__()

        class _Console:
            pass

        self.console = _Console()
        self.console.auto_approve_gated_tools = full_access
        self.console.full_access = full_access


def _refusal(command: str, full_access: bool):
    return _Host(full_access).policy_refusal_for_call(
        "run_shell_command", {"command": command}
    )


class TestFullAccessRefusesSystemInstalls:
    """Full access asks nothing, so the install is refused, with guidance."""

    @pytest.mark.parametrize(
        "command",
        [
            "python -m pip install openpyxl",
            "python3 -m pip install --user openpyxl",
            "npm install -g typescript",
            'powershell -Command "python -m pip install openpyxl"',
        ],
    )
    def test_refused_before_anything_runs(self, system_python, command):
        error = _refusal(command, full_access=True)
        assert error is not None, command
        assert error["tier"] == TIER_REFUSE
        assert error["executed"] is False
        assert error.get("system_install") is True
        assert "virtual environment" in error["hint"]
        assert "tell the user" in error["hint"].lower()

    def test_the_tool_itself_refuses_too(self, system_python, tmp_path, monkeypatch):
        """The executor re-checks, so a call that skipped the pre-flight still stops."""
        import gaia.agents.base.tools as tools_mod

        captured = {}

        def fake_tool(**kwargs):
            def wrap(fn):
                captured[kwargs.get("name", fn.__name__)] = fn
                return fn

            return wrap

        monkeypatch.setattr(tools_mod, "tool", fake_tool)
        host = _Host(full_access=True)
        host.register_shell_tools()
        ran = []
        monkeypatch.setattr(
            "gaia.agents.tools.shell_tools._run_step",
            lambda *a, **k: ran.append(a),
        )

        result = captured["run_shell_command"](
            command="python -m pip install openpyxl",
            working_directory=str(tmp_path),
        )

        assert result["status"] == "error"
        assert result["tier"] == TIER_REFUSE
        assert ran == []

    def test_an_install_into_the_active_venv_still_runs_unasked(
        self, tmp_path, monkeypatch
    ):
        exe = _interpreter(tmp_path / ".venv", venv=True)
        monkeypatch.setattr(package_installs, "_locate", lambda program, cwd: exe)
        assert _refusal("python -m pip install openpyxl", full_access=True) is None

    def test_ordinary_developer_commands_are_unaffected(self, system_python):
        assert _refusal("python -m pytest -q", full_access=True) is None
        assert _refusal("npm install", full_access=True) is None


class TestAskModeAsksOnceWithARiskNote:
    def test_system_pip_install_is_never_an_always(self, system_python):
        error, _ = _Host(False)._line_verdict("python -m pip install openpyxl")
        assert error is not None
        assert error["tier"] == TIER_CONFIRM_ONCE
        assert "share" in error["risk_note"]
