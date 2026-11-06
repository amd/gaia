# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Phone numbers the phone book will accept: E.164, never short, premium or emergency."""

from __future__ import annotations

import re

from gaia.phone.errors import PhoneError

_DIGITS = re.compile(r"\d+")

#: Dialled alone, these reach emergency services in some country.
EMERGENCY = frozenset(
    {"911", "112", "999", "000", "110", "119", "100", "101", "102", "08"}
)

#: Prefixes (after "+") that bill the caller at premium or satellite rates.
PREMIUM_PREFIXES = (
    "1900",  # US/Canada premium
    "1976",  # US pay-per-call
    "449",  # UK premium
    "4487",  # UK revenue-share
    "3389",  # France premium
    "4990",  # Germany premium
    "881",  # global satellite
    "882",  # international networks
    "883",  # international networks
    "870",  # Inmarsat
    "979",  # international premium
)


def normalize(raw: str) -> str:
    """Return *raw* as E.164 (``+15551234567``), or raise :class:`PhoneError`.

    A number without a ``+`` is read as North American when it has 10 digits
    (or 11 starting with 1); any other number must be given with its country
    code, because guessing a country can dial a stranger.
    """
    text = str(raw or "").strip()
    if not text:
        raise PhoneError("bad_number", "Enter a phone number.")
    if re.search(r"[^\d+\-().\s]", text):
        raise PhoneError(
            "bad_number", "A phone number can only have digits, spaces, + - ( and )."
        )
    plus = text.startswith("+")
    digits = "".join(_DIGITS.findall(text))
    if digits in EMERGENCY or (not plus and len(digits) <= 3):
        raise PhoneError("bad_number", "GAIA never calls emergency or service numbers.")
    if not plus:
        if len(digits) == 10:
            digits = "1" + digits
        elif not (len(digits) == 11 and digits.startswith("1")):
            raise PhoneError(
                "bad_number",
                "Add the country code with a +, for example +44 20 7946 0958.",
            )
    if not 8 <= len(digits) <= 15:
        raise PhoneError(
            "bad_number", "That isn't a full phone number with its country code."
        )
    if digits.startswith("0"):
        raise PhoneError("bad_number", "A country code never starts with 0.")
    if digits.startswith("1") and (len(digits) != 11 or digits[1] in "01"):
        raise PhoneError("bad_number", "That isn't a valid US or Canadian number.")
    for prefix in PREMIUM_PREFIXES:
        if digits.startswith(prefix):
            raise PhoneError(
                "bad_number", "GAIA won't call premium-rate or satellite numbers."
            )
    return "+" + digits


def masked(number: str) -> str:
    """``+1 •••• 4567`` for logs and screens that don't need the whole number."""
    return f"{number[:2]} •••• {number[-4:]}" if len(number) > 6 else "••••"
