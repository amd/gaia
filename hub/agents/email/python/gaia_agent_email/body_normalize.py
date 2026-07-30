# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Strip known mail-infrastructure banners from an inbound body before it
reaches the model (#2642).

Conservative by design: recognizes a small, enumerable set of banner
openers, only at the true top of a body, with a capped removal. A body that
merely discusses one of these phrases (rather than opening with it) is left
untouched — see the hard-negative tests in
``tests/test_body_normalize_2642.py``.

Called from ``tools.read_tools._thread_message_blocks`` and
``_format_message_for_llm``, on the raw decoded body, before
``wrap_untrusted_body``. Not wired into ``tools.summarize_tools``,
``tools.llm_triage``, or ``tools.calendar_tools`` — each builds its own LLM
prompt directly from a decoded body and is unaffected by this module.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional, Pattern, Tuple

# Untrusted-body delimiter shape; single quantified class, no nested
# quantifiers, so an unclosed match stays linear-time (see
# TestNoPathologicalRuntime) instead of a hang.
_DELIMITER_TOKEN_RE = re.compile(r"<<<[A-Z0-9_]+>>>")

# Banner detection only ever scans this leading prefix, so cost is
# independent of body size.
_SCAN_WINDOW_CHARS = 2048

# Hard cap on any single removed span, regardless of what a matched trigger
# would otherwise consume.
_MAX_BANNER_REMOVAL_CHARS = 300
_MAX_BANNER_REMOVAL_LINES = 5

# Cf/Cc characters (e.g. ZWSP, BOM) are not ``str.isspace()`` — stripped
# before anchoring so one can't hide a banner from every ``^`` pattern.
_INVISIBLE_CATEGORIES = ("Cf", "Cc")

# Anchored at position 0 of the scan window — a body merely discussing one
# of these phrases (not opening with it) never matches.
_BANNER_TRIGGERS: Tuple[Pattern[str], ...] = (
    # A classification marking alone on its own first line.
    re.compile(r"^AMD General[ \t]*(?:\r?\n|$)"),
    # The external-sender caution opener; the banner continues past this.
    re.compile(r"^Caution: This message originated from an External Source\.?"),
)


def _leading_invisible_len(text: str) -> int:
    """Count leading characters in the Cf/Cc Unicode categories."""
    count = 0
    for ch in text:
        if unicodedata.category(ch) in _INVISIBLE_CATEGORIES:
            count += 1
        else:
            break
    return count


def _match_banner_trigger(window: str):
    """Return the ``Match`` for the first recognized trigger at the start
    of ``window``, or ``None``."""
    for pattern in _BANNER_TRIGGERS:
        match = pattern.match(window)
        if match:
            return match
    return None


def _nth_newline_end(text: str, n: int) -> Optional[int]:
    """Index right after the nth (1-based) ``\\n`` in ``text``, or ``None``
    if fewer than ``n`` newlines are present."""
    seen = 0
    for idx, ch in enumerate(text):
        if ch == "\n":
            seen += 1
            if seen == n:
                return idx + 1
    return None


# End of the banner's own paragraph: a newline, optional same-line
# whitespace, then another newline.
_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n")


def _paragraph_break_end(text: str) -> Optional[int]:
    """Index right after the first blank-line boundary in ``text``, or
    ``None`` if there isn't one."""
    match = _BLANK_LINE_RE.search(text)
    return match.end() if match else None


def _capped_removal_end(window: str) -> int:
    """How many leading characters of ``window`` to remove, given it opens
    with a recognized banner trigger.

    Prefers the natural end of the banner's own paragraph when that fits
    under both hard caps; otherwise cuts at whichever cap binds first.
    """
    char_cap = min(len(window), _MAX_BANNER_REMOVAL_CHARS)
    bounded = window[:char_cap]
    line_cap_end = _nth_newline_end(bounded, _MAX_BANNER_REMOVAL_LINES)
    natural_end = _paragraph_break_end(bounded)
    if natural_end is not None and (
        line_cap_end is None or natural_end <= line_cap_end
    ):
        return natural_end
    if line_cap_end is not None:
        return line_cap_end
    return char_cap


def normalize_email_body(body: str) -> str:
    """Strip forged delimiter tokens and known infrastructure banners from
    a raw, decoded email body — before ``wrap_untrusted_body``.

    Two independent passes: (1) an unconditional, full-body delimiter-token
    scrub, then (2) leading-banner detection over a bounded prefix window
    with a capped removal. A body that does not open with a recognized
    banner is returned exactly as given, aside from pass 1.
    """
    if not body:
        return body

    # Unconditional — not gated on whether a banner is also found below.
    body = _DELIMITER_TOKEN_RE.sub("", body)

    leading_len = _leading_invisible_len(body)
    rest = body[leading_len:]
    window = rest[:_SCAN_WINDOW_CHARS]
    normalized_window = unicodedata.normalize("NFKC", window)

    if len(normalized_window) == len(window):
        match = _match_banner_trigger(normalized_window)
    else:
        # Length-changing NFKC decomposition — offsets would no longer line
        # up with `window`, so match the raw text instead of risking a
        # misaligned slice.
        match = _match_banner_trigger(window)

    if match is None:
        return body

    # Never less than the trigger's own match — no partial banner left behind.
    removal_end = max(match.end(), _capped_removal_end(window))
    removal_end = min(removal_end, len(window))
    return rest[removal_end:]
