# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Rebuilding an empty repository must not leave searchable deleted code."""

import os
from pathlib import Path
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
        failing_call = patch.object(
            sdk, "_read_file_safe", side_effect=PermissionError("access denied")
        )
    with failing_call, pytest.raises((OSError, RuntimeError)):
        sdk.index_repository()
    assert sdk._meta_path.read_bytes() == before
    assert sdk._faiss_index.ntotal == 1
    assert CodeIndexSDK(sdk.config).get_status()["total_chunks"] == 1


@pytest.mark.parametrize(
    "change", ["binary", "policy", "vanished_stat", "vanished_read"]
)
def test_intentional_skips_can_establish_an_empty_index(indexed_repo, change):
    sdk, source = indexed_repo
    if change == "binary":
        source.write_bytes(b"\x00binary content")
        context = patch.object(
            sdk, "_load_embedder", side_effect=AssertionError("no model")
        )
    elif change == "policy":
        context = patch.object(
            sdk._path_validator, "is_path_allowed", return_value=False
        )
    else:
        target = "os.path.getsize" if change == "vanished_stat" else "builtins.open"
        original = os.path.getsize if change == "vanished_stat" else open

        def disappear(path, *args, **kwargs):
            if Path(path) == source:
                source.unlink(missing_ok=True)
            return original(path, *args, **kwargs)

        context = patch(target, side_effect=disappear)
    with context:
        assert sdk.index_repository().chunks_created == 0
    assert not sdk.is_indexed()
    assert not sdk._meta_path.exists()
    assert not sdk._index_path.exists()


@pytest.mark.parametrize("candidate", ["no_files", "binary", "empty_text"])
@pytest.mark.parametrize("limit", ["max_walk_entries", "max_files"])
def test_truncated_scan_cannot_establish_empty_index(indexed_repo, candidate, limit):
    sdk, source = indexed_repo
    source.unlink()
    nested = source.parent / "nested"
    nested.mkdir()
    (nested / "hidden.py").write_text("def hidden(): pass\n", encoding="utf-8")
    if candidate == "binary":
        source.write_bytes(b"\x00binary")
    elif candidate == "empty_text":
        source.write_text("", encoding="utf-8")
    setattr(sdk.config, limit, 0)
    before = sdk._meta_path.read_bytes()
    with pytest.raises(RuntimeError, match="max_walk_entries"):
        sdk.index_repository()
    assert sdk._meta_path.read_bytes() == before
    assert sdk._faiss_index.ntotal == 1


def test_actual_read_permission_error_retains_cache(indexed_repo):
    sdk, source = indexed_repo
    original = open

    def deny_source(path, *args, **kwargs):
        if Path(path) == source:
            raise PermissionError("source locked")
        return original(path, *args, **kwargs)

    before = sdk._meta_path.read_bytes()
    with patch("builtins.open", side_effect=deny_source):
        with pytest.raises(RuntimeError, match="permissions and retry"):
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


@pytest.mark.parametrize("failure", ["missing_root", "walk", "stat"])
def test_discovery_errors_do_not_erase_previous_index(indexed_repo, failure):
    sdk, source = indexed_repo
    metadata_before = sdk._meta_path.read_bytes()
    index_before = sdk._index_path.read_bytes()
    if failure == "missing_root":
        source.parent.rename(source.parent.with_name("temporarily-unavailable"))
        with pytest.raises(RuntimeError, match="available and readable"):
            sdk.index_repository()
    else:
        target = "os.scandir" if failure == "walk" else "os.path.getsize"
        original = os.scandir if failure == "walk" else os.path.getsize

        def deny_repository_access(path):
            if Path(path) in (source, source.parent):
                raise PermissionError("access denied")
            return original(path)

        with patch(target, side_effect=deny_repository_access):
            with pytest.raises(RuntimeError, match="access denied"):
                sdk.index_repository()
    assert sdk._meta_path.read_bytes() == metadata_before
    assert sdk._index_path.read_bytes() == index_before
    assert sdk._faiss_index.ntotal == 1
