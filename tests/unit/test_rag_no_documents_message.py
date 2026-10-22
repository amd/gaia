# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An Agent UI chat with nothing attached must not report an empty library.

A chat searches only its own attachments, so "No documents are currently
indexed" led the model to tell the user their RAG index was empty while the
Document library held the very files they asked about.

ChatAgent's own message is pinned in
hub/agents/chat/python/tests/test_no_documents_message.py.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.rag_tools import RAGToolsMixin

DEFAULT = "No documents are currently indexed."
SCOPED = "Nothing is attached to this chat."


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


class _Host(Agent, RAGToolsMixin):
    def __init__(self, rag, **kwargs):
        self.rag = rag
        self.indexed_files = set()
        super().__init__(**kwargs)

    def _register_tools(self):
        self.register_rag_tools()


class _ScopedHost(_Host):
    def _no_documents_message(self) -> str:
        return SCOPED


def _empty_rag():
    return SimpleNamespace(
        index=None, chunks=[], indexed_files=set(), file_to_chunk_indices={}
    )


def _tool(name):
    return _TOOL_REGISTRY[name]["function"]


def _make(cls, rag):
    with patch("gaia.agents.base.agent.AgentSDK"):
        return cls(rag, skip_lemonade=True, silent_mode=True)


@pytest.mark.parametrize("cls, expected", [(_Host, DEFAULT), (_ScopedHost, SCOPED)])
def test_every_empty_index_result_carries_the_hosts_message(cls, expected):
    _make(cls, _empty_rag())

    queried = _tool("query_documents")(query="APAC Q3 units")
    listed = _tool("list_indexed_documents")()
    searched = _tool("search_indexed_chunks")(pattern="APAC")

    assert queried["status"] == "no_documents"
    assert queried["message"] == expected
    assert queried["instruction"].startswith(expected)
    assert listed["count"] == 0 and listed["message"] == expected
    assert searched["error"] == expected


def test_disabled_rag_reports_the_same_message():
    _make(_ScopedHost, None)

    assert _tool("query_documents")(query="q")["message"] == SCOPED
    assert _tool("list_indexed_documents")()["message"] == SCOPED


def test_a_listing_with_documents_has_no_empty_message():
    rag = _empty_rag()
    rag.indexed_files = {"/docs/report.md"}
    rag.chunks = ["chunk"]
    rag.file_metadata = {}
    _make(_ScopedHost, rag)

    listed = _tool("list_indexed_documents")()

    assert listed["count"] == 1
    assert "message" not in listed
