# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Content search must read every text file, not a list of known extensions.

Grepping a Python project for a version string did not look in
``pyproject.toml`` — the extension allowlist omitted ``.toml`` — and returned
``status: success`` with a partial result. Asked to bump a version "wherever
it is declared", the agent edited what it could see and reported done.

Any such list is wrong for the next language someone searches, so the filter
is now the rule ``grep -r`` uses: skip files containing a NUL byte, read the
rest.
"""

import os

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_tools import FileSearchToolsMixin, _looks_binary


class _Host(FileSearchToolsMixin):
    pass


@pytest.fixture
def grep(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    _Host().register_file_search_tools()
    yield _TOOL_REGISTRY["search_file_content"]["function"]
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _files(result):
    return {
        os.path.basename(m.get("file") or m.get("path", ""))
        for m in (result.get("matches") or [])
    }


#: One declaration per file, in the formats a real project actually uses.
VERSION_SITES = {
    "pyproject.toml": 'version = "1.2.0"',
    "setup.cfg": "version = 1.2.0",
    "pkg.py": '__version__ = "1.2.0"',
    "CHANGELOG.md": "## 1.2.0",
    "app.ts": 'const V = "1.2.0";',
    "main.go": 'const V = "1.2.0"',
    "Dockerfile": "ENV VERSION=1.2.0",
    "Makefile": "VERSION := 1.2.0",
}


class TestEveryTextFileIsSearched:
    @pytest.mark.parametrize("name", sorted(VERSION_SITES))
    def test_a_declaration_is_found_whatever_the_file_is_called(
        self, grep, tmp_path, name
    ):
        (tmp_path / name).write_text(VERSION_SITES[name], encoding="utf-8")
        assert name in _files(grep(pattern="1.2.0", directory="."))

    def test_all_sites_are_found_in_one_pass(self, grep, tmp_path):
        for name, text in VERSION_SITES.items():
            (tmp_path / name).write_text(text, encoding="utf-8")
        assert _files(grep(pattern="1.2.0", directory=".")) == set(VERSION_SITES)


class TestBinariesAreStillSkipped:
    def test_a_binary_holding_the_pattern_is_not_reported(self, grep, tmp_path):
        (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00 1.2.0")
        (tmp_path / "notes.md").write_text("1.2.0", encoding="utf-8")
        found = _files(grep(pattern="1.2.0", directory="."))
        assert "notes.md" in found
        assert "logo.png" not in found

    def test_the_sniff_reads_text_and_binary_correctly(self, tmp_path):
        text = tmp_path / "a.txt"
        text.write_text("plain content", encoding="utf-8")
        blob = tmp_path / "a.bin"
        blob.write_bytes(b"\x00\x01\x02")
        assert not _looks_binary(text)
        assert _looks_binary(blob)

    def test_an_unreadable_path_reads_as_binary(self, tmp_path):
        # Skipping what we cannot open is honest; treating it as text would
        # raise inside the search loop and fail the whole call.
        assert _looks_binary(tmp_path / "does-not-exist")


class TestNarrowingStillWorks:
    def test_an_explicit_file_pattern_still_restricts_the_search(self, grep, tmp_path):
        (tmp_path / "a.py").write_text("1.2.0", encoding="utf-8")
        (tmp_path / "b.md").write_text("1.2.0", encoding="utf-8")
        found = _files(grep(pattern="1.2.0", directory=".", file_pattern="*.py"))
        assert found == {"a.py"}
