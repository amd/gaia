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
    """Drop the ``<think>`` reasoning a streamed reply opens with.

    As in :func:`split_reasoning`, only a block the reply *opens* with is
    reasoning: once answer text has been shown, a tag is quoted prose and
    passes through. Only a possible leading tag is held back, so the answer
    still streams. A reply cut off inside its block is reasoning to the end.
    Text before a lone ``</think>`` (the template opened the block in the
    prompt) has already been shown by then; only the tag itself is dropped.
    ``saw_reasoning`` records whether a leading tag was dropped.
    """

    def __init__(self) -> None:
        self._held = ""
        self._inside = False
        self._answered = False
        self.saw_reasoning = False

    def feed(self, chunk: str) -> str:
        """The part of *chunk* that is answer and safe to show now."""
        if self._answered:
            return chunk
        self._held += chunk
        while not self._answered:
            if self._inside:
                end = self._held.find(THINK_CLOSE)
                if end < 0:
                    keep = _partial_tag(self._held)
                    self._held = self._held[len(self._held) - keep :] if keep else ""
                    return ""
                self._held = self._held[end + len(THINK_CLOSE) :]
                self._inside = False
                continue
            lead = self._held.lstrip()
            for tag in (THINK_OPEN, THINK_CLOSE):
                if lead.startswith(tag):
                    self._held = lead[len(tag) :]
                    self._inside = tag == THINK_OPEN
                    self.saw_reasoning = True
                    break
            else:
                if (
                    not lead
                    or THINK_OPEN.startswith(lead)
                    or THINK_CLOSE.startswith(lead)
                ):
                    return ""  # nothing yet, or a tag still arriving
                self._answered = True
                self._held, shown = "", lead
                return shown
        return ""

    def flush(self) -> str:
        """Whatever was held back, once the reply has ended."""
        held, self._held = ("" if self._inside else self._held.lstrip()), ""
        return held
