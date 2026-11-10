# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A document too big to index inside the tool budget must not hang (#4744).

A 1,667-page PDF embeds for longer than the 600 s tool limit on a CPU
embedder, so ``index_document`` sat silent for ten minutes and was abandoned.
The tool now hands a slow index to a background thread after a short budget
and says so; the SDK embeds outside its state lock so searches keep working.
"""

import os
import threading
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools import rag_tools
from gaia.agents.tools.rag_tools import RAGToolsMixin
from gaia.rag.sdk import RAGSDK, RAGConfig

# The tool call itself runs on a thread joined with this deadline, so a
# regression back to a blocking index fails instead of hanging the suite.
CALL_DEADLINE_S = 5.0


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


@pytest.fixture(autouse=True)
def _short_budget(monkeypatch):
    monkeypatch.setattr(rag_tools, "INDEX_FOREGROUND_BUDGET_S", 0.2, raising=False)


class _RagHost(RAGToolsMixin):
    def __init__(self, rag):
        self.rag = rag
        self.indexed_files = set()
        self.current_session = None
        self.rebuild_system_prompt = MagicMock()


def _call(name, **kwargs):
    holder = {}
    worker = threading.Thread(
        target=lambda: holder.update(result=_TOOL_REGISTRY[name]["function"](**kwargs)),
        daemon=True,
    )
    worker.start()
    worker.join(CALL_DEADLINE_S)
    assert not worker.is_alive(), f"{name} blocked on a slow index"
    return holder["result"]


@pytest.fixture
def slow_rag():
    """A RAG whose indexing embeds 40 of 100 chunks, then waits for release."""
    release = threading.Event()
    rag = MagicMock()
    rag.indexed_files = set()

    def index_document(path, progress_callback=None, wait_callback=None):
        if progress_callback is not None:
            progress_callback(0, 100)
            progress_callback(40, 100)
        assert release.wait(30)
        rag.indexed_files.add(path)
        return {"success": True, "file_name": "big.pdf", "num_chunks": 100}

    rag.index_document.side_effect = index_document
    yield rag, release
    release.set()


def test_slow_index_returns_in_progress_then_success(tmp_path, slow_rag):
    rag, release = slow_rag
    doc = tmp_path / "big.pdf"
    doc.write_bytes(b"%PDF-1.4")
    host = _RagHost(rag)
    host.register_rag_tools()

    first = _call("index_document", file_path=str(doc))
    assert first["status"] == "in_progress", first
    assert "background" in first["message"]
    assert "40 of 100 chunks embedded" in first["message"]
    assert first["total_chunks"] == 100
    assert "read the relevant part of the file directly" in first["hint"]
    assert str(doc) not in host.indexed_files

    # Asking again waits on the same job instead of starting a second index.
    again = _call("index_document", file_path=str(doc))
    assert again["status"] == "in_progress"
    assert rag.index_document.call_count == 1

    release.set()
    done = _call("index_document", file_path=str(doc))
    assert done["status"] == "success", done
    assert rag.index_document.call_count == 1
    assert str(doc) in host.indexed_files
    host.rebuild_system_prompt.assert_called()


class _ObservedEvent(threading.Event):
    def __init__(self):
        super().__init__()
        self.waited_on = threading.Event()

    def wait(self, timeout=None):
        self.waited_on.set()
        return super().wait(timeout)


def test_asking_while_a_finished_index_is_recorded_does_not_index_again(
    tmp_path, slow_rag, monkeypatch
):
    rag, release = slow_rag
    doc = tmp_path / "big.pdf"
    doc.write_bytes(b"%PDF-1.4")
    host = _RagHost(rag)
    host.register_rag_tools()
    assert _call("index_document", file_path=str(doc))["status"] == "in_progress"
    (job,) = rag_tools._INDEX_JOBS[rag].values()
    job.done = _ObservedEvent()

    recording, proceed = threading.Event(), threading.Event()
    record = rag_tools._finish_unattended

    def held_open(*args):
        recording.set()
        assert proceed.wait(CALL_DEADLINE_S)
        record(*args)

    monkeypatch.setattr(rag_tools, "_finish_unattended", held_open)
    release.set()
    assert recording.wait(CALL_DEADLINE_S)
    # Embedded but not yet on the agent: not "indexing", not searchable here.
    assert rag_tools.documents_still_indexing(rag) == []
    assert str(doc) not in host.indexed_files

    holder = {}
    caller = threading.Thread(
        target=lambda: holder.update(
            result=_TOOL_REGISTRY["index_document"]["function"](file_path=str(doc))
        ),
        daemon=True,
    )
    caller.start()
    try:
        assert job.done.waited_on.wait(
            CALL_DEADLINE_S
        ), "the caller started a second index instead of waiting on the first"
    finally:
        proceed.set()
        caller.join(CALL_DEADLINE_S)
    assert holder["result"]["status"] == "success", holder
    assert rag.index_document.call_count == 1
    assert str(doc) in host.indexed_files
    assert not rag_tools._INDEX_JOBS.get(rag)


class _StaleSet(set):
    """A set whose next *stale* membership checks answer from before *finish* ran."""

    finish = None
    stale = 0
    before = None

    def __contains__(self, item):
        if not self.stale:
            return super().__contains__(item)
        self.stale -= 1
        if self.before is None:
            self.before = set(self)
            self.finish()
        return item in self.before


def test_an_index_finishing_right_after_the_guard_is_not_run_again(tmp_path, slow_rag):
    rag, release = slow_rag
    doc = tmp_path / "big.pdf"
    doc.write_bytes(b"%PDF-1.4")
    host = _RagHost(rag)
    host.indexed_files = _StaleSet()
    host.register_rag_tools()
    assert _call("index_document", file_path=str(doc))["status"] == "in_progress"
    (job,) = rag_tools._INDEX_JOBS[rag].values()

    def finish_and_leave_the_map():
        release.set()
        deadline = time.monotonic() + CALL_DEADLINE_S
        while job.ended is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert job.ended is not None

    # The guard sees the file unindexed, then the job records it and ends.
    host.indexed_files.finish = finish_and_leave_the_map
    host.indexed_files.stale = 2  # the guard checks the path and its real path
    done = _call("index_document", file_path=str(doc))

    assert done["status"] == "success", done
    assert rag.index_document.call_count == 1
    assert str(doc) in host.indexed_files


def test_an_index_that_ended_before_the_ask_does_not_count(tmp_path, slow_rag):
    rag, release = slow_rag
    doc = tmp_path / "big.pdf"
    doc.write_bytes(b"%PDF-1.4")
    release.set()
    result, job = rag_tools._index_within_budget(rag, str(doc))
    assert result["success"] and job.ended is not None

    # The file changed after that index: asking now must index it again.
    again, _ = rag_tools._index_within_budget(rag, str(doc), since=time.perf_counter())
    assert again["success"]
    assert rag.index_document.call_count == 2


def test_still_indexing_lists_a_document_only_until_it_is_searchable(
    tmp_path, slow_rag
):
    rag, release = slow_rag
    doc = tmp_path / "big.pdf"
    doc.write_bytes(b"%PDF-1.4")
    host = _RagHost(rag)
    host.register_rag_tools()
    assert rag_tools.documents_still_indexing(rag) == []
    assert rag_tools.documents_still_indexing(None) == []

    assert _call("index_document", file_path=str(doc))["status"] == "in_progress"
    (pending,) = rag_tools.documents_still_indexing(rag)
    assert pending.endswith("big.pdf")
    assert not rag.indexed_files

    # Finished in the background: searchable, with nobody having asked again.
    release.set()
    deadline = time.monotonic() + CALL_DEADLINE_S
    while rag_tools.documents_still_indexing(rag) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert rag_tools.documents_still_indexing(rag) == []
    assert rag.indexed_files


def test_query_specific_file_does_not_block_on_auto_index(tmp_path, slow_rag):
    rag, _ = slow_rag
    doc = tmp_path / "big.pdf"
    doc.write_bytes(b"%PDF-1.4")
    host = _RagHost(rag)
    host.register_rag_tools()

    result = _call("query_specific_file", file_path=str(doc), query="needle")
    assert result["status"] == "in_progress", result
    assert result["file_name"] == "big.pdf"


def test_fast_index_answers_in_the_same_call(tmp_path):
    rag = MagicMock()
    rag.index_document.return_value = {"success": True, "file_name": "small.txt"}
    doc = tmp_path / "small.txt"
    doc.write_text("hello", encoding="utf-8")
    host = _RagHost(rag)
    host.register_rag_tools()

    result = _call("index_document", file_path=str(doc))
    assert result["status"] == "success", result
    assert str(doc) in host.indexed_files


def test_background_failure_is_reported_not_swallowed(tmp_path):
    rag = MagicMock()
    rag.index_document.side_effect = ConnectionError("embedder unreachable")
    doc = tmp_path / "broken.pdf"
    doc.write_bytes(b"%PDF-1.4")
    host = _RagHost(rag)
    host.register_rag_tools()

    result = _call("index_document", file_path=str(doc))
    assert result["status"] == "error"
    assert "embedder unreachable" in result["error"]


def _until(predicate):
    deadline = time.monotonic() + CALL_DEADLINE_S
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    return predicate()


@pytest.mark.parametrize("tool_name", ["index_document", "query_specific_file"])
def test_background_index_is_saved_to_the_session_without_a_second_call(
    tmp_path, slow_rag, tool_name
):
    rag, release = slow_rag
    doc = tmp_path / "big.pdf"
    doc.write_bytes(b"%PDF-1.4")
    host = _RagHost(rag)
    host.current_session = MagicMock(indexed_documents=[])
    host.session_manager = MagicMock()
    host.session_manager.save_session.return_value = True
    host.register_rag_tools()

    args = {"file_path": str(doc)}
    if tool_name == "query_specific_file":
        args["query"] = "needle"
    assert _call(tool_name, **args)["status"] == "in_progress"
    assert host.current_session.indexed_documents == []

    release.set()
    assert _until(lambda: host.current_session.indexed_documents == [str(doc)])
    assert str(doc) in host.indexed_files
    host.session_manager.save_session.assert_called_with(host.current_session)
    host.rebuild_system_prompt.assert_called()
    assert _until(lambda: not rag_tools._INDEX_JOBS.get(rag))
    assert rag_tools.documents_failed_indexing(rag) == []


def test_a_finished_background_job_is_not_handed_back_as_a_stale_result(tmp_path):
    release = threading.Event()
    rag = MagicMock()
    rag.indexed_files = set()

    def index_document(path, progress_callback=None, wait_callback=None):
        assert release.wait(30)
        rag.indexed_files.add(path)
        return {"success": True}

    rag.index_document.side_effect = index_document
    path = str(tmp_path / "big.pdf")
    try:
        result, job = rag_tools._index_within_budget(rag, path, budget=0)
        assert result is None
    finally:
        release.set()
    assert _until(lambda: not rag_tools._INDEX_JOBS.get(rag))

    # The document was removed meanwhile: asking again must index it again.
    rag.indexed_files.clear()
    result, again = rag_tools._index_within_budget(rag, path, budget=5)
    assert result == {"success": True}
    assert again is not job
    assert rag.index_document.call_count == 2
    assert rag.indexed_files == {path}


def test_two_spellings_of_one_file_share_one_job(tmp_path, slow_rag):
    rag, release = slow_rag
    (tmp_path / "sub").mkdir()
    plain = str(tmp_path / "big.pdf")
    roundabout = str(tmp_path / "sub" / ".." / "big.pdf")

    _, first = rag_tools._index_within_budget(rag, plain, budget=0)
    _, second = rag_tools._index_within_budget(rag, roundabout, budget=0)

    assert second is first
    release.set()
    assert first.done.wait(CALL_DEADLINE_S)
    assert rag.index_document.call_count == 1


@pytest.mark.parametrize(
    "outcome, reason",
    [
        (ConnectionError("embedder unreachable"), "embedder unreachable"),
        ({"success": False, "error": "no text in file"}, "no text in file"),
    ],
)
def test_a_background_failure_is_reported_until_indexed_again(
    tmp_path, outcome, reason
):
    release = threading.Event()
    rag = MagicMock()
    rag.indexed_files = set()

    def index_document(path, progress_callback=None, wait_callback=None):
        assert release.wait(30)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    rag.index_document.side_effect = index_document
    host = _RagHost(rag)
    host.current_session = MagicMock(indexed_documents=[])
    doc = tmp_path / "big.pdf"
    doc.write_bytes(b"%PDF-1.4")
    host.register_rag_tools()

    assert _call("index_document", file_path=str(doc))["status"] == "in_progress"
    release.set()
    assert _until(lambda: rag_tools.documents_failed_indexing(rag))
    ((path, why),) = rag_tools.documents_failed_indexing(rag)
    assert path == os.path.realpath(doc)
    assert reason in why
    assert host.current_session.indexed_documents == []
    assert str(doc) not in host.indexed_files

    rag.index_document.side_effect = None
    rag.index_document.return_value = {"success": True}
    assert _call("index_document", file_path=str(doc))["status"] == "success"
    assert rag_tools.documents_failed_indexing(rag) == []


@pytest.fixture
def sdk(tmp_path):
    pytest.importorskip("faiss", reason="Real vector-index tests require faiss-cpu")
    with patch("gaia.rag.sdk.AgentSDK"):
        rag = RAGSDK(
            RAGConfig(cache_dir=str(tmp_path / "cache"), allowed_paths=[str(tmp_path)])
        )
    rag._load_embedder = lambda: None
    rag._encode_texts = lambda texts, **kwargs: np.ones(
        (len(texts), 4), dtype="float32"
    )
    rag._get_hmac_key = lambda: b"test-index-key" * 3
    return rag


def test_index_document_reports_embedding_progress(sdk, tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("content", encoding="utf-8")
    seen = []
    with (
        patch("gaia.rag.sdk.PROGRESS_SLICE_CHUNKS", 2),
        patch.object(
            sdk, "_split_text_into_chunks", return_value=["a", "b", "c", "d", "e"]
        ),
    ):
        result = sdk.index_document(
            str(doc), progress_callback=lambda *a: seen.append(a)
        )
    assert result["success"]
    assert seen == [(0, 5), (2, 5), (4, 5), (5, 5)]
    assert sdk.index.ntotal == 5


def test_searches_are_not_locked_out_while_a_document_embeds(sdk, tmp_path):
    first = tmp_path / "first.txt"
    first.write_text("Already searchable.", encoding="utf-8")
    assert sdk.index_document(str(first))["success"]

    embedding = threading.Event()
    release = threading.Event()
    encode = sdk._encode_texts

    def slow_encode(texts, **kwargs):
        embedding.set()
        assert release.wait(10)
        return encode(texts, **kwargs)

    sdk._encode_texts = slow_encode
    second = tmp_path / "second.txt"
    second.write_text("Slow to embed.", encoding="utf-8")
    worker = threading.Thread(target=sdk.index_document, args=(str(second),))
    worker.start()
    try:
        assert embedding.wait(5)
        acquired = sdk._state_lock.acquire(timeout=1)
        assert acquired, "index_document held the state lock while embedding"
        sdk._state_lock.release()
        assert sdk._snapshot_query_state()["indexed_file_count"] == 1
    finally:
        release.set()
        worker.join(10)
    assert sdk.indexed_files == {str(first), str(second)}
