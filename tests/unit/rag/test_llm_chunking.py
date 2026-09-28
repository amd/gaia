# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""LLM-based chunking: termination, chunk-size bound, and loud failures."""

import json
import math
from unittest.mock import patch

import numpy as np
import pytest

from gaia.rag.sdk import RAGSDK, RAGConfig

CHUNK_SIZE = 500
OVERLAP = 100
MAX_CHARS = CHUNK_SIZE * 4


class _RunawayLoop(BaseException):
    """Escapes the chunker's error handling so a non-terminating loop fails fast."""


class StubLLM:
    """Stands in for LemonadeClient.completions with a hard call budget."""

    def __init__(self, response="[]", limit=25):
        self.response = response
        self.limit = limit
        self.prompts = []

    def completions(self, model, prompt, **kwargs):
        self.prompts.append(prompt)
        if len(self.prompts) > self.limit:
            raise _RunawayLoop(f"chunker made more than {self.limit} LLM calls")
        text = self.response(prompt) if callable(self.response) else self.response
        return {"choices": [{"text": text}]}


@pytest.fixture
def rag(tmp_path):
    pytest.importorskip("faiss", reason="RAGSDK requires faiss-cpu")
    with patch("gaia.rag.sdk.AgentSDK"):
        sdk = RAGSDK(
            RAGConfig(
                cache_dir=str(tmp_path / "cache"),
                allowed_paths=[str(tmp_path)],
                chunk_size=CHUNK_SIZE,
                chunk_overlap=OVERLAP,
                use_llm_chunking=True,
            )
        )
    sdk._load_embedder = lambda: None
    sdk._encode_texts = lambda texts, **kwargs: np.ones(
        (len(texts), 4), dtype="float32"
    )
    sdk._get_hmac_key = lambda: b"test-index-key" * 3
    return sdk


def _long_text(n_chars):
    words, size, i = [], 0, 0
    while size < n_chars:
        word = f"word{i}"
        words.append(word)
        size += len(word) + 1
        i += 1
    return " ".join(words)[:n_chars].rsplit(" ", 1)[0]


def test_short_input_terminates_after_one_call(rag):
    text = "A short document that fits in a single LLM segment."
    rag.llm_client = StubLLM()

    chunks = rag._llm_based_chunking(text, CHUNK_SIZE, OVERLAP)

    assert chunks == [text]
    assert len(rag.llm_client.prompts) == 1


def test_long_input_terminates_with_bounded_chunks(rag):
    text = _long_text(15_000)
    rag.llm_client = StubLLM()

    chunks = rag._llm_based_chunking(text, CHUNK_SIZE, OVERLAP)

    segment_size = MAX_CHARS * 3
    stride = segment_size - OVERLAP * 4
    assert len(rag.llm_client.prompts) <= math.ceil(len(text) / stride)
    assert all(len(chunk) <= MAX_CHARS for chunk in chunks)
    covered = set(" ".join(chunks).split())
    assert covered == set(text.split())


def test_llm_sees_the_whole_segment(rag):
    text = _long_text(15_000)
    rag.llm_client = StubLLM()

    rag._llm_based_chunking(text, CHUNK_SIZE, OVERLAP)

    first_segment = text[: MAX_CHARS * 3].rsplit(" ", 1)[0]
    assert f"---\n{first_segment}\n---" in rag.llm_client.prompts[0]


def test_llm_split_positions_are_honored(rag):
    paragraphs = ["Alpha section text.", "Beta section text.", "Gamma section text."]
    text = " ".join(paragraphs)
    splits = [len(paragraphs[0]) + 1, len(paragraphs[0]) + len(paragraphs[1]) + 2]
    rag.llm_client = StubLLM(response=f"```json\n{json.dumps(splits)}\n```")

    assert rag._llm_based_chunking(text, CHUNK_SIZE, OVERLAP) == paragraphs


def test_out_of_range_positions_are_ignored(rag):
    text = "First half. Second half."
    rag.llm_client = StubLLM(response="[-5, 0, 12, 12, 9999]")

    assert rag._llm_based_chunking(text, CHUNK_SIZE, OVERLAP) == [
        "First half.",
        "Second half.",
    ]


def test_split_array_is_found_after_bracketed_prose(rag):
    text = "First half. Second half."
    rag.llm_client = StubLLM(response="Splitting [Page 1] at: [12]")

    assert rag._llm_based_chunking(text, CHUNK_SIZE, OVERLAP) == [
        "First half.",
        "Second half.",
    ]


def test_unparseable_llm_response_raises(rag):
    rag.llm_client = StubLLM(response="Sure! Split it after the first sentence.")

    with pytest.raises(RuntimeError, match="LLM chunking failed"):
        rag._llm_based_chunking("Some text. More text.", CHUNK_SIZE, OVERLAP)


def test_llm_failure_is_not_replaced_by_heuristic_chunking(rag):
    class FailingLLM:
        def completions(self, **kwargs):
            raise ConnectionError("Lemonade Server not reachable")

    rag.llm_client = FailingLLM()

    with pytest.raises(RuntimeError, match="Lemonade Server not reachable"):
        rag._split_text_into_chunks("Some text. More text.")


def test_index_document_reports_llm_chunking_failure(rag, tmp_path):
    class FailingLLM:
        def completions(self, **kwargs):
            raise ConnectionError("Lemonade Server not reachable")

    rag.llm_client = FailingLLM()
    document = tmp_path / "doc.txt"
    document.write_text("Some text. More text.", encoding="utf-8")

    stats = rag.index_document(str(document))

    assert not stats["success"]
    assert "LLM chunking failed" in stats["error"]
    assert str(document) not in rag.indexed_files
