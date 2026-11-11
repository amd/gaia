# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Indexing a chat's documents must not hold the lock every other chat needs to start."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

import gaia.ui._chat_helpers as helpers
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest


def _free_for_another_thread():
    """The lock is an RLock, so only another thread can tell whether it is held."""
    result = []

    def probe():
        got = helpers.tool_registry_lock.acquire(blocking=False)
        result.append(got)
        if got:
            helpers.tool_registry_lock.release()

    thread = threading.Thread(target=probe)
    thread.start()
    thread.join()
    return result[0]


class _Agent:
    """Records whether the setup lock was free while its documents were indexed."""

    def __init__(self, rag_documents, index_failures=None, cache_index_result=None):
        self.rag_documents = rag_documents
        self.indexed = []
        self.index_failures = index_failures or []
        self.cache_index_result = cache_index_result or {"success": True}
        self.lock_free_during_index = None
        self.indexed_files = set()
        self.rag = SimpleNamespace(index_document=self._index_document)
        self.console = None
        self.conversation_history = []
        self.model_id = "M-GGUF"
        self.query = None
        if rag_documents:
            self._index_documents(rag_documents)

    def _index_document(self, _path):
        return self.cache_index_result

    def _register_tools(self):
        pass

    def rebuild_system_prompt(self):
        pass

    def _index_documents(self, documents):
        self.lock_free_during_index = _free_for_another_thread()
        self.indexed.extend(documents)
        return self.index_failures

    def process_query(self, message):
        self.query = message
        return "ok"


class _Registry:
    def __init__(self, index_failures=None, cache_index_result=None):
        self.agent = None
        self.index_failures = index_failures or []
        self.cache_index_result = cache_index_result or {"success": True}

    def get(self, _agent_type):
        return SimpleNamespace(is_sidecar=False)

    def canonical_id(self, agent_type):
        return agent_type

    def resolve_model(self, _agent_type):
        return "M-GGUF"

    def create_agent(self, _agent_type, **kwargs):
        self.agent = _Agent(
            kwargs.get("rag_documents") or [],
            self.index_failures,
            self.cache_index_result,
        )
        return self.agent


@pytest.fixture
def turn(monkeypatch, tmp_path):
    registry = _Registry()
    doc = tmp_path / "report.pdf"
    doc.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(helpers, "_agent_registry", registry)
    monkeypatch.setattr(helpers, "_maybe_load_expected_model", lambda *a, **k: None)
    monkeypatch.setattr(
        helpers, "_resolve_rag_paths", lambda _db, _ids: ([str(doc)], [])
    )
    helpers._agent_cache.clear()
    db = ChatDatabase(":memory:")
    session = db.create_session(model="M-GGUF", agent_type="chat")
    yield db, session, registry, str(doc)
    helpers._agent_cache.clear()
    db.close()


def test_a_non_streaming_turn_indexes_outside_the_setup_lock(turn):
    db, session, registry, doc = turn
    request = ChatRequest(session_id=session["id"], message="hi", stream=False)

    assert asyncio.run(helpers._get_chat_response(db, session, request)) == "ok"

    assert registry.agent.indexed == [doc]
    assert registry.agent.lock_free_during_index is True


def test_a_non_streaming_turn_tells_the_agent_when_indexing_failed(turn):
    db, session, registry, _doc = turn
    registry.index_failures = ["Failed to index report.pdf: encrypted PDF"]
    request = ChatRequest(
        session_id=session["id"], message="Summarise it", stream=False
    )

    assert asyncio.run(helpers._get_chat_response(db, session, request)) == (
        "ok\n\nFailed to index report.pdf: encrypted PDF"
    )

    assert registry.agent.query == (
        "Summarise it\n\nFailed to index report.pdf: encrypted PDF"
    )


def test_a_cached_non_streaming_turn_tells_the_agent_when_indexing_failed(turn):
    db, session, registry, _doc = turn
    first = ChatRequest(session_id=session["id"], message="First", stream=False)
    assert asyncio.run(helpers._get_chat_response(db, session, first)) == "ok"
    registry.agent.cache_index_result = {"success": False, "error": "encrypted PDF"}
    request = ChatRequest(
        session_id=session["id"], message="Summarise it", stream=False
    )

    assert asyncio.run(helpers._get_chat_response(db, session, request)) == (
        "ok\n\nFailed to index report.pdf: encrypted PDF"
    )

    assert registry.agent.query == (
        "Summarise it\n\nFailed to index report.pdf: encrypted PDF"
    )
