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
# stays only as the one directly after a visible character, as emoji use it.
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


def _marked(text: str) -> Iterator[Tuple[str, bool]]:
    """Each character of *text*, and whether a prompt shows it as a code."""
    previous = ""
    for char in _unfolded(text):
        if is_variation_selector(char):
            hidden = (
                not previous
                or previous.isspace()
                or previous in KEPT_AS_WRITTEN
                or is_hidden(previous)
                or is_variation_selector(previous)
            )
        else:
            hidden = is_hidden(char)
        yield char, hidden
        previous = char


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
    "DEFAULT_IGNORABLE",
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
