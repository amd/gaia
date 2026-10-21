# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A document another program holds is reported as locked, not as a generic failure.

``index_document`` turned every read error into ``{"success": False}``, and
``_safe_open`` rewrapped a failed open without its errno, so a caller such as
the chat agent's file watcher could not tell a locked file (worth retrying)
from a broken one.
"""

from __future__ import annotations

import errno
import os
import sys
from unittest.mock import patch

import numpy as np
import pytest

from gaia.utils import file_lock
from gaia.utils.file_lock import held_by_another_program

pytest.importorskip("faiss", reason="RAGSDK requires faiss-cpu")

from gaia.rag.sdk import RAGSDK, RAGConfig  # noqa: E402


def _eacces():
    return PermissionError(errno.EACCES, "Permission denied", "report.txt")


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(file_lock, "_WINDOWS", True)


@pytest.fixture
def rag(tmp_path):
    with patch("gaia.rag.sdk.AgentSDK"):
        return RAGSDK(
            RAGConfig(
                cache_dir=str(tmp_path / "cache"),
                allowed_paths=[str(tmp_path)],
                show_stats=False,
            )
        )


@pytest.fixture
def doc(tmp_path):
    path = tmp_path / "report.txt"
    path.write_text("some text worth indexing", encoding="utf-8")
    return path


def test_a_windows_access_denied_is_a_lock(windows):
    assert held_by_another_program(_eacces())


def test_a_sharing_violation_code_is_a_lock(windows):
    error = PermissionError(None, "sharing violation")
    error.winerror = 32
    assert held_by_another_program(error)


def test_access_denied_off_windows_is_not_a_lock(monkeypatch):
    monkeypatch.setattr(file_lock, "_WINDOWS", False)
    assert not held_by_another_program(_eacces())


@pytest.mark.parametrize(
    "error",
    [
        PermissionError("Access denied: not in allowed paths"),
        FileNotFoundError(errno.ENOENT, "No such file"),
        RuntimeError("boom"),
    ],
    ids=["policy-refusal", "missing", "not-os-error"],
)
def test_other_errors_are_not_locks(windows, error):
    assert not held_by_another_program(error)


def test_a_locked_read_is_reported_as_file_locked(windows, rag, doc):
    with patch.object(rag, "_extract_text_from_file", side_effect=_eacces()):
        result = rag.index_document(str(doc))
    assert result["success"] is False
    assert result["error_type"] == "file_locked"
    assert "another program may have it open" in result["error"]


@pytest.mark.parametrize(
    "error",
    [PermissionError("Access denied: not in allowed paths"), OSError("disk on fire")],
    ids=["policy-refusal", "other-os-error"],
)
def test_a_read_that_is_not_locked_is_a_plain_failure(windows, rag, doc, error):
    with patch.object(rag, "_extract_text_from_file", side_effect=error):
        result = rag.index_document(str(doc))
    assert result["success"] is False
    assert "error_type" not in result
    assert result["error"] == str(error)


def test_a_cache_write_refused_is_not_reported_as_the_document_locked(
    windows, rag, doc
):
    """replace_retrying raises EACCES for a read-only or held cache file."""
    refused = PermissionError(errno.EACCES, "Cannot replace cache: read-only", "c")
    with (
        patch.object(
            rag, "_split_text_into_chunks", return_value=["some text worth indexing"]
        ),
        patch.object(
            rag,
            "_create_vector_index",
            return_value=(object(), None, np.zeros((1, 4), dtype="float32")),
        ),
        patch.object(rag, "_create_faiss_index", return_value=object()),
        patch.object(rag, "_save_cache", side_effect=refused),
    ):
        result = rag.index_document(str(doc))
    assert result["success"] is False
    assert "error_type" not in result
    assert "read-only" in result["error"]


def test_safe_open_keeps_the_errno_of_a_failed_open(rag, doc, monkeypatch):
    def _denied(path, flags, *args):
        raise PermissionError(errno.EACCES, "Permission denied", path)

    monkeypatch.setattr("gaia.rag.sdk.os.open", _denied)
    with pytest.raises(PermissionError) as raised:
        rag._safe_open(str(doc), "rb")
    assert raised.value.errno == errno.EACCES
    assert raised.value.filename == str(doc)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows mandatory locks")
def test_a_real_partial_lock_is_reported_as_file_locked(rag, tmp_path):
    import msvcrt

    doc = tmp_path / "big.txt"
    doc.write_bytes(b"v2 " * 40000)
    with open(doc, "r+b") as holder:
        holder.seek(50000)
        msvcrt.locking(holder.fileno(), msvcrt.LK_NBLCK, 10000)
        try:
            result = rag.index_document(str(doc))
        finally:
            holder.seek(50000)
            msvcrt.locking(holder.fileno(), msvcrt.LK_UNLCK, 10000)
    assert result["error_type"] == "file_locked"
    assert os.path.exists(doc)


def _hash_read_locked(rag, doc, monkeypatch):
    """Lock the document for every read, the hash read included."""
    real_open = rag._safe_open

    def _open(path, mode="rb"):
        if os.path.realpath(path) == os.path.realpath(doc):
            raise _eacces()
        return real_open(path, mode)

    monkeypatch.setattr(rag, "_safe_open", _open)


def test_a_lock_seen_while_hashing_is_reported_before_any_parser_runs(
    windows, rag, tmp_path, monkeypatch
):
    """A zip parser reports a locked .docx as "not a zip file", hiding the lock."""
    doc = tmp_path / "report.docx"
    doc.write_bytes(b"PK\x03\x04 not really a docx")
    _hash_read_locked(rag, doc, monkeypatch)
    with patch.object(
        rag, "_extract_text_from_file", side_effect=ValueError("File is not a zip file")
    ) as extract:
        result = rag.index_document(str(doc))
    assert result["error_type"] == "file_locked"
    extract.assert_not_called()


def test_other_cache_key_callers_still_get_a_path_only_key_for_a_locked_file(
    windows, rag, doc, monkeypatch
):
    _hash_read_locked(rag, doc, monkeypatch)
    assert rag._content_key(str(doc)).endswith("_notfound")


def test_a_lock_is_logged_as_a_warning_not_an_error(
    windows, rag, doc, monkeypatch, caplog
):
    """The file watcher retries a lock; only its final give-up is an error."""
    real_open = rag._safe_open
    reads = []

    def _open(path, mode="rb"):
        reads.append(path)
        if len(reads) > 1:  # the hash read succeeds, the text read is locked
            raise _eacces()
        return real_open(path, mode)

    monkeypatch.setattr(rag, "_safe_open", _open)
    with caplog.at_level("WARNING"):
        result = rag.index_document(str(doc))
    assert result["error_type"] == "file_locked"
    assert [r for r in caplog.records if r.levelname == "WARNING"]
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


def _make(kind, path):
    if kind == "docx":
        docx = pytest.importorskip("docx")
        document = docx.Document()
        for i in range(200):
            document.add_paragraph(f"para {i} " * 60)
        document.save(path)
    else:
        pptx = pytest.importorskip("pptx")
        deck = pptx.Presentation()
        for i in range(30):
            slide = deck.slides.add_slide(deck.slide_layouts[1])
            slide.shapes.title.text = f"slide {i}"
            slide.placeholders[1].text = f"body {i} " * 80
        deck.save(path)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows mandatory locks")
@pytest.mark.parametrize("kind", ["docx", "pptx"])
@pytest.mark.parametrize("span", ["whole", "partial"])
def test_a_real_lock_on_an_office_file_is_reported_as_file_locked(
    rag, tmp_path, kind, span
):
    import msvcrt

    doc = tmp_path / f"doc.{kind}"
    _make(kind, doc)
    size = os.path.getsize(doc)
    offset, length = (0, size) if span == "whole" else (size // 3, max(1, size // 10))
    with open(doc, "r+b") as holder:
        holder.seek(offset)
        msvcrt.locking(holder.fileno(), msvcrt.LK_NBLCK, length)
        try:
            result = rag.index_document(str(doc))
        finally:
            holder.seek(offset)
            msvcrt.locking(holder.fileno(), msvcrt.LK_UNLCK, length)
    assert result["error_type"] == "file_locked", result.get("error")
