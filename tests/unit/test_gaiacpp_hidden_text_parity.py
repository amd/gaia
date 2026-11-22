# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""gaiacpp's approval cards hide no character the Python agent's cards show as a code.

gaiacpp's ``visible()`` in ``cpp/agents/gaiacpp/stdio_server.cpp`` carries its
own copy of the sets ``person_approval.visible`` uses; these tests keep the two equal.
"""

import re
import unicodedata
from pathlib import Path

import pytest

from gaia.agents.base import person_approval

_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "cpp"
    / "agents"
    / "gaiacpp"
    / "stdio_server.cpp"
)
_SURROGATES = range(0xD800, 0xE000)


def _table(name):
    text = _SOURCE.read_text(encoding="utf-8")
    body = re.search(rf"constexpr CodeRange {name}\[\] = \{{(.*?)\n\}};", text, re.S)
    assert body, f"{name} not found in {_SOURCE}"
    return tuple(
        (int(low, 16), int(high, 16))
        for low, high in re.findall(r"\{0x([0-9A-F]+), 0x([0-9A-F]+)\}", body.group(1))
    )


def _points(ranges):
    return {p for low, high in ranges for p in range(low, high + 1)}


def test_every_character_the_python_cards_code_is_coded_by_gaiacpp():
    cpp = _points(_table("kHidden"))
    python = {
        p
        for p in range(0x110000)
        if p not in _SURROGATES and person_approval.is_hidden(chr(p))
    }
    assert sorted(python - cpp) == []
    # Anything extra must be a character this Python's Unicode data doesn't know yet.
    extra = [p for p in cpp - python if unicodedata.category(chr(p)) != "Cn"]
    assert extra == []


@pytest.mark.parametrize(
    "name, ranges",
    [
        ("kPictographic", person_approval.EXTENDED_PICTOGRAPHIC),
        ("kIdeographs", person_approval.CJK_IDEOGRAPHS),
    ],
)
def test_variation_selector_contexts_match(name, ranges):
    assert _table(name) == tuple(ranges)


def test_variation_selectors_are_left_to_their_context():
    assert not _points(_table("kHidden")) & _points(person_approval.VARIATION_SELECTORS)
