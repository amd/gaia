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


def _eacces(filename=None):
    """A read of a locked range carries no filename; a refused open names the file."""
    if filename is None:
        return PermissionError(errno.EACCES, "Permission denied")
    return PermissionError(errno.EACCES, "Permission denied", str(filename))


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


def _wrapped(outer, inner, how):
    try:
        try:
            raise inner
        except BaseException as caught:
            if how == "cause":
                raise outer from caught
            raise outer
    except BaseException as error:
        return error


@pytest.mark.parametrize("how", ["cause", "context"])
def test_a_lock_a_parser_wrapped_is_still_reported_as_file_locked(
    windows, rag, doc, how
):
    """python-docx and python-pptx re-raise the OS error as their own."""
    error = _wrapped(ValueError("File is not a zip file"), _eacces(), how)
    with patch.object(rag, "_extract_text_from_file", side_effect=error):
        result = rag.index_document(str(doc))
    assert result["error_type"] == "file_locked"


def test_a_wrapped_error_with_no_lock_in_its_chain_is_a_plain_failure(
    windows, rag, doc
):
    error = _wrapped(ValueError("bad zip"), OSError("disk on fire"), "cause")
    with patch.object(rag, "_extract_text_from_file", side_effect=error):
        result = rag.index_document(str(doc))
    assert "error_type" not in result
    assert result["error"] == "bad zip"


def _make_any(ext, path):
    if ext in (".docx", ".pptx"):
        _make(ext[1:], path)
    elif ext == ".pdf":
        pypdf = pytest.importorskip("pypdf")
        writer = pypdf.PdfWriter()
        for _ in range(20):
            writer.add_blank_page(612, 792)
        writer.write(str(path))
    elif ext == ".xlsx":
        openpyxl = pytest.importorskip("openpyxl")
        book = openpyxl.Workbook()
        for i in range(2000):
            book.active.append([i, f"row {i}" * 5])
        book.save(path)
    elif ext == ".csv":
        path.write_text("a,b\n" + "".join(f"{i},x{i}\n" for i in range(20000)))
    elif ext == ".json":
        path.write_text('{"k": [' + ",".join(str(i) for i in range(20000)) + "]}")
    else:
        path.write_bytes(b"v2 " * 40000)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows mandatory locks")
@pytest.mark.parametrize(
    "ext", [".txt", ".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".json"]
)
@pytest.mark.parametrize("span", ["whole", "partial"])
def test_a_lock_taken_after_the_hash_read_is_reported_as_file_locked(
    rag, tmp_path, ext, span, caplog
):
    """The race the hash-read check can't see: the lock lands before parsing."""
    import msvcrt

    doc = tmp_path / f"doc{ext}"
    _make_any(ext, doc)
    size = os.path.getsize(doc)
    offset, length = (0, size) if span == "whole" else (size // 3, max(1, size // 10))
    real_key = rag._content_key
    holder = []

    def _key_then_lock(path, raise_locked=False):
        key = real_key(path, raise_locked=raise_locked)
        if not holder:
            handle = open(doc, "r+b")
            handle.seek(offset)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, length)
            holder.append(handle)
        return key

    try:
        with (
            patch.object(rag, "_content_key", side_effect=_key_then_lock),
            caplog.at_level("WARNING"),
        ):
            result = rag.index_document(str(doc))
    finally:
        for handle in holder:
            handle.seek(offset)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, length)
            handle.close()
    assert result["error_type"] == "file_locked", result.get("error")
    errors = [
        r.getMessage()
        for r in caplog.records
        if r.levelname == "ERROR" and r.name.startswith("gaia.rag")
    ]
    assert errors == []


def test_a_stop_raised_while_a_lock_was_being_handled_is_not_reported_as_locked(
    windows, rag, doc
):
    """A cancelled index must stay cancelled, not become a lock to retry."""
    from gaia.rag.sdk import IndexingStopped

    stopped = _wrapped(IndexingStopped("stopped"), _eacces(), "context")
    with patch.object(rag, "_extract_text_from_file", side_effect=stopped):
        with pytest.raises(IndexingStopped):
            rag.index_document(str(doc))


def _bad_zip_over_a_lock(*args, **kwargs):
    """What zipfile raises for a locked file: BadZipFile, with the lock as context."""
    import zipfile

    try:
        raise _eacces()
    except PermissionError:
        raise zipfile.BadZipFile("File is not a zip file")


def _lock_after_hash(rag, monkeypatch):
    real_open = rag._safe_open
    reads = []

    def _open(path, mode="rb"):
        reads.append(path)
        if len(reads) > 1:
            raise _eacces()
        return real_open(path, mode)

    monkeypatch.setattr(rag, "_safe_open", _open)


def _setup_text(rag, path, monkeypatch):
    path.write_text("some text worth indexing", encoding="utf-8")
    _lock_after_hash(rag, monkeypatch)


def _setup_csv(rag, path, monkeypatch):
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    _lock_after_hash(rag, monkeypatch)


def _setup_json(rag, path, monkeypatch):
    path.write_text('{"k": [1, 2]}', encoding="utf-8")
    _lock_after_hash(rag, monkeypatch)


def _setup_xlsx(rag, path, monkeypatch):
    openpyxl = pytest.importorskip("openpyxl")
    path.write_bytes(b"PK placeholder")

    def _locked(*args, **kwargs):
        raise _eacces()

    monkeypatch.setattr(openpyxl, "load_workbook", _locked)


def _setup_docx(rag, path, monkeypatch):
    pytest.importorskip("docx")
    path.write_bytes(b"PK placeholder")
    monkeypatch.setattr("gaia.rag.docx_utils.zipfile.ZipFile", _bad_zip_over_a_lock)


def _setup_pptx_zip(rag, path, monkeypatch):
    pytest.importorskip("pptx")
    path.write_bytes(b"PK placeholder")
    monkeypatch.setattr("gaia.rag.sdk.zipfile.ZipFile", _bad_zip_over_a_lock)


def _locked_open_of(path, real_open=open):
    """``open`` that refuses *path* the way Windows refuses a held file."""

    def _open(file, *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and os.path.realpath(
            file
        ) == os.path.realpath(path):
            raise _eacces(file)
        return real_open(file, *args, **kwargs)

    return _open


def _setup_pptx_open(rag, path, monkeypatch):
    """The lock lands after the zip-bomb check, as the library opens the file."""
    pytest.importorskip("pptx")
    _make("pptx", path)
    monkeypatch.setattr("gaia.rag.sdk.open", _locked_open_of(path), raising=False)


def _setup_docx_open(rag, path, monkeypatch):
    pytest.importorskip("docx")
    _make("docx", path)
    monkeypatch.setattr(
        "gaia.rag.docx_utils.open", _locked_open_of(path), raising=False
    )


@pytest.mark.parametrize(
    "name, setup",
    [
        ("doc.txt", _setup_text),
        ("doc.csv", _setup_csv),
        ("doc.json", _setup_json),
        ("doc.xlsx", _setup_xlsx),
        ("doc.docx", _setup_docx),
        ("doc.pptx", _setup_pptx_zip),
        ("deck.pptx", _setup_pptx_open),
        ("letter.docx", _setup_docx_open),
    ],
    ids=["text", "csv", "json", "xlsx", "docx", "pptx-zip", "pptx-open", "docx-open"],
)
def test_each_parser_logs_a_lock_as_a_warning_not_an_error(
    windows, rag, tmp_path, monkeypatch, caplog, name, setup
):
    """Runs everywhere: the real-lock race test above is Windows-only."""
    doc = tmp_path / name
    setup(rag, doc, monkeypatch)
    with caplog.at_level("WARNING"):
        result = rag.index_document(str(doc))
    assert result["error_type"] == "file_locked", result.get("error")
    records = [r for r in caplog.records if r.name.startswith("gaia.rag")]
    assert [r.getMessage() for r in records if r.levelname == "ERROR"] == []
    assert any("may have it locked" in r.getMessage() for r in records)
    assert not any("Corrupted" in r.getMessage() for r in records)


def test_a_cancelled_model_slot_wait_is_not_reported_as_locked(windows, rag, doc):
    from gaia.daemon.broker_client import LeaseWaitCancelled

    stopped = _wrapped(LeaseWaitCancelled("cancelled"), _eacces(), "context")
    with patch.object(rag, "_extract_text_from_file", side_effect=stopped):
        with pytest.raises(LeaseWaitCancelled):
            rag.index_document(str(doc))


def test_a_wrapped_stop_is_not_reported_as_locked(windows, rag, doc):
    from gaia.rag.sdk import IndexingStopped

    stop = _wrapped(IndexingStopped("stopped"), _eacces(), "context")
    wrapped = _wrapped(RuntimeError("client wrapped the stop"), stop, "cause")
    with patch.object(rag, "_extract_text_from_file", side_effect=wrapped):
        with pytest.raises(IndexingStopped):
            rag.index_document(str(doc))


def _suppressed(outer, inner):
    try:
        try:
            raise inner
        except BaseException:
            raise outer from None
    except BaseException as error:
        return error


@pytest.mark.parametrize("how", ["cause", "context", "from-none"])
def test_a_corrupt_file_with_another_files_lock_in_its_chain_is_not_locked(
    windows, rag, doc, tmp_path, how
):
    """A temp or cache file's EACCES says nothing about the document."""
    other = _eacces(tmp_path / "convert" / "slides.tmp")
    corrupt = ValueError("not a valid file")
    if how == "from-none":
        error = _suppressed(corrupt, _eacces())
    else:
        error = _wrapped(corrupt, other, how)
    with patch.object(rag, "_extract_text_from_file", side_effect=error):
        result = rag.index_document(str(doc))
    assert "error_type" not in result
    assert result["error"] == "not a valid file"


def test_a_lock_that_names_the_document_is_still_found(windows, rag, doc):
    error = _wrapped(ValueError("not a zip file"), _eacces(doc), "context")
    with patch.object(rag, "_extract_text_from_file", side_effect=error):
        result = rag.index_document(str(doc))
    assert result["error_type"] == "file_locked"


@pytest.mark.parametrize(
    "kind, module, name",
    [("pptx", "pptx", "Presentation"), ("docx", "docx", "Document")],
)
def test_the_library_is_given_a_stream_not_a_path(rag, tmp_path, kind, module, name):
    """Given a path, python-pptx and python-docx call is_zipfile, which hides a lock."""
    library = pytest.importorskip(module)
    doc = tmp_path / f"doc.{kind}"
    _make(kind, doc)
    real = getattr(library, name)
    seen = []

    def _spy(source, *args, **kwargs):
        seen.append(source)
        return real(source, *args, **kwargs)

    with patch.object(library, name, _spy):
        rag._extract_text_from_file(str(doc))
    assert seen and not isinstance(seen[0], (str, os.PathLike))


def _hold(path, how):
    """Take a real Windows lock on *path*; return a function that releases it."""
    import msvcrt

    if how == "range":
        handle = open(path, "r+b")
        size = os.path.getsize(path)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, size)

        def release():
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, size)
            handle.close()

        return release
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    held = kernel32.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
    assert held != wintypes.HANDLE(-1).value, ctypes.get_last_error()
    return lambda: kernel32.CloseHandle(held)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows mandatory locks")
@pytest.mark.parametrize("how", ["range", "share-none"])
@pytest.mark.parametrize(
    "kind, module, name",
    [("pptx", "pptx", "Presentation"), ("docx", "docx", "Document")],
)
def test_a_real_lock_taken_just_before_the_library_opens_is_reported_as_locked(
    rag, tmp_path, caplog, kind, module, name, how
):
    """After the zip-bomb check, before the library open: the gap is_zipfile hid."""
    library = pytest.importorskip(module)
    doc = tmp_path / f"doc.{kind}"
    _make(kind, doc)
    real = getattr(library, name)
    releases = []

    def _lock_then_open(source, *args, **kwargs):
        if not releases:
            releases.append(_hold(doc, how))
        return real(source, *args, **kwargs)

    # Lock at whichever comes first: GAIA's open of the stream, or the library
    # opening the path itself (what a revert to passing the path does).
    real_open = open

    def _open(file, *args, **kwargs):
        if not releases and os.path.realpath(file) == os.path.realpath(doc):
            releases.append(_hold(doc, how))
        return real_open(file, *args, **kwargs)

    target = "gaia.rag.sdk.open" if kind == "pptx" else "gaia.rag.docx_utils.open"
    try:
        with (
            patch(target, _open, create=True),
            patch.object(library, name, _lock_then_open),
            caplog.at_level("WARNING"),
        ):
            result = rag.index_document(str(doc))
    finally:
        for release in releases:
            release()
    assert result["error_type"] == "file_locked", result.get("error")
    errors = [
        r.getMessage()
        for r in caplog.records
        if r.levelname == "ERROR" and r.name.startswith("gaia.rag")
    ]
    assert errors == []


def _spellings(tmp_path):
    """The document and its other Windows spellings."""
    import ctypes

    folder = tmp_path / "Quarterly Reports 2026"
    folder.mkdir()
    doc = folder / "Board Presentation Final.docx"
    doc.write_bytes(b"x")
    buffer = ctypes.create_unicode_buffer(1024)
    ctypes.windll.kernel32.GetShortPathNameW(str(doc), buffer, 1024)
    drive, rest = os.path.splitdrive(str(doc))
    return str(doc), {
        "upper-case": str(doc).upper(),
        "8.3": buffer.value,
        "unc": "\\\\localhost\\" + drive[0] + "$" + rest,
        "extended": "\\\\?\\" + str(doc),
    }


def _skip_if_unavailable(doc, forms, form):
    """Short names and admin shares are machine settings, not GAIA's behaviour."""
    if form == "8.3" and forms[form] == doc:
        pytest.skip("8.3 short names are disabled on this volume")
    if form == "unc" and not os.path.exists(forms[form]):
        pytest.skip("the C$ admin share is not reachable on this machine")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path spellings")
@pytest.mark.parametrize("form", ["upper-case", "8.3", "unc", "extended"])
def test_a_lock_naming_the_document_in_another_spelling_is_its_lock(tmp_path, form):
    from gaia.rag.sdk import _same_file

    doc, forms = _spellings(tmp_path)
    _skip_if_unavailable(doc, forms, form)
    assert _same_file(forms[form], doc)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path spellings")
@pytest.mark.parametrize("form", ["upper-case", "8.3"])
def test_the_fallback_compare_handles_case_and_short_names(tmp_path, monkeypatch, form):
    """Without samefile (a file that can't be stat'd), normcase(realpath) decides."""
    from gaia.rag import sdk

    doc, forms = _spellings(tmp_path)
    _skip_if_unavailable(doc, forms, form)

    def _no_samefile(a, b):
        raise OSError("cannot stat")

    monkeypatch.setattr(sdk.os.path, "samefile", _no_samefile)
    assert sdk._same_file(forms[form], doc)


def test_the_fallback_compare_resolves_a_relative_path(tmp_path, monkeypatch):
    from gaia.rag import sdk

    doc = tmp_path / "report.txt"
    doc.write_text("x")
    monkeypatch.chdir(tmp_path)

    def _no_samefile(a, b):
        raise OSError("cannot stat")

    monkeypatch.setattr(sdk.os.path, "samefile", _no_samefile)
    assert sdk._same_file("report.txt", str(doc))
    assert not sdk._same_file("other.txt", str(doc))


def test_a_different_file_is_not_the_document(tmp_path):
    from gaia.rag.sdk import _same_file

    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("x")
    b.write_text("x")
    assert not _same_file(str(a), str(b))


def test_a_stop_suppressed_with_from_none_is_not_a_stop(windows, rag, doc):
    """A traceback hides a suppressed context, so the stop walk must too."""
    from gaia.rag.sdk import IndexingStopped

    error = _suppressed(ValueError("not a valid file"), IndexingStopped("old stop"))
    with patch.object(rag, "_extract_text_from_file", side_effect=error):
        result = rag.index_document(str(doc))
    assert result["success"] is False
    assert result["error"] == "not a valid file"


def test_the_lock_warning_carries_only_the_lock_not_the_parsers_advice(
    windows, rag, tmp_path, monkeypatch, caplog
):
    """Word's wrapper says "corrupted ... re-download"; that is wrong for a lock."""
    doc = tmp_path / "letter.docx"
    _setup_docx_open(rag, doc, monkeypatch)
    with caplog.at_level("WARNING"):
        result = rag.index_document(str(doc))
    assert result["error_type"] == "file_locked"
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    lock_warnings = [m for m in warnings if "may have it locked" in m]
    assert lock_warnings
    assert not any("corrupted" in m or "Re-download" in m for m in lock_warnings)


def test_a_hard_link_to_the_document_is_the_document(tmp_path):
    """Only samefile sees two names for one file; no path compare can."""
    from gaia.rag.sdk import _same_file

    doc = tmp_path / "report.txt"
    doc.write_text("x")
    alias = tmp_path / "alias.txt"
    try:
        os.link(doc, alias)
    except OSError as e:
        pytest.skip(f"hard links unsupported here: {e}")
    assert _same_file(str(alias), str(doc))


def test_the_fallback_compare_follows_a_symlink(tmp_path, monkeypatch):
    from gaia.rag import sdk

    doc = tmp_path / "report.txt"
    doc.write_text("x")
    link = tmp_path / "link.txt"
    try:
        os.symlink(doc, link)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"symlinks unavailable here: {e}")

    def _no_samefile(a, b):
        raise OSError("cannot stat")

    monkeypatch.setattr(sdk.os.path, "samefile", _no_samefile)
    assert sdk._same_file(str(link), str(doc))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows paths ignore case")
def test_a_missing_file_is_compared_ignoring_case(tmp_path):
    """samefile can't stat a file that is gone; the fallback must still match."""
    from gaia.rag.sdk import _same_file

    gone = str(tmp_path / "Gone Report.docx")
    assert _same_file(gone.upper(), gone.lower())
