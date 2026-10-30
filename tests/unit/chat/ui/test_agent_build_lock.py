# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Indexing a chat's documents must not hold the lock every other chat needs to start."""

import asyncio
import threading

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

    def __init__(self, rag_documents):
        self.rag_documents = rag_documents
        self.indexed = []
        self.lock_free_during_index = None
        self.indexed_files = set()
        self.rag = object()
        self.console = None
        self.conversation_history = []
        self.model_id = "M-GGUF"
        if rag_documents:
            self._index_documents(rag_documents)

    def _index_documents(self, documents):
        self.lock_free_during_index = _free_for_another_thread()
        self.indexed.extend(documents)

    def process_query(self, _message):
        return "ok"


class _Registry:
    def __init__(self):
        self.agent = None

    def get(self, _agent_type):
        return object()

    def canonical_id(self, agent_type):
        return agent_type

    def resolve_model(self, _agent_type):
        return "M-GGUF"

    def create_agent(self, _agent_type, **kwargs):
        self.agent = _Agent(kwargs.get("rag_documents") or [])
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
