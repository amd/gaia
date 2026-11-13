# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Stop a streamed reply that has turned into noise before anyone sees it.

A local model whose state is corrupted keeps sampling the same token until it
hits the output limit: Gemma 4 streamed tens of thousands of ``<unused49>``
tokens this way. Penalties cannot stop it, so the stream is checked instead.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterator, List, Optional, Tuple

#: Gemma's reserved vocabulary slots. They are never trained, so a model that
#: samples one is no longer producing language.
RESERVED_TOKEN_RE = re.compile(r"<unused\d+>")
_ONLY_RESERVED_RE = re.compile(r"\s*(?:<unused\d+>\s*)+")

#: Identical text pieces in a row that end a reply. Tokenizers merge runs of a
#: character into one token, so real text never comes close.
REPEAT_LIMIT = 200

_TEXT_FIELDS = ("content", "reasoning_content")


class DegenerateOutput(Exception):
    """The reply stream turned into noise; ``token`` is what it kept sending."""

    def __init__(self, token: str, reason: str):
        self.token = token
        self.reason = reason
        super().__init__(f"{reason}: {token[:40]!r}")


def _texts(chunk: Dict[str, Any]) -> Iterator[Tuple[str, str]]:
    for choice in chunk.get("choices") or ():
        delta = choice.get("delta") or {}
        for field in _TEXT_FIELDS:
            text = delta.get(field)
            if text:
                yield field, text


def _run_key(chunk: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """The one text piece *chunk* carries, or None when it carries anything else."""
    choices = chunk.get("choices") or ()
    if len(choices) != 1 or chunk.get("usage"):
        return None
    choice = choices[0]
    delta = choice.get("delta") or {}
    if choice.get("finish_reason") or delta.get("tool_calls"):
        return None
    pieces = list(_texts(chunk))
    return pieces[0] if len(pieces) == 1 else None


class DegenerateOutputGuard:
    """Pass a reply's chunks through, holding back a run of identical ones.

    :meth:`feed` returns the chunks safe to hand on now and raises
    :class:`DegenerateOutput` once the reply is noise, so none of the noise is
    ever handed on. Call :meth:`flush` when the stream ends.
    """

    def __init__(self, repeat_limit: int = REPEAT_LIMIT):
        self.repeat_limit = repeat_limit
        self._key: Optional[Tuple[str, str]] = None
        self._count = 0
        self._held: List[Dict[str, Any]] = []

    def feed(self, chunk: Dict[str, Any]) -> List[Dict[str, Any]]:
        for _field, text in _texts(chunk):
            if _ONLY_RESERVED_RE.fullmatch(text):
                raise DegenerateOutput(text.strip(), "reserved token")
        key = _run_key(chunk)
        if key is not None and key == self._key:
            self._count += 1
            if self._count >= self.repeat_limit:
                raise DegenerateOutput(key[1], f"same token {self._count} times")
            self._held.append(chunk)
            return []
        out = self.flush()
        out.append(chunk)
        self._key, self._count = key, 1
        return out

    def flush(self) -> List[Dict[str, Any]]:
        out, self._held = self._held, []
        return out
