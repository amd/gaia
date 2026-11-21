# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""index_document given a folder says it's a folder, never that a file is empty.

A folder's size reads as 0 on Windows, so the model was told "File is empty"
and passed it on to the user as damaged documents.
"""

from pathlib import Path
from unittest.mock import patch

from gaia.rag.sdk import RAGSDK, RAGConfig


def _rag(tmp_path: Path) -> RAGSDK:
    with (
        patch.object(RAGSDK, "_check_dependencies", return_value=None),
        patch("gaia.rag.sdk.AgentSDK", autospec=True),
    ):
        return RAGSDK(
            config=RAGConfig(
                cache_dir=str(tmp_path / ".gaia"),
                allowed_paths=[str(tmp_path)],
                show_stats=False,
                use_local_llm=False,
            )
        )


def test_a_folder_is_named_as_a_folder(tmp_path):
    folder = tmp_path / "documents"
    folder.mkdir()
    (folder / "policy.docx").write_bytes(b"PK")

    result = _rag(tmp_path).index_document(str(folder))

    assert result["success"] is False
    assert "is a folder, not a file" in result["error"]
    assert "empty" not in result["error"].lower()
    assert not result.get("empty_file")


def test_an_empty_file_is_still_called_empty(tmp_path):
    empty = tmp_path / "zero.txt"
    empty.write_bytes(b"")

    result = _rag(tmp_path).index_document(str(empty))

    assert result["error"] == "File is empty"
