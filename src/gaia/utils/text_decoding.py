# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The one way GAIA works out how a text file is encoded.

A byte-order mark names its codec (UTF-16/UTF-32 from PowerShell 5.1 ``>`` and
some Notepad saves, UTF-8 with BOM); otherwise UTF-8; otherwise the legacy code
page charset-normalizer detects (Windows-1256 Arabic, Shift-JIS, GBK,
Windows-1251…), with CP1252 kept for Western text it reads the same way. Text
too short to detect (a word or two) reads in this PC's Windows code page. The
file tools, the Agent UI preview and document Q&A all decode through here, so a
file reads the same everywhere.
"""

import codecs
import functools
import itertools
import os
import unicodedata
from typing import Optional, Tuple, Union

from charset_normalizer import from_bytes

# UTF-32 first: its little-endian BOM begins with the UTF-16 LE BOM.
_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)

#: Codecs tried, for error messages that say what was attempted.
TRIED_ENCODINGS = (
    "utf-32, utf-8-sig, utf-16 (by byte-order mark), utf-8, cp1252, "
    "detected legacy encodings (Windows-1256, Shift-JIS, GBK…)"
)

# Bytes legacy code pages map to control characters text never holds; NUL marks binary.
# ESC (ANSI colour codes in logs) and SUB (DOS end-of-file) do turn up in real text.
_CONTROL_BYTES = frozenset(range(32)) - {9, 10, 12, 13, 26, 27}

#: How much of a file :func:`file_encoding` reads to decide.
SNIFF_BYTES = 1 << 16


#: Fewer non-ASCII bytes than this give detection nothing to go on.
_TOO_FEW_TO_TELL = 8
#: Up to about a sentence: past this, a zero score (common for CJK) is not a guess.
_SHORT_TEXT = 64


def _western_glued_to_ascii(data: bytes, western: str) -> bool:
    """Whether a non-ASCII byte sits inside an ASCII word the Western way.

    Beside an ASCII letter ("café"), or beside a digit as a currency sign
    ("12€"). A local letter beside a digit is not: Russian "5кг" and "№1",
    Greek "5ευρώ" are local text. *western* is *data* read as cp1252, one
    character per byte.
    """
    for i, byte in enumerate(data):
        if byte < 0x80:
            continue
        neighbours = data[max(0, i - 1) : i] + data[i + 1 : i + 2]
        if any(chr(b).isascii() and chr(b).isalpha() for b in neighbours):
            return True
        if any(chr(b).isdigit() for b in neighbours) and (
            unicodedata.category(western[i]) == "Sc"
        ):
            return True
    return False


@functools.lru_cache(maxsize=1)
def _ansi_code_page() -> Optional[str]:
    """This PC's Windows ANSI code page (``cp1256`` on Arabic Windows), or None.

    None off Windows and when the code page is UTF-8 (65001).
    """
    if os.name != "nt":
        return None
    import ctypes

    page = ctypes.windll.kernel32.GetACP()
    if page == 65001:
        return None
    name = f"cp{page}"
    try:
        codecs.lookup(name)
    except LookupError:
        return None
    return name


def _local_legacy_encoding(data: bytes) -> Optional[str]:
    """This PC's ANSI code page when it decodes *data* and isn't the Western one.

    A legacy file on a PC is nearly always in that PC's code page; this settles
    the short files the detector can only guess at.
    """
    page = _ansi_code_page()
    if page is None or page == "cp1252":
        return None
    try:
        data.decode(page)
    except UnicodeDecodeError:
        return None
    try:
        western = data.decode("cp1252")
    except UnicodeDecodeError:
        return page
    # "cafй", "12Ђ": Western text read in the local page.
    if _latin_script_only(western) and _western_glued_to_ascii(data, western):
        return None
    return page


def _latin_script_only(text: str) -> bool:
    """Whether every non-ASCII letter in *text* is a Latin one."""
    return all(
        unicodedata.name(char, "").startswith("LATIN")
        for char in text
        if ord(char) > 127 and char.isalpha()
    )


def detect_encoding(data: bytes, truncated: bool = False) -> str:
    """The codec that decodes *data* as text.

    The BOM codecs (``utf-16``, ``utf-32``, ``utf-8-sig``) consume the BOM, so
    it never reaches the decoded text. *truncated* means *data* is the head of
    a longer file, so a character cut at its end does not count against it.

    Raises:
        UnicodeDecodeError: no text encoding fits *data*.
    """
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            return encoding
    try:
        data.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError as exc:
        if truncated and exc.start >= len(data) - 3:
            return "utf-8"
    if truncated and b"\n" in data:
        # Whole lines only, so a double-byte character isn't split at the cut.
        whole_lines = data[: data.rindex(b"\n") + 1]
        if not whole_lines.isascii():
            data = whole_lines
    if not _CONTROL_BYTES.isdisjoint(data):
        raise UnicodeDecodeError(
            "text", data, 0, len(data), "control bytes: not a text file"
        )

    try:
        cp1252_text = data.decode("cp1252")
    except UnicodeDecodeError:
        cp1252_text = None
    # A BOM-less UTF-16/32 guess on a few bytes is noise.
    best = next(
        (
            m
            for m in from_bytes(data)
            if not m.encoding.startswith(("utf_16", "utf_32"))
        ),
        None,
    )
    high = sum(byte >= 0x80 for byte in data)
    # No language evidence (a word or a few short lines): every guess scores 0.
    if high < _SHORT_TEXT and (best is None or not best.coherence):
        local = _local_legacy_encoding(data)
        if local is not None:
            return local
    if best is not None:
        # Latin-script text scores as cp1250/cp775 too, and a handful of bytes
        # ("café") scores as anything: both are Windows ANSI. Another script
        # (Cyrillic names in an English CSV) is the detector's call.
        if cp1252_text is not None and (
            high < _TOO_FEW_TO_TELL or _latin_script_only(str(best))
        ):
            return "cp1252"
        return best.encoding
    # Symbol-heavy Western text ("12€ — 15€") the detector can't place.
    if cp1252_text is not None and high <= sum(0x20 < byte < 0x80 for byte in data):
        return "cp1252"
    raise UnicodeDecodeError(
        "text", data, 0, len(data), "no text encoding fits these bytes"
    )


def decode_text(data: bytes) -> Tuple[str, str]:
    """``(text, encoding)`` for the whole of *data*.

    Raises:
        UnicodeDecodeError: no text encoding fits *data*.
    """
    encoding = detect_encoding(data)
    return data.decode(encoding), encoding


def name_file(
    exc: UnicodeDecodeError, path: Union[str, os.PathLike]
) -> UnicodeDecodeError:
    """*exc* again, with *path* in its reason so the message names the file."""
    return UnicodeDecodeError(
        exc.encoding,
        exc.object,
        exc.start,
        exc.end,
        f"{os.fspath(path)} is not readable text: {exc.reason}",
    )


def file_encoding(path: Union[str, os.PathLike]) -> str:
    """The codec to open *path* as text with: its first 64 KB decide, the rest confirms UTF-8.

    Raises:
        UnicodeDecodeError: no text encoding fits; the message names *path*.
    """
    with open(path, "rb") as stream:
        head = stream.read(SNIFF_BYTES)
        truncated = len(head) == SNIFF_BYTES
        try:
            encoding = detect_encoding(head, truncated=truncated)
            # An ASCII head says nothing about the accented word on line 9,000.
            if truncated and encoding == "utf-8":
                bad, saw_utf8 = _first_non_utf8(stream, head)
                if bad is not None:
                    if saw_utf8:
                        # Real UTF-8 came first: cp1252 would garble all of it.
                        raise UnicodeDecodeError(
                            "utf-8",
                            b"",
                            0,
                            1,
                            f"UTF-8 except for a damaged byte at offset {bad}; "
                            "re-save the file as UTF-8",
                        )
                    encoding = detect_encoding(*_window_at(stream, bad))
        except UnicodeDecodeError as exc:
            raise name_file(exc, path) from exc
    return encoding


def _first_non_utf8(stream, head: bytes) -> Tuple[Optional[int], bool]:
    """``(offset, saw_utf8)``: the first byte of *head* plus the rest of *stream* that
    isn't UTF-8, and whether multibyte UTF-8 came before it."""
    decoder = codecs.getincrementaldecoder("utf-8")()
    chunks = itertools.chain((head,), iter(lambda: stream.read(1 << 20), b""))
    consumed = 0
    saw_utf8 = False
    for chunk in itertools.chain(chunks, (b"",)):
        # exc.start counts from the bytes the decoder held back from the last chunk.
        pending = len(decoder.getstate()[0])
        try:
            text = decoder.decode(chunk, final=not chunk)
        except UnicodeDecodeError as exc:
            start = max(0, exc.start - pending)
            return (
                consumed - pending + exc.start,
                saw_utf8 or not chunk[:start].isascii(),
            )
        saw_utf8 = saw_utf8 or not text.isascii()
        consumed += len(chunk)
    return None, saw_utf8


def _window_at(stream, offset: int) -> Tuple[bytes, bool]:
    """``(bytes, truncated)``: up to :data:`SNIFF_BYTES` from the line holding *offset*."""
    start = max(0, offset - SNIFF_BYTES // 2)
    stream.seek(start)
    window = stream.read(SNIFF_BYTES)
    truncated = bool(stream.read(1))
    if start:
        # Start on a line boundary so a double-byte character isn't split.
        newline = window.rfind(b"\n", 0, offset - start)
        window = window[newline + 1 :]
    return window, truncated
