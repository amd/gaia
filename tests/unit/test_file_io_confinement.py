# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Regression tests for the file-write confinement guarantee (issue #3316, Part 1).

Every write-capable tool in ``FileIOToolsMixin`` used to read
``path_validator`` defensively (``getattr(self, "path_validator", None)``)
and, when it was absent, skip the *entire* confinement branch — the
allow/deny check, the denial return, and the audit record — then fall
through and perform the write anyway. A host that forgot to bind
``path_validator`` therefore wrote files with no confinement check and no
audit trail.

These tests pin the fix for all six write-capable tools: a missing
validator now denies the write outright. The target file must not exist
(or must be unchanged) afterward — asserted on the filesystem, not just the
returned status — because a returned "error" dict that still wrote the file
would be exactly the bug this guards against.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.security import PathValidator


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


class _UnconfinedHost(Agent, FileIOToolsMixin):
    """A host that never binds path_validator — the exact defect shape."""

    def _register_tools(self):
        self.register_file_io_tools()


class _ConfinedHost(Agent, FileIOToolsMixin):
    """Reference host with a real PathValidator, for the contrasting golden path."""

    def __init__(self, **kwargs):
        self.path_validator = PathValidator()
        super().__init__(**kwargs)

    def _register_tools(self):
        self.register_file_io_tools()


def _make(cls, **kwargs):
    with patch("gaia.agents.base.agent.AgentSDK"):
        return cls(skip_lemonade=True, silent_mode=True, **kwargs)


def _tool(name):
    return _TOOL_REGISTRY[name]["function"]


def test_write_python_file_denies_unconfined_write(tmp_path):
    _make(_UnconfinedHost)
    target = tmp_path / "new_module.py"

    result = _tool("write_python_file")(file_path=str(target), content="x = 1\n")

    assert result["status"] == "error"
    assert not target.exists()


def test_edit_python_file_denies_unconfined_edit(tmp_path):
    _make(_UnconfinedHost)
    target = tmp_path / "existing.py"
    target.write_text("x = 1\n")

    result = _tool("edit_python_file")(
        file_path=str(target), old_content="x = 1", new_content="x = 2"
    )

    assert result["status"] == "error"
    assert target.read_text() == "x = 1\n"


def test_write_markdown_file_denies_unconfined_write(tmp_path):
    _make(_UnconfinedHost)
    target = tmp_path / "notes.md"

    result = _tool("write_markdown_file")(file_path=str(target), content="# Notes")

    assert result["status"] == "error"
    assert not target.exists()


def test_write_file_denies_unconfined_write(tmp_path):
    _make(_UnconfinedHost)
    target = tmp_path / "config.json"

    result = _tool("write_file")(file_path=str(target), content="{}")

    assert result["status"] == "error"
    assert not target.exists()


def test_edit_file_denies_unconfined_edit(tmp_path):
    _make(_UnconfinedHost)
    target = tmp_path / "config.json"
    target.write_text("{}")

    result = _tool("edit_file")(
        file_path=str(target), old_content="{}", new_content='{"a": 1}'
    )

    assert result["status"] == "error"
    assert target.read_text() == "{}"


def test_replace_function_denies_unconfined_edit(tmp_path):
    _make(_UnconfinedHost)
    target = tmp_path / "mod.py"
    target.write_text("def f():\n    return 1\n")

    result = _tool("replace_function")(
        file_path=str(target),
        function_name="f",
        new_implementation="def f():\n    return 2",
    )

    assert result["status"] == "error"
    assert target.read_text() == "def f():\n    return 1\n"


def test_all_six_write_tools_deny_when_unconfined(tmp_path):
    """One assertion covering every write-capable tool, so a future addition
    to FileIOToolsMixin that skips this list is easy to spot in review."""
    _make(_UnconfinedHost)

    write_tool_calls = {
        "write_python_file": dict(file_path=str(tmp_path / "a.py"), content="x = 1\n"),
        "write_markdown_file": dict(file_path=str(tmp_path / "b.md"), content="# hi"),
        "write_file": dict(file_path=str(tmp_path / "c.json"), content="{}"),
    }
    for tool_name, kwargs in write_tool_calls.items():
        result = _tool(tool_name)(**kwargs)
        assert result["status"] == "error", tool_name
        assert not Path(kwargs["file_path"]).exists(), tool_name


def test_write_file_succeeds_and_is_audited_when_confined(tmp_path):
    """Contrast: a confined host writes normally and produces an audit trail."""
    agent = _make(_ConfinedHost)
    agent.path_validator.allowed_paths.add(tmp_path.resolve())
    target = tmp_path / "confined.txt"

    result = _tool("write_file")(file_path=str(target), content="hello")

    assert result["status"] == "success"
    assert target.read_text() == "hello"
