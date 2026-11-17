# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A script that needs a module GAIA's own Python lacks is pointed at the system's.

``run_python`` runs the interpreter GAIA ships, so a project's own packages
are not importable there. The model saw only "No module named 'torch'" and
either gave up or reinstalled the package into GAIA's interpreter.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("gaia_agent")

from gaia_agent import chat_agent  # noqa: E402
from gaia_agent.chat_agent import (  # noqa: E402
    _python_run_result,
    _system_python_hint,
    system_python,
)

_MISSING = "ModuleNotFoundError: No module named 'torch.nn'"
_SYSTEM = os.path.join(os.sep, "usr", "bin", "python3")


def _which(found):
    return lambda name: found.get(name)


def _failed(stderr):
    return SimpleNamespace(returncode=1, stderr=stderr)


def test_the_system_python_is_one_on_path_that_is_not_gaias(monkeypatch):
    monkeypatch.setattr(chat_agent.shutil, "which", _which({"python3": _SYSTEM}))
    assert system_python() == _SYSTEM


def test_gaias_own_interpreter_is_not_the_system_python(monkeypatch):
    monkeypatch.setattr(chat_agent.shutil, "which", _which({"python3": sys.executable}))
    assert system_python() is None


def test_the_windows_store_stub_is_not_a_python(monkeypatch):
    stub = os.path.join("C:\\", "Users", "x", "WindowsApps", "python.exe")
    monkeypatch.setattr(chat_agent.shutil, "which", _which({"python": stub}))
    assert system_python() is None


def test_python_is_used_when_there_is_no_python3(monkeypatch):
    monkeypatch.setattr(chat_agent.shutil, "which", _which({"python": _SYSTEM}))
    assert system_python() == _SYSTEM


def test_a_missing_module_names_the_system_python(monkeypatch):
    monkeypatch.setattr(chat_agent, "system_python", lambda: _SYSTEM)
    result = _python_run_result(_failed(_MISSING), "", _MISSING, 0.1, shell_tool=True)
    assert result["status"] == "error"
    assert "has no torch" in result["error"]
    assert _SYSTEM in result["error"]
    assert "run_shell_command" in result["error"]


@pytest.mark.parametrize(
    "stderr, shell_tool, other",
    [
        (_MISSING, False, _SYSTEM),  # no shell tool to run it with
        (_MISSING, True, None),  # no other Python on this machine
        ("ZeroDivisionError: division by zero", True, _SYSTEM),  # not an import
    ],
)
def test_no_hint_without_a_way_to_act_on_it(monkeypatch, stderr, shell_tool, other):
    monkeypatch.setattr(chat_agent, "system_python", lambda: other)
    assert _system_python_hint(stderr, shell_tool) is None
    result = _python_run_result(_failed(stderr), "", stderr, 0.1, shell_tool=shell_tool)
    assert "system Python" not in result["error"]


def test_a_bundled_library_keeps_its_own_hint(monkeypatch):
    """pandas missing from a source install is retried without it, not rerouted."""
    monkeypatch.setattr(chat_agent, "system_python", lambda: _SYSTEM)
    stderr = "ModuleNotFoundError: No module named 'pandas'"
    result = _python_run_result(_failed(stderr), "", stderr, 0.1, shell_tool=True)
    assert "pip is not available" in result["error"]
    assert "system Python" not in result["error"]


def test_the_description_says_whose_python_this_is():
    from gaia_agent.chat_agent import _lists_script_libraries

    def sample():
        """Run.

        {script_libraries}
        """

    assert "GAIA's own Python: has " in _lists_script_libraries(sample).__doc__
