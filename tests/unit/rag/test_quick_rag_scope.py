# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""quick_rag() reads the file it is given, wherever it is (#4800).

Without ``allowed_paths`` the SDK only reads the working directory. A PDF, DOCX,
XLSX or PPTX outside it used to index anyway, because its parser opens the path
itself; index_document now refuses a file it cannot read for the cache key, so
quick_rag has to grant the file it names, as AgentSDK._index_named_document does.
"""

from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from gaia.rag.sdk import RAGSDK, quick_rag

pypdf = pytest.importorskip("pypdf")
pytest.importorskip("faiss", reason="Real vector-index tests require faiss-cpu")


def _write_text_pdf(path, text):
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=300, height=200)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    with open(path, "wb") as f:
        writer.write(f)


@pytest.fixture
def stubbed_sdk():
    with (
        patch("gaia.rag.sdk.AgentSDK"),
        patch.object(RAGSDK, "_load_embedder", lambda self: None),
        patch.object(
            RAGSDK,
            "_encode_texts",
            lambda self, texts, **_kw: np.ones((len(texts), 4), dtype="float32"),
        ),
        patch.object(RAGSDK, "_get_hmac_key", lambda self: b"test-cache-key" * 3),
        patch.object(
            RAGSDK,
            "query",
            lambda self, question, **_kw: SimpleNamespace(text="answer"),
        ),
    ):
        yield


def test_a_pdf_outside_the_working_directory_is_read(
    tmp_path, monkeypatch, stubbed_sdk
):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    docs = tmp_path / "docs"
    docs.mkdir()
    pdf = docs / "guide.pdf"
    _write_text_pdf(pdf, "Launch is on Tuesday")

    answer = quick_rag(
        str(pdf),
        "When is the launch?",
        cache_dir=str(tmp_path / "cache"),
        use_vlm=False,
        use_llm_chunking=False,
        show_stats=False,
    )

    assert answer == "answer"


def test_an_explicit_allowed_paths_is_not_widened(tmp_path, monkeypatch, stubbed_sdk):
    """A caller who scoped the SDK keeps that scope; naming a file doesn't override it."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    docs = tmp_path / "docs"
    docs.mkdir()
    pdf = docs / "guide.pdf"
    _write_text_pdf(pdf, "Launch is on Tuesday")

    with pytest.raises(ValueError, match="not indexed|Failed to index"):
        quick_rag(
            str(pdf),
            "When is the launch?",
            allowed_paths=[str(elsewhere)],
            cache_dir=str(tmp_path / "cache"),
            use_vlm=False,
            use_llm_chunking=False,
            show_stats=False,
        )
