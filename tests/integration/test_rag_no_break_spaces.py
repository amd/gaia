# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Dense extracted tables must fit the real embedder, including cached chunks."""

from gaia.rag.sdk import RAGSDK, RAGConfig


def test_index_dense_no_break_spaces_with_real_embeddings(require_lemonade, tmp_path):
    doc = tmp_path / "table.txt"
    doc.write_text("7\u00a0" * 950, encoding="utf-8")
    config = RAGConfig(
        cache_dir=str(tmp_path / "cache"),
        allowed_paths=[str(tmp_path)],
        use_vlm=False,
        use_llm_chunking=False,
    )

    for from_cache in (False, True):
        sdk = RAGSDK(config)
        result = sdk.index_document(str(doc))

        assert result["success"], result.get("error")
        assert result.get("from_cache", False) == from_cache
        assert sdk.chunks == [doc.read_text(encoding="utf-8").strip()]
        assert sdk.file_embeddings[str(doc)].shape == (1, 768)
