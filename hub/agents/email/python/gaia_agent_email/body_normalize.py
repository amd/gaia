# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Strip known mail-infrastructure banners from an inbound body before it
reaches the model (#2642), plus the shared quoted-reply-chain and
signature-block reduction (#2643 lever 4).

Conservative by design: recognizes a small, enumerable set of banner
openers, only at the true top of a body, with a capped removal. A body that
merely discusses one of these phrases (rather than opening with it) is left
untouched — see the hard-negative tests in
``tests/test_body_normalize_2642.py``.

``normalize_email_body`` (banner + delimiter scrub) is called from
``tools.read_tools._thread_message_blocks`` and ``_format_message_for_llm``,
on the raw decoded body, before ``wrap_untrusted_body``. Banner stripping is
NOT yet wired into ``tools.summarize_tools``, ``tools.llm_triage``, or
``tools.calendar_tools`` — each builds its own LLM prompt directly from a
decoded body, and extending it there changes triage/classification output
for every message, which needs a ``gaia eval agent`` run first (tracked in
#2647). The delimiter scrub alone (``scrub_delimiter_tokens``) IS safe
everywhere — a ``<<<TOKEN>>>``-shaped string is never legitimate content —
so those three paths call it directly before their own
``wrap_untrusted_body``.

``strip_reply_chain_and_signature`` (#2643) is the SHARED seam this module
and ``voice_profile.py`` were converging toward: rather than a second
implementation of quote/signature detection, it reuses
``voice_profile.strip_quoted_text`` (quote-chain removal) and
``voice_profile._SIGNOFF_RE`` / ``_SIGNOFF_SCAN_LINES`` (the existing
signoff-phrase detector, originally built for voice-profile STYLE analysis
in ``analyze_sent_bodies``) to ALSO cut the trailing signature block. Wired
ONLY into ``tools.read_tools.triage_inbox_impl``'s classifier-escalation
body — the LLM classification path, where quoted history and a signature
block are boilerplate that cost tokens without changing the category
decision. Deliberately NOT wired into the read-tool display paths
(``get_message`` / ``get_thread`` / ``summarize_thread`` /
``_format_message_for_llm`` / ``_thread_message_blocks``) — a user asking to
read a message wants to see the whole thing, quoted chain included; only the
LLM's OWN classification input is reduced. This is an eval-affecting
surface (unlike the rest of #2643): it changes body text the model reads.
"""

from __future__ import annotations

import re
import unicodedata
from typing import List, Optional, Pattern, Tuple

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


def scrub_delimiter_tokens(body: str) -> str:
    """Strip any ``<<<...>>>``-shaped delimiter token from ``body``.

    Safe to call on ANY inbound body, unconditionally — a real message never
    legitimately contains this exact shape, so removing it cannot change a
    classification or summary outcome. Public so every body-to-prompt path
    can close the input-side forgery hole (see module docstring) without
    waiting on the banner-stripping side of ``normalize_email_body``, which
    is eval-affecting on some of those paths.
    """
    if not body:
        return body
    return _DELIMITER_TOKEN_RE.sub("", body)


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
# whitespace, then another newline. CRLF-tolerant (RFC 5322 wire format) —
# each `\n` may be preceded by a `\r` that a plain `[ \t]*` would not match.
_BLANK_LINE_RE = re.compile(r"\r?\n[ \t]*\r?\n")


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
    body = scrub_delimiter_tokens(body)

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


def strip_reply_chain_and_signature(body: str) -> str:
    """Cut ``body`` down to the sender's own new content (#2643 lever 4).

    Two passes, both reusing ``voice_profile``'s existing machinery rather
    than a second implementation:

    1. ``voice_profile.strip_quoted_text`` — drops everything from the first
       attribution line ("On ... wrote:", "--- Original Message ---") or
       ``>``-quoted line onward.
    2. A bottom-up scan over the trailing ``_SIGNOFF_SCAN_LINES`` lines (the
       same window and the same ``_SIGNOFF_RE`` pattern
       ``analyze_sent_bodies`` uses to find a voice profile's signoff) for
       the LAST line that is JUST a signoff phrase ("Best,", "Cheers") —
       everything from that line onward (the signoff plus the signature
       block beneath it: name, title, phone, disclaimer) is cut.

    Intended for body text about to reach an LLM for classification, where
    the quoted history and signature are boilerplate that cost tokens
    without changing the category decision — NOT for a read-tool display
    path, where a user reading a message wants to see the whole thing.
    """
    if not body:
        return body

    # Deferred import: avoids a module-load-order dependency between
    # body_normalize.py and voice_profile.py (neither currently imports the
    # other at module scope) purely for this one reuse.
    from gaia_agent_email.voice_profile import (
        _SIGNOFF_RE,
        _SIGNOFF_SCAN_LINES,
        strip_quoted_text,
    )

    without_quotes = strip_quoted_text(body)
    lines = without_quotes.splitlines()

    window_start = max(0, len(lines) - _SIGNOFF_SCAN_LINES)
    cut_at: Optional[int] = None
    for idx in range(len(lines) - 1, window_start - 1, -1):
        if _SIGNOFF_RE.match(lines[idx].strip()):
            cut_at = idx
            break

    kept: List[str] = lines[:cut_at] if cut_at is not None else lines
    return "\n".join(kept).strip()
