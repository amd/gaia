# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Rebuilding an empty repository must not leave searchable deleted code."""

from unittest.mock import patch

import numpy as np
import pytest

from gaia.code_index.sdk import CodeIndexConfig, CodeIndexSDK


@pytest.fixture
def indexed_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "old.py"
    source.write_text("def obsolete_function():\n    return 1\n", encoding="utf-8")
    config = CodeIndexConfig(repo_path=str(repo), cache_dir=str(tmp_path / "cache"))
    sdk = CodeIndexSDK(config)
    with patch.object(
        sdk,
        "_encode_texts_with_sync",
        side_effect=lambda texts, chunks: (
            np.ones((len(chunks), 4), dtype=np.float32),
            chunks,
        ),
    ):
        assert sdk.index_repository().chunks_created == 1
    assert sdk.is_indexed()
    return sdk, source


@pytest.mark.parametrize("change", ["empty", "delete", "exclude"])
def test_successful_empty_reindex_clears_memory_and_disk(indexed_repo, change):
    sdk, source = indexed_repo
    if change == "empty":
        source.write_text("", encoding="utf-8")
    elif change == "delete":
        source.unlink()
    else:
        (source.parent / ".gitignore").write_text("old.py\n", encoding="utf-8")

    with patch.object(sdk, "_load_embedder", side_effect=AssertionError("no model")):
        assert sdk.index_repository().chunks_created == 0
        assert sdk.search("obsolete_function") == []
        assert not sdk.is_indexed()
        assert not sdk.get_status()["indexed"]

    reopened = CodeIndexSDK(sdk.config)
    assert not reopened.get_status()["indexed"]
    assert reopened.search("obsolete_function") == []


@pytest.mark.parametrize("failure", ["scan", "parse", "embed", "unreadable"])
def test_failed_reindex_keeps_the_existing_generation(indexed_repo, failure):
    sdk, source = indexed_repo
    source.write_text("def replacement():\n    return 2\n", encoding="utf-8")
    before = sdk._meta_path.read_bytes()
    if failure == "scan":
        failing_call = patch.object(sdk, "_discover_files", side_effect=OSError("scan"))
    elif failure == "parse":
        failing_call = patch(
            "gaia.code_index.parsers.chunk_code_file", side_effect=RuntimeError("parse")
        )
    elif failure == "embed":
        failing_call = patch.object(
            sdk, "_encode_texts_with_sync", side_effect=RuntimeError("embed")
        )
    else:
        failing_call = patch.object(sdk, "_read_file_safe", return_value=None)
    with failing_call, pytest.raises((OSError, RuntimeError)):
        sdk.index_repository()
    assert sdk._meta_path.read_bytes() == before
    assert sdk._faiss_index.ntotal == 1
    assert CodeIndexSDK(sdk.config).get_status()["total_chunks"] == 1


def test_fresh_empty_repository_needs_no_model(tmp_path):
    sdk = CodeIndexSDK(
        CodeIndexConfig(repo_path=str(tmp_path), cache_dir=str(tmp_path / "cache"))
    )
    with patch.object(sdk, "_load_embedder", side_effect=AssertionError("no model")):
        assert sdk.index_repository().chunks_created == 0
        assert sdk.search("anything") == []
