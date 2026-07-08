# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Local voice/style profile derived from the user's Sent mail (#1607).

Pure functions only — no I/O, no LLM. ``build_voice_profile`` (see
``tools/voice_tools.py``) fetches Sent bodies through the mail backend,
runs :func:`analyze_sent_bodies`, and persists the resulting profile via
``action_store``. The profile holds DERIVED features (greeting/sign-off
phrases, length, formality signals) — never raw Sent content — so the
SQLite row can't leak private mail even if exfiltrated.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from typing import Any, Dict, List

# Lines like "On Mon, Jun 2, 2026 at 9:00 AM Maria <m@x.com> wrote:" start
# the quoted original in Gmail-style replies; everything after is not the
# user's own writing.
_ATTRIBUTION_RE = re.compile(r"^On\b.*wrote:\s*$", re.IGNORECASE)
_ORIGINAL_MESSAGE_RE = re.compile(
    r"^-{2,}\s*(Original|Forwarded) Message\s*-{2,}", re.IGNORECASE
)

_GREETING_RE = re.compile(
    r"^(hey|hi|hello|dear|greetings|good (?:morning|afternoon|evening))\b",
    re.IGNORECASE,
)

_SIGNOFF_RE = re.compile(
    r"^(cheers|thanks|thank you|many thanks|best|best regards|regards|"
    r"kind regards|warm regards|sincerely|talk soon|take care)[\s,.!]*$",
    re.IGNORECASE,
)

# ['’] — mail clients (Gmail web composer included) emit typographic
# apostrophes; matching only ASCII would misread casual writers as formal.
_CONTRACTION_RE = re.compile(r"\b\w+['’](?:ll|re|ve|d|s|t|m)\b", re.IGNORECASE)

# How many top greeting / sign-off variants the profile keeps.
_TOP_N = 3


def strip_quoted_text(body: str) -> str:
    """Return only the user's own writing from a reply/forward body.

    Cuts at the first attribution line ("On ... wrote:", "--- Original
    Message ---") and drops ``>``-quoted lines.
    """
    kept: List[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if _ATTRIBUTION_RE.match(stripped) or _ORIGINAL_MESSAGE_RE.match(stripped):
            break
        if stripped.startswith(">"):
            continue
        kept.append(line)
    return "\n".join(kept)


def _first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


# 8 lines: sign-offs sit above the name + a signature block (title, phone,
# address, URL) that routinely runs 4-6 lines.
_SIGNOFF_SCAN_LINES = 8


def _trailing_lines(text: str, count: int = _SIGNOFF_SCAN_LINES) -> List[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-count:]


def analyze_sent_bodies(bodies: List[str]) -> Dict[str, Any]:
    """Derive a style profile from Sent message bodies.

    Raises ``ValueError`` when no body contains usable (non-quoted,
    non-empty) text — the caller surfaces that as an actionable error
    instead of persisting an empty profile.
    """
    usable = [
        cleaned
        for cleaned in (strip_quoted_text(b or "").strip() for b in bodies)
        if cleaned
    ]
    if not usable:
        raise ValueError(
            "no usable Sent message bodies to analyze — the sampled Sent "
            "messages were empty or entirely quoted text"
        )

    greeting_counts: Counter = Counter()
    signoff_counts: Counter = Counter()
    word_counts: List[int] = []
    messages_with_contractions = 0
    exclamations = 0

    for text in usable:
        first = _first_nonempty_line(text)
        greeting_match = _GREETING_RE.match(first)
        if greeting_match:
            greeting_counts[greeting_match.group(1).capitalize()] += 1
        # Bottom-up so the sign-off is found even under a long signature
        # block; only one sign-off is credited per message.
        for line in reversed(_trailing_lines(text)):
            signoff_match = _SIGNOFF_RE.match(line)
            if signoff_match:
                signoff_counts[signoff_match.group(1).capitalize()] += 1
                break
        word_counts.append(len(text.split()))
        if _CONTRACTION_RE.search(text):
            messages_with_contractions += 1
        exclamations += text.count("!")

    sample_count = len(usable)
    return {
        "greetings": [g for g, _ in greeting_counts.most_common(_TOP_N)],
        "signoffs": [s for s, _ in signoff_counts.most_common(_TOP_N)],
        "median_words": int(statistics.median(word_counts)),
        "uses_contractions": messages_with_contractions / sample_count >= 0.3,
        "exclamation_rate": round(exclamations / sample_count, 2),
        "sample_count": sample_count,
    }


def render_style_guidance(profile: Dict[str, Any]) -> str:
    """Render the profile as a system-prompt block for draft composition."""
    greetings = profile.get("greetings") or []
    signoffs = profile.get("signoffs") or []
    median_words = profile.get("median_words", 0)
    tone_bits: List[str] = []
    if profile.get("uses_contractions"):
        tone_bits.append("use contractions naturally (I'll, let's, can't)")
    else:
        tone_bits.append("avoid contractions; keep phrasing formal")
    if profile.get("exclamation_rate", 0) >= 0.3:
        tone_bits.append("an occasional exclamation mark fits their voice")
    else:
        tone_bits.append("avoid exclamation marks")

    greeting_line = (
        f'- Open with their usual greeting style: {", ".join(repr(g) for g in greetings)}\n'
        if greetings
        else ""
    )
    signoff_line = (
        f'- Sign off the way they do: {", ".join(repr(s) for s in signoffs)}\n'
        if signoffs
        else ""
    )
    return (
        "VOICE & STYLE (derived locally from the user's Sent mail, "
        f"sample of {profile.get('sample_count', 0)}):\n"
        "When composing any draft body (draft_reply, draft_forward), write "
        "in the user's own voice:\n"
        f"{greeting_line}"
        f"{signoff_line}"
        f"- Aim for roughly {median_words} words — match their typical length.\n"
        f"- Tone: {'; '.join(tone_bits)}.\n"
        "Drafts are still returned for user approval — never send without "
        "confirmation."
    )
