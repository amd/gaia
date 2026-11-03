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
from typing import Any

import idna

# Controls and invisible characters that can hide or reorder text on the
# approver's screen. Tab and newline are layout; the marks RTL and Indic writing
# need (LRM, RLM, ZWJ, ZWNJ) can't reorder text, so they stay as written.
_HIDDEN_RE = re.compile(
    "[\x00-\x08\x0b-\x1f\x7f-\x9f\u00ad\u180e\u200b"
    "\u2028-\u202e\u2060-\u2064\u2066-\u2069\ufeff]"
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


def has_hidden(text: str) -> bool:
    return bool(_HIDDEN_RE.search(text.replace("\r\n", "\n")))


def visible(text: str) -> str:
    """*text* with every hiding or reordering character written as ``[U+XXXX]``."""
    return _HIDDEN_RE.sub(
        lambda match: f"[U+{ord(match.group()):04X}]", text.replace("\r\n", "\n")
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
        if unicodedata.category(char) in ("Cc", "Cf", "Zl", "Zp"):
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
    "HIDDEN_NOTE",
    "a_person_can_answer",
    "has_hidden",
    "preapproved",
    "sendable_address",
    "visible",
    "visible_line",
]
