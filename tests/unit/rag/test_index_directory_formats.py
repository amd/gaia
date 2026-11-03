# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A folder import indexes every format a single-file import does (#3871)."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools import rag_tools
from gaia.agents.tools.rag_tools import RAGToolsMixin
from gaia.rag import sdk as rag_sdk
from gaia.rag.sdk import (
    FOLDER_INDEX_EXTENSIONS,
    RAGSDK,
    folder_index_skip_reason,
)
from gaia.utils.document_formats import UNSUPPORTED_DOCUMENTS
from gaia.utils.image_formats import IMAGE_EXTENSIONS

OFFICE = ["report.docx", "slides.pptx", "data.xlsx"]
TEXT = ["notes.txt", "readme.md", "guide.rst", "table.csv", "records.json"]
CODE = ["main.py", "app.ts", "lib.go", "mod.rs", "style.css", "conf.yaml"]
UNSUPPORTED = [
    "legacy.doc",
    "old.xls",
    "photo.png",
    "archive.zip",
    "Makefile",
    "prod.env",
]


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


class _RagHost(RAGToolsMixin):
    def __init__(self, rag):
        self.rag = rag
        self.indexed_files = set()
        self.current_session = None
        self.max_chunks = 5
        self.rebuild_system_prompt = MagicMock()


def _index_dir(tmp_path, names, *, recursive=False, rag=None):
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"content")
    if rag is None:
        rag = MagicMock()
        rag.index_document.return_value = {"success": True}
    _RagHost(rag).register_rag_tools()
    result = _TOOL_REGISTRY["index_directory"]["function"](
        directory_path=str(tmp_path), recursive=recursive
    )
    sent = {Path(c.args[0]).name for c in rag.index_document.call_args_list}
    return result, sent, rag


class TestSelection:
    @pytest.mark.parametrize("recursive", [False, True])
    def test_every_supported_file_reaches_indexing(self, tmp_path, recursive):
        names = OFFICE + TEXT + CODE
        result, sent, _ = _index_dir(tmp_path, names, recursive=recursive)
        assert sent == set(names)
        assert result["indexed_count"] == len(names)
        assert result["skipped_count"] == 0
        assert result["skipped_files"] == []

    @pytest.mark.parametrize("recursive", [False, True])
    def test_unsupported_files_are_skipped_with_reasons(self, tmp_path, recursive):
        result, sent, _ = _index_dir(
            tmp_path, ["notes.txt"] + UNSUPPORTED, recursive=recursive
        )
        assert sent == {"notes.txt"}
        assert result["skipped_count"] == len(UNSUPPORTED)
        reasons = {s["file"]: s["reason"] for s in result["skipped_files"]}
        assert set(reasons) == set(UNSUPPORTED)
        assert ".docx" in reasons["legacy.doc"]
        assert ".xlsx" in reasons["old.xls"]
        assert "vision model" in reasons["photo.png"]
        assert ".zip" in reasons["archive.zip"]
        assert "no file extension" in reasons["Makefile"]
        assert "secrets" in reasons["prod.env"]

    def test_recursive_walk_reaches_nested_office_files(self, tmp_path):
        names = ["top.docx", "a/b/deep.pptx", "a/sheet.xlsx", "a/b/skip.bin"]
        result, sent, _ = _index_dir(tmp_path, names, recursive=True)
        assert sent == {"top.docx", "deep.pptx", "sheet.xlsx"}
        assert result["skipped_files"] == [
            {
                "file": "a/b/skip.bin",
                "reason": ".bin files are not a supported document type",
            }
        ]

    def test_non_recursive_ignores_subfolders(self, tmp_path):
        result, sent, _ = _index_dir(tmp_path, ["top.docx", "sub/deep.docx"])
        assert sent == {"top.docx"}
        assert result["skipped_count"] == 0

    @pytest.mark.parametrize("name", ["REPORT.DOCX", "Slides.PpTx", "Data.XLSX"])
    def test_extension_match_is_case_insensitive(self, tmp_path, name):
        _, sent, _ = _index_dir(tmp_path, [name])
        assert sent == {name}

    def test_empty_directory(self, tmp_path):
        result, sent, _ = _index_dir(tmp_path, [])
        assert sent == set()
        assert result["status"] == "success"
        assert result["indexed_count"] == 0
        assert result["skipped_files"] == []

    def test_skipped_list_is_capped(self, tmp_path):
        many = [f"f{i}.zip" for i in range(rag_tools._MAX_SKIPPED_REPORTED + 5)]
        result, _, _ = _index_dir(tmp_path, many)
        assert result["skipped_count"] == len(many)
        assert len(result["skipped_files"]) == rag_tools._MAX_SKIPPED_REPORTED
        assert result["skipped_files_truncated"] is True

    def test_skipped_list_at_cap_is_not_truncated(self, tmp_path):
        many = [f"f{i}.zip" for i in range(rag_tools._MAX_SKIPPED_REPORTED)]
        result, _, _ = _index_dir(tmp_path, many)
        assert len(result["skipped_files"]) == rag_tools._MAX_SKIPPED_REPORTED
        assert "skipped_files_truncated" not in result


class TestFailureStaysAFailure:
    def test_failed_extraction_is_failed_not_skipped(self, tmp_path):
        rag = MagicMock()
        rag.index_document.side_effect = [
            {"success": False, "error": "python-docx is not installed"}
        ]
        result, sent, _ = _index_dir(tmp_path, ["report.docx"], rag=rag)
        assert sent == {"report.docx"}
        assert result["status"] == "error"
        assert result["failed_files"] == ["report.docx"]
        assert result["failure_reasons"] == {
            "report.docx": "python-docx is not installed"
        }
        assert result["skipped_count"] == 0

    def test_raising_extractor_is_failed_not_skipped(self, tmp_path):
        rag = MagicMock()
        rag.index_document.side_effect = ImportError("No module named 'pptx'")
        result, _, _ = _index_dir(tmp_path, ["slides.pptx"], rag=rag)
        assert result["failed_files"] == ["slides.pptx"]
        assert "pptx" in result["failure_reasons"]["slides.pptx"]
        assert result["skipped_files"] == []


class TestContract:
    """The folder set and the extractor dispatch cannot drift apart."""

    def test_folder_set_excludes_unreadable_image_and_secret_types(self):
        assert not FOLDER_INDEX_EXTENSIONS & set(UNSUPPORTED_DOCUMENTS)
        assert not FOLDER_INDEX_EXTENSIONS & set(IMAGE_EXTENSIONS)
        assert ".env" not in FOLDER_INDEX_EXTENSIONS

    @pytest.mark.parametrize("ext", sorted(FOLDER_INDEX_EXTENSIONS))
    def test_every_folder_extension_has_a_dedicated_extractor(self, ext):
        """No folder extension may reach the 'unknown type, try as text' branch."""
        sdk = RAGSDK.__new__(RAGSDK)
        sdk.log = MagicMock()
        sdk._refuse_binary = MagicMock(
            side_effect=AssertionError(f"{ext} fell through to the unknown branch")
        )
        for name in dir(RAGSDK):
            if name.startswith("_extract_text_from_") and name != (
                "_extract_text_from_file"
            ):
                setattr(sdk, name, MagicMock(return_value=("text", 1, {})))
        sdk._extract_text_from_epub = MagicMock(return_value=("text", 1))
        sdk._extract_text_from_text_file = MagicMock(return_value="text")
        for name in ("csv", "json", "xlsx", "docx"):
            setattr(sdk, f"_extract_text_from_{name}", MagicMock(return_value="t"))
        sdk._extract_text_from_file(f"doc{ext}")
        sdk._refuse_binary.assert_not_called()

    @pytest.mark.parametrize("ext", sorted(UNSUPPORTED_DOCUMENTS))
    def test_legacy_office_reason_names_the_conversion(self, ext):
        reason = folder_index_skip_reason(f"x{ext}")
        assert reason == rag_sdk.unsupported_document_message(f"x{ext}", ext)

    @pytest.mark.parametrize("name", ["x.docx", "x.DOCX", "dir.v1/x.pptx"])
    def test_supported_names(self, name):
        assert folder_index_skip_reason(name) is None

    @pytest.mark.parametrize("name", ["x.docx.zip", "x.", "noext", ".hidden"])
    def test_unsupported_names(self, name):
        assert folder_index_skip_reason(name)
