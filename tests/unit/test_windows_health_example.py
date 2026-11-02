# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regression tests for the Windows MCP system-health example (#334)."""

import importlib.util
import re
from pathlib import Path

EXAMPLE = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "mcp_windows_system_health_agent.py"
)


def _prompt() -> str:
    spec = importlib.util.spec_from_file_location("win_health_example", EXAMPLE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.WindowsSystemHealthAgent._get_system_prompt(None)


def test_notepad_opens_a_new_empty_file():
    prompt = _prompt()
    assert "New-Item" in prompt
    assert re.search(r"Start-Process notepad -ArgumentList \$f", prompt)


def test_no_bare_notepad_launch_command():
    # A bare `Start-Process notepad` command reuses an open window and appends to it.
    for line in _prompt().splitlines():
        if line.startswith(("$", "Start-Process")) and "notepad" in line:
            assert "-ArgumentList $f" in line, line


def test_error_hint_does_not_suggest_pip_install_uv():
    src = EXAMPLE.read_text(encoding="utf-8")
    assert "pip install uv" not in src
    assert "uvx --version" in src
    assert "uvx windows-mcp" in src
