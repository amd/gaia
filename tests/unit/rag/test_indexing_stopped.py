# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Stopping an index from its progress callback keeps the document's cache."""

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from gaia.rag.sdk import RAGSDK, IndexingStopped, RAGConfig


@pytest.fixture
def sdk(tmp_path):
    pytest.importorskip("faiss", reason="RAGSDK requires faiss-cpu")
    with patch("gaia.rag.sdk.AgentSDK"):
        rag = RAGSDK(
            RAGConfig(cache_dir=str(tmp_path / "cache"), allowed_paths=[str(tmp_path)])
        )
    rag._get_hmac_key = lambda: b"test-index-key" * 3
    rag._load_embedder = lambda: None
    rag._encode_texts = lambda texts, **kw: np.ones((len(texts), 4), "float32")
    return rag


def _stop(_done, _total):
    raise IndexingStopped("doc.txt")


def test_a_stopped_first_index_raises_and_indexes_nothing(sdk, tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("some content to index", encoding="utf-8")

    with pytest.raises(IndexingStopped):
        sdk.index_document(str(doc), progress_callback=_stop)
    assert str(doc) not in sdk.indexed_files


def test_stopping_a_cached_document_keeps_its_cache(sdk, tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("some content to index", encoding="utf-8")
    assert sdk.index_document(str(doc))["success"]
    cached = sorted(
        p.name for p in Path(sdk.config.cache_dir).rglob("*") if p.is_file()
    )
    assert cached
    sdk.remove_document(str(doc))

    with pytest.raises(IndexingStopped):
        sdk.index_document(str(doc), progress_callback=_stop)

    after = sorted(p.name for p in Path(sdk.config.cache_dir).rglob("*") if p.is_file())
    assert after == cached


def test_a_heartbeat_can_stop_a_pdf_between_pages(sdk, tmp_path):
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=200, height=200)
    doc = tmp_path / "doc.pdf"
    with open(doc, "wb") as f:
        writer.write(f)
    beats = []

    def heartbeat():
        beats.append(1)
        if len(beats) == 2:
            raise IndexingStopped("doc.pdf")

    with pytest.raises(IndexingStopped):
        sdk.index_document(str(doc), heartbeat=heartbeat)
    assert len(beats) == 2


def test_a_cancelled_wait_for_the_model_keeps_the_cache_too(sdk, tmp_path):
    from gaia.daemon.broker_client import LeaseWaitCancelled

    doc = tmp_path / "doc.txt"
    doc.write_text("some content to index", encoding="utf-8")
    assert sdk.index_document(str(doc))["success"]
    cached = sorted(
        p.name for p in Path(sdk.config.cache_dir).rglob("*") if p.is_file()
    )
    sdk.remove_document(str(doc))

    def cancelled_while_queued(*_a, **_k):
        raise LeaseWaitCancelled("stopped while waiting for the model slot")

    sdk._load_embedder = cancelled_while_queued
    sdk._encode_texts = cancelled_while_queued

    with pytest.raises(LeaseWaitCancelled):
        sdk.index_document(str(doc))

    after = sorted(p.name for p in Path(sdk.config.cache_dir).rglob("*") if p.is_file())
    assert after == cached
