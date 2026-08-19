# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Gmail search-query grammar: ``newer_than:``/``older_than:`` durations (#2830).

Gmail silently returns zero results for a duration value it doesn't
understand — no error, indistinguishable from an empty mailbox. A 4B-class
model asked for "the last two weeks" reaches for ``newer_than:2w`` some of
the time; Gmail has no ``w`` unit, so that turn confidently reports "no
messages" for mail that is actually there. This module is the validator:
parse the value, normalize what Gmail silently rejects but a human clearly
meant, and raise an actionable ``ValueError`` for anything else — mirroring
``tools.read_tools._parse_gmail_date_value``'s contract for the sibling
``after:``/``before:``/``older:``/``newer:`` operators.

The accept-list below is measured directly against live Gmail, not read
from a doc — Gmail's own documentation omits ``h`` (hours) entirely, yet
``newer_than:12h`` is a working query.

A peer module rather than living in ``tools/read_tools.py`` (already very
large) — imported from there, which is the sole call site that wires it into
``normalize_gmail_date_operators``.
"""

from __future__ import annotations

import re

# Value grammar mirrors read_tools._DATE_OP_RE's quoted-string/bare-token
# fallback so a malformed value still reaches the validator instead of being
# silently passed through (`newer_than:"2w"`, `newer_than:-3d`) -- but it stops
# at Gmail's grouping punctuation: `(newer_than:7d)` must capture `7d`, never
# `7d)`. Stripping the bracket after the fact would drop it from the rewritten
# query and unbalance the expression, so it must never enter the match.
#
# Op group is exactly newer_than|older_than, not newer(?:_than)? -- the
# broader form would also match bare newer:/older: and misparse their
# already-correct date as a duration.
DURATION_OP_RE = re.compile(
    r"""
    \b(?P<op>newer_than|older_than):
    (?P<val>
        "[^"]*"
      | [^\s)}\]]+
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# `\d+` (no sign, no dot) rejects "1.5w"/"-3d" by simply not matching --
# no separate range/type check needed after the fact.
_DURATION_VALUE_RE = re.compile(r"^(\d+)([A-Za-z]+)$")

# Passed through byte-identical, case preserved -- re-casing an already-valid
# value (e.g. "14D") is a no-op that only adds a way to be wrong.
_DURATION_PASSTHROUGH_UNITS = {"h", "d", "m", "y"}


def parse_gmail_duration_value(raw: str, *, op: str) -> str:
    """Parse one ``newer_than:``/``older_than:`` value into what Gmail accepts.

    Accepts an integer count plus a case-insensitive ``h``/``d``/``m``/``y``
    unit, returned byte-identical to how it was written. ``w`` (weeks) is
    not a Gmail unit -- Gmail silently returns zero results for it rather
    than an error -- so it is converted to the equivalent day count
    (lossless: a week is exactly seven days). Anything else raises
    ``ValueError`` naming the accepted units and an example, per
    ``_parse_gmail_date_value``'s precedent.
    """
    value = raw.strip().strip('"').strip()
    m = _DURATION_VALUE_RE.fullmatch(value)
    unit = m.group(2).lower() if m else ""
    if m and unit in _DURATION_PASSTHROUGH_UNITS:
        return value
    if m and unit == "w":
        return f"{int(m.group(1)) * 7}d"
    raise ValueError(
        f"search_messages: cannot parse duration value {raw!r} for the "
        f"'{op}:' operator. Use an integer plus h/d/m/y — hours/days/"
        f"months/years (e.g. {op}:14d for 14 days). 'w' (weeks) is not a "
        f"Gmail unit; use the equivalent day count instead."
    )
