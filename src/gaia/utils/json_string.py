# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Lenient decoding of JSON string values out of model output.

Models that answer in ``{"thought": ..., "answer": "..."}`` form often emit
JSON that ``json.loads`` rejects — raw newlines inside the string, a Windows
path with a bare backslash, or an object cut off by the output-token cap. The
helpers here decode the string value anyway, honouring backslash escapes so an
escaped quote never ends the value early, and report whether the closing quote
was actually seen so a truncated value is never mistaken for a complete one.
"""

import re
from typing import Optional, Tuple

_SIMPLE_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}
_HEX = frozenset("0123456789abcdefABCDEF")
# What may follow a string value's closing quote in a JSON object or array.
_VALUE_END_RE = re.compile(r'\s*(?:\}|\]|,\s*"[^"\n]*"\s*:|$)')
_BARE_BACKSLASH_END_RE = re.compile(r'\\"\s*\}\s*$')


def _read_hex4(raw: str, pos: int) -> Tuple[Optional[int], bool]:
    """Read the four hex digits of a ``\\u`` escape starting at ``raw[pos]``.

    Returns ``(code_point, incomplete)``. ``incomplete`` is True when ``raw``
    ends before four characters are available; ``code_point`` is None when the
    digits are present but not hex.
    """
    digits = raw[pos : pos + 4]
    if len(digits) < 4:
        if all(c in _HEX for c in digits):
            return None, True
        return None, False
    if all(c in _HEX for c in digits):
        return int(digits, 16), False
    return None, False


def decode_json_string_prefix(raw: str) -> Tuple[str, int, bool]:
    """Decode the body of a JSON string — the text after its opening quote.

    Decoding stops at the first unescaped ``"``. Escapes follow JSON
    (``\\n``, ``\\"``, ``\\uXXXX`` including surrogate pairs); an unknown
    escape such as ``\\U`` in ``C:\\Users`` is kept verbatim, and raw control
    characters are kept as-is.

    Returns ``(decoded, consumed, closed)``:

    - ``closed`` — the closing quote was found; ``consumed`` is the index just
      past it.
    - otherwise ``consumed`` is how much of ``raw`` was decoded. A trailing
      escape that is not complete yet (``\\``, ``\\u00``, or a high surrogate
      waiting for its low half) is left unconsumed so a streaming caller can
      retry it once more text arrives.
    """
    out = []
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch == '"':
            return "".join(out), i + 1, True
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= n:
            break  # lone trailing backslash — wait for the escaped char
        esc = raw[i + 1]
        if esc in _SIMPLE_ESCAPES:
            out.append(_SIMPLE_ESCAPES[esc])
            i += 2
            continue
        if esc != "u":
            out.append("\\" + esc)
            i += 2
            continue
        code, incomplete = _read_hex4(raw, i + 2)
        if incomplete:
            break
        if code is None:
            out.append("\\u")
            i += 2
            continue
        if 0xD800 <= code <= 0xDBFF:
            # High surrogate: combine with a following \uDC00-\uDFFF.
            tail = raw[i + 6 : i + 8]
            if len(tail) < 2 and "\\u".startswith(tail):
                break
            if tail == "\\u":
                low, low_incomplete = _read_hex4(raw, i + 8)
                if low_incomplete:
                    break
                if low is not None and 0xDC00 <= low <= 0xDFFF:
                    out.append(chr(0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)))
                    i += 12
                    continue
            out.append("\ufffd")
            i += 6
            continue
        if 0xDC00 <= code <= 0xDFFF:
            out.append("\ufffd")
        else:
            out.append(chr(code))
        i += 6
    return "".join(out), i, False


def json_string_field_start(text: str, key: str) -> Optional[int]:
    """Index just past the opening quote of ``"key": "`` in ``text``."""
    # The lookbehind skips an escaped \"key\" quoted inside another value.
    match = re.search(r'(?<!\\)"' + re.escape(key) + r'"\s*:\s*"', text)
    return match.end() if match else None


def extract_json_string_field(text: str, key: str) -> Optional[Tuple[str, bool]]:
    """Decode the string value of ``key`` from JSON-ish ``text``.

    Works on JSON that ``json.loads`` rejects — raw control characters,
    invalid escapes, or an object cut off mid-value.

    A quote that is not followed by JSON structure (``, "next":``, ``}``,
    ``]`` or the end of the text) is a quote the model forgot to escape, and
    is kept as part of the value. A value whose last characters are ``\\"}``
    ended with a bare backslash (``C:\\temp\\``), not an escaped quote.

    Returns ``(value, complete)`` where ``complete`` is False when the text
    ended before the value's closing quote (the value holds everything that
    was received), or None when ``key`` has no string value in ``text``.
    """
    start = json_string_field_start(text, key)
    if start is None:
        return None
    body = text[start:]
    parts = []
    pos = 0
    while True:
        decoded, consumed, closed = decode_json_string_prefix(body[pos:])
        if not closed:
            break
        parts.append(decoded)
        pos += consumed
        if _VALUE_END_RE.match(body, pos):
            return "".join(parts), True
        parts.append('"')
    bare_backslash = _BARE_BACKSLASH_END_RE.search(body, pos)
    if bare_backslash:
        decoded, _, _ = decode_json_string_prefix(body[pos : bare_backslash.start()])
        parts.append(decoded + "\\")
        return "".join(parts), True
    parts.append(decoded)
    return "".join(parts), False


class JsonStringStreamDecoder:
    """Decode a JSON string value as its raw text arrives in pieces.

    Feed the raw text that follows the opening quote; each ``feed`` returns
    only the newly decoded text. An escape split across two pieces is held
    until it completes. Once the closing quote arrives, ``closed`` is True and
    anything after it (``"}`` and other JSON structure) collects in ``tail``
    instead of being returned as text.
    """

    def __init__(self) -> None:
        self._pending = ""
        self.closed = False
        self.tail = ""

    def feed(self, raw: str) -> str:
        if self.closed:
            self.tail += raw
            return ""
        self._pending += raw
        decoded, consumed, closed = decode_json_string_prefix(self._pending)
        if closed:
            self.closed = True
            self.tail = self._pending[consumed:]
            self._pending = ""
        else:
            self._pending = self._pending[consumed:]
        return decoded
