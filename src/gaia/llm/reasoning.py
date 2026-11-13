# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Split a model's inline ``<think>`` reasoning from its answer.

One rule for every path that reads a raw reply, so the answer the user sees,
memory extraction and skill synthesis agree on where reasoning ends.
"""

import re
from typing import List, Optional, Tuple

THINK_OPEN = "<think>"
THINK_CLOSE = "</think>"

_THINK_BLOCK_PATTERN = re.compile(r"<think>(.*?)</think>", re.DOTALL)
# Anchored: only a reply that *opens* with an unclosed block was cut off
# mid-thought. A `<think>` mentioned mid-sentence is prose, and treating it as
# reasoning silently deletes everything the model said after it.
_CUT_OFF_THINK_PATTERN = re.compile(r"\A<think>(?!.*</think>)(.*)\Z", re.DOTALL)


def has_lone_close(text: str) -> bool:
    """Whether *text*'s first ``</think>`` has no ``<think>`` before it."""
    head, found, _ = text.partition(THINK_CLOSE)
    return bool(found) and THINK_OPEN not in head


def split_reasoning(text: str) -> Tuple[str, Optional[str]]:
    """Split inline ``<think>`` reasoning out of a raw reply.

    Returns ``(answer_text, reasoning)``. A reply that *opens* with an unclosed
    ``<think>`` was cut off mid-thought and is reasoning to the end, and text
    before a lone ``</think>`` (the template opened the block in the prompt) is
    reasoning. Apply it to a raw reply once: an answer may quote the tags.
    """
    parts: List[str] = []
    if has_lone_close(text):
        head, _, text = text.partition(THINK_CLOSE)
        parts.append(head.strip())
    cut_off = _CUT_OFF_THINK_PATTERN.match(text.lstrip())
    if cut_off:
        parts.append(cut_off.group(1).strip())
        text = ""
    parts.extend(m.strip() for m in _THINK_BLOCK_PATTERN.findall(text))
    answer = _THINK_BLOCK_PATTERN.sub("", text).strip()
    return answer, "\n\n".join(p for p in parts if p) or None


def strip_reasoning(text: str) -> str:
    """*text*'s answer with its inline reasoning left out."""
    return split_reasoning(text)[0]


def _partial_tag(text: str) -> int:
    """Length of *text*'s tail that may be the start of a think tag."""
    for size in range(min(len(text), len(THINK_CLOSE) - 1), 0, -1):
        tail = text[-size:]
        if THINK_OPEN.startswith(tail) or THINK_CLOSE.startswith(tail):
            return size
    return 0


class ReasoningStreamFilter:
    """Drop inline ``<think>`` reasoning from a reply as it streams.

    Only a possible partial tag at a chunk's end is held back, so the answer
    still streams. A reply cut off inside a block is reasoning to the end.
    """

    def __init__(self) -> None:
        self._held = ""
        self._inside = False
        self._answered = False

    def feed(self, chunk: str) -> str:
        """The part of *chunk* that is answer and safe to show now."""
        self._held += chunk
        out: List[str] = []
        while True:
            if self._inside:
                end = self._held.find(THINK_CLOSE)
                if end < 0:
                    keep = _partial_tag(self._held)
                    self._held = self._held[len(self._held) - keep :] if keep else ""
                    break
                self._held = self._held[end + len(THINK_CLOSE) :]
                self._inside = False
                continue
            start = self._held.find(THINK_OPEN)
            if start >= 0:
                out.append(self._held[:start])
                self._held = self._held[start + len(THINK_OPEN) :]
                self._inside = True
                continue
            # A lone close tag: the template opened the block in the prompt.
            self._held = self._held.replace(THINK_CLOSE, "")
            keep = _partial_tag(self._held)
            cut = len(self._held) - keep
            out.append(self._held[:cut])
            self._held = self._held[cut:]
            break
        return self._shown("".join(out))

    def flush(self) -> str:
        """Whatever was held back, once the reply has ended."""
        held, self._held = ("" if self._inside else self._held), ""
        return self._shown(held)

    def _shown(self, text: str) -> str:
        # The blank line a think block leaves before the answer isn't answer.
        if not self._answered:
            text = text.lstrip()
            self._answered = bool(text)
        return text
