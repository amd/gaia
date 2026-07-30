# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Strip known mail-infrastructure banners from an inbound body before it
reaches the model (#2642).

Corporate mail gateways stamp boilerplate at the very top of a message body
-- a sensitivity marking, an external-sender caution -- that sits inside the
region a summarizer scans for "who said this." Left in place, the banner can
outcompete the real sender for the role of author: a real thread produced a
summary attributing a decision to "AMD General" (the classification
marking) because the marking sat exactly where the model expected the
author's own words to begin.

This module is deliberately conservative: it recognizes a small, enumerable
set of known banner openers, only at the true top of a body, and never
removes more than a small capped span. A message that legitimately
*discusses* one of these phrases -- rather than opening with it -- is left
untouched (see the hard-negative tests in
``tests/test_body_normalize_2642.py``).

Called from ``tools.read_tools._thread_message_blocks`` and
``_format_message_for_llm`` on the raw decoded body, before
``wrap_untrusted_body``. Not wired into every body-to-prompt path in this
package -- ``tools.summarize_tools``, ``tools.llm_triage`` and
``tools.calendar_tools`` each build their own LLM prompt directly from a
decoded body and are unaffected by this module; see the #2642 plan for what
this increment covers.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional, Pattern, Tuple

# Matches a literal untrusted-body delimiter (or any similarly-shaped
# ``<<<TOKEN>>>`` marker). A single quantified character class with no
# nested quantifiers, so matching cost stays linear even over an unclosed
# 50 KB run (see TestNoPathologicalRuntime) -- never the source of a hang.
# Mirrors thread_fold._DELIMITER_TOKEN_RE, which only scrubs the fold LLM's
# *output*; this one runs on every inbound body, closing the matching
# input-side hole (a literal delimiter token in a raw body was, until now,
# wrapped unscrubbed).
_DELIMITER_TOKEN_RE = re.compile(r"<<<[A-Z0-9_]+>>>")

# Only the leading ~2 KB of a body is ever scanned for a banner opener --
# real banners sit at the true top, and bounding the window means banner
# detection costs the same whether the body is 500 bytes or 500 KB.
_SCAN_WINDOW_CHARS = 2048

# Hard ceiling on any single removed span, regardless of what a matched
# trigger would otherwise consume. Without this, a sender who opens with a
# banner-shaped line and never sends a following blank line would have their
# real content folded into the deleted span -- reproducing the "summary
# omits the real message" failure this change exists to prevent.
_MAX_BANNER_REMOVAL_CHARS = 300
_MAX_BANNER_REMOVAL_LINES = 5

# Categories treated as invisible formatting/control noise at the leading
# edge. A zero-width space (U+200B) or BOM (U+FEFF) sitting in front of a
# banner would otherwise defeat every ``^``-anchored pattern below --
# ``str.strip()`` does not remove them (they are not ``str.isspace()``), so
# a caller's own ``.strip()`` cannot be relied on to have cleared them.
_INVISIBLE_CATEGORIES = ("Cf", "Cc")

# Each pattern identifies the START of a known mail-infrastructure banner,
# anchored at position 0 of the (bounded, normalized) scan window -- a body
# that merely discusses one of these phrases mid-sentence, or quotes it
# later in a reply, never matches. That anchoring is what keeps this
# conservative rather than "delete anything that looks like boilerplate."
# Bounded: no nested quantifiers, so matching cost is linear in the window.
_BANNER_TRIGGERS: Tuple[Pattern[str], ...] = (
    # A bare classification marking occupying its own first line -- the
    # "AMD General" style header some corporate mail gateways stamp above
    # the real body.
    re.compile(r"^AMD General[ \t]*(?:\r?\n|$)"),
    # The external-sender caution sentence gateways prepend. The banner
    # text continues past this clause (same or next line), so the trigger
    # only needs to recognize the opening, not the full boilerplate.
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


# A blank line: a newline, optional same-line horizontal whitespace, then
# another newline. Marks the end of the banner's own paragraph.
_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n")


def _paragraph_break_end(text: str) -> Optional[int]:
    """Index right after the first blank-line boundary in ``text``, or
    ``None`` if there isn't one."""
    match = _BLANK_LINE_RE.search(text)
    return match.end() if match else None


def _capped_removal_end(window: str) -> int:
    """How many leading characters of ``window`` to remove, given it opens
    with a recognized banner trigger.

    Prefers the natural end of the banner's own paragraph (through the next
    blank line) when that fits under BOTH hard caps; otherwise cuts at
    whichever cap binds first. Never removes more than
    ``_MAX_BANNER_REMOVAL_CHARS`` characters, and never a span containing
    more than ``_MAX_BANNER_REMOVAL_LINES`` newlines.
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
    a raw, decoded email body -- before ``wrap_untrusted_body``.

    Two independent passes:

    1. Unconditional, full-body delimiter-token scrub -- never conditional
       on whether a banner is also found (closes the input-side forgery
       hole described in the module docstring).
    2. Leading-banner detection over a bounded ~2 KB prefix window, with a
       capped removal.

    A body that does not open with a recognized banner is returned exactly
    as given, aside from the delimiter scrub in step 1.
    """
    if not body:
        return body

    # 1. Unconditional delimiter scrub -- full body, any length (linear-time
    # regardless; see TestNoPathologicalRuntime).
    body = _DELIMITER_TOKEN_RE.sub("", body)

    # 2. Leading-banner detection, bounded window.
    leading_len = _leading_invisible_len(body)
    rest = body[leading_len:]
    window = rest[:_SCAN_WINDOW_CHARS]
    normalized_window = unicodedata.normalize("NFKC", window)

    if len(normalized_window) == len(window):
        match = _match_banner_trigger(normalized_window)
    else:
        # NFKC changed the window's length (a rare compatibility
        # decomposition) -- offsets in the normalized text would no longer
        # line up with `window`, so fall back to the raw text rather than
        # risk slicing at the wrong point. A homoglyph-spelled banner that
        # also happens to trigger a length-changing decomposition is a
        # known, unexercised gap of this fallback.
        match = _match_banner_trigger(window)

    if match is None:
        return body

    # The removal span always covers at least the recognized trigger itself
    # (never leaves a partial banner fragment behind), extended to the
    # banner's natural paragraph end when that fits under both caps.
    removal_end = max(match.end(), _capped_removal_end(window))
    removal_end = min(removal_end, len(window))
    return rest[removal_end:]
