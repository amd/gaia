# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Which inbox threads are waiting on the user's reply.

A port of the standalone email agent's waiting-on-you detector
(``gaia_agent_email.tools.waiting_on_you_tools``), rebuilt on the flagship's
provider-neutral message shape so one detector serves Gmail and Outlook. It is
a port, not an import: the flagship must not depend on the package it replaces.

Precision is the whole difficulty. Marketing mail routinely carries a ``?``
and a human-looking sender, so a thread qualifies only when ALL of these hold:

1. Its latest message is inbound, from a sender that does not look automated,
   whom the user never told to stop writing.
2. That message asks directly for a reply, a decision, or a meeting time —
   phrasing, never punctuation.
3. The thread itself shows the user already in the conversation: two or more
   earlier messages of theirs, or one with real substance. Having written to
   the sender in some *other* thread corroborates nothing; that path let one
   pricing inquiry vouch for every later newsletter from the same vendor.
4. It is not confidently promotional (a Gmail Promotions/Social tab or an
   unmistakable promo subject), unless the body carries a real deadline.

Read-only: it lists, reads threads, and reports. Nothing is marked or moved.
"""

from __future__ import annotations

import html
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import getaddresses, parseaddr
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from gaia.agents.tools._email.errors import MailboxError

DEFAULT_MAX_INBOX_SCAN = 50
MAX_INBOX_SCAN_CAP = 200

# The Sent folder is scanned only to learn whom the user told to stop writing.
DEFAULT_MAX_SENT_LOOKBACK = 50

# Matches the Gmail backend's fan-out: under Gmail's per-user concurrency limit.
_THREAD_FETCH_CONCURRENCY = 5

_DAY_MS = 24 * 60 * 60 * 1000

SIGNAL_DIRECT_ASK = "direct_ask"
SIGNAL_MEETING_TIME = "meeting_time"
CORROBORATION_THREAD_REPLY = "thread_reply_history"

# --------------------------------------------------------------------------
# text signals — pure substring checks on lowercased text
# --------------------------------------------------------------------------

_DIRECT_ASK_PHRASES: Tuple[str, ...] = (
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

# Verb-shaped meeting phrases; they need a concrete day/time token alongside.
_MEETING_VERB_PHRASES: Tuple[str, ...] = (
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

_VERB_TIME_RE = re.compile(
    r"\bmonday\b|\btuesday\b|\bwednesday\b|\bthursday\b|\bfriday\b"
    r"|\bsaturday\b|\bsunday\b|\btomorrow\b|\btonight\b|\bthis afternoon\b"
    r"|\bthis morning\b|\bnext week\b|\b\d{1,2}\s*(?:am|pm)\b|\b\d{1,2}:\d{2}\b",
    re.IGNORECASE,
)

# Invite phrasing strong enough to stand on its own.
_INVITE_PHRASES: Tuple[str, ...] = (
    "are you free",
    "are you available",
    "do you have time",
    "let's meet",
    "lets meet",
    "let's grab",
    "lets grab",
    "can we meet",
    "can we chat",
    "can we hop on",
    "hop on a call",
    "schedule a meeting",
    "schedule a call",
    "schedule a time",
    "set up a meeting",
    "set up a call",
    "setup a meeting",
    "book a meeting",
    "book a time",
    "calendar invite",
    "meeting invite",
    "meeting request",
    "invite you to",
    "would you like to meet",
    "want to meet",
    "let's connect",
    "lets connect",
    "let's set up",
    "lets set up",
    "does that time work",
    "does that work for you",
    "i'd like to schedule",
    "would like to schedule",
    "i want to schedule",
    "any chance to meet",
    "any chance we could meet",
    "any chance you could meet",
    "any chance you're free",
)

_MEETING_NOUNS: Tuple[str, ...] = (
    "meeting",
    "meet",
    "call",
    "1:1",
    "one-on-one",
    "sync",
    "huddle",
    "standup",
    "stand-up",
    "zoom",
    "google meet",
    "teams meeting",
)

# A noun and a time 82+ chars apart were two unrelated mentions in every
# measured marketing false positive; 60 keeps genuine same-clause phrasing.
_NOUN_TIME_PROXIMITY_CHARS = 60

_SLOT_PROPOSAL_PHRASES: Tuple[str, ...] = (
    "here are some times",
    "here are a few times",
    "here are some options",
    "propose several times",
    "propose a few times",
    "let me know what time works",
    "let me know which time works",
    "does that work for",
    "does this work for",
    "work for you",
    "i'm available",
    "i am available",
)

_TIME_RE = re.compile(
    r"\bmonday\b|\btuesday\b|\bwednesday\b|\bthursday\b|\bfriday\b"
    r"|\bsaturday\b|\bsunday\b|\btomorrow\b|\bnext week\b|\bthis week\b"
    r"|\bthis afternoon\b|\bthis morning\b|\btonight\b|\bnoon\b|\bmidday\b"
    r"|\b\d{1,2}\s*(?:am|pm)\b|\b\d{1,2}:\d{2}\b|\b\d{1,2}\s*o'clock\b",
    re.IGNORECASE,
)

_OPT_OUT_PHRASES: Tuple[str, ...] = (
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

# "Please remove me from this list." is 6 words; a real reply is more.
_MIN_SUBSTANTIVE_WORDS = 8

_AUTOMATED_SENDER_KEYWORDS: Tuple[str, ...] = (
    "noreply",
    "no-reply",
    "donotreply",
    "do-not-reply",
    "auto-confirm",
    "notifications@",
    "alerts@",
    "store-news",
)

_PROMO_SUBJECT_KEYWORDS: Tuple[str, ...] = (
    "50% off",
    "sale ends",
    "limited time",
    "special offer",
    "deal of the day",
    "discount code",
    "coupon",
    "this week's deals",
)

# A real obligation buried under a promotions label must still reach the user.
_COMMITMENT_SIGNAL_PATTERNS: Tuple[str, ...] = (
    "action required",
    "response required",
    "responses are required",
    "respond by",
    "reply by",
    "rsvp by",
    "register by",
    "renew by",
    "confirm by",
    "due by",
    "payment due",
    "past due",
    "past-due",
    "overdue",
    "final notice",
    "final reminder",
    "deadline",
    "no later than",
    "attendance is required",
    "attendance required",
    "mandatory attendance",
    "required to attend",
    "must attend",
    "confirm your attendance",
    "failure to",
    "non-compliance",
    "noncompliance",
    "will be suspended",
    "will be cancelled",
    "will be canceled",
    "will be terminated",
    "will be revoked",
    "will be charged",
    "late fee",
    "avoid a late fee",
    "loss of access",
    "budget exceeded",
    "over budget",
    "you have exceeded",
    "you've exceeded",
    "exceeded your",
    "overdrawn",
)

# Gmail tabs that mean bulk mail. Outlook has no equivalent, so there the veto
# rests on the subject keywords alone.
_PROMO_LABELS = frozenset({"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL"})
# A tab that confidently says "not promotional" short-circuits the subject check.
_NON_PROMO_LABELS = frozenset({"CATEGORY_UPDATES", "CATEGORY_PERSONAL"})

_TAG_RE = re.compile(r"<[^>]+>")
_FRACTION_RE = re.compile(r"(\.\d{6})\d+")


def has_direct_ask_signal(subject_lower: str, body_lower: str) -> bool:
    text = f"{subject_lower or ''}\n{body_lower or ''}"
    return any(phrase in text for phrase in _DIRECT_ASK_PHRASES)


def has_meeting_time_signal(subject_lower: str, body_lower: str) -> bool:
    """An informal proposal ("free to meet Thursday at 9am") — verb plus time."""
    text = f"{subject_lower or ''}\n{body_lower or ''}"
    if not any(phrase in text for phrase in _MEETING_VERB_PHRASES):
        return False
    return bool(_VERB_TIME_RE.search(text))


def _spans(term: str, text: str) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    start = 0
    while True:
        idx = text.find(term, start)
        if idx == -1:
            return out
        out.append((idx, idx + len(term)))
        start = idx + 1


def _noun_near_time(text: str) -> bool:
    nouns = [span for n in _MEETING_NOUNS for span in _spans(n, text)]
    if not nouns:
        return False
    for match in _TIME_RE.finditer(text):
        t_start, t_end = match.start(), match.end()
        for n_start, n_end in nouns:
            if n_end <= t_start:
                distance = t_start - n_end
            elif t_end <= n_start:
                distance = n_start - t_end
            else:
                distance = 0
            if distance <= _NOUN_TIME_PROXIMITY_CHARS:
                return True
    return False


def is_meeting_request(subject: str, body: str) -> bool:
    """The calendar heuristic's high-confidence positives only.

    Soft language ("let's catch up sometime") is ambiguous, not a positive, so
    it never qualifies a thread here.
    """
    text = f"{subject or ''}\n{body or ''}".lower()
    if any(p in text for p in _INVITE_PHRASES):
        return True
    if _noun_near_time(text):
        return True
    return any(p in text for p in _SLOT_PROPOSAL_PHRASES) and bool(
        _TIME_RE.search(text)
    )


def is_opt_out_reply(body_lower: str) -> bool:
    text = body_lower or ""
    return any(phrase in text for phrase in _OPT_OUT_PHRASES)


def is_substantive_text(text: str) -> bool:
    return len((text or "").split()) >= _MIN_SUBSTANTIVE_WORDS


def is_automated_sender(sender: str) -> bool:
    sender_lower = (sender or "").lower()
    return any(kw in sender_lower for kw in _AUTOMATED_SENDER_KEYWORDS)


def is_confidently_promotional(
    subject: str, body: str, label_ids: Sequence[str]
) -> bool:
    """True only where the email agent's category heuristic was confident."""
    labels = set(label_ids or ())
    subject_lower = (subject or "").lower()
    body_lower = (body or "").lower()
    if labels & _PROMO_LABELS:
        return not any(
            p in subject_lower or p in body_lower for p in _COMMITMENT_SIGNAL_PATTERNS
        )
    if labels & _NON_PROMO_LABELS:
        return False
    return any(kw in subject_lower for kw in _PROMO_SUBJECT_KEYWORDS)


def normalize_for_suppression(addr: str) -> str:
    """Casefold and drop a plus-tag, so ``v+news@x`` can't dodge ``v@x``'s opt-out."""
    addr = (addr or "").strip().lower()
    local, sep, domain = addr.partition("@")
    return f"{local.split('+', 1)[0]}{sep}{domain}"


def sender_address(rendered: str) -> str:
    """The bare, lowercased address from a ``Name <addr>`` rendering."""
    return (parseaddr(rendered or "")[1] or rendered or "").strip().lower()


def recipient_addresses(rendered: str) -> List[str]:
    out: List[str] = []
    for _, addr in getaddresses([rendered or ""]):
        addr = addr.strip().lower()
        if addr and addr not in out:
            out.append(addr)
    return out


def plain_body(message: Dict[str, Any]) -> str:
    """The body as text. Signals are substring checks, so tags must go."""
    body = message.get("body") or ""
    if (message.get("body_content_type") or "text").lower() == "html":
        body = html.unescape(_TAG_RE.sub(" ", body))
    return body


_ATTRIBUTION_RE = re.compile(r"^On\b.*wrote:\s*$", re.IGNORECASE)
_ORIGINAL_MESSAGE_RE = re.compile(
    r"^-{2,}\s*(Original|Forwarded) Message\s*-{2,}", re.IGNORECASE
)


def strip_quoted_text(body: str) -> str:
    """Keep the sender's new text, not an older quoted ask.

    Waiting-on-you signals must not inspect the whole rendered body: a reply
    that only quotes an earlier ``Could you...?`` is not a new request.
    """
    kept: List[str] = []
    for line in (body or "").splitlines():
        stripped = line.strip()
        if _ATTRIBUTION_RE.match(stripped) or _ORIGINAL_MESSAGE_RE.match(stripped):
            break
        if stripped.startswith(">"):
            continue
        kept.append(line)
    return "\\n".join(kept).strip()


def is_auto_reply(message: Dict[str, Any]) -> bool:
    """Whether provider headers identify the message as an automatic reply."""
    auto_submitted = (message.get("auto_submitted") or "").strip().lower()
    x_autoreply = (message.get("x_autoreply") or "").strip().lower()
    return bool(auto_submitted and auto_submitted != "no") or x_autoreply in {
        "yes",
        "true",
    }


def received_ms(message: Dict[str, Any]) -> int:
    """``received`` (UTC ISO-8601) as epoch millis, or fail naming the message.

    An age computed from a guessed timestamp would silently mis-rank threads.
    """
    raw = (message.get("received") or "").strip()
    if not raw:
        raise MailboxError(
            f"message {message.get('id')!r} has no received time, so its age "
            "cannot be computed. Retry; report it at "
            "https://github.com/amd/gaia/issues if it repeats."
        )
    # Python 3.10's fromisoformat takes neither "Z" nor 7 fractional digits.
    normalized = _FRACTION_RE.sub(r"\1", raw.replace("Z", "+00:00"))
    try:
        stamp = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise MailboxError(
            f"message {message.get('id')!r} has an unreadable received time "
            f"{raw!r}. Report it at https://github.com/amd/gaia/issues."
        ) from exc
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return int(stamp.timestamp() * 1000)


# --------------------------------------------------------------------------
# the scan
# --------------------------------------------------------------------------


def _fetch_threads(backend, thread_ids: Sequence[str]) -> List[Dict[str, Any]]:
    """Every thread, in order. One failure fails the scan — never a short list."""
    if not thread_ids:
        return []
    with ThreadPoolExecutor(
        max_workers=min(_THREAD_FETCH_CONCURRENCY, len(thread_ids))
    ) as pool:
        return list(pool.map(backend.get_thread, thread_ids))


def _sent_messages(thread: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [m for m in thread.get("messages") or [] if not m.get("draft")]


def scan_opted_out_senders(backend, *, user_email: str, max_sent: int) -> Set[str]:
    """Addresses the user has told, in any thread, to stop writing to them."""
    listing = backend.list_thread_ids("sent", limit=max_sent)
    opted_out: Set[str] = set()
    for thread in _fetch_threads(backend, listing["thread_ids"]):
        for msg in _sent_messages(thread):
            if sender_address(msg.get("from", "")) != user_email:
                continue
            if not is_opt_out_reply(plain_body(msg).lower()):
                continue
            opted_out.update(
                normalize_for_suppression(a)
                for a in recipient_addresses(msg.get("to", ""))
                if a != user_email
            )
    return opted_out


def _qualify(
    thread: Dict[str, Any],
    *,
    user_email: str,
    opted_out: Set[str],
) -> Optional[Tuple[Dict[str, Any], str]]:
    """``(latest message, signal)`` when the thread waits on the user, else None."""
    messages = sorted(_sent_messages(thread), key=received_ms)
    if not messages:
        return None
    latest = messages[-1]
    sender_raw = latest.get("from", "")
    sender = sender_address(sender_raw)
    if sender == user_email:
        return None  # The user spoke last; the ball is in the other court.
    if is_automated_sender(sender_raw):
        return None
    if normalize_for_suppression(sender) in opted_out:
        return None

    if is_auto_reply(latest):
        return None

    to_addresses = {
        normalize_for_suppression(address)
        for address in recipient_addresses(latest.get("to", ""))
    }
    cc_addresses = {
        normalize_for_suppression(address)
        for address in recipient_addresses(latest.get("cc", ""))
    }
    user_address = normalize_for_suppression(user_email)
    if user_address not in to_addresses and user_address in cc_addresses:
        return None  # The ask is addressed to someone else, not the user.

    subject = latest.get("subject", "")
    body = strip_quoted_text(plain_body(latest))
    if is_confidently_promotional(subject, body, latest.get("_label_ids") or ()):
        return None

    subject_lower, body_lower = subject.lower(), body.lower()
    if has_direct_ask_signal(subject_lower, body_lower):
        signal = SIGNAL_DIRECT_ASK
    elif has_meeting_time_signal(subject_lower, body_lower) or is_meeting_request(
        subject, body
    ):
        signal = SIGNAL_MEETING_TIME
    else:
        return None

    # Only the user's OWN earlier messages count. Counting every message let a
    # vendor's cold intro plus a one-word "thanks" pass the two-message bar.
    prior_bodies: List[str] = []
    for msg in messages[:-1]:
        if sender_address(msg.get("from", "")) != user_email:
            continue
        text = plain_body(msg)
        if is_opt_out_reply(text.lower()):
            return None  # Told this sender to stop, in this very thread.
        prior_bodies.append(text)
    if len(prior_bodies) < 2 and not (
        prior_bodies and is_substantive_text(prior_bodies[0])
    ):
        return None
    return latest, signal


def detect_waiting_on_you(
    backend,
    *,
    max_inbox: int = DEFAULT_MAX_INBOX_SCAN,
    max_sent_lookback: int = DEFAULT_MAX_SENT_LOOKBACK,
    min_age_hours: int = 0,
    now_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """Inbox threads where the user owes a reply, most overdue first.

    ``backend`` needs ``get_user_email``, ``list_thread_ids`` and
    ``get_thread`` — both mailbox backends provide them.
    """
    if min_age_hours < 0:
        raise ValueError(f"min_age_hours must be >= 0, got {min_age_hours!r}")
    if max_inbox < 1:
        raise ValueError(f"max_inbox must be >= 1, got {max_inbox!r}")
    if now_ms is None:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    user_email = (backend.get_user_email() or "").strip().lower()
    opted_out = scan_opted_out_senders(
        backend, user_email=user_email, max_sent=max_sent_lookback
    )
    listing = backend.list_thread_ids("inbox", limit=max_inbox)
    thread_ids = listing["thread_ids"]
    threads = _fetch_threads(backend, thread_ids)

    min_age_ms = min_age_hours * 60 * 60 * 1000
    flagged: List[Tuple[int, Dict[str, Any], Dict[str, Any]]] = []
    unchecked: List[str] = []
    for thread_id, thread in zip(thread_ids, threads):
        if thread.get("truncated"):
            # Only part of the conversation came back, so its latest message
            # is unknown. Reported, never guessed at.
            unchecked.append(thread_id)
            continue
        verdict = _qualify(thread, user_email=user_email, opted_out=opted_out)
        if verdict is None:
            continue
        latest, signal = verdict
        age_ms = now_ms - received_ms(latest)
        if age_ms < min_age_ms:
            continue
        item = {
            "message_id": latest.get("id"),
            "thread_id": thread_id,
            "from": latest.get("from", ""),
            "subject": latest.get("subject", ""),
            "received": latest.get("received", ""),
            "age_days": int(age_ms // _DAY_MS),
            "signal": signal,
            "corroboration": CORROBORATION_THREAD_REPLY,
        }
        flagged.append((age_ms, item, latest))

    flagged.sort(key=lambda entry: entry[0], reverse=True)
    return {
        "waiting_on_you": [item for _, item, _ in flagged],
        "latest_messages": [latest for _, _, latest in flagged],
        "inbox_scanned": listing["messages_listed"],
        "threads_checked": len(thread_ids) - len(unchecked),
        "threads_unchecked": unchecked,
        "scan_truncated": listing["truncated"],
    }


__all__ = [
    "CORROBORATION_THREAD_REPLY",
    "DEFAULT_MAX_INBOX_SCAN",
    "DEFAULT_MAX_SENT_LOOKBACK",
    "MAX_INBOX_SCAN_CAP",
    "SIGNAL_DIRECT_ASK",
    "SIGNAL_MEETING_TIME",
    "detect_waiting_on_you",
    "has_direct_ask_signal",
    "has_meeting_time_signal",
    "is_confidently_promotional",
    "is_meeting_request",
    "normalize_for_suppression",
    "scan_opted_out_senders",
]
