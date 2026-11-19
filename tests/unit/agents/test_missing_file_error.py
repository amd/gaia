# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A missing file tells the model when to stop looking."""

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.base.verification import check_was_executed
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.agents.tools.file_tools import FileSearchToolsMixin
from gaia.agents.tools.path_access import missing_file_error
from gaia.security import PathValidator


def test_a_missing_folder_is_named_and_the_search_is_capped(tmp_path):
    path = tmp_path / "projects" / "reports" / "quarterly_review_2024.pdf"
    result = missing_file_error(path)
    assert result["status"] == "error"
    assert check_was_executed(result) is False
    assert "does not exist either" in result["error"]
    assert "one file search for 'quarterly_review_2024.pdf'" in result["error"]
    assert "do not keep searching other folders or drives" in result["error"]


def test_an_existing_folder_without_the_file_says_so(tmp_path):
    result = missing_file_error(tmp_path / "notes.md")
    assert "exists, but the file is not in it" in result["error"]


@pytest.fixture
def tools(tmp_path):
    search = FileSearchToolsMixin()
    io = FileIOToolsMixin()
    io.path_validator = PathValidator()
    io.path_validator.allowed_paths.add(tmp_path.resolve())
    io._truncation_budget = lambda: (3000, 2000)
    saved = dict(_TOOL_REGISTRY)
    try:
        search.register_file_search_tools()
        found = {"get_file_info": _TOOL_REGISTRY["get_file_info"]["function"]}
        io.register_file_io_tools()
        found["read_file"] = _TOOL_REGISTRY["read_file"]["function"]
        found["edit_file"] = _TOOL_REGISTRY["edit_file"]["function"]
        yield found
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def test_file_info_on_a_missing_file_says_when_to_stop(tools, tmp_path):
    result = tools["get_file_info"](str(tmp_path / "benefits_addendum_2026.pdf"))
    assert result["operation"] == "get_file_info"
    assert check_was_executed(result) is False
    assert "do not keep searching other folders or drives" in result["error"]


def test_read_and_edit_on_a_missing_file_say_when_to_stop(tools, tmp_path):
    missing = str(tmp_path / "notes.md")
    read = tools["read_file"](missing)
    edit = tools["edit_file"](missing, old_content="a", new_content="b")
    for result in (read, edit):
        assert "do not keep searching other folders or drives" in result["error"]
