# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``read_file``, ``edit_python_file`` and ``replace_function`` must name a
directory as the problem, not hand the model a raw OS errno (#3890).

``os.path.exists()`` is true for a directory, so the existing-path guard at
each of the three call sites let a directory through to ``open()``, which
raises ``IsADirectoryError`` into the generic ``except Exception`` handler as
an unstructured errno string the model cannot act on.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.security import PathValidator


@pytest.fixture
def tools(tmp_path):
    """The three registered tools, with a PathValidator scoped to tmp_path."""
    mixin = FileIOToolsMixin()
    mixin.console = None
    mixin.path_validator = PathValidator(allowed_paths=[str(tmp_path)])
    mixin._validate_python_syntax = MagicMock(
        return_value={"is_valid": True, "errors": []}
    )
    mixin._parse_python_code = MagicMock()

    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        mixin.register_file_io_tools()
        yield {
            "read_file": _TOOL_REGISTRY["read_file"]["function"],
            "edit_python_file": _TOOL_REGISTRY["edit_python_file"]["function"],
            "replace_function": _TOOL_REGISTRY["replace_function"]["function"],
        }
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


@pytest.fixture
def a_directory(tmp_path):
    target = tmp_path / "some_dir"
    target.mkdir()
    return target


def _assert_names_the_directory(result, path):
    assert result["status"] == "error"
    assert "Errno" not in result["error"]
    assert "directory" in result["error"].lower()
    assert str(path) in result["error"]


class TestReadFileOnADirectory:
    def test_names_the_directory_instead_of_the_raw_errno(self, tools, a_directory):
        result = tools["read_file"](file_path=str(a_directory))
        _assert_names_the_directory(result, a_directory)

    def test_a_real_file_still_reads(self, tools, tmp_path):
        target = tmp_path / "ok.txt"
        target.write_text("content", encoding="utf-8")

        result = tools["read_file"](file_path=str(target))

        assert result["status"] == "success"
        assert result["content"] == "content"

    def test_a_missing_path_still_reports_not_found(self, tools, tmp_path):
        missing = tmp_path / "nope.txt"

        result = tools["read_file"](file_path=str(missing))

        assert result["status"] == "error"
        assert "not found" in result["error"].lower()


class TestEditPythonFileOnADirectory:
    def test_names_the_directory_instead_of_the_raw_errno(self, tools, a_directory):
        result = tools["edit_python_file"](
            file_path=str(a_directory), old_content="x", new_content="y"
        )
        _assert_names_the_directory(result, a_directory)


class TestReplaceFunctionOnADirectory:
    def test_names_the_directory_instead_of_the_raw_errno(self, tools, a_directory):
        result = tools["replace_function"](
            file_path=str(a_directory),
            function_name="foo",
            new_implementation="def foo():\n    return 1",
        )
        _assert_names_the_directory(result, a_directory)
