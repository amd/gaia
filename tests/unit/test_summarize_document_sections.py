# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""summarize_document sends every section on its own, and stops when abandoned.

The RAG chat SDK keeps conversation history, so summarizing section N used to
carry sections 1..N-1 along with it: on a 1,666-page PDF the third request was
79,102 tokens against a 65,536-token context, and every later section failed.

Each reply also gets a summary-sized output budget and never comes back cut
short: under the RAG client's 1,024-token default a thinking model reasoned
through most of it, and a 46-minute meeting's summary stopped mid-sentence,
before any owner or date.
"""

import threading
from types import SimpleNamespace

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.rag_tools import SUMMARY_MAX_TOKENS, RAGToolsMixin
from gaia.llm.lemonade_client import LARGE_DEFAULT_MODEL_NAME
from gaia.tool_cancellation import set_tool_cancel_event

DOC = "/docs/handbook.pdf"


class _Chat:
    effective_model = "Gemma-4-E4B-it-GGUF"

    def __init__(self, on_send=None, finish_reasons=()):
        self.calls = []
        self._on_send = on_send
        self._finish_reasons = list(finish_reasons)

    def send(self, prompt, **kwargs):
        self.calls.append(kwargs)
        if self._on_send:
            self._on_send()
        reason = self._finish_reasons.pop(0) if self._finish_reasons else "stop"
        return SimpleNamespace(text="summary", finish_reason=reason)


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


def test_every_summary_call_gets_the_summary_budget(summarize):
    chat = _Chat()
    tool = summarize(chat, pages=6)

    tool(file_path=DOC, summary_type="brief", max_words_per_section=120)

    assert chat.calls
    assert all(call["max_tokens"] == SUMMARY_MAX_TOKENS for call in chat.calls)


def test_a_thinking_model_summarizes_with_thinking_off(summarize):
    chat = _Chat()
    chat.effective_model = LARGE_DEFAULT_MODEL_NAME
    tool = summarize(chat, pages=1)

    tool(file_path=DOC, summary_type="brief")

    assert chat.calls[0]["chat_template_kwargs"] == {"enable_thinking": False}


def test_a_summary_cut_off_at_the_limit_is_an_error_not_a_summary(summarize):
    chat = _Chat(finish_reasons=["length"])
    tool = summarize(chat, pages=1)

    result = tool(file_path=DOC, summary_type="detailed")

    assert result["status"] == "error"
    assert "summary" not in result
    assert "cut off" in result["error"]


def test_a_section_cut_off_fails_the_summary_instead_of_vanishing(summarize):
    chat = _Chat(finish_reasons=["stop", "length"])
    tool = summarize(chat, pages=6)

    result = tool(file_path=DOC, summary_type="brief", max_words_per_section=120)

    assert result["status"] == "error"
    assert "cut off" in result["error"]
