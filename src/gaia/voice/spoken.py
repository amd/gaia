# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The spoken-reply contract: split ``<spoken>…</spoken>`` off a streamed answer.

A voice turn's answer opens with a short spoken summary in ``<spoken>`` tags,
then the detailed text. :class:`SpokenSplitter` takes the answer as it streams
and routes each piece to the spoken channel or the detail channel, with the
tags removed. It never buffers more than a tag's length of detail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Tuple

OPEN = "<spoken>"
CLOSE = "</spoken>"

#: Spoken when the model leaves the block out, so a format slip is audible as
#: a known line instead of the detail's first sentence read as if it were one.
MISSING_SPOKEN_LINE = "Done. The details are on screen."

_WS = re.compile(r"\s+")


@dataclass
class SpokenSplitter:
    """Feed answer text with :meth:`feed`; read ``(channel, text)`` pieces back.

    ``channel`` is ``"spoken"`` or ``"detail"``. Leading whitespace before the
    open tag is ignored; any other text first means the block is missing.
    """

    _state: str = "start"  # start → spoken → gap → detail
    _pending: str = ""
    spoken: List[str] = field(default_factory=list)
    missing: bool = False

    def feed(self, text: str) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        self._pending += text
        while self._pending:
            if self._state == "start":
                stripped = self._pending.lstrip()
                if not stripped:
                    return out
                if stripped.startswith(OPEN):
                    self._pending = stripped[len(OPEN) :]
                    self._state = "spoken"
                    continue
                if OPEN.startswith(stripped):
                    return out  # might still become the tag
                self.missing = True
                self._state = "detail"
                self._pending = stripped
                continue
            if self._state == "spoken":
                idx = self._pending.find(CLOSE)
                if idx >= 0:
                    piece = self._pending[:idx]
                    self._pending = self._pending[idx + len(CLOSE) :]
                    self._state = "gap"
                    if piece:
                        self.spoken.append(piece)
                        out.append(("spoken", piece))
                    continue
                keep = _partial_suffix(self._pending, CLOSE)
                piece = self._pending[: len(self._pending) - keep]
                self._pending = self._pending[len(piece) :]
                if piece:
                    self.spoken.append(piece)
                    out.append(("spoken", piece))
                return out
            if self._state == "gap":
                # Whitespace between the block and the detail is dropped,
                # however the stream happens to split it.
                self._pending = self._pending.lstrip()
                if not self._pending:
                    return out
                self._state = "detail"
            out.append(("detail", self._pending))
            self._pending = ""
        return out

    def reset(self) -> None:
        """A tool step started: what came before was narration, not the answer."""
        self._state = "start"
        self._pending = ""
        self.spoken = []
        self.missing = False

    def finish(self) -> List[Tuple[str, str]]:
        """Flush what is left when the answer ends."""
        out: List[Tuple[str, str]] = []
        if self._state == "start":
            if self._pending.strip():
                out.append(("detail", self._pending.strip()))
            self.missing = True
        elif self._state == "spoken":
            # An unclosed block: what was said stands, the slip is recorded.
            if self._pending:
                self.spoken.append(self._pending)
                out.append(("spoken", self._pending))
            self.missing = not self.spoken_text
        elif self._pending.strip() or (self._state == "detail" and self._pending):
            out.append(("detail", self._pending))
        self._pending = ""
        self._state = "detail"
        return out

    @property
    def spoken_text(self) -> str:
        return _WS.sub(" ", "".join(self.spoken)).strip()


def _partial_suffix(text: str, tag: str) -> int:
    """Length of the longest suffix of *text* that is a prefix of *tag*."""
    for n in range(min(len(tag) - 1, len(text)), 0, -1):
        if text.endswith(tag[:n]):
            return n
    return 0


def without_tags(answer: str) -> str:
    """*answer* as text: a leading spoken block becomes its own first paragraph."""
    if OPEN not in answer:
        return answer
    spoken, detail, missing = split_spoken(answer)
    if missing:
        return answer
    return f"{spoken}\n\n{detail}".strip()


def split_spoken(answer: str) -> Tuple[str, str, bool]:
    """Split a whole answer: ``(spoken, detail, missing)``."""
    splitter = SpokenSplitter()
    pieces = splitter.feed(answer) + splitter.finish()
    detail = "".join(text for channel, text in pieces if channel == "detail")
    return splitter.spoken_text, detail.strip(), splitter.missing
