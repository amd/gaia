# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A document taken off a chat is no longer searched by its cached agent.

The agent is cached per chat with every document it indexed. Deleting a
document from the library, or dropping it from the chat's document list, left
it in that index, so the next turn still searched and cited it.
"""

import asyncio
import threading
from types import SimpleNamespace

import pytest

import gaia.ui._chat_helpers as helpers
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest


class _IndexedAgent:
    """Agent stand-in that answers with the documents its index holds."""

    def __init__(self, paths=()):
        self.indexed_files = set(paths)
        self.conversation_history = []
        self.rag = SimpleNamespace()
        self._cancel_event = threading.Event()
        self.console = None
        self.answered = False

    def _register_tools(self):
        pass

    def _index_documents(self, paths):
        self.indexed_files.update(paths)
        return []

    def rebuild_system_prompt(self):
        pass

    def process_query(self, _message, **_kwargs):
        self.answered = True
        return " | ".join(sorted(self.indexed_files)) or "no documents"


class _Registry:
    """Builds a fresh stand-in, as the real registry builds a fresh agent."""

    def __init__(self):
        self.built = []

    def get(self, _agent_type):
        return SimpleNamespace(is_sidecar=False)

    def canonical_id(self, agent_type):
        return agent_type

    def resolve_model(self, _agent_type):
        return None

    def create_agent(self, _agent_type, **_kwargs):
        self.built.append(_IndexedAgent())
        return self.built[-1]


@pytest.fixture
def chat_with_two_documents(tmp_path, monkeypatch):
    registry = _Registry()
    monkeypatch.setattr(helpers, "_agent_registry", registry)
    monkeypatch.setattr(helpers, "_maybe_load_expected_model", lambda *a, **k: None)
    helpers._agent_cache.clear()
    db = ChatDatabase(":memory:")
    session = db.create_session(model="M-GGUF", agent_type="chat")
    ids, paths = [], []
    for name in ("kept.txt", "gone.txt"):
        path = tmp_path / name
        path.write_text(name, "utf-8")
        doc = db.add_document(
            filename=name, filepath=str(path), file_hash=name, file_size=1
        )
        db.attach_document(session["id"], doc["id"])
        ids.append(doc["id"])
        paths.append(str(path))
    cached = _IndexedAgent(paths)
    helpers._store_agent(session["id"], "M-GGUF", ids, cached, "chat")
    yield SimpleNamespace(
        db=db,
        session_id=session["id"],
        ids=ids,
        paths=paths,
        cached=cached,
        registry=registry,
    )
    helpers._agent_cache.clear()
    db.close()


def _ask(chat):
    session = chat.db.get_session(chat.session_id)
    request = ChatRequest(session_id=chat.session_id, message="codes?", stream=False)
    return asyncio.run(helpers._get_chat_response(chat.db, session, request))


def test_a_cache_hit_keeps_the_agent_while_its_documents_stay(chat_with_two_documents):
    chat = chat_with_two_documents

    _ask(chat)

    assert chat.cached.answered
    assert chat.registry.built == []


@pytest.mark.parametrize("remove", ["delete", "detach_by_update"])
def test_a_removed_document_is_not_searched_on_the_next_turn(
    chat_with_two_documents, remove
):
    chat = chat_with_two_documents
    kept_id, gone_id = chat.ids
    if remove == "delete":
        chat.db.delete_document(gone_id)
    else:
        chat.db.update_session(chat.session_id, document_ids=[kept_id])

    answer = _ask(chat)

    assert not chat.cached.answered
    [rebuilt] = chat.registry.built
    assert rebuilt.indexed_files == {chat.paths[0]}
    assert chat.paths[1] not in answer


def test_a_newly_attached_document_keeps_the_cached_agent(
    chat_with_two_documents, tmp_path
):
    chat = chat_with_two_documents
    extra = tmp_path / "extra.txt"
    extra.write_text("extra", "utf-8")
    doc = chat.db.add_document(
        filename="extra.txt", filepath=str(extra), file_hash="extra", file_size=1
    )
    chat.db.attach_document(chat.session_id, doc["id"])

    _ask(chat)
    chat.db.detach_document(chat.session_id, doc["id"])
    _ask(chat)

    # The second turn remembered the third document, so dropping it rebuilds.
    assert len(chat.registry.built) == 1
