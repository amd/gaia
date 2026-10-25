# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A Windows path written with backslashes is checked like one written with '/'."""

import os

import pytest

from gaia.agents.tools import shell_tools


@pytest.mark.parametrize(
    "command, path",
    [
        (r"more C:\Windows\win.ini", r"C:\Windows\win.ini"),
        (r"dir C:\Windows\System32\drivers\etc", r"C:\Windows\System32\drivers\etc"),
        (r'type "C:\Program Files\x.txt"', r"C:\Program Files\x.txt"),
    ],
)
def test_the_path_check_sees_the_path_cmd_exe_runs(monkeypatch, command, path):
    monkeypatch.setattr(shell_tools, "_pipe_dialect", lambda: "cmd")
    steps, refusal = shell_tools._parse_line(command)
    assert refusal is None
    (segment,) = steps[0].segments
    assert segment[1] == path


@pytest.mark.skipif(os.name != "nt", reason="runs cmd.exe on a Windows path")
def test_a_backslash_path_outside_the_scope_is_refused(tmp_path):
    from gaia.agents.base.tools import _TOOL_REGISTRY
    from gaia.security import PathValidator

    saved = dict(_TOOL_REGISTRY)
    try:
        mixin = shell_tools.ShellToolsMixin()
        mixin.path_validator = PathValidator(allowed_paths=[str(tmp_path)])
        mixin.register_shell_tools()
        mixin._shell_session = shell_tools.ShellSession(start_cwd=str(tmp_path))
        result = _TOOL_REGISTRY["run_shell_command"]["function"](
            r"more C:\Windows\win.ini"
        )
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)

    assert result["status"] == "error"
    assert "Access denied" in result["error"]
    assert "fonts" not in str(result)
