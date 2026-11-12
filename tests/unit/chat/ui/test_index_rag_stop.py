# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Stop ends the loading of a chat's documents between two of them."""

import threading

from gaia.ui._chat_helpers import _index_rag_with_progress


class _Handler:
    def __init__(self):
        self.cancelled = threading.Event()
        self.events = []

    def _emit(self, event):
        self.events.append(event)


class _Rag:
    def __init__(self, handler, stop_after):
        self.handler = handler
        self.stop_after = stop_after
        self.loaded = []

    def index_document(self, path):
        self.loaded.append(path)
        if len(self.loaded) == self.stop_after:
            self.handler.cancelled.set()
        return {"num_chunks": 1, "from_cache": True}


class _Agent:
    def __init__(self, rag):
        self.rag = rag
        self.indexed_files = set()


def test_stop_ends_the_loading_and_says_what_is_left():
    handler = _Handler()
    agent = _Agent(_Rag(handler, stop_after=2))
    paths = [f"/docs/doc{n}.txt" for n in range(5)]

    _index_rag_with_progress(agent, paths, handler)

    assert agent.rag.loaded == paths[:2]
    assert agent.indexed_files == set(paths[:2])
    result = handler.events[-1]
    assert result["type"] == "tool_result"
    assert result["success"] is False
    assert "Stopped" in result["summary"]
    assert "3 of 5" in result["summary"]


def test_an_unstopped_load_reports_every_document():
    handler = _Handler()
    agent = _Agent(_Rag(handler, stop_after=None))
    paths = [f"/docs/doc{n}.txt" for n in range(3)]

    _index_rag_with_progress(agent, paths, handler)

    assert agent.rag.loaded == paths
    assert handler.events[-1]["success"] is True
    assert handler.events[-1]["summary"].startswith("Indexed 3 document(s) in ")


def test_a_stop_before_the_first_document_loads_none():
    handler = _Handler()
    handler.cancelled.set()
    agent = _Agent(_Rag(handler, stop_after=None))
    paths = [f"/docs/doc{n}.txt" for n in range(4)]

    _index_rag_with_progress(agent, paths, handler)

    assert agent.rag.loaded == []
    assert agent.indexed_files == set()
    assert handler.events[-1]["success"] is False
    assert "4 of 4" in handler.events[-1]["summary"]


def test_a_stop_during_the_last_document_reports_all_loaded():
    handler = _Handler()
    agent = _Agent(_Rag(handler, stop_after=3))
    paths = [f"/docs/doc{n}.txt" for n in range(3)]

    _index_rag_with_progress(agent, paths, handler)

    assert agent.rag.loaded == paths
    assert handler.events[-1]["success"] is True
    assert "Stopped" not in handler.events[-1]["summary"]
