# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A document attached to a running chat joins its cached agent's file scope.

The agent is cached per chat with the scope it was built with. Without widening
it on the next turn, a document attached in between stays unreadable to it.
"""

import asyncio
import threading
from types import SimpleNamespace

import pytest

import gaia.ui._chat_helpers as helpers
from gaia.security import PathValidator
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest


class _ScopedAgent:
    """Cached agent stand-in that records whether it can read the document."""

    def __init__(self, scope, document):
        self.path_validator = PathValidator(allowed_paths=[str(scope)])
        self.document = str(document)
        self.indexed_files = {self.document}  # skip re-indexing on the hit
        self.conversation_history = []
        self.rag = None
        self.can_read = None
        self._cancel_event = threading.Event()
        self.console = None

    def _register_tools(self):
        pass

    def process_query(self, _message, **_kwargs):
        self.can_read = self.path_validator.is_path_allowed(
            self.document, prompt_user=False
        )
        return "ok"


@pytest.fixture
def chat_with_late_document(tmp_path, monkeypatch):
    monkeypatch.setattr(helpers, "_agent_registry", None)
    monkeypatch.setattr(helpers, "_maybe_load_expected_model", lambda *a, **k: None)
    helpers._agent_cache.clear()
    scope = tmp_path / "scope"
    scope.mkdir()
    document = tmp_path / "Documents" / "agent-memory-architecture.md"
    document.parent.mkdir()
    document.write_text("bootstrap items are tagged discovery\n", "utf-8")

    db = ChatDatabase(":memory:")
    session = db.create_session(model="M-GGUF", agent_type="chat")
    agent = _ScopedAgent(scope, document)
    helpers._store_agent(session["id"], "M-GGUF", [], agent, "chat")
    doc = db.add_document(
        filename=document.name,
        filepath=str(document),
        file_hash="h",
        file_size=1,
        chunk_count=1,
    )
    db.attach_document(session["id"], doc["id"])
    assert not agent.path_validator.is_path_allowed(str(document), prompt_user=False)
    yield db, db.get_session(session["id"]), agent
    helpers._agent_cache.clear()
    db.close()


def test_streaming_cache_hit_can_read_a_newly_attached_document(
    chat_with_late_document,
):
    db, session, agent = chat_with_late_document
    request = ChatRequest(session_id=session["id"], message="hi", stream=True)

    async def drain():
        run = SimpleNamespace(handler=None)
        async for _ in helpers._stream_chat_impl(run, db, session, request):
            pass

    asyncio.run(drain())

    assert agent.can_read is True


def test_non_streaming_cache_hit_can_read_a_newly_attached_document(
    chat_with_late_document,
):
    db, session, agent = chat_with_late_document
    request = ChatRequest(session_id=session["id"], message="hi", stream=False)

    asyncio.run(helpers._get_chat_response(db, session, request))

    assert agent.can_read is True
