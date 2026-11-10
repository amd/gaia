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
