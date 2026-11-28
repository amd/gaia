# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shared text-file helpers for the file tool mixins.

Opening a file in the encoding :mod:`gaia.utils.text_decoding` detects (BOM,
UTF-8, or a legacy code page such as CP1252, Windows-1256 or Shift-JIS),
reading and writing UTF-8 with line endings left alone, and bounding a matching
line to an excerpt that still shows the match.
"""

import os
from typing import Tuple, Union

from gaia.utils.text_decoding import file_encoding as text_encoding
from gaia.utils.text_decoding import name_file

__all__ = [
    "MATCH_EXCERPT_CHARS",
    "match_excerpt",
    "read_exact",
    "read_text",
    "text_encoding",
    "write_exact",
]

MATCH_EXCERPT_CHARS = 200
_ELLIPSIS = "..."


def read_text(path: Union[str, os.PathLike]) -> Tuple[str, str]:
    """``(content, encoding)`` for a text file.

    Raises:
        UnicodeDecodeError: the file is not text in any encoding; the message
            names *path*.
    """
    encoding = text_encoding(path)
    try:
        with open(path, "r", encoding=encoding) as stream:
            content = stream.read()
    except UnicodeDecodeError as exc:
        raise name_file(exc, path) from exc
    # Real UTF-16/32 text never decodes to NUL; binary that opens with FF FE does.
    nul = content.find("\x00") if encoding in ("utf-16", "utf-32") else -1
    if nul != -1:
        raise UnicodeDecodeError(
            encoding,
            b"\x00",
            0,
            1,
            f"{os.fspath(path)} has NUL at character {nul}: not a text file",
        )
    return content, encoding


def read_exact(path: Union[str, os.PathLike]) -> str:
    """Read *path* as text without translating its line endings.

    ``open()`` in text mode, and ``Path.read_text``, default to universal
    newlines: every CRLF in the file arrives as a bare LF. Paired with
    :func:`write_exact` this keeps a read/modify/write round trip
    byte-exact, so a one-line edit stays a one-line diff on every platform.
    """
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def write_exact(path: Union[str, os.PathLike], content: str) -> None:
    """Write *content* to *path* with its line endings left alone.

    ``open()`` in text mode, and ``Path.write_text``, translate every LF to
    ``os.linesep`` on write. On Windows that silently turns an LF-terminated
    file into a CRLF one, which no byte-comparing tool treats as a no-op: a
    three-line edit to a 400-line file is reported as 400 lines changed.
    """
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(content)


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
