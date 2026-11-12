# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regression tests for RAG text-document encoding detection."""

import codecs
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gaia.rag.sdk import RAGSDK


@pytest.fixture
def rag(monkeypatch):
    """A minimal RAGSDK instance for text extraction without ML dependencies."""
    sdk = object.__new__(RAGSDK)
    sdk.config = SimpleNamespace(show_stats=False)
    sdk.log = MagicMock()
    monkeypatch.setattr(sdk, "_safe_open", lambda path, mode: open(path, mode))
    return sdk


def test_text_file_preserves_cp1252_punctuation(rag, tmp_path):
    document = tmp_path / "menu_cp1252.txt"
    expected = "Café special: €12 — “today only”."
    document.write_bytes(expected.encode("cp1252"))

    assert rag._extract_text_from_text_file(str(document)) == expected


@pytest.mark.parametrize(
    ("bom", "encoding"),
    [
        pytest.param(codecs.BOM_UTF16_LE, "utf-16-le", id="little-endian"),
        pytest.param(codecs.BOM_UTF16_BE, "utf-16-be", id="big-endian"),
    ],
)
def test_text_file_detects_utf16_bom(rag, tmp_path, bom, encoding):
    document = tmp_path / "dock_inventory_utf16.txt"
    expected = "Dock inventory passphrase: violet-otter-92."
    document.write_bytes(bom + expected.encode(encoding))

    assert rag._extract_text_from_text_file(str(document)) == expected


def test_csv_preserves_cp1252_punctuation(rag, tmp_path):
    document = tmp_path / "menu_cp1252.csv"
    document.write_bytes(
        "item,price,note\r\ncafé,€12,“today only”\r\n".encode("cp1252")
    )

    extracted = rag._extract_text_from_csv(str(document))

    assert "item: café" in extracted
    assert "price: €12" in extracted
    assert "note: “today only”" in extracted


@pytest.mark.parametrize(
    ("bom", "encoding"),
    [
        pytest.param(codecs.BOM_UTF16_LE, "utf-16-le", id="little-endian"),
        pytest.param(codecs.BOM_UTF16_BE, "utf-16-be", id="big-endian"),
    ],
)
def test_csv_detects_utf16_bom(rag, tmp_path, bom, encoding):
    document = tmp_path / "dock_inventory_utf16.csv"
    content = "location,doors\r\nNorth dock,14\r\n"
    document.write_bytes(bom + content.encode(encoding))

    extracted = rag._extract_text_from_csv(str(document))

    assert "location: North dock" in extracted
    assert "doors: 14" in extracted


@pytest.mark.parametrize(
    ("bom", "encoding"),
    [
        pytest.param(codecs.BOM_UTF32_LE, "utf-32-le", id="little-endian"),
        pytest.param(codecs.BOM_UTF32_BE, "utf-32-be", id="big-endian"),
    ],
)
def test_text_file_detects_utf32_bom(rag, tmp_path, bom, encoding):
    document = tmp_path / "utf32.txt"
    expected = "Dock inventory passphrase: violet-otter-92."
    document.write_bytes(bom + expected.encode(encoding))

    assert rag._extract_text_from_text_file(str(document)) == expected


def test_truncated_utf16_raises_with_tried_encodings(rag, tmp_path):
    document = tmp_path / "broken.txt"
    document.write_bytes(codecs.BOM_UTF16_LE + b"a")

    with pytest.raises(ValueError, match="Tried encodings: .*utf-8,.*cp1252"):
        rag._extract_text_from_text_file(str(document))
