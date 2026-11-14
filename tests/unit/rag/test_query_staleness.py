# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Searches never serve an edited document's old text (#6939, #6932)."""

import errno
import os
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools import rag_tools
from gaia.agents.tools.rag_tools import RAGToolsMixin
from gaia.rag.sdk import RAGSDK, RAGConfig
from gaia.utils import file_lock

OLD = "The launch is on Tuesday in Lisbon. " * 30
NEW = "The launch moved to Friday in Oslo. " * 30
OTHER = "The budget review is in March. " * 30


def _make(tmp_path):
    pytest.importorskip("faiss", reason="Real vector-index tests require faiss-cpu")
    config = RAGConfig(
        cache_dir=str(tmp_path / "cache"),
        allowed_paths=[str(tmp_path)],
        use_llm_chunking=False,
    )
    with patch("gaia.rag.sdk.AgentSDK"):
        sdk = RAGSDK(config)

    def _encode(texts, **_kwargs):
        return np.ones((len(texts), 4), dtype="float32")

    sdk._load_embedder = lambda: None
    sdk._encode_texts = _encode
    sdk._encode_query = lambda _query: np.ones((1, 4), dtype="float32")
    sdk._get_hmac_key = lambda: b"test-cache-key" * 3
    return sdk


class _RagHost(RAGToolsMixin):
    def __init__(self, rag):
        self.rag = rag
        self.indexed_files = set()
        self.current_session = None
        self.max_chunks = 5
        self.rebuild_system_prompt = MagicMock()

    def _generate_search_keys(self, query):
        return [query]


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


def _age(path: Path, seconds: int = 3600):
    """Backdate *path* so its timestamps are well outside the racy window."""
    then = time.time() - seconds
    os.utime(path, (then, then))


def _write_keeping_stat(path: Path, text: str):
    """Rewrite *path* with the same size and modification time as before."""
    st = path.stat()
    path.write_text(text, encoding="utf-8")
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))


def _setup(tmp_path, *docs):
    sdk = _make(tmp_path)
    host = _RagHost(sdk)
    paths = []
    for name, text, aged in docs:
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        if aged:
            _age(path)
        assert sdk.index_document(str(path))["success"]
        host.indexed_files.add(str(path))
        paths.append(path)
    host.register_rag_tools()
    return sdk, paths


def _tool(name):
    return _TOOL_REGISTRY[name]["function"]


def _library(query="launch"):
    return _tool("query_documents")(query=query)


def _one_file(path, query="launch"):
    return _tool("query_specific_file")(file_path=str(path), query=query)


def _served(result) -> str:
    return " ".join(c["content"] for c in result.get("chunks", []))


# --- the bug: an edit is served stale ---------------------------------------


def test_library_search_serves_the_edited_text(tmp_path):
    _, (doc,) = _setup(tmp_path, ("plan.txt", OLD, False))
    doc.write_text(NEW, encoding="utf-8")

    result = _library()

    assert result["status"] == "success", result
    assert "Oslo" in _served(result)
    assert "Lisbon" not in str(result)


def test_file_search_serves_the_edited_text(tmp_path):
    _, (doc,) = _setup(tmp_path, ("plan.txt", OLD, False))
    doc.write_text(NEW, encoding="utf-8")

    result = _one_file(doc)

    assert result["status"] == "success", result
    assert "Oslo" in _served(result)
    assert "Lisbon" not in str(result)


def test_exact_text_search_serves_the_edited_text(tmp_path):
    _, (doc,) = _setup(tmp_path, ("plan.txt", OLD, False))
    doc.write_text(NEW, encoding="utf-8")
    search = _tool("search_indexed_chunks")

    assert search(pattern="Oslo")["count"] > 0
    assert search(pattern="Lisbon")["count"] == 0


def test_edit_keeping_size_and_mtime_right_after_indexing_is_caught(tmp_path):
    _, (doc,) = _setup(tmp_path, ("plan.txt", OLD, False))
    _write_keeping_stat(doc, OLD.replace("Lisbon", "Madrid"))

    assert "Madrid" in _served(_one_file(doc))
    assert "Madrid" in _served(_library())


def test_edit_to_an_aged_file_is_caught(tmp_path):
    _, (doc,) = _setup(tmp_path, ("plan.txt", OLD, True))
    doc.write_text(NEW, encoding="utf-8")

    assert "Oslo" in _served(_library())
    assert "Lisbon" not in _served(_library())


# --- adversarial: never pay for an unchanged file ---------------------------


@pytest.mark.parametrize("aged", [True, False], ids=["aged", "just-written"])
def test_unchanged_file_is_never_reembedded(tmp_path, aged):
    sdk, (doc,) = _setup(tmp_path, ("plan.txt", OLD, aged))
    reindexed = MagicMock(side_effect=sdk.index_document)
    sdk.index_document = reindexed

    for _ in range(3):
        assert "Lisbon" in _served(_library())
        assert "Lisbon" in _served(_one_file(doc))
        assert _tool("search_indexed_chunks")(pattern="Lisbon")["count"] > 0

    reindexed.assert_not_called()


def test_unchanged_aged_file_is_not_even_hashed(tmp_path):
    sdk, (doc,) = _setup(tmp_path, ("plan.txt", OLD, True))
    hashed = MagicMock(side_effect=sdk._content_key)
    sdk._content_key = hashed

    _library()
    _one_file(doc)
    _tool("search_indexed_chunks")(pattern="Lisbon")

    hashed.assert_not_called()


def test_touched_but_unchanged_file_is_hashed_once_then_trusted(tmp_path):
    sdk, (doc,) = _setup(tmp_path, ("plan.txt", OLD, True))
    _age(doc, 1800)
    hashed = MagicMock(side_effect=sdk._content_key)
    sdk._content_key = hashed
    reindexed = MagicMock(side_effect=sdk.index_document)
    sdk.index_document = reindexed

    assert "Lisbon" in _served(_library())
    assert hashed.call_count == 1
    assert "Lisbon" in _served(_library())
    assert hashed.call_count == 1
    reindexed.assert_not_called()


# --- adversarial: a file that cannot be checked is not served ---------------


def test_deleted_file_is_not_served(tmp_path):
    _, (doc, other) = _setup(
        tmp_path, ("plan.txt", OLD, True), ("budget.txt", OTHER, True)
    )
    doc.unlink()

    scoped = _one_file(doc)
    assert scoped["status"] == "error", scoped
    assert "no longer exists" in scoped["error"]
    assert "Lisbon" not in str(scoped)

    library = _library()
    assert library["status"] == "success", library
    assert "Lisbon" not in str(library)
    assert "March" in _served(library)
    assert [f["file"] for f in library["files_left_out"]] == ["plan.txt"]
    assert "plan.txt" in library["instruction"]

    assert _tool("search_indexed_chunks")(pattern="Lisbon")["count"] == 0


def test_library_of_only_deleted_files_reports_an_error(tmp_path):
    _, (doc,) = _setup(tmp_path, ("plan.txt", OLD, True))
    doc.unlink()

    result = _library()

    assert result["status"] == "error", result
    assert "plan.txt" in result["error"]
    assert "Lisbon" not in str(result)


def test_locked_changed_file_is_not_served(tmp_path, monkeypatch):
    sdk, (doc,) = _setup(tmp_path, ("plan.txt", OLD, True))
    doc.write_text(NEW, encoding="utf-8")
    monkeypatch.setattr(file_lock, "_WINDOWS", True)

    def _locked(*_args, **_kwargs):
        raise PermissionError(errno.EACCES, "Permission denied", str(doc))

    monkeypatch.setattr(sdk, "_safe_open", _locked)

    scoped = _one_file(doc)
    assert scoped["status"] == "error", scoped
    assert "another program" in scoped["error"]
    assert "Lisbon" not in str(scoped)
    assert _library()["status"] == "error"
    # The old generation is kept for when the file is readable again.
    assert str(doc.absolute()) in sdk.indexed_files


def _refuse_opening(monkeypatch, doc, error):
    from gaia.rag import sdk as sdk_module

    real_open = open

    def _open(file, *args, **kwargs):
        if Path(file) == doc.absolute():
            raise error
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(sdk_module, "open", _open, raising=False)


def test_unchanged_file_the_user_can_no_longer_read_is_not_served(
    tmp_path, monkeypatch
):
    """Removing read permission moves no stat field on Windows."""
    _, (doc, other) = _setup(
        tmp_path, ("acl_me.txt", OLD, True), ("budget.txt", OTHER, True)
    )
    _refuse_opening(
        monkeypatch, doc, PermissionError(errno.EACCES, "Access is denied", str(doc))
    )

    scoped = _one_file(doc)
    assert scoped["status"] == "error", scoped
    assert "can no longer be read" in scoped["error"]
    assert "Lisbon" not in str(scoped)

    library = _library()
    assert "Lisbon" not in str(library)
    assert "March" in _served(library)
    assert [f["reason"] for f in library["files_left_out"]] == ["denied"]


def test_unchanged_file_another_program_holds_is_called_locked(tmp_path, monkeypatch):
    _, (doc,) = _setup(tmp_path, ("plan.txt", OLD, True))
    sharing = PermissionError(errno.EACCES, "in use", str(doc))
    sharing.winerror = 32
    _refuse_opening(monkeypatch, doc, sharing)

    scoped = _one_file(doc)
    assert "another program" in scoped["error"]
    assert "Lisbon" not in str(scoped)


def test_failed_reindex_reports_the_error_and_serves_nothing(tmp_path):
    _, (doc,) = _setup(tmp_path, ("plan.txt", OLD, True))
    doc.write_text("   \n\t \n", encoding="utf-8")

    result = _one_file(doc)

    assert result["status"] == "error", result
    assert "plan.txt" in result["error"]
    assert "Lisbon" not in str(result)


# --- adversarial: a slow re-index never blocks searches ----------------------


def test_slow_reindex_reports_in_progress_without_holding_the_index_lock(
    tmp_path, monkeypatch
):
    sdk, (doc, _) = _setup(
        tmp_path, ("plan.txt", OLD, True), ("budget.txt", OTHER, True)
    )
    monkeypatch.setattr(rag_tools, "QUERY_REINDEX_BUDGET_S", 0.2)
    entered, release = threading.Event(), threading.Event()
    real = sdk._create_vector_index

    def _slow(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return real(*args, **kwargs)

    sdk._create_vector_index = _slow
    doc.write_text(NEW, encoding="utf-8")
    try:
        start = time.monotonic()
        scoped = _one_file(doc)
        assert time.monotonic() - start < 5
        assert scoped["status"] == "in_progress", scoped
        assert "Lisbon" not in str(scoped)
        assert entered.wait(5)

        # The re-embed runs outside the index lock.
        acquired = []

        def _probe():
            if sdk._state_lock.acquire(timeout=2):
                acquired.append(True)
                sdk._state_lock.release()

        probe = threading.Thread(target=_probe)
        probe.start()
        probe.join()
        assert acquired == [True]

        library = _library()
        assert library["status"] == "success", library
        assert "March" in _served(library)
        assert "Lisbon" not in str(library)
        assert [f["file"] for f in library["files_left_out"]] == ["plan.txt"]
    finally:
        release.set()

    deadline = time.monotonic() + 10
    while rag_tools.documents_still_indexing(sdk) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert "Oslo" in _served(_one_file(doc))


# --- the SDK check ------------------------------------------------------------


def test_freshness_states(tmp_path):
    sdk = _make(tmp_path)
    doc = tmp_path / "plan.txt"
    doc.write_text(OLD, encoding="utf-8")
    assert sdk.freshness(str(doc)) == "not_indexed"
    assert sdk.index_document(str(doc))["success"]
    assert sdk.freshness(str(doc)) == "current"

    doc.write_text(NEW, encoding="utf-8")
    assert sdk.freshness(str(doc)) == "changed"

    doc.unlink()
    assert sdk.freshness(str(doc)) == "missing"


def test_reindexing_an_edit_keeps_the_old_text_searchable_until_the_swap(tmp_path):
    """Replacing a generation never leaves a gap where the document is missing."""
    sdk = _make(tmp_path)
    doc = tmp_path / "plan.txt"
    doc.write_text(OLD, encoding="utf-8")
    assert sdk.index_document(str(doc))["success"]
    seen = []
    real = sdk._create_vector_index

    def _observe(*args, **kwargs):
        seen.append(str(doc.absolute()) in sdk.indexed_files)
        return real(*args, **kwargs)

    sdk._create_vector_index = _observe
    doc.write_text(NEW, encoding="utf-8")
    result = sdk.index_document(str(doc))

    assert result["success"] and result.get("reindexed") is True, result
    assert seen == [True]
    assert "Lisbon" not in " ".join(sdk.chunks)


# --- a left-out file never takes an unchanged file's chunks with it ----------


def test_identical_chunk_in_an_unchanged_file_is_still_served(tmp_path):
    _, (doc, copy) = _setup(tmp_path, ("plan.txt", OLD, True), ("copy.txt", OLD, True))
    doc.unlink()

    library = _library()
    assert library["status"] == "success", library
    assert "Lisbon" in _served(library)
    sources = {c["source_file"] for c in library["chunks"]}
    assert sources == {str(copy.resolve())}
    assert [f["file"] for f in library["files_left_out"]] == ["plan.txt"]

    exact = _tool("search_indexed_chunks")(pattern="Lisbon")
    assert exact["count"] > 0
    assert [f["file"] for f in exact["files_left_out"]] == ["plan.txt"]


# --- a failed re-index keeps the index tool's specific advice ----------------


def _failing_reindex(sdk, stats):
    real = sdk.index_document

    def _index(path, *args, **kwargs):
        if Path(path).name == "plan.txt":
            return {"success": False, "file_name": "plan.txt", **stats}
        return real(path, *args, **kwargs)

    sdk.index_document = _index


def test_reindex_with_the_embedder_down_keeps_its_next_step(tmp_path):
    sdk, (doc, _) = _setup(
        tmp_path, ("plan.txt", OLD, True), ("budget.txt", OTHER, True)
    )
    doc.write_text(NEW, encoding="utf-8")
    _failing_reindex(
        sdk,
        {"error": "Embedding model unavailable", "error_type": "embedding_unavailable"},
    )

    scoped = _one_file(doc)
    assert scoped["status"] == "error", scoped
    assert scoped["error_type"] == "embedding_unavailable"
    assert "gaia init --profile rag" in scoped["next_step"]
    assert "Lisbon" not in str(scoped)

    library = _library()
    assert library["status"] == "success", library
    (entry,) = library["files_left_out"]
    assert entry["error_type"] == "embedding_unavailable"
    assert "gaia init --profile rag" in entry["message"]
    assert "Lisbon" not in str(library)


def test_reindex_with_the_cache_busy_keeps_its_hint(tmp_path):
    sdk, (doc,) = _setup(tmp_path, ("plan.txt", OLD, True))
    doc.write_text(NEW, encoding="utf-8")
    _failing_reindex(
        sdk,
        {
            "error": "Document cache is busy",
            "error_type": "cache_busy",
            "cache_dir": str(tmp_path / "cache"),
        },
    )

    scoped = _one_file(doc)

    assert scoped["status"] == "error", scoped
    assert scoped["error_type"] == "cache_busy"
    assert "Another process is using the document cache" in scoped["hint"]
    assert "Lisbon" not in str(scoped)


# --- end to end ---------------------------------------------------------------


def _call_within(name, deadline_s=5.0, **kwargs):
    holder = {}
    worker = threading.Thread(
        target=lambda: holder.update(result=_tool(name)(**kwargs)), daemon=True
    )
    worker.start()
    worker.join(deadline_s)
    assert not worker.is_alive(), f"{name} blocked on a re-index"
    return holder["result"]


def test_a_real_sdk_serves_the_current_text_after_a_same_size_edit(tmp_path):
    """A same-size edit with the mtime restored still reaches a scoped query."""
    _, (doc,) = _setup(tmp_path, ("plan.txt", OLD, False))
    _write_keeping_stat(doc, NEW)

    result = _call_within(
        "query_specific_file", file_path=str(doc), query="launch location"
    )

    assert result["status"] == "success", result
    assert "Oslo" in _served(result)
    assert "Lisbon" not in _served(result)
