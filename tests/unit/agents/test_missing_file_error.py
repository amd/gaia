# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A missing file tells the model when to stop looking."""

from gaia.agents.base.verification import check_was_executed
from gaia.agents.tools.path_access import missing_file_error


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
