# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Adversarial checks on full-text document reads and fetch_page windows (#5100).

Hostile files (corrupt, encrypted, empty, mislabelled, decompression bombs,
huge workbooks, non-UTF-8 text) must come back as a clear error or as their
text — never a crash, never binary noise reported as content. ``fetch_page``
paging and ``find`` must always make progress, treat ``find`` as plain text,
and say when nothing matched.
"""

from __future__ import annotations

import io
import time
import zipfile
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools import document_text
from gaia.agents.tools.browser_tools import FIND_CONTEXT_CHARS, text_window
from gaia.agents.tools.document_text import MAX_EXTRACT_CHARS
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.security import PathValidator

docx = pytest.importorskip("docx")

BUDGET = 2000
URL = "https://example.com/p"


@pytest.fixture
def read_file(tmp_path):
    saved = dict(_TOOL_REGISTRY)
    host = FileIOToolsMixin()
    host.path_validator = PathValidator()
    host.path_validator.allowed_paths.add(tmp_path.resolve())
    host._truncation_budget = lambda: (3000, BUDGET)
    host.register_file_io_tools()
    try:
        yield _TOOL_REGISTRY["read_file"]["function"]
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def _is_clean_error(result) -> bool:
    return (
        isinstance(result, dict)
        and result.get("status") == "error"
        and isinstance(result.get("error"), str)
        and result["error"].strip() != ""
    )


def _docx(path, paragraphs):
    document = docx.Document()
    for text in paragraphs:
        document.add_paragraph(text)
    document.save(str(path))
    return path


# ---------------------------------------------------------------------------
# Hostile documents are a clear error, never a crash or noise
# ---------------------------------------------------------------------------

OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")


@pytest.mark.parametrize(
    "name, data",
    [
        ("empty.docx", b""),
        ("empty.pdf", b""),
        ("empty.xlsx", b""),
        ("binary.docx", bytes(range(256)) * 64),
        ("binary.pdf", bytes(range(256)) * 64),
        ("binary.xlsx", bytes(range(256)) * 64),
        ("text.docx", b"just some text, not a zip"),
        ("png.pdf", b"\x89PNG\r\n\x1a\n" + b"\x00" * 512),
        # An encrypted Office file is an OLE container, not a zip.
        ("locked.docx", OLE_MAGIC + b"\x00" * 4096),
        ("locked.xlsx", OLE_MAGIC + b"\x00" * 4096),
        ("truncated.pdf", b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog"),
    ],
    ids=lambda value: value if isinstance(value, str) else "bytes",
)
def test_a_hostile_document_is_a_clean_error(read_file, tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)

    result = read_file(str(path))

    assert _is_clean_error(result), result
    assert "content" not in result


def test_a_zip_that_is_not_a_word_document_is_a_clean_error(read_file, tmp_path):
    path = tmp_path / "other.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("hello.txt", "not a word document")

    assert _is_clean_error(read_file(str(path)))


def test_a_decompression_bomb_docx_is_refused_before_it_is_parsed(
    read_file, tmp_path, monkeypatch
):
    monkeypatch.setattr("gaia.rag.docx_utils.MAX_UNCOMPRESSED_BYTES", 1024 * 1024)
    path = tmp_path / "bomb.docx"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<w:t>" + "a" * (4 * 1024 * 1024) + "</w:t>")
    path.write_bytes(buffer.getvalue())
    assert path.stat().st_size < 64 * 1024

    with patch("docx.Document", side_effect=AssertionError("parsed a bomb")):
        result = read_file(str(path))

    assert _is_clean_error(result), result
    assert "too large" in result["error"].lower()


def test_a_corrupt_pdf_page_does_not_crash_the_read(read_file, tmp_path, monkeypatch):
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    path = tmp_path / "odd.pdf"
    with open(path, "wb") as handle:
        writer.write(handle)
    monkeypatch.setattr(
        pypdf.PageObject,
        "extract_text",
        lambda self, *a, **k: (_ for _ in ()).throw(KeyError("/Contents")),
    )

    result = read_file(str(path))

    assert _is_clean_error(result), result


def test_a_password_protected_pdf_says_so(read_file, tmp_path):
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt(user_password="secret", algorithm="RC4-128")
    path = tmp_path / "locked.pdf"
    with open(path, "wb") as handle:
        writer.write(handle)

    result = read_file(str(path))

    assert _is_clean_error(result), result
    assert "password" in result["error"].lower()


def test_a_workbook_with_many_sheets_is_read_sheet_by_sheet(read_file, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    book.active.title = "S0"
    book.active.append(["v", 0])
    for i in range(1, 60):
        book.create_sheet(f"S{i}").append(["v", i])
    path = tmp_path / "many.xlsx"
    book.save(path)

    text = document_text.extract_document_text(str(path))[0]

    assert text.count("[Sheet: ") == 60
    assert "[Sheet: S59]" in text
    assert read_file(str(path))["status"] == "success"


def test_a_workbook_past_the_text_cap_is_refused_not_cut(
    read_file, tmp_path, monkeypatch
):
    openpyxl = pytest.importorskip("openpyxl")
    monkeypatch.setattr(document_text, "MAX_EXTRACT_CHARS", 20_000)
    book = openpyxl.Workbook(write_only=True)
    sheet = book.create_sheet()
    for i in range(5_000):
        sheet.append([f"row {i}", i, i * 2.5])
    path = tmp_path / "huge.xlsx"
    book.save(path)

    started = time.monotonic()
    result = read_file(str(path))

    assert time.monotonic() - started < 60
    assert _is_clean_error(result), result
    assert "index_document" in result["error"]


@pytest.mark.parametrize("size, refused", [(0, False), (1, False), (2, True)])
def test_the_text_cap_boundary(read_file, tmp_path, monkeypatch, size, refused):
    """Exactly the cap is read; one character more is refused, never truncated."""
    path = _docx(tmp_path / "edge.docx", ["placeholder"])
    text = "x" * (MAX_EXTRACT_CHARS - 1 + size)
    monkeypatch.setattr("gaia.rag.docx_utils.extract_docx_text", lambda _p: text)

    result = read_file(str(path))

    if refused:
        assert _is_clean_error(result), result
        assert "index_document" in result["error"]
    else:
        assert result["status"] == "success", result
        assert result["total_chars"] == len(text)


@pytest.mark.parametrize(
    "offset, limit", [(-1, None), (0, 0), (0, -5), ("10", None), (True, None)]
)
def test_bad_offsets_and_limits_are_refused(read_file, tmp_path, offset, limit):
    path = _docx(tmp_path / "a.docx", ["hello"])

    result = read_file(str(path), offset=offset, limit=limit)

    assert _is_clean_error(result), result


def test_an_offset_past_the_end_of_a_document_is_an_error(read_file, tmp_path):
    path = _docx(tmp_path / "a.docx", ["hello"])

    assert _is_clean_error(read_file(str(path), offset=10_000))


def test_paging_a_long_document_always_moves_forward(read_file, tmp_path):
    path = _docx(tmp_path / "long.docx", ["y" * 5000 for _ in range(10)])
    offset, seen = 0, []
    for _ in range(100):
        result = read_file(str(path), offset=offset)
        assert result["status"] == "success", result
        seen.append(result["content"])
        if result["next_offset"] is None:
            break
        assert result["next_offset"] > offset
        offset = result["next_offset"]
    else:
        pytest.fail("paging never reached the end")

    assert "".join(seen).count("y") == 50_000


@pytest.mark.parametrize(
    "data",
    [
        "café crème".encode("cp1252"),
        b"\xff\xfe\x00\x00garbage\x80\x81\x82",
        b"",
    ],
    ids=["cp1252", "bad-bom", "empty"],
)
def test_non_utf8_text_is_its_text_or_marked_binary_never_noise(
    read_file, tmp_path, data
):
    path = tmp_path / "notes.txt"
    path.write_bytes(data)

    result = read_file(str(path))

    assert result.get("status") in ("success", "error"), result
    if result["status"] == "error":
        assert _is_clean_error(result)
    elif result.get("file_type") == "binary":
        assert result["content"] == f"[Binary file, {len(data)} bytes]"
    else:
        assert "\x00" not in result["content"]


def test_utf16_text_with_a_bom_reads_as_text(read_file, tmp_path):
    path = tmp_path / "notes.txt"
    path.write_bytes("naïve résumé".encode("utf-16"))

    result = read_file(str(path))

    assert result["status"] == "success"
    assert "naïve résumé" in result["content"]


# ---------------------------------------------------------------------------
# fetch_page windows
# ---------------------------------------------------------------------------


def test_an_offset_past_the_end_of_a_page_says_so():
    shown = text_window("short page", URL, 512, offset=10_000)

    assert "past the end" in shown


@pytest.mark.parametrize("term", [".*", "(", "[a-z]+", "$9.99", "a|b", "\\d", "^", "?"])
def test_find_treats_regex_metacharacters_as_plain_text(term):
    page = "Intro. " * 50 + f"The literal token {term} appears once here. " + "x " * 50

    shown = text_window(page, URL, 5000, find=term)

    assert term in shown
    assert shown.count("[at character") == 1


def test_find_with_no_match_says_so_and_does_not_infer():
    shown = text_window("A page about boards. " * 100, URL, 5000, find="$1,299")

    assert "No occurrence of '$1,299'" in shown
    assert "Do not infer it" in shown


@pytest.mark.parametrize("term", ["", "   ", "\n"])
def test_a_blank_find_is_plain_paging(term):
    page = "z" * 3000

    shown = text_window(page, URL, 1000, find=term)

    assert shown.startswith("z" * 1000)
    assert "offset=1000" in shown


def test_find_on_a_ten_megabyte_page_is_fast_and_bounded():
    page = ("filler text " * 1000 + "price $42.00 ") * 800
    assert len(page) > 9_000_000

    started = time.monotonic()
    shown = text_window(page, URL, 5000, find="price")

    assert time.monotonic() - started < 10
    assert len(shown) < 5000 + 500
    assert "more passage(s) match 'price'" in shown


def test_plain_paging_a_ten_megabyte_page_is_bounded_and_resumable():
    page = "0123456789" * 1_000_000

    shown = text_window(page, URL, 20000, offset=5_000_000)

    assert len(shown) < 20000 + 500
    assert "offset=5020000" in shown


def test_following_every_continue_marker_reaches_the_end_of_a_passage():
    """A passage longer than the window continues forward, even with find kept."""
    page = "a" * 2000 + " PRICE " + "b" * 2000
    max_length = 120
    offset, steps = 0, 0
    while steps < 200:
        steps += 1
        shown = text_window(page, URL, max_length, offset=offset, find="PRICE")
        if "offset=" not in shown:
            break
        next_offset = int(shown.rsplit("offset=", 1)[1].split(")")[0])
        assert next_offset > offset, (offset, shown[-200:])
        offset = next_offset
    assert steps < 200, "the window never stopped asking for more"


def test_find_matches_are_case_insensitive_and_keep_context():
    page = "x" * 1000 + "Total Price: $95.00" + "y" * 1000

    shown = text_window(page, URL, 5000, find="total price")

    assert "Total Price: $95.00" in shown
    assert "x" * FIND_CONTEXT_CHARS in shown


def test_a_javascript_only_page_says_it_has_no_text(tmp_path):
    from gaia.agents.tools.browser_tools import BrowserToolsMixin

    tools = {}

    class _Host(BrowserToolsMixin):
        def __init__(self):
            self._web_client = MagicMock()
            self._path_validator = None
            self._tools = {}

    def capture(atomic=True):
        def decorator(func):
            tools[func.__name__] = func
            return func

        return decorator

    with patch("gaia.agents.base.tools.tool", capture):
        host = _Host()
        host.register_browser_tools()
    response = MagicMock()
    response.headers = {"Content-Type": "text/html"}
    response.text = "<html><body><div id='root'></div><script>app()</script></body>"
    host._web_client.get.return_value = response
    soup = MagicMock()
    soup.find.return_value = None
    host._web_client.parse_html.return_value = soup
    host._web_client.extract_text.return_value = "Loading..."

    shown = tools["fetch_page"](URL)

    assert "rendered by JavaScript" in shown
    assert "Truncated" not in shown


@pytest.mark.parametrize(
    "kwargs",
    [
        {"offset": -1},
        {"offset": True},
        {"offset": "5"},
        {"find": "x", "extract": "html"},
    ],
)
def test_fetch_page_refuses_bad_paging_arguments(kwargs):
    from gaia.agents.tools.browser_tools import BrowserToolsMixin

    tools = {}

    class _Host(BrowserToolsMixin):
        def __init__(self):
            self._web_client = MagicMock()
            self._path_validator = None
            self._tools = {}

    def capture(atomic=True):
        def decorator(func):
            tools[func.__name__] = func
            return func

        return decorator

    with patch("gaia.agents.base.tools.tool", capture):
        host = _Host()
        host.register_browser_tools()

    shown = tools["fetch_page"](URL, **kwargs)

    assert shown.startswith("Error:")
    host._web_client.get.assert_not_called()
