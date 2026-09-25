# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``FileSearchToolsMixin.read_file`` must name a directory as the problem,
not hand the model a raw OS errno (amd/gaia#3891 comment, second call site).

``os.path.exists()`` is true for a directory, so the existing-path guard let
it through to ``open()``, which raises ``IsADirectoryError`` into the
generic ``except Exception`` handler as an unstructured errno string.
``file_io_tools.py``'s ``read_file`` already guards against this; this mixin
is a separate registration and did not.
"""

from __future__ import annotations

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_tools import FileSearchToolsMixin


@pytest.fixture
def read_file(tmp_path):
    mixin = FileSearchToolsMixin()

    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        mixin.register_file_search_tools()
        yield _TOOL_REGISTRY["read_file"]["function"]
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


class TestReadFileOnADirectory:
    def test_names_the_directory_instead_of_the_raw_errno(self, read_file, tmp_path):
        a_directory = tmp_path / "some_dir"
        a_directory.mkdir()

        result = read_file(file_path=str(a_directory))

        assert result["status"] == "error"
        assert "Errno" not in result["error"]
        assert "directory" in result["error"].lower()
        assert str(a_directory) in result["error"]

    def test_a_real_file_still_reads(self, read_file, tmp_path):
        target = tmp_path / "ok.txt"
        target.write_text("content", encoding="utf-8")

        result = read_file(file_path=str(target))

        assert result["status"] == "success"
        assert result["content"] == "content"
