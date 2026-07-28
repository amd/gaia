# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Dependency-free text-signal predicates (#2581).

This module imports nothing from ``gaia_agent_email`` — only the standard
library. That is a hard invariant (locked in by
``tests/unit/email/test_text_signals_leaf.py``), not a style preference:
``triage_heuristics.py`` is a deliberate import leaf with zero internal
imports, and anything that wants to be usable from a leaf module (now or
later) must itself stay leaf-shaped. Adding a ``gaia_agent_email`` import
here — even one that looks harmless today — risks closing an import cycle
the moment some leaf module needs one of these predicates.

Every function here takes already-lowercased text and returns a bool. They
are pure substring / regex checks — no I/O, no state, no LLM.

``has_direct_ask_signal`` exists because a bare ``?`` is not a usable
signal: measured against the email-triage adversarial corpus
(``tests/fixtures/email/vendor_corpus_seed.jsonl``), 47 of 104 PROMOTIONAL
rows contain a literal ``?`` from a non-automated-looking sender. The
phrase list below is deliberately built from phrasing that asks the reader
directly for a decision, an update, or an action — not from punctuation.

``has_meeting_time_signal`` exists because the calendar module's existing
``detect_meeting_request_heuristic`` (see ``calendar_tools.py``) does not
catch informal phrasing like "any chance to meet this Thursday at 9am" —
none of its ``_INVITE_PHRASES`` match "chance to meet", and its meeting-noun
rule keys on the noun "meeting", not the verb "meet". This predicate is a
narrow addition for that gap; it does not change or replace the existing
heuristic.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Direct-ask signal
# ---------------------------------------------------------------------------
#
# Phrasing that asks the reader directly for a reply, a decision, an update,
# or an action. Chosen to catch genuine correspondence ("did you get a
# chance to look at this?") while staying narrow enough that mass-market
# marketing copy — which routinely uses a bare "?" — does not qualify on
# phrasing alone. This list is NOT sufficient by itself to call a message
# "awaiting your reply" — see ``waiting_on_you_tools.py``, which additionally
# requires corroboration that the sender is a genuine correspondent before
# qualifying a message.
_DIRECT_ASK_PHRASES: tuple[str, ...] = (
    "can you",
    "could you",
    "would you",
    "will you",
    "any chance you",
    "any chance we",
    "any update on",
    "any updates on",
    "any thoughts on",
    "any feedback on",
    "let me know if",
    "let me know what",
    "let me know when",
    "let me know your",
    "what do you think",
    "what are your thoughts",
    "when works for you",
    "when do you",
    "when would you",
    "do you have",
    "do you know",
    "did you get a chance",
    "did you have a chance",
    "have you had a chance",
    "were you able to",
    "would you be able to",
    "checking in on",
    "circling back on",
    "following up on",
    "wanted to follow up",
    "still waiting on",
    "waiting on your",
    "need your input",
    "need your feedback",
    "need your thoughts",
    "need your approval",
    "need your sign-off",
    "need your ok",
    "could you please",
    "can you please",
    "please advise",
    "please confirm",
    "please let me know",
    "please review",
    "please take a look",
)


def has_direct_ask_signal(subject_lower: str, body_lower: str) -> bool:
    """True when the subject or body asks the reader directly for a reply.

    Args:
        subject_lower: The message subject, already lowercased.
        body_lower: The message body, already lowercased.

    Deliberately phrasing-based, not punctuation-based — see the module
    docstring for why a bare ``?`` is not a usable signal on its own.
    """
    text = f"{subject_lower or ''}\n{body_lower or ''}"
    return any(phrase in text for phrase in _DIRECT_ASK_PHRASES)


# ---------------------------------------------------------------------------
# Informal meeting-time signal
# ---------------------------------------------------------------------------
#
# Verb-shaped meeting phrases the calendar module's noun-keyed heuristic
# misses (it keys on "meeting", not "meet"). Requires co-occurrence with a
# concrete day/time token, mirroring the calendar heuristic's own
# precision rule: a bare "meet" or "chance to meet" with no time attached
# is not enough to call it a scheduling proposal.
_MEETING_VERB_PHRASES: tuple[str, ...] = (
    "chance to meet",
    "free to meet",
    "able to meet",
    "meet up",
    "meet for",
    "meet this",
    "meet next",
    "meet tomorrow",
    "grab some time",
    "grab time",
    "find time to meet",
    "find a time to meet",
)

_TIME_TOKEN_PATTERNS = (
    r"\bmonday\b",
    r"\btuesday\b",
    r"\bwednesday\b",
    r"\bthursday\b",
    r"\bfriday\b",
    r"\bsaturday\b",
    r"\bsunday\b",
    r"\btomorrow\b",
    r"\btonight\b",
    r"\bthis afternoon\b",
    r"\bthis morning\b",
    r"\bnext week\b",
    r"\b\d{1,2}\s*(?:am|pm)\b",
    r"\b\d{1,2}:\d{2}\b",
)
_TIME_TOKEN_RE = re.compile("|".join(_TIME_TOKEN_PATTERNS), re.IGNORECASE)


def has_meeting_time_signal(subject_lower: str, body_lower: str) -> bool:
    """True for an informal meeting-time proposal, e.g. "any chance to meet
    this Thursday at 9am" — phrasing the existing calendar heuristic misses.

    Requires one of the verb phrases above AND a concrete day/time token, so
    a bare "let's meet up sometime" (no time) stays a non-match — consistent
    with the calendar module's own precision-first design.
    """
    text = f"{subject_lower or ''}\n{body_lower or ''}"
    if not any(phrase in text for phrase in _MEETING_VERB_PHRASES):
        return False
    return bool(_TIME_TOKEN_RE.search(text))


# ---------------------------------------------------------------------------
# Opt-out / substance signals
# ---------------------------------------------------------------------------
#
# These exist for corroboration hardening (#2581 checkpoint fix): a
# corroboration signal that trusts ANY prior message between the user and a
# sender, regardless of content, is gameable — an adversarial verifier proved
# that a single one-line prior contact (an unsubscribe reply, or an unrelated
# one-off cold outreach) is enough to manufacture "known correspondent" or
# "already replied to this thread" for every subsequent marketing message
# from that address. Two content-aware checks close that gap:
#
# - ``is_opt_out_reply`` — a message where the USER told the sender to stop
#   contacting them is evidence the user wants LESS contact from that
#   address, never more. It must never count as corroboration, and the
#   caller should treat it as an active signal to suppress that sender
#   entirely, not just fail to corroborate on it.
# - ``is_substantive_text`` — a single prior message only counts as
#   corroboration if it has real content. A three-to-six-word one-liner
#   ("what's the pricing?", "Please remove me from this list.") is not
#   evidence of an ongoing relationship on its own; two or more such
#   exchanges, or one message with real substance, is.

_OPT_OUT_PHRASES: tuple[str, ...] = (
    "unsubscribe",
    "remove me from this list",
    "remove me from your list",
    "take me off this list",
    "take me off your list",
    "stop emailing me",
    "stop contacting me",
    "stop sending me",
    "please stop sending",
    "no longer interested",
    "not interested, please remove",
    "opt out",
    "opt-out",
    "do not contact me again",
    "do not email me again",
)


def is_opt_out_reply(body_lower: str) -> bool:
    """True when the text reads as the user asking to stop being contacted.

    A message like this is evidence the sender relationship is unwanted, not
    corroboration that it is genuine correspondence — see module docstring.
    """
    text = body_lower or ""
    return any(phrase in text for phrase in _OPT_OUT_PHRASES)


_MIN_SUBSTANTIVE_WORDS = 8


def is_substantive_text(text: str, *, min_words: int = _MIN_SUBSTANTIVE_WORDS) -> bool:
    """True when ``text`` has enough real content to read as a genuine
    message rather than a one-line dismissal or a bare inquiry.

    Word-count is a blunt instrument, deliberately: it is cheap, has no
    false sense of precision, and is enough to separate "Please remove me
    from this list." (6 words) or "what's the pricing?" (3 words) from an
    actual reply.
    """
    return len((text or "").split()) >= min_words


__all__ = [
    "has_direct_ask_signal",
    "has_meeting_time_signal",
    "is_opt_out_reply",
    "is_substantive_text",
]
