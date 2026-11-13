# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A question finds its passage however either side spelled digits, vowel marks
and letter variants, and the text shown to users stays as written."""

import threading
import unicodedata
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.rag_tools import RAGToolsMixin
from gaia.rag.arabic_text import normalize_arabic_pdf_text
from gaia.rag.keyword_search import KeywordIndex
from gaia.rag.sdk import RAGSDK, RAGConfig


def _top(query, texts):
    return [i for i, _score, _words in KeywordIndex(texts).rank(query)]


@pytest.mark.parametrize(
    ("document", "query"),
    [
        pytest.param("وصل کتاب جدید إلى المكتبة", "كتاب جديد", id="persian-kaf-yeh"),
        pytest.param("قرأت ٱلمخطوطة كلها", "المخطوطة", id="alef-wasla"),
        pytest.param("المسئول عن المشروع", "المسؤول", id="hamza-seat"),
        pytest.param("担当 早⼄⼥綾⾹ 様", "早乙女綾香", id="kangxi-radicals"),
        pytest.param("कर्मचारी संख्या ४८७ है", "487", id="devanagari-digits"),
    ],
)
def test_keyword_search_meets_spelling_variants(document, query):
    texts = ["نص لا علاقة له بالسؤال", document, "unrelated filler text"]

    assert _top(query, texts)[:1] == [1]


class _Recorder:
    def __init__(self):
        self.inputs = []

    def embeddings(self, texts, model=None, timeout=None):
        self.inputs.extend(texts)
        return {
            "data": [{"index": i, "embedding": [1.0, 0.0]} for i, _ in enumerate(texts)]
        }


@pytest.fixture
def sdk(tmp_path):
    pytest.importorskip("faiss", reason="RAGSDK requires faiss-cpu")
    with patch("gaia.rag.sdk.AgentSDK"):
        rag = RAGSDK(
            RAGConfig(cache_dir=str(tmp_path / "cache"), use_llm_chunking=False)
        )
    rag.embedder = _Recorder()
    return rag


def test_document_and_query_reach_the_embedder_in_one_form(sdk):
    sdk._encode_texts(["الكِتَابُ رقم ١٢٣٤ في ٱلمكتبة"])
    sdk._encode_query("ﻛﺘﺎب رقم ١٢٣٤")

    assert sdk.embedder.inputs == ["الكتاب رقم 1234 في المكتبة", "كتاب رقم 1234"]


def test_saved_vectors_follow_what_was_embedded(sdk):
    assert sdk._vectors_path(["رقم ١٢٣٤"]) == sdk._vectors_path(["رقم 1234"])
    assert sdk._vectors_path(["رقم 1234"]) != sdk._vectors_path(["رقم 1235"])


def test_exact_text_search_meets_digits_and_vowel_marks(tmp_path):
    document = tmp_path / "ledger.txt"
    document.write_bytes(b"placeholder")
    chunks = ["الكِتَابُ رقم ١٢٣٤ محفوظ", "unrelated"]
    rag = SimpleNamespace(
        chunks=chunks,
        indexed_files={str(document)},
        chunk_to_file={0: str(document), 1: str(document)},
        file_to_chunk_indices={str(document): [0, 1]},
        _state_lock=threading.RLock(),
        freshness=lambda _path: "unchanged",
    )
    host = RAGToolsMixin.__new__(RAGToolsMixin)
    host.rag = rag
    host.indexed_files = set(rag.indexed_files)
    host.current_session = None

    saved = dict(_TOOL_REGISTRY)
    try:
        host.register_rag_tools()
        search = _TOOL_REGISTRY["search_indexed_chunks"]["function"]
        by_digits = search(pattern="رقم 1234")
        by_word = search(pattern="الكتاب")
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)

    for result in (by_digits, by_word):
        assert result["status"] == "success", result
        assert result["match_details"][0]["content"] == chunks[0]


def test_pdf_radical_glyphs_become_the_kanji_they_draw():
    # Chrome's Japanese PDFs report some kanji as Kangxi radicals.
    assert normalize_arabic_pdf_text("開始⽇は2027年3⽉9⽇") == "開始日は2027年3月9日"


def test_upload_name_is_stored_composed(tmp_path, monkeypatch):
    pytest.importorskip("faiss", reason="RAGSDK requires faiss-cpu")
    from starlette.testclient import TestClient

    import gaia.ui.server as server
    from gaia.ui.server import create_app

    async def fake_index(path, display_name=None, **_kwargs):
        return 1

    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    monkeypatch.setattr(server, "_index_document", fake_index)
    client = TestClient(create_app(db_path=":memory:"))
    decomposed = unicodedata.normalize("NFD", "café_مُحَمَّد.txt")
    assert decomposed != unicodedata.normalize("NFC", decomposed)

    resp = client.post(
        "/api/documents/upload",
        files={"file": (decomposed, b"some text")},
        headers={"x-gaia-ui": "1", "x-gaia-client": "agent-ui"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["filename"] == unicodedata.normalize("NFC", decomposed)
    assert unicodedata.is_normalized("NFC", body["filepath"])
