# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every indexed chunk is embedded whole (#3534).

The embedder takes one input per ubatch, so a chunk longer than that either
fails or loses its tail. Chunks are therefore cut to fit, and a too-long input
is refused instead of silently truncated.
"""

import os
import random
from unittest.mock import Mock, patch

import pytest

from gaia.rag import sdk as rag_sdk
from gaia.rag.sdk import (
    EMBED_MAX_CHARS,
    EMBEDDER_UBATCH_TOKENS,
    RAGSDK,
    RAGConfig,
    split_for_embedding,
)


class RecordingEmbedder:
    """Stands in for LemonadeClient.embeddings and keeps every input it got."""

    def __init__(self):
        self.inputs = []

    def embeddings(self, texts, model=None, timeout=None):
        self.inputs.extend(texts)
        return {
            "data": [{"index": i, "embedding": [1.0, 0.0]} for i in range(len(texts))]
        }


def _make(tmp_path, **overrides):
    pytest.importorskip("faiss", reason="RAGSDK requires faiss-cpu")
    config = RAGConfig(
        cache_dir=str(tmp_path / "cache"),
        allowed_paths=[str(tmp_path)],
        **{"use_llm_chunking": False, **overrides},
    )
    with patch("gaia.rag.sdk.AgentSDK"):
        sdk = RAGSDK(config)
    sdk.recorder = RecordingEmbedder()

    def _load():
        sdk.embedder = sdk.recorder

    sdk._load_embedder = _load
    sdk._get_hmac_key = lambda: b"test-cache-key" * 3
    return sdk


def _numeric_table(rows):
    random.seed(7)
    return "\n".join(
        f"| {2000 + r} | {random.randint(0, 99999)}.{random.randint(0, 99)} | "
        f"{random.randint(0, 999)}.{random.randint(0, 9)}% |"
        for r in range(rows)
    )


def test_cap_fits_the_embedder_ubatch_at_one_char_per_token():
    # Digits tokenize one per char, so the char cap must also fit as tokens.
    assert EMBED_MAX_CHARS < EMBEDDER_UBATCH_TOKENS


def test_default_chunk_size_fits_the_cap():
    assert RAGConfig().chunk_size * 4 <= EMBED_MAX_CHARS


def test_embedder_loads_with_the_ubatch_the_cap_is_derived_from(tmp_path):
    pytest.importorskip("faiss", reason="RAGSDK requires faiss-cpu")
    with patch("gaia.rag.sdk.AgentSDK"):
        sdk = RAGSDK(RAGConfig(cache_dir=str(tmp_path)))
    client = Mock()
    with (
        patch("gaia.llm.lemonade_client.LemonadeClient", return_value=client),
        patch("gaia.daemon.broker_client.model_lease"),
    ):
        sdk._load_embedder()
    assert client.load_model.call_args.kwargs["llamacpp_args"] == (
        f"--ubatch-size {EMBEDDER_UBATCH_TOKENS}"
    )


@pytest.mark.parametrize(
    "text",
    [
        _numeric_table(400),
        "word " * 3000,
        "x" * 9000,
        ("A long paragraph without any sentence break and lots of words " * 80),
    ],
    ids=["numeric_table", "spaced_words", "no_whitespace", "one_long_sentence"],
)
def test_split_for_embedding_keeps_every_piece_under_cap_and_drops_nothing(text):
    pieces = split_for_embedding(text)
    assert all(0 < len(p) <= EMBED_MAX_CHARS for p in pieces)
    assert "".join(pieces).replace(" ", "").replace("\n", "") == text.replace(
        " ", ""
    ).replace("\n", "")


def test_split_for_embedding_prefers_line_breaks():
    lines = [f"line {i} " + "y" * 40 for i in range(200)]
    for piece in split_for_embedding("\n".join(lines), max_chars=500):
        assert piece.startswith("line ")


def test_split_for_embedding_rejects_nonpositive_cap():
    with pytest.raises(ValueError):
        split_for_embedding("abc", max_chars=0)


@pytest.mark.parametrize(
    "body,chunk_size",
    [
        (_numeric_table(300), 500),
        ("\n\n".join(["The quarterly revenue grew steadily. " * 30] * 12), 1024),
    ],
    ids=["table_default_size", "prose_large_chunk_size"],
)
def test_indexing_embeds_every_chunk_whole(tmp_path, body, chunk_size):
    doc = tmp_path / "report.txt"
    doc.write_text(body, encoding="utf-8")
    sdk = _make(tmp_path, chunk_size=chunk_size)

    assert sdk.index_document(str(doc))["success"]

    assert sdk.chunks
    assert max(len(c) for c in sdk.chunks) <= EMBED_MAX_CHARS
    # One vector per stored chunk, and each vector saw its chunk's full text.
    assert sdk.recorder.inputs == list(sdk.chunks)


def test_llm_chunking_output_is_also_cut_to_fit(tmp_path):
    sdk = _make(tmp_path, use_llm_chunking=True)
    sdk.llm_client = Mock()
    sdk._llm_based_chunking = lambda *a, **k: ["short", "z " * 4000]

    chunks = sdk._split_text_into_chunks("ignored")

    assert chunks[0] == "short"
    assert len(chunks) > 2
    assert max(len(c) for c in chunks) <= EMBED_MAX_CHARS


def test_oversized_input_is_refused_not_truncated(tmp_path):
    sdk = _make(tmp_path)
    sdk._load_embedder()

    with pytest.raises(ValueError, match=str(EMBED_MAX_CHARS)):
        sdk._encode_texts(["ok", "q" * (EMBED_MAX_CHARS + 1)])
    assert sdk.recorder.inputs == []


def test_long_query_is_cut_with_a_warning(tmp_path):
    sdk = _make(tmp_path)
    sdk._load_embedder()
    sdk._embedding_cache = Mock(get=Mock(return_value=None))
    sdk.log = Mock()

    sdk._encode_query("w" * (EMBED_MAX_CHARS + 50))

    assert sdk.recorder.inputs == ["w" * EMBED_MAX_CHARS]
    assert "50 are ignored" in sdk.log.warning.call_args.args[0]


def test_chunk_size_above_cap_warns_at_startup(tmp_path):
    pytest.importorskip("faiss", reason="RAGSDK requires faiss-cpu")
    with patch("gaia.rag.sdk.AgentSDK"), patch("gaia.rag.sdk.get_logger") as get_logger:
        RAGSDK(RAGConfig(cache_dir=str(tmp_path), chunk_size=1024))
    assert "chunk_size=1024" in get_logger.return_value.warning.call_args.args[0]


def test_changing_the_cap_invalidates_cached_chunks(tmp_path):
    doc = tmp_path / "notes.txt"
    doc.write_text("The launch is on Tuesday. " * 40, encoding="utf-8")
    before = os.path.basename(_make(tmp_path)._get_cache_path(str(doc)))
    with patch.object(rag_sdk, "EMBED_MAX_CHARS", 1200):
        after = os.path.basename(_make(tmp_path)._get_cache_path(str(doc)))
    assert before != after


def test_embedder_reloaded_with_a_small_batch_is_reloaded_at_full_size(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("gaia.rag.sdk.time.sleep", lambda _: None)
    sdk = _make(tmp_path)
    shrunk = Mock()
    shrunk.embeddings.side_effect = RuntimeError(
        "input (564 tokens) is too large to process. increase the physical "
        "batch size (current batch size: 512)"
    )
    sdk.embedder = shrunk
    sdk.log = Mock()

    vectors = sdk._encode_texts(["x" * 1500])

    assert vectors.shape == (1, 2)
    assert sdk.recorder.inputs == ["x" * 1500]
    assert any(
        "--ubatch-size" in call.args[0] for call in sdk.log.warning.call_args_list
    )
