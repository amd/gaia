# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shared parsing of message timestamps into epoch milliseconds.

Gmail's ``internalDate`` is an epoch-millis string. Microsoft Graph's
``receivedDateTime`` is ISO-8601, and the Outlook backend normalises it to the
Gmail shape at its boundary so every consumer sees one representation.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Union


def parse_timestamp_ms(raw: Union[str, int]) -> int:
    """Return ``raw`` (epoch milliseconds or an ISO-8601 string) as epoch millis.

    Raises ``ValueError`` for anything else — a time computed from a guessed
    timestamp would silently mis-order or mis-age messages.
    """
    text = str(raw)
    try:
        return int(text)
    except ValueError:
        pass
    # Graph emits 7-digit (100 ns) fractions; py3.10 fromisoformat caps at 6.
    iso = re.sub(r"(\.\d{6})\d+", r"\1", text.replace("Z", "+00:00"))
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError as exc:
        raise ValueError(f"{raw!r} is neither epoch milliseconds nor ISO-8601") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)
