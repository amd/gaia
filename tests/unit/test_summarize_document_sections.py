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
        self.prompts = []
        self._on_send = on_send
        self._finish_reasons = list(finish_reasons)

    def send(self, prompt, **kwargs):
        self.prompts.append(prompt)
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


@pytest.mark.parametrize(
    ("summary_type", "required", "forbidden"),
    [
        (
            "brief",
            ("at most three complete sentences", "no headings or Markdown bullets"),
            ("## Key Takeaways", "Create a concise 2-3 paragraph"),
        ),
        (
            "bullets",
            ("at most three top-level bullets", "under 20 words", "nested sub-bullets"),
            ("## Key Takeaways", "Use sub-bullets"),
        ),
        (
            "detailed",
            ("at least 250 words", "every number, percentage, dollar amount"),
            ("## Key Takeaways",),
        ),
    ],
)
def test_business_summary_styles_match_the_skill_contract(
    summarize, summary_type, required, forbidden
):
    chat = _Chat()
    tool = summarize(chat, pages=1)

    result = tool(file_path=DOC, summary_type=summary_type)

    assert result["status"] == "success"
    [prompt] = chat.prompts
    assert all(text in prompt for text in required)
    assert all(text not in prompt for text in forbidden)


def test_long_documents_keep_section_detail_for_final_style_synthesis(summarize):
    chat = _Chat()
    tool = summarize(chat, pages=6)

    result = tool(file_path=DOC, summary_type="brief", max_words_per_section=120)

    assert result["status"] == "success"
    *section_prompts, final_prompt = chat.prompts
    assert len(section_prompts) > 1
    assert "at most three complete sentences" in final_prompt
    assert "# Document Summary" not in final_prompt
    assert "## Key Takeaways" not in final_prompt
    assert all(
        "Extract the key facts, numbers, decisions" in prompt
        for prompt in section_prompts
    )
    assert all("[Page N] markers" in prompt for prompt in section_prompts)
    assert all(
        "at most three complete sentences" not in prompt for prompt in section_prompts
    )


_STYLE_MARKERS = {
    "brief": "at most three complete sentences",
    "detailed": "at least 250 words",
    "bullets": "at most three top-level bullets",
}


@pytest.mark.parametrize("summary_type", sorted(_STYLE_MARKERS))
@pytest.mark.parametrize("pages", [1, 6])
def test_no_prompt_leaks_metadata_or_drops_grounding(summarize, summary_type, pages):
    chat = _Chat()
    tool = summarize(chat, pages=pages)

    result = tool(file_path=DOC, summary_type=summary_type, max_words_per_section=120)

    assert result["status"] == "success"
    final_prompt = chat.prompts[-1]
    assert "CRITICAL GROUNDING RULE" in final_prompt
    last_paragraph = final_prompt.strip().split(chr(10) * 2)[-1]
    assert _STYLE_MARKERS[summary_type] in last_paragraph
    other_styles = [m for t, m in _STYLE_MARKERS.items() if t != summary_type]
    for prompt in chat.prompts:
        for leaked in ("Document Information", "**Pages**", "Total Words"):
            assert leaked not in prompt
        assert all(marker not in prompt for marker in other_styles)
    for prompt in chat.prompts[:-1]:
        assert all(marker not in prompt for marker in _STYLE_MARKERS.values())


@pytest.mark.parametrize("pages", [1, 6])
def test_result_tells_the_agent_to_reshape_unsupported_styles(summarize, pages):
    tool = summarize(_Chat(), pages=pages)

    result = tool(file_path=DOC, summary_type="brief", max_words_per_section=120)

    assert "reshape the draft" in result["instruction"]
    assert "document metadata" not in result["instruction"]


@pytest.mark.parametrize("summary_type", ["executive", "action_items", "Brief", ""])
def test_unsupported_styles_are_still_rejected_before_any_llm_call(
    summarize, summary_type
):
    chat = _Chat()
    tool = summarize(chat, pages=1)

    result = tool(file_path=DOC, summary_type=summary_type)

    assert result["status"] == "error"
    assert chat.prompts == []


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
