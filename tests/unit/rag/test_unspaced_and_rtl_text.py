# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Chinese, Japanese and Arabic documents are searched, chunked and summarized
as well as English ones: keyword search, sentence cuts with overlap, and
summary sections sized by how much text there really is."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.rag_tools import RAGToolsMixin, _split_oversized_page
from gaia.rag.keyword_search import KeywordIndex
from gaia.rag.sdk import EMBED_MAX_CHARS, RAGSDK, RAGConfig
from gaia.rag.text_units import join_pieces, last_tokens

ZH_SENTENCES = [
    f"第{n}季度北京办公室的销售额为{n * 37}万元，比去年同期增长了百分之{n % 9 + 1}。"
    for n in range(1, 160)
]
AR_SENTENCES = [
    f"بلغت إيرادات الفرع رقم {n} في الربع الأخير نحو {n * 37} مليون ريال بزيادة واضحة؟"
    for n in range(1, 120)
]


def _sdk(**overrides):
    with patch("gaia.rag.sdk.AgentSDK"):
        return RAGSDK(RAGConfig(use_llm_chunking=False, **overrides))


def _top(query, texts):
    return [i for i, _score, _words in KeywordIndex(texts).rank(query)]


def test_chinese_keyword_search_matches_inside_an_unspaced_sentence():
    texts = [
        "上海工厂的员工人数为两千人。",
        "北京办公室的年度预算为三百万元。",
        "广州仓库的面积为五千平方米。",
    ]

    assert _top("年度预算是多少", texts)[:1] == [1]


def test_japanese_keyword_search_matches_kanji_and_kana_words():
    texts = [
        "大阪支社の社員数は百二十人です。",
        "東京本社の売上高は前年比12%増の45億円でした。",
    ]

    assert _top("東京本社の売上高", texts)[:1] == [1]


@pytest.mark.parametrize(
    ("text", "count"),
    [
        pytest.param("東京は晴れです。大阪は雨です！明日はどうですか？", 3, id="ja"),
        pytest.param("今天下雨。明天晴天！", 2, id="zh"),
        pytest.param("هذه الجملة الأولى. هل هذه الثانية؟ وهذه الثالثة.", 3, id="ar"),
        pytest.param("یہ پہلا جملہ ہے۔ یہ دوسرا ہے۔", 2, id="ur"),
        pytest.param("यह पहला वाक्य है। यह दूसरा है।", 2, id="hi"),
    ],
)
def test_sentences_end_at_non_latin_punctuation(text, count):
    assert len(_sdk()._split_into_sentences(text)) == count


def test_korean_keyword_search_matches_a_word_with_its_particle():
    texts = [
        "부산 공장의 직원 수는 이천 명입니다.",
        "서울 사무소의 연간 예산은 삼백만 원입니다.",
    ]

    assert _top("서울 사무소 예산", texts)[:1] == [1]


def test_chinese_overlap_starts_at_a_sentence_even_with_spaces_inside():
    text = "广州仓库的面积为五千平方米。上海工厂 Q3 report 的员工人数为两千人。"

    assert last_tokens(text, 20) == "上海工厂 Q3 report 的员工人数为两千人。"


def test_joining_keeps_spaces_in_korean_and_drops_them_only_in_chinese():
    assert join_pieces(["서울", "사무소의", "예산"]) == "서울 사무소의 예산"
    assert join_pieces(["今天下雨。", "明天晴天！"]) == "今天下雨。明天晴天！"
    assert join_pieces(["sales", "rose."]) == "sales rose."


def test_english_abbreviations_still_do_not_split():
    sentences = _sdk()._split_into_sentences("Dr. Smith met Mr. Jones. They agreed.")

    assert sentences == ["Dr. Smith met Mr. Jones.", "They agreed."]


@pytest.mark.parametrize(
    "sentences",
    [pytest.param(ZH_SENTENCES, id="zh"), pytest.param(AR_SENTENCES, id="ar")],
)
def test_long_paragraph_is_cut_at_sentences_with_overlap(sentences):
    sdk = _sdk(chunk_size=200, chunk_overlap=40)
    paragraph = "".join(sentences) if sentences is ZH_SENTENCES else " ".join(sentences)

    chunks = sdk._split_text_into_chunks(paragraph)

    assert len(chunks) > 2
    assert all(len(chunk) <= EMBED_MAX_CHARS for chunk in chunks)
    for chunk in chunks[:-1]:
        assert chunk.rstrip()[-1] in "。؟", chunk[-30:]
    for before, after in zip(chunks, chunks[1:]):
        first_sentence = sentences[[s in after for s in sentences].index(True)]
        assert first_sentence in before, "consecutive chunks share no sentence"


def test_chinese_chunks_are_sized_by_tokens_not_bytes_of_english():
    # A Chinese character costs about one token, not a quarter of one.
    sdk = _sdk(chunk_size=200, chunk_overlap=40)

    chunks = sdk._split_text_into_chunks("".join(ZH_SENTENCES))

    assert max(len(chunk) for chunk in chunks) <= 300


def test_long_chinese_text_without_page_markers_is_split_for_summary():
    text = "".join(ZH_SENTENCES) * 8

    parts = _split_oversized_page(text, 2000)

    assert len(parts) > 1
    assert "".join(parts) == text


DOC = "/docs/report_zh.pdf"


class _Chat:
    effective_model = "Gemma-4-E4B-it-GGUF"

    def __init__(self):
        self.prompts = []

    def send(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return SimpleNamespace(text="摘要", finish_reason="stop")


class _Host(RAGToolsMixin):
    def __init__(self, chat, text):
        self.rag = SimpleNamespace(
            indexed_files=[DOC],
            file_metadata={DOC: {"full_text": text, "num_pages": 4}},
            chat=chat,
        )


def test_long_chinese_document_is_summarized_in_several_sections():
    saved = dict(_TOOL_REGISTRY)
    try:
        page = "".join(ZH_SENTENCES)
        text = "\n".join(f"[Page {i}]\n{page}" for i in range(1, 5))
        chat = _Chat()
        _Host(chat, text).register_rag_tools()
        tool = _TOOL_REGISTRY["summarize_document"]["function"]

        result = tool(file_path=DOC, max_words_per_section=8000)

        assert result.get("status") != "error", result
        assert len(chat.prompts) > 2, "one section holding the whole document"
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)
