# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Cut streamed text into pieces worth sending to speech synthesis.

The first piece ends at the first clause break or after a few words, so speech
starts while the model is still writing. Later pieces end at sentence breaks,
which sound more natural.
"""

from __future__ import annotations

import re
from typing import List

FIRST_PIECE_WORDS = 8
_CLAUSE_END = re.compile(r"[,;:—–]\s|[.!?…]+[\"')\]]*\s")
_SENTENCE_END = re.compile(r"[.!?…]+[\"')\]]*\s")
_WORD = re.compile(r"\S+\s")


class ClauseChunker:
    """Feed text with :meth:`feed`; each call returns the pieces now complete."""

    def __init__(self, first_words: int = FIRST_PIECE_WORDS):
        self._buf = ""
        self._first = True
        self._first_words = first_words

    def feed(self, text: str) -> List[str]:
        self._buf += text
        out: List[str] = []
        while True:
            cut = self._next_cut()
            if cut is None:
                return out
            piece, self._buf = self._buf[:cut].strip(), self._buf[cut:]
            self._first = False
            if piece:
                out.append(piece)

    def finish(self) -> List[str]:
        piece, self._buf = self._buf.strip(), ""
        return [piece] if piece else []

    def _next_cut(self):
        if self._first:
            match = _CLAUSE_END.search(self._buf)
            words = list(_WORD.finditer(self._buf))
            by_words = (
                words[self._first_words - 1].end()
                if len(words) >= self._first_words
                else None
            )
            ends = [p for p in (match.end() if match else None, by_words) if p]
            return min(ends) if ends else None
        match = _SENTENCE_END.search(self._buf)
        return match.end() if match else None


def first_sentence(text: str) -> str:
    """The first sentence of *text*, for a spoken status line."""
    text = " ".join(text.split())
    match = _SENTENCE_END.search(text + " ")
    return text[: match.end()].strip() if match else text
