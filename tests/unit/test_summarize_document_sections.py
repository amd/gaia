# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""summarize_document sends every section on its own, and stops when abandoned.

The RAG chat SDK keeps conversation history, so summarizing section N used to
carry sections 1..N-1 along with it: on a 1,666-page PDF the third request was
79,102 tokens against a 65,536-token context, and every later section failed.
"""

import threading
from types import SimpleNamespace

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.rag_tools import RAGToolsMixin
from gaia.tool_cancellation import set_tool_cancel_event

DOC = "/docs/handbook.pdf"


class _Chat:
    def __init__(self, on_send=None):
        self.calls = []
        self._on_send = on_send

    def send(self, prompt, **kwargs):
        self.calls.append(kwargs)
        if self._on_send:
            self._on_send()
        return SimpleNamespace(text="summary")


class _Host(RAGToolsMixin):
    def __init__(self, chat, pages):
        text = "\n".join(f"[Page {i}]\n" + "word " * 50 for i in range(1, pages + 1))
        self.rag = SimpleNamespace(
            indexed_files=[DOC],
            file_metadata={DOC: {"full_text": text, "num_pages": pages}},
            chat=chat,
        )


@pytest.fixture
def summarize():
    saved = dict(_TOOL_REGISTRY)

    def build(chat, pages):
        _Host(chat, pages).register_rag_tools()
        return _TOOL_REGISTRY["summarize_document"]["function"]

    yield build
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


def test_each_section_and_the_synthesis_are_sent_without_history(summarize):
    chat = _Chat()
    tool = summarize(chat, pages=6)

    result = tool(file_path=DOC, summary_type="brief", max_words_per_section=120)

    assert result.get("status") != "error", result
    assert len(chat.calls) > 2, "expected several sections plus a synthesis"
    assert all(call.get("no_history") is True for call in chat.calls)


def test_a_single_section_document_is_sent_without_history(summarize):
    chat = _Chat()
    tool = summarize(chat, pages=1)

    tool(file_path=DOC, summary_type="brief")

    assert [call.get("no_history") for call in chat.calls] == [True]


def test_an_abandoned_summary_stops_between_sections(summarize):
    cancelled = threading.Event()
    chat = _Chat(on_send=cancelled.set)
    tool = summarize(chat, pages=6)

    set_tool_cancel_event(cancelled)
    try:
        tool(file_path=DOC, summary_type="brief", max_words_per_section=120)
    finally:
        set_tool_cancel_event(None)

    assert len(chat.calls) == 1
