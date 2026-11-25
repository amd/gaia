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


def test_the_variation_selector_ranges_match():
    assert _table("kVariationSelectors") == tuple(person_approval.VARIATION_SELECTORS)


def test_the_keycap_bases_match():
    text = _SOURCE.read_text(encoding="utf-8")
    found = re.search(r'constexpr char kKeycapBases\[\] = "([^"]*)";', text)
    assert found, f"kKeycapBases not found in {_SOURCE}"
    assert set(found.group(1)) == set(person_approval._KEYCAP_BASES)


def test_a_keycap_and_an_ideograph_selector_follow_the_python_rule():
    text = _SOURCE.read_text(encoding="utf-8")
    body = re.search(r"bool selectorFits\(.*?\n\}\n", text, re.S)
    assert body, f"selectorFits not found in {_SOURCE}"
    # FE0E/FE0F after an emoji, FE0F in a keycap, E0100-E01EF after an ideograph.
    for piece in (
        "cp == 0xFE0E || cp == 0xFE0F",
        "among(kPictographic, previous)",
        "cp == 0xFE0F && keycapBase",
        "cps[at + 1] == 0x20E3",
        "cp >= 0xE0100 && cp <= 0xE01EF) return among(kIdeographs, previous)",
    ):
        assert piece in body.group(0), piece


def test_what_prose_keeps_matches():
    assert _points(_table("kKeptInProse")) == {
        ord(c) for c in person_approval.KEPT_AS_WRITTEN
    }


def test_every_character_a_python_name_codes_is_coded_by_gaiacpp():
    """A path, URL, id, program or "always allow" label (Shown::Name in C++)."""
    cpp = (
        _points(_table("kHidden"))
        | _points(_table("kKeptInProse"))
        | _points(_table("kVariationSelectors"))
    )
    python = {
        p
        for p in range(0x110000)
        if p not in _SURROGATES and person_approval.strict_hidden(chr(p))
    }
    assert sorted(python - cpp) == []
    extra = [p for p in cpp - python if unicodedata.category(chr(p)) != "Cn"]
    assert extra == []
