# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shared text-file helpers for the file tool mixins.

Decoding by byte-order mark, so UTF-16/UTF-32 text (what Windows PowerShell 5.1
``>`` redirection and some Notepad saves write) reads as text, not binary; and
bounding a matching line to an excerpt that still shows the match.
"""

import codecs
import os
from typing import Tuple, Union

# UTF-32 first: its little-endian BOM begins with the UTF-16 LE BOM.
_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)

MATCH_EXCERPT_CHARS = 200
_ELLIPSIS = "..."


def text_encoding(path: Union[str, os.PathLike]) -> str:
    """The codec to open *path* as text with: its BOM's codec, else UTF-8.

    The BOM codecs (``utf-16``, ``utf-32``, ``utf-8-sig``) consume the BOM, so
    it never reaches the decoded text.
    """
    with open(path, "rb") as stream:
        head = stream.read(4)
    for bom, encoding in _BOMS:
        if head.startswith(bom):
            return encoding
    return "utf-8"


def read_text(path: Union[str, os.PathLike]) -> Tuple[str, str]:
    """``(content, encoding)`` for a text file.

    Raises:
        UnicodeDecodeError: the file is not text in its detected encoding.
    """
    encoding = text_encoding(path)
    with open(path, "r", encoding=encoding) as stream:
        content = stream.read()
    # Real UTF-16/32 text never decodes to NUL; binary that opens with FF FE does.
    nul = content.find("\x00") if encoding in ("utf-16", "utf-32") else -1
    if nul != -1:
        raise UnicodeDecodeError(
            encoding, b"\x00", 0, 1, f"NUL at character {nul}: not a text file"
        )
    return content, encoding


def match_excerpt(
    line: str, start: int, end: int, max_chars: int = MATCH_EXCERPT_CHARS
) -> str:
    """*line* stripped and cut to *max_chars*, keeping ``line[start:end]`` visible.

    A short line comes back whole. A long one is a window centred on the match,
    with ``...`` marking each cut, so a hit deep in a minified or single-line
    file is still shown rather than the line's unrelated opening.
    """
    lead = len(line) - len(line.lstrip())
    text = line.strip()
    if len(text) <= max_chars:
        return text
    start = min(max(start - lead, 0), len(text))
    end = min(max(end - lead, start), len(text))
    room = max_chars - 2 * len(_ELLIPSIS)
    left = start - max(0, room - (end - start)) // 2
    left = max(0, min(left, len(text) - room))
    right = left + room
    head = _ELLIPSIS if left > 0 else ""
    tail = _ELLIPSIS if right < len(text) else ""
    return head + text[left:right] + tail
