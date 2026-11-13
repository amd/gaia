# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Measure, split and join document text the same way in every script.

Chinese and Japanese put no spaces between words, so a whitespace split sees a
whole paragraph as one word. Here each Han, kana or Hangul character counts as
one unit, about what it costs the model in tokens.
"""

import re
from typing import List, Sequence

# Han and kana, written without spaces between words.
_UNSPACED_CHARS = (
    "\u3005-\u3007\u3040-\u30ff\u31f0-\u31ff\u3400-\u4dbf"
    "\u4e00-\u9fff\uf900-\ufaff\uff66-\uff9f\U00020000-\U0003134f"
)
# Hangul is spaced, but a syllable still costs about a token.
WIDE_CHARS = _UNSPACED_CHARS + "\u1100-\u11ff\uac00-\ud7af"
_WIDE = re.compile(f"[{WIDE_CHARS}]")
_UNIT = re.compile(f"[{WIDE_CHARS}]|[^\\s{WIDE_CHARS}]+")
_NO_SPACE = re.compile(f"[{_UNSPACED_CHARS}\u3000-\u303f\uff01-\uff65]")

SENTENCE_BREAK = re.compile(
    # A full stop before a capital, or before a letter of a script with no case.
    r"(?<=[.!?])\s+(?=[A-Z\u0590-\u08ff\u0900-\u0dff])"
    # Arabic, Urdu and Hindi sentence ends.
    r"|(?<=[\u061f\u06d4\u0964\u0965])\s+"
    # Chinese and Japanese ends, unless a closing quote or bracket follows.
    "|(?<=[\u3002\uff01\uff1f])(?![\u300d\u300f\uff09\u3015\u3011\u201d\u2019])\\s*"
)
_WIDE_SENTENCE_END = re.compile("[\u3002\uff01\uff1f]")


def wide_runs(word: str) -> List[str]:
    """``word`` cut into its runs of wide characters and of everything else."""
    return re.findall(f"[{WIDE_CHARS}]+|[^{WIDE_CHARS}]+", word)


def count_units(text: str) -> int:
    """Words, counting each wide character as a word of its own."""
    return len(_UNIT.findall(text))


def estimate_tokens(text: str) -> int:
    """About how many model tokens ``text`` costs: one per wide character, else 4 chars."""
    wide = len(_WIDE.findall(text))
    return wide + (len(text) - wide) // 4


def split_by_units(text: str, size: int) -> List[str]:
    """``text`` cut into consecutive parts of at most ``size`` units, losing nothing."""
    starts = [match.start() for match in _UNIT.finditer(text)][size::size]
    bounds = [0, *starts, len(text)]
    return [text[a:b] for a, b in zip(bounds, bounds[1:]) if text[a:b]]


def join_pieces(pieces: Sequence[str]) -> str:
    """Join sentences or words with a space, except between Chinese or Japanese."""
    joined = ""
    for piece in pieces:
        if not joined:
            joined = piece
        elif _NO_SPACE.match(joined[-1]) and _NO_SPACE.match(piece[:1]):
            joined += piece
        else:
            joined += " " + piece
    return joined


def last_tokens(text: str, n_tokens: int) -> str:
    """About the last ``n_tokens`` of ``text``, starting at a word or sentence."""
    total = estimate_tokens(text)
    if _WIDE.search(text):
        target_chars = max(1, len(text) * n_tokens // total) if total else len(text)
    else:
        target_chars = n_tokens * 4
    if len(text) <= target_chars:
        return text
    trimmed = text[-target_chars:]
    sentence_end = _WIDE_SENTENCE_END.search(trimmed)
    if sentence_end and sentence_end.end() < len(trimmed):
        return trimmed[sentence_end.end() :]
    first_space = trimmed.find(" ")
    if first_space > 0:
        return trimmed[first_space + 1 :]
    return trimmed
