# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""File tools read BOM-marked text as text and show search hits on long lines.

A UTF-16 file (what PowerShell 5.1 ``>`` writes) came back from read_file as
``[Binary file, N bytes]`` and search never matched inside it. A hit deep in a
single-line file came back as the line's first 200 chars, without the match.
"""

import codecs

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.agents.tools.file_tools import FileSearchToolsMixin
from gaia.agents.tools.text_files import MATCH_EXCERPT_CHARS, match_excerpt
from gaia.security import PathValidator

SECRET = "Le code d'accès du coffre est 7731-Ω."
NEEDLE = "NEEDLE=ORCHID-93"


@pytest.fixture
def search_tools():
    saved = dict(_TOOL_REGISTRY)
    try:
        FileSearchToolsMixin().register_file_search_tools()
        yield {
            name: _TOOL_REGISTRY[name]["function"]
            for name in ("read_file", "search_file_content", "get_file_info")
        }
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


@pytest.fixture
def io_tools(tmp_path):
    host = FileIOToolsMixin()
    host.path_validator = PathValidator()
    host.path_validator.allowed_paths.add(tmp_path.resolve())
    host._truncation_budget = lambda: (3000, 2000)
    saved = dict(_TOOL_REGISTRY)
    try:
        host.register_file_io_tools()
        yield {
            name: _TOOL_REGISTRY[name]["function"]
            for name in ("read_file", "search_code")
        }
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


BOM_FORMS = [
    ("utf-16", lambda text: text.encode("utf-16")),
    ("utf-16", lambda text: codecs.BOM_UTF16_BE + text.encode("utf-16-be")),
    ("utf-32", lambda text: text.encode("utf-32")),
    ("utf-8-sig", lambda text: text.encode("utf-8-sig")),
]


@pytest.fixture(params=BOM_FORMS, ids=["utf16le", "utf16be", "utf32", "utf8bom"])
def bom_file(request, tmp_path):
    encoding, encode = request.param
    path = tmp_path / "code.txt"
    path.write_bytes(encode(SECRET + "\nsecond line\n"))
    return path, encoding


@pytest.fixture
def long_line_file(tmp_path):
    path = tmp_path / "big.txt"
    path.write_text("lorem ipsum " * 436000 + NEEDLE + " ", encoding="utf-8")
    return path


class TestBomTextReadsAsText:
    @pytest.mark.parametrize("tools", ["search_tools", "io_tools"])
    def test_read_file_decodes_by_bom(self, request, tools, bom_file):
        path, encoding = bom_file
        result = request.getfixturevalue(tools)["read_file"](str(path))
        assert result["status"] == "success"
        assert result.get("is_binary") is not True
        assert result["content"] == SECRET + "\nsecond line\n"
        assert result["encoding"] == encoding

    @pytest.mark.parametrize("tools", ["search_tools", "io_tools"])
    def test_read_file_pages_by_bom(self, request, tools, bom_file):
        path, _ = bom_file
        result = request.getfixturevalue(tools)["read_file"](
            str(path), offset=3, limit=4
        )
        assert result["status"] == "success"
        assert result["content"] == SECRET[3:7]

    def test_line_range_decodes_by_bom(self, io_tools, bom_file):
        path, _ = bom_file
        result = io_tools["read_file"](str(path), start_line=2, end_line=2)
        assert result["status"] == "success"
        assert result["content"] == "     2\tsecond line\n"

    def test_plain_utf8_result_is_unchanged(self, search_tools, tmp_path):
        path = tmp_path / "plain.txt"
        path.write_text(SECRET, encoding="utf-8")
        result = search_tools["read_file"](str(path))
        assert result["content"] == SECRET
        assert "encoding" not in result

    @pytest.mark.parametrize("tools", ["search_tools", "io_tools"])
    @pytest.mark.parametrize(
        "data",
        [bytes(range(256)) * 4, b"\xff\xfeA\x00\x00\x00B\x00"],
        ids=["no-bom", "utf16-bom-with-nul"],
    )
    def test_binary_still_reads_as_binary(self, request, tools, data, tmp_path):
        path = tmp_path / "blob.bin"
        path.write_bytes(data)
        result = request.getfixturevalue(tools)["read_file"](str(path))
        assert result["is_binary"] is True
        assert result["content"] == f"[Binary file, {len(data)} bytes]"

    def test_search_file_content_matches_inside_bom_text(self, search_tools, bom_file):
        path, _ = bom_file
        result = search_tools["search_file_content"]("7731", directory=str(path.parent))
        assert [(m["line"], m["content"]) for m in result["matches"]] == [(1, SECRET)]

    def test_search_code_matches_inside_bom_text(self, io_tools, bom_file):
        path, _ = bom_file
        result = io_tools["search_code"](
            directory=str(path.parent), pattern="7731", file_extension=".txt"
        )
        assert result["results"][0]["matches"] == [{"line": 1, "content": SECRET}]

    def test_get_file_info_previews_bom_text(self, search_tools, bom_file):
        path, encoding = bom_file
        result = search_tools["get_file_info"](str(path))
        assert result["encoding"] == encoding
        assert result["preview"] == SECRET + "\nsecond line"


class TestLongLineSearchHit:
    def test_search_file_content_shows_the_match(self, search_tools, long_line_file):
        result = search_tools["search_file_content"](
            "NEEDLE=", directory=str(long_line_file.parent)
        )
        content = result["matches"][0]["content"]
        assert NEEDLE in content
        assert content.startswith("...")
        assert len(content) <= MATCH_EXCERPT_CHARS

    def test_search_file_content_context_shows_the_match(
        self, search_tools, long_line_file
    ):
        result = search_tools["search_file_content"](
            "NEEDLE=", directory=str(long_line_file.parent), context_lines=1
        )
        (line,) = result["matches"][0]["context"]
        assert NEEDLE in line and len(line) <= MATCH_EXCERPT_CHARS

    def test_search_code_is_bounded_and_shows_the_match(self, io_tools, long_line_file):
        result = io_tools["search_code"](
            directory=str(long_line_file.parent),
            pattern="NEEDLE=",
            file_extension=".txt",
        )
        (match,) = result["results"][0]["matches"]
        assert NEEDLE in match["content"]
        assert len(match["content"]) <= MATCH_EXCERPT_CHARS


class TestMatchExcerpt:
    def test_a_short_line_comes_back_stripped_and_whole(self):
        assert match_excerpt("  short line  \n", 2, 7) == "short line"

    def test_a_match_near_the_start_cuts_only_the_tail(self):
        line = "NEEDLE " + "x" * 500
        excerpt = match_excerpt(line, 0, 6)
        assert excerpt.startswith("NEEDLE") and excerpt.endswith("...")
        assert len(excerpt) <= MATCH_EXCERPT_CHARS

    def test_a_match_in_the_middle_is_centred_with_both_cuts(self):
        line = "a" * 1000 + "NEEDLE" + "b" * 1000
        excerpt = match_excerpt(line, 1000, 1006)
        assert excerpt.startswith("...") and excerpt.endswith("...")
        assert len(excerpt) == MATCH_EXCERPT_CHARS
        middle = excerpt.index("NEEDLE")
        assert abs(middle - (len(excerpt) - middle - 6)) <= 2

    def test_a_match_longer_than_the_cap_shows_its_start(self):
        line = "a" * 1000 + "N" * 500 + "b" * 1000
        excerpt = match_excerpt(line, 1000, 1500)
        assert excerpt == "..." + "N" * (MATCH_EXCERPT_CHARS - 6) + "..."

    def test_offsets_account_for_stripped_indentation(self):
        line = " " * 50 + "a" * 1000 + "NEEDLE" + "b" * 1000
        assert "NEEDLE" in match_excerpt(line, 1050, 1056)
