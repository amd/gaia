# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Calls only a person may approve, and the text that person reads.

Pre-approval (Full access, ``GAIA_AUTO_APPROVE_TOOLS``, ``gaia -p
--dangerously-skip-permissions``) answers tool prompts on the user's behalf. A
call that reaches other people, such as sending mail or inviting someone to an
event, still needs a person: where one can answer, the tool marks the call with
``NEEDS_PERSON_ARG`` so they are asked; where nobody can, it refuses.

What the person approves must be what goes out. Text is shown with characters
that hide or reorder it written as ``[U+XXXX]``, and an address is checked and
shown in the form it is sent.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterator, Tuple

import idna

# Shown as a code: every format, control and line/paragraph separator
# character, every Default_Ignorable_Code_Point (Unicode's list of characters
# that render as nothing), and a few letters that render blank. Tab and newline
# are layout, and the joiners and marks RTL and Indic writing need (ZWJ, ZWNJ,
# LRM, RLM) can't reorder text, so those stay as written. A variation selector
# stays only where Unicode defines it: FE0E/FE0F right after an emoji or FE0F
# in a keycap (1, FE0F, U+20E3), and E0100-E01EF right after a CJK ideograph.
# Anywhere else it shows nothing, so it is a code.
# CalendarApproval.tsx mirrors these lists; a test keeps them equal.
HIDDEN_CATEGORIES = frozenset({"Cf", "Cc", "Zl", "Zp"})
KEPT_AS_WRITTEN = frozenset(
    {"\u0009", "\u000a", "\u200c", "\u200d", "\u200e", "\u200f"}
)
BLANK_LOOKING = frozenset({"\u034f", "\u115f", "\u1160", "\u2800", "\u3164", "\uffa0"})
DEFAULT_IGNORABLE = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)
VARIATION_SELECTORS = (
    (0xFE00, 0xFE0F),
    (0xE0100, 0xE01EF),
)
# Unicode's Extended_Pictographic (emoji) code points, which FE0E/FE0F may follow.
EXTENDED_PICTOGRAPHIC = (
    (0x00A9, 0x00A9),
    (0x00AE, 0x00AE),
    (0x203C, 0x203C),
    (0x2049, 0x2049),
    (0x2122, 0x2122),
    (0x2139, 0x2139),
    (0x2194, 0x2199),
    (0x21A9, 0x21AA),
    (0x231A, 0x231B),
    (0x2328, 0x2328),
    (0x23CF, 0x23CF),
    (0x23E9, 0x23F3),
    (0x23F8, 0x23FA),
    (0x24C2, 0x24C2),
    (0x25AA, 0x25AB),
    (0x25B6, 0x25B6),
    (0x25C0, 0x25C0),
    (0x25FB, 0x25FE),
    (0x2600, 0x2604),
    (0x260E, 0x260E),
    (0x2611, 0x2611),
    (0x2614, 0x2615),
    (0x2618, 0x2618),
    (0x261D, 0x261D),
    (0x2620, 0x2620),
    (0x2622, 0x2623),
    (0x2626, 0x2626),
    (0x262A, 0x262A),
    (0x262E, 0x262F),
    (0x2638, 0x263A),
    (0x2640, 0x2640),
    (0x2642, 0x2642),
    (0x2648, 0x2653),
    (0x265F, 0x2660),
    (0x2663, 0x2663),
    (0x2665, 0x2666),
    (0x2668, 0x2668),
    (0x267B, 0x267B),
    (0x267E, 0x267F),
    (0x2692, 0x2697),
    (0x2699, 0x2699),
    (0x269B, 0x269C),
    (0x26A0, 0x26A1),
    (0x26A7, 0x26A7),
    (0x26AA, 0x26AB),
    (0x26B0, 0x26B1),
    (0x26BD, 0x26BE),
    (0x26C4, 0x26C5),
    (0x26C8, 0x26C8),
    (0x26CE, 0x26CF),
    (0x26D1, 0x26D1),
    (0x26D3, 0x26D4),
    (0x26E9, 0x26EA),
    (0x26F0, 0x26F5),
    (0x26F7, 0x26FA),
    (0x26FD, 0x26FD),
    (0x2702, 0x2702),
    (0x2705, 0x2705),
    (0x2708, 0x270D),
    (0x270F, 0x270F),
    (0x2712, 0x2712),
    (0x2714, 0x2714),
    (0x2716, 0x2716),
    (0x271D, 0x271D),
    (0x2721, 0x2721),
    (0x2728, 0x2728),
    (0x2733, 0x2734),
    (0x2744, 0x2744),
    (0x2747, 0x2747),
    (0x274C, 0x274C),
    (0x274E, 0x274E),
    (0x2753, 0x2755),
    (0x2757, 0x2757),
    (0x2763, 0x2764),
    (0x2795, 0x2797),
    (0x27A1, 0x27A1),
    (0x27B0, 0x27B0),
    (0x27BF, 0x27BF),
    (0x2934, 0x2935),
    (0x2B05, 0x2B07),
    (0x2B1B, 0x2B1C),
    (0x2B50, 0x2B50),
    (0x2B55, 0x2B55),
    (0x3030, 0x3030),
    (0x303D, 0x303D),
    (0x3297, 0x3297),
    (0x3299, 0x3299),
    (0x1F004, 0x1F004),
    (0x1F02C, 0x1F02F),
    (0x1F094, 0x1F09F),
    (0x1F0AF, 0x1F0B0),
    (0x1F0C0, 0x1F0C0),
    (0x1F0CF, 0x1F0D0),
    (0x1F0F6, 0x1F0FF),
    (0x1F170, 0x1F171),
    (0x1F17E, 0x1F17F),
    (0x1F18E, 0x1F18E),
    (0x1F191, 0x1F19A),
    (0x1F1AE, 0x1F1E5),
    (0x1F201, 0x1F20F),
    (0x1F21A, 0x1F21A),
    (0x1F22F, 0x1F22F),
    (0x1F232, 0x1F23A),
    (0x1F23C, 0x1F23F),
    (0x1F249, 0x1F25F),
    (0x1F266, 0x1F321),
    (0x1F324, 0x1F393),
    (0x1F396, 0x1F397),
    (0x1F399, 0x1F39B),
    (0x1F39E, 0x1F3F0),
    (0x1F3F3, 0x1F3F5),
    (0x1F3F7, 0x1F3FA),
    (0x1F400, 0x1F4FD),
    (0x1F4FF, 0x1F53D),
    (0x1F549, 0x1F54E),
    (0x1F550, 0x1F567),
    (0x1F56F, 0x1F570),
    (0x1F573, 0x1F57A),
    (0x1F587, 0x1F587),
    (0x1F58A, 0x1F58D),
    (0x1F590, 0x1F590),
    (0x1F595, 0x1F596),
    (0x1F5A4, 0x1F5A5),
    (0x1F5A8, 0x1F5A8),
    (0x1F5B1, 0x1F5B2),
    (0x1F5BC, 0x1F5BC),
    (0x1F5C2, 0x1F5C4),
    (0x1F5D1, 0x1F5D3),
    (0x1F5DC, 0x1F5DE),
    (0x1F5E1, 0x1F5E1),
    (0x1F5E3, 0x1F5E3),
    (0x1F5E8, 0x1F5E8),
    (0x1F5EF, 0x1F5EF),
    (0x1F5F3, 0x1F5F3),
    (0x1F5FA, 0x1F64F),
    (0x1F680, 0x1F6C5),
    (0x1F6CB, 0x1F6D2),
    (0x1F6D5, 0x1F6E5),
    (0x1F6E9, 0x1F6E9),
    (0x1F6EB, 0x1F6F0),
    (0x1F6F3, 0x1F6FF),
    (0x1F7DA, 0x1F7FF),
    (0x1F80C, 0x1F80F),
    (0x1F848, 0x1F84F),
    (0x1F85A, 0x1F85F),
    (0x1F888, 0x1F88F),
    (0x1F8AE, 0x1F8AF),
    (0x1F8BC, 0x1F8BF),
    (0x1F8C2, 0x1F8CF),
    (0x1F8D9, 0x1F8FF),
    (0x1F90C, 0x1F93A),
    (0x1F93C, 0x1F945),
    (0x1F947, 0x1F9FF),
    (0x1FA58, 0x1FA5F),
    (0x1FA6E, 0x1FAFF),
    (0x1FC00, 0x1FFFD),
)
# The CJK unified and compatibility ideograph blocks, which E0100-E01EF may follow.
CJK_IDEOGRAPHS = (
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x20000, 0x2A6DF),
    (0x2A700, 0x2B73F),
    (0x2B740, 0x2B81F),
    (0x2B820, 0x2CEAF),
    (0x2CEB0, 0x2EBEF),
    (0x2EBF0, 0x2EE5F),
    (0x2F800, 0x2FA1F),
    (0x30000, 0x3134F),
    (0x31350, 0x323AF),
)
_KEYCAP_BASES = frozenset("0123456789#*")

HIDDEN_NOTE = "Hidden characters are shown as [U+XXXX]."

_ADDRESS_RE = re.compile(r"[^@\s<>\",;]+@[^@\s<>\",;]+")


def preapproved(console: Any) -> bool:
    """True when *console* would approve a gated call without asking anyone."""
    enabled = getattr(console, "auto_approve_confirmations_enabled", None)
    return callable(enabled) and bool(enabled())


def a_person_can_answer(console: Any) -> bool:
    """True when a call marked ``NEEDS_PERSON_ARG`` reaches a person on *console*."""
    return (
        getattr(console, "asks_past_preapproval", False) is True
        and getattr(console, "answers_confirmations", False) is True
        and not getattr(console, "background_mode", False)
    )


def _among(ranges: Tuple[Tuple[int, int], ...], char: str) -> bool:
    point = ord(char)
    return any(low <= point <= high for low, high in ranges)


def is_variation_selector(char: str) -> bool:
    return _among(VARIATION_SELECTORS, char)


def is_hidden(char: str) -> bool:
    """True when *char* hides or reorders text wherever it stands.

    A variation selector depends on what it follows: see :func:`visible`.
    """
    if char in KEPT_AS_WRITTEN or is_variation_selector(char):
        return False
    return (
        char in BLANK_LOOKING
        or unicodedata.category(char) in HIDDEN_CATEGORIES
        or _among(DEFAULT_IGNORABLE, char)
    )


def _unfolded(text: str) -> str:
    return text.replace("\r\n", "\n")


def _selector_fits(text: str, at: int) -> bool:
    """True when the variation selector at ``text[at]`` does what Unicode defines."""
    char = text[at]
    previous = text[at - 1] if at else ""
    if not previous:
        return False
    if char in ("\ufe0e", "\ufe0f"):
        if _among(EXTENDED_PICTOGRAPHIC, previous):
            return True
        following = text[at + 1] if at + 1 < len(text) else ""
        return char == "\ufe0f" and previous in _KEYCAP_BASES and following == "\u20e3"
    if 0xE0100 <= ord(char) <= 0xE01EF:
        return _among(CJK_IDEOGRAPHS, previous)
    return False


def _marked(text: str) -> Iterator[Tuple[str, bool]]:
    """Each character of *text*, and whether a prompt shows it as a code."""
    text = _unfolded(text)
    for at, char in enumerate(text):
        if is_variation_selector(char):
            yield char, not _selector_fits(text, at)
        else:
            yield char, is_hidden(char)


def has_hidden(text: str) -> bool:
    return any(hidden for _, hidden in _marked(text))


def visible(text: str) -> str:
    """*text* with every hiding or reordering character written as ``[U+XXXX]``."""
    return "".join(
        f"[U+{ord(char):04X}]" if hidden else char for char, hidden in _marked(text)
    )


def visible_line(text: str) -> str:
    """:func:`visible` for a one-line field, so a line break can't push text away."""
    return visible(text).replace("\n", "[U+000A]")


def sendable_address(address: str, field: str) -> str:
    """*address* as it goes on the wire, or raise ``ValueError`` naming *field*.

    An international domain comes back in its ASCII form, so a look-alike
    letter shows as what it is.
    """
    for char in address:
        # Stricter than display: no joiner or mark belongs in an address.
        if (
            is_hidden(char)
            or is_variation_selector(char)
            or unicodedata.category(char) in HIDDEN_CATEGORIES
        ):
            raise ValueError(
                f"`{field}` has an address with an invisible or control character "
                f"(U+{ord(char):04X}), which could disguise who receives it: "
                f"{visible_line(address)!r}. Give the plain address."
            )
    if not _ADDRESS_RE.fullmatch(address):
        raise ValueError(
            f"`{field}` has an address that can't be used: {address!r}. Give a "
            "plain address like name@example.com."
        )
    local, _, domain = address.rpartition("@")
    if not local.isascii():
        raise ValueError(
            f"`{field}` has non-ASCII characters before the @ in {address!r}, which "
            "GAIA can't send to. Check the address with its owner."
        )
    if domain.isascii():
        return address
    try:
        ascii_domain = idna.encode(domain, uts46=True).decode("ascii")
    except idna.IDNAError as exc:
        raise ValueError(
            f"`{field}` has a domain that can't be used: {domain!r} ({exc}). Check "
            "the spelling."
        ) from exc
    return f"{local}@{ascii_domain}"


__all__ = [
    "BLANK_LOOKING",
    "CJK_IDEOGRAPHS",
    "DEFAULT_IGNORABLE",
    "EXTENDED_PICTOGRAPHIC",
    "HIDDEN_CATEGORIES",
    "HIDDEN_NOTE",
    "KEPT_AS_WRITTEN",
    "VARIATION_SELECTORS",
    "a_person_can_answer",
    "has_hidden",
    "is_hidden",
    "is_variation_selector",
    "preapproved",
    "sendable_address",
    "visible",
    "visible_line",
]
