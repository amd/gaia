# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The fence around a short field written by someone else, such as an event title.

A tool result marks such text so the model treats it as data. The text inside
can never spell the fence itself, however it nests, cases or widens it.
"""

from __future__ import annotations

import unicodedata
from typing import List

from gaia.agents.base.person_approval import is_hidden

# The same fence for a short field in a JSON result, such as an event title.
UNTRUSTED_FIELD_OPEN = "<<<UNTRUSTED_TEXT>>>"
UNTRUSTED_FIELD_CLOSE = "<<<END_UNTRUSTED_TEXT>>>"


def _fence_side(char: str) -> str:
    """``<`` or ``>`` for a character that reads as one, in any width; else ""."""
    if char in "<>":
        return char
    folded = unicodedata.normalize("NFKC", char)
    return folded if folded in ("<", ">") else ""


def _without_fence_runs(text: str) -> str:
    """Each run of three or more ``<`` (or ``>``) look-alikes cut to two ASCII ones.

    A hidden character inside a run doesn't break it. No fence can be spelled from
    what is left, whatever the case, width, nesting or invisible padding.
    """
    out: List[str] = []
    start = 0
    while start < len(text):
        side = _fence_side(text[start])
        if not side:
            out.append(text[start])
            start += 1
            continue
        count, last, at = 0, start, start
        while at < len(text):
            if _fence_side(text[at]) == side:
                count, last = count + 1, at
            elif not is_hidden(text[at]):
                break
            at += 1
        out.append(side * 2 if count >= 3 else text[start : last + 1])
        start = last + 1
    return "".join(out)


def strip_untrusted_field_markers(text: str) -> str:
    """*text* without the field fence, and unable to spell one.

    Markers are removed until none is left, so one hidden inside another can't
    reassemble; then any other run that could read as a fence is cut short.
    """
    previous = None
    while previous != text:
        previous = text
        text = text.replace(UNTRUSTED_FIELD_OPEN, "").replace(UNTRUSTED_FIELD_CLOSE, "")
    return _without_fence_runs(text)


def wrap_untrusted_field(text: str) -> str:
    """Fence a short field, removing any marker in it so it can't close the fence."""
    return f"{UNTRUSTED_FIELD_OPEN}{strip_untrusted_field_markers(text)}{UNTRUSTED_FIELD_CLOSE}"


__all__ = [
    "UNTRUSTED_FIELD_CLOSE",
    "UNTRUSTED_FIELD_OPEN",
    "strip_untrusted_field_markers",
    "wrap_untrusted_field",
]
