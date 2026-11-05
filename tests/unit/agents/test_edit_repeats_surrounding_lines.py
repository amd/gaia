# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
An edit whose ``new_content`` repeats the lines around ``old_content`` is refused.

On Gemma-4-E4B the model asked to change one line passed the whole intended
file as ``new_content``. The tool spliced it in place of that one line, so the
file came out with its other lines twice, and the answer claimed success.

No LLM or external service required.
"""

import importlib
from pathlib import Path

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_edit import apply_unique_replacement
from gaia.security import PathValidator

SHOPPING = "Shopping list\n- milk\n- bread\n- eggs\n"
WHOLE_FILE_AS_SPAN = "Shopping list\n- oat milk\n- bread\n- eggs\n- apples"

# (id, module, mixin class, registrar)
EDIT_FILE_TOOLS = [
    (
        "file_io_tools.edit_file",
        "gaia.agents.tools.file_io_tools",
        "FileIOToolsMixin",
        "register_file_io_tools",
    ),
    (
        "file_tools.edit_file",
        "gaia.agents.tools.file_tools",
        "FileSearchToolsMixin",
        "register_file_search_tools",
    ),
]


@pytest.fixture(params=EDIT_FILE_TOOLS, ids=[entry[0] for entry in EDIT_FILE_TOOLS])
def edit_file(request, tmp_path, mock_home):
    _, module_name, class_name, registrar = request.param
    mixin = getattr(importlib.import_module(module_name), class_name)()
    mixin.path_validator = PathValidator()
    mixin.path_validator.allowed_paths.add(tmp_path.resolve())

    saved = dict(_TOOL_REGISTRY)
    try:
        getattr(mixin, registrar)()
        function = _TOOL_REGISTRY["edit_file"]["function"]
        read_file = _TOOL_REGISTRY["read_file"]["function"]

        def call(path, old, new):
            assert read_file(str(path))["status"] == "success"
            return function(str(path), old, new)

        yield call
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


@pytest.fixture
def shopping(tmp_path) -> Path:
    path = tmp_path / "shopping.txt"
    path.write_bytes(SHOPPING.encode("utf-8"))
    return path


class TestWholeFileInsideASpanIsRefused:
    def test_the_rc_case_is_an_error(self, edit_file, shopping):
        result = edit_file(shopping, "- milk", WHOLE_FILE_AS_SPAN)
        assert result["status"] == "error"

    def test_the_rc_case_writes_nothing(self, edit_file, shopping):
        edit_file(shopping, "- milk", WHOLE_FILE_AS_SPAN)
        assert shopping.read_bytes().decode("utf-8") == SHOPPING

    def test_the_error_names_the_repeated_lines(self, edit_file, shopping):
        result = edit_file(shopping, "- milk", WHOLE_FILE_AS_SPAN)
        assert "- bread" in result["repeated_lines"]
        assert "- eggs" in result["repeated_lines"]

    def test_the_error_says_how_to_retry(self, edit_file, shopping):
        message = edit_file(shopping, "- milk", WHOLE_FILE_AS_SPAN)["error"]
        assert "only the text that replaces old_content" in message
        assert "write_file" in message

    def test_the_error_carries_the_current_content(self, edit_file, shopping):
        result = edit_file(shopping, "- milk", WHOLE_FILE_AS_SPAN)
        assert "- milk" in result["current_content"]

    def test_the_lines_above_alone_are_caught(self, edit_file, tmp_path):
        path = tmp_path / "notes.md"
        path.write_bytes(b"# Notes\nIntro line here\n- first\n- second\n")
        result = edit_file(
            path, "- second", "# Notes\nIntro line here\n- first\n- second, edited"
        )
        assert result["status"] == "error"
        assert path.read_bytes() == b"# Notes\nIntro line here\n- first\n- second\n"


class TestOneLineFromEachSideIsRefused:
    """Echoing the line above and the line below is the same paste, just shorter."""

    def test_both_sides_echo_is_an_error(self, edit_file, shopping):
        result = edit_file(shopping, "- bread", "- milk\n- rye bread\n- eggs")
        assert result["status"] == "error"
        assert result["repeated_lines"] == ["- milk", "- eggs"]
        assert shopping.read_bytes().decode("utf-8") == SHOPPING

    def test_the_error_says_to_include_the_lines_in_old_content(
        self, edit_file, shopping
    ):
        result = edit_file(shopping, "- bread", "- milk\n- rye bread\n- eggs")
        assert "add them to old_content too" in result["error"]

    def test_including_the_lines_in_old_content_lands_it(self, edit_file, shopping):
        result = edit_file(
            shopping, "- milk\n- bread\n- eggs", "- milk\n- rye bread\n- eggs"
        )
        assert result["status"] == "success"
        assert shopping.read_bytes().decode("utf-8") == SHOPPING.replace(
            "- bread", "- rye bread"
        )


class TestDuplicatingTheBlockAbove:
    CONFIG = "[server]\nhost = localhost\nport = 8080\n[client]\n"

    @pytest.fixture
    def config(self, tmp_path) -> Path:
        path = tmp_path / "app.ini"
        path.write_bytes(self.CONFIG.encode("utf-8"))
        return path

    def test_without_the_block_in_old_content_it_is_refused(self, edit_file, config):
        result = edit_file(
            config, "[client]", "host = localhost\nport = 8080\n[client]"
        )
        assert result["status"] == "error"
        assert config.read_bytes().decode("utf-8") == self.CONFIG

    def test_with_the_block_in_old_content_it_lands(self, edit_file, config):
        result = edit_file(
            config,
            "host = localhost\nport = 8080\n[client]",
            "host = localhost\nport = 8080\n[client]\nhost = localhost\nport = 8080",
        )
        assert result["status"] == "success"
        assert config.read_bytes().decode("utf-8") == (
            "[server]\nhost = localhost\nport = 8080\n"
            "[client]\nhost = localhost\nport = 8080\n"
        )


class TestLegitimateEditsStillWork:
    def test_changing_one_line(self, edit_file, shopping):
        assert edit_file(shopping, "- milk", "- oat milk")["status"] == "success"
        assert shopping.read_bytes().decode("utf-8") == SHOPPING.replace(
            "- milk", "- oat milk"
        )

    def test_appending_after_the_last_line(self, edit_file, shopping):
        result = edit_file(shopping, "- eggs\n", "- eggs\n- apples\n")
        assert result["status"] == "success"
        assert shopping.read_bytes().decode("utf-8") == SHOPPING + "- apples\n"

    def test_the_requested_change_as_two_edits(self, edit_file, shopping):
        assert edit_file(shopping, "- milk", "- oat milk")["status"] == "success"
        assert edit_file(shopping, "- eggs\n", "- eggs\n- apples\n")["status"] == (
            "success"
        )
        assert shopping.read_bytes().decode("utf-8") == (
            "Shopping list\n- oat milk\n- bread\n- eggs\n- apples\n"
        )

    def test_a_span_covering_the_whole_file(self, edit_file, shopping):
        result = edit_file(shopping, SHOPPING, WHOLE_FILE_AS_SPAN + "\n")
        assert result["status"] == "success"

    def test_repeating_lines_from_inside_the_span(self, edit_file, shopping):
        result = edit_file(shopping, "- bread", "- bread\n- bread")
        assert result["status"] == "success"

    def test_repeating_short_structural_lines(self, edit_file, tmp_path):
        path = tmp_path / "a.js"
        path.write_bytes(b"function a() {\n  one();\n}\n}\n")
        result = edit_file(path, "  one();", "  one();\n  if (x) {\n  }\n}")
        assert result["status"] == "success"

    def test_success_shows_the_edited_lines(self, edit_file, shopping):
        result = edit_file(shopping, "- milk", "- oat milk")
        assert "- oat milk" in result["after"]
        assert result["after_start_line"] == 1


class TestHelperContract:
    def test_the_helper_refuses_the_rc_case(self):
        updated, error = apply_unique_replacement(
            "shopping.txt", SHOPPING, "- milk", WHOLE_FILE_AS_SPAN
        )
        assert updated is None
        assert error["match_count"] == 1
        assert error["repeated_lines"]

    def test_one_echoed_line_from_one_side_is_allowed(self):
        content = "a = 1\nb = 2\nc = 3\n"
        updated, error = apply_unique_replacement(
            "f.py", content, "b = 2", "a = 1\nb = 9"
        )
        assert error is None
        assert updated == "a = 1\na = 1\nb = 9\nc = 3\n"

    def test_crlf_files_are_caught_too(self):
        crlf = SHOPPING.replace("\n", "\r\n")
        updated, error = apply_unique_replacement(
            "shopping.txt", crlf, "- milk", WHOLE_FILE_AS_SPAN
        )
        assert updated is None and error is not None
