# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The one way GAIA works out how a text file is encoded.

A byte-order mark names its codec (UTF-16/UTF-32 from PowerShell 5.1 ``>`` and
some Notepad saves, UTF-8 with BOM); otherwise UTF-8; otherwise the legacy code
page charset-normalizer detects (Windows-1256 Arabic, Shift-JIS, GBK,
Windows-1251…), with CP1252 kept for Western text it reads the same way. The
file tools, the Agent UI preview and document Q&A all decode through here, so a
file reads the same everywhere.
"""

import codecs
import os
from typing import Tuple, Union

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
_CONTROL_BYTES = frozenset(range(32)) - {9, 10, 12, 13}

#: How much of a file :func:`file_encoding` reads to decide.
SNIFF_BYTES = 1 << 16


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

    matches = from_bytes(data)
    try:
        cp1252_text = data.decode("cp1252")
    except UnicodeDecodeError:
        cp1252_text = None
    # Short Western text scores as cp1250/cp775 too; keep cp1252 when it reads the same.
    if cp1252_text is not None and any(str(m) == cp1252_text for m in matches):
        return "cp1252"
    best = matches.best()
    if best is not None:
        return best.encoding
    # Symbol-heavy Western text ("12€ — 15€") the detector can't place.
    high = sum(byte >= 0x80 for byte in data)
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
            if truncated and encoding == "utf-8" and not _rest_is_utf8(stream, head):
                stream.seek(0)
                encoding = detect_encoding(stream.read())
        except UnicodeDecodeError as exc:
            raise name_file(exc, path) from exc
    return encoding


def _rest_is_utf8(stream, head: bytes) -> bool:
    """Whether *head* plus the rest of *stream* decodes as UTF-8."""
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        decoder.decode(head)
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            decoder.decode(chunk)
        decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        return False
    return True
