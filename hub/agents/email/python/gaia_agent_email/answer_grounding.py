# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Deterministic post-checks on the agent's own final answer text.

The system prompt asks the model to stay honest about what it actually did
and what it actually saw, but prompt compliance is probabilistic — a model
can still narrate a mutation it never called a tool for, contradict a tool
result it just received, or echo internal payload scaffolding into prose.
These functions inspect the FINAL answer text against the turn's own tool
trace (``result["conversation"]``, which ``Agent._process_query_impl``
resets to empty at the start of every call, so it always scopes to exactly
this turn) and either flag or rewrite the parts that are not grounded in
what actually happened.

Every function here is pure and side-effect free — no LLM calls, no I/O —
so the guard is unit-testable without a live model or a live mailbox.
``EmailTriageAgent.process_query`` is the single call site that wires these
into the output boundary.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, Iterator, List, Optional

from gaia_agent_email.attention_cache import ATTENTION_CACHE_TTL_SECONDS
from gaia_agent_email.attention_cache import peek as _peek_attention_cache
from gaia_agent_email.tools.calendar_tools import (
    _listed_event_count_from_conversation,
    append_conflict_grounding_correction,
    response_has_ungrounded_conflict_claim,
)

from gaia.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Shared helpers — reading the turn's own tool trace
# ---------------------------------------------------------------------------


def _parse_tool_payload(content: Any) -> Optional[Dict[str, Any]]:
    """Best-effort decode of a ``role: tool`` conversation entry's content.

    Handles every shape a tool result can arrive in: a JSON string, a native
    tool-calling wire block (``[{"type": "text", "text": "..."}]``), or an
    already-parsed dict. Unwraps the ``{"ok": true, "data": {...}}`` envelope
    convention when present. Returns ``None`` when the content cannot be
    read as a mapping — never raises, since a conversation entry from an
    unrelated tool shape is not this function's error to report.
    """
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return None
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return _parse_tool_payload(block.get("text"))
        return None
    if not isinstance(content, dict):
        return None
    data = content.get("data")
    if isinstance(data, dict):
        return data
    return content


def _tool_entries(conversation: Optional[List[Dict[str, Any]]]) -> Iterator[Dict[str, Any]]:
    for entry in conversation or []:
        if isinstance(entry, dict) and entry.get("role") == "tool":
            yield entry


def tools_called_this_turn(conversation: Optional[List[Dict[str, Any]]]) -> List[str]:
    """Names of every tool invoked in this turn's conversation trace."""
    return [entry.get("name") for entry in _tool_entries(conversation) if entry.get("name")]


def last_tool_payload(
    conversation: Optional[List[Dict[str, Any]]], tool_name: str
) -> Optional[Dict[str, Any]]:
    """The most recent parsed result of ``tool_name`` called this turn, if any."""
    payload = None
    for entry in _tool_entries(conversation):
        if entry.get("name") == tool_name:
            parsed = _parse_tool_payload(entry.get("content"))
            if parsed is not None:
                payload = parsed
    return payload


# ---------------------------------------------------------------------------
# Guard 1 — a mutation claimed without a matching tool call this turn
# ---------------------------------------------------------------------------

# Completion framing: the shapes a model uses to say "this already happened",
# as opposed to explaining what an action does or offering to perform one.
_COMPLETION_LEAD = (
    r"(?:has|have|was|were)\s+(?:now\s+|already\s+|successfully\s+)?(?:been\s+)?"
    r"|i(?:'ve|\s+have)\s+(?:now\s+|already\s+|successfully\s+)?"
    r"|(?:successfully|done)[\s:—-]*(?:i\s+)?(?:just\s+)?"
    r"|is\s+now\s+|are\s+now\s+|just\s+got\s+"
)
_MUTATION_VERB = (
    r"archiv\w*|(?:un)?star\w*|marked\s+(?:as\s+)?(?:un)?read|trashed"
    r"|deleted|label(?:l)?ed|quarantined|unquarantined|restored|sent"
    r"|forwarded|scheduled|snoozed"
)
_SUCCESS_CLAIM_RE = re.compile(
    rf"\b(?:{_COMPLETION_LEAD})(?:{_MUTATION_VERB})\b"
    rf"|\bmoved\s+to\s+(?:trash|the\s+\S+\s+label)\b",
    re.IGNORECASE,
)

UNGROUNDED_SUCCESS_FALLBACK = (
    "I was not able to confirm that action actually completed — no tool call "
    "was recorded for this turn. Please ask again and I will only report it "
    "as done once a tool call actually confirms it."
)


def find_ungrounded_success_claim(
    final_answer: Optional[str], conversation: Optional[List[Dict[str, Any]]]
) -> Optional[str]:
    """Return the matched phrase when ``final_answer`` claims a mutation
    completed but this turn's tool trace is empty; ``None`` when grounded.

    Deliberately turn-scoped and tool-agnostic: it does not try to match the
    claimed verb to a specific tool name (a model paraphrases too freely for
    that to be reliable). Any completion-framed mutation claim is
    contradicted by the plain fact that zero tools ran this turn — the agent
    has no other channel through which a mailbox mutation could happen.
    """
    if not final_answer:
        return None
    if tools_called_this_turn(conversation):
        return None
    match = _SUCCESS_CLAIM_RE.search(final_answer)
    return match.group(0) if match else None


# ---------------------------------------------------------------------------
# Guard 2 — negative claims contradicted by this turn's own scan result
# ---------------------------------------------------------------------------

_NO_URGENT_RE = re.compile(r"\bno\b[^.!?]{0,40}\burgent\b", re.IGNORECASE)
_NO_ACTIONABLE_RE = re.compile(r"\bno\b[^.!?]{0,40}\bactionable\b", re.IGNORECASE)
_ALL_CLEAR_RE = re.compile(
    r"\b(?:nothing needs|inbox is clear|all clear|nothing urgent|nothing actionable)\b",
    re.IGNORECASE,
)
_COVERAGE_QUALIFIER_RE = re.compile(
    r"\bscanned\b|\bout of\b|\bof the\b|\bmost recent\b|\bunread\b|\bolder\b"
    r"|\bnot everything\b|\bso far\b|\bpartial\b",
    re.IGNORECASE,
)


def find_unqualified_negative_claim(
    final_answer: Optional[str], conversation: Optional[List[Dict[str, Any]]]
) -> Optional[str]:
    """Return a reason string when ``final_answer`` contradicts this turn's
    own ``pre_scan_inbox`` result, ``None`` when the claim is grounded.

    Two independent checks, both scoped to the SAME envelope the model
    itself received this turn (never a separately-rendered surface it has
    no visibility into):

    - "no urgent" / "no actionable" while the matching list is non-empty.
    - an unqualified all-clear phrase while ``scanned`` under-covers
      ``total_unread`` and the answer carries no coverage qualifier.
    """
    if not final_answer:
        return None
    envelope = last_tool_payload(conversation, "pre_scan_inbox")
    if envelope is None:
        return None

    urgent = envelope.get("urgent") or []
    actionable = envelope.get("actionable") or []
    if urgent and _NO_URGENT_RE.search(final_answer):
        return f"claims no urgent items while pre_scan_inbox returned {len(urgent)}"
    if actionable and _NO_ACTIONABLE_RE.search(final_answer):
        return f"claims no actionable items while pre_scan_inbox returned {len(actionable)}"

    scanned = envelope.get("scanned")
    total_unread = envelope.get("total_unread")
    if (
        isinstance(scanned, int)
        and isinstance(total_unread, int)
        and scanned < total_unread
        and _ALL_CLEAR_RE.search(final_answer)
        and not _COVERAGE_QUALIFIER_RE.search(final_answer)
    ):
        return (
            f"unqualified all-clear claim while scanned={scanned} < "
            f"total_unread={total_unread}"
        )
    return None


# ``total_unread`` is a per-INBOX-label count -- a single mailbox's own
# unread inbox, or (when several mailboxes are connected) the SUM of each
# one's own INBOX count. It is never a whole-account or all-folders total,
# so "across your mailboxes/accounts" always overclaims its scope, whether
# one mailbox or several are connected.
_CROSS_MAILBOX_UNREAD_CLAIM_RE = re.compile(
    r"\bunread\b[^.!?]{0,60}\bacross\s+(?:your\s+|the\s+)?(?:connected\s+)?"
    r"(?:mailboxes|accounts|inboxes)\b"
    r"|\bacross\s+(?:your\s+|the\s+)?(?:connected\s+)?(?:mailboxes|accounts|inboxes)"
    r"[^.!?]{0,60}\bunread\b",
    re.IGNORECASE,
)


def find_unlicensed_cross_mailbox_claim(
    final_answer: Optional[str], conversation: Optional[List[Dict[str, Any]]]
) -> Optional[str]:
    """Return a reason when ``final_answer`` describes this turn's
    ``pre_scan_inbox`` ``total_unread`` as spanning multiple mailboxes or
    accounts. Fires whenever a ``total_unread`` value was actually returned,
    independent of how many mailboxes are connected — the field is
    INBOX-label-scoped either way, never a whole-account total.
    """
    if not final_answer:
        return None
    envelope = last_tool_payload(conversation, "pre_scan_inbox")
    if envelope is None or envelope.get("total_unread") is None:
        return None
    if _CROSS_MAILBOX_UNREAD_CLAIM_RE.search(final_answer):
        return "describes total_unread as spanning multiple mailboxes/accounts"
    return None


# ---------------------------------------------------------------------------
# Guard 3 — internal payload scaffolding leaking into prose
# ---------------------------------------------------------------------------

_SHOWN_TO_USER_MARKER_RE = re.compile(
    r"\n*\[shown to the user\]\n*", re.IGNORECASE
)
_ENVELOPE_FIELD_LABEL_RE = re.compile(
    r"\[(?:urgent|actionable|informational|suggested_archives|needs_review"
    r"|preferences_applied|totals|items|coverage|waiting_on_you|action_item"
    r"|meeting_request|mailbox_errors|scan_truncated|degraded)\]\s*",
    re.IGNORECASE,
)
_RAW_MESSAGE_ID_RE = re.compile(r"\(id [0-9a-f]{16}\)\s*|\b[0-9a-f]{16}\b", re.IGNORECASE)
_UNICODE_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")

_SCAFFOLDING_CHECKS = (
    ("shown-to-user marker", _SHOWN_TO_USER_MARKER_RE),
    ("envelope field-name label", _ENVELOPE_FIELD_LABEL_RE),
    ("raw provider message id", _RAW_MESSAGE_ID_RE),
    ("undecoded unicode escape", _UNICODE_ESCAPE_RE),
)


def find_scaffolding_leak(text: Optional[str]) -> Optional[str]:
    """Return which known internal-scaffolding pattern appears in ``text``,
    or ``None`` when the text is clean. Detection only — see
    ``strip_scaffolding_leaks`` for the rewrite."""
    if not text:
        return None
    for label, pattern in _SCAFFOLDING_CHECKS:
        if pattern.search(text):
            return label
    return None


def decode_stray_unicode_escapes(text: str) -> str:
    """Turn a literal ``\\uXXXX`` escape sequence into the character it
    names. A safety net for any path that still hands the model (or the
    model's own output) an ``ensure_ascii``-escaped string."""
    if not text or "\\u" not in text:
        return text
    return _UNICODE_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), text)


def strip_scaffolding_leaks(text: str) -> str:
    """Remove internal render/envelope scaffolding from a final answer.

    Targeted substitution rather than replacing the whole message: the
    surrounding prose is presumed fine, only these specific tokens are not
    meant for the reader.
    """
    if not text:
        return text
    cleaned = _SHOWN_TO_USER_MARKER_RE.sub("\n", text)
    cleaned = _ENVELOPE_FIELD_LABEL_RE.sub("", cleaned)
    cleaned = _RAW_MESSAGE_ID_RE.sub("", cleaned)
    cleaned = decode_stray_unicode_escapes(cleaned)
    return cleaned.strip()


def _honest_prescan_summary(envelope: Dict[str, Any]) -> str:
    """A minimal, always-grounded pre-scan sentence built straight from the
    envelope's own counts — the fallback used when the model's own framing
    sentence contradicts that same envelope."""
    urgent = len(envelope.get("urgent") or [])
    actionable = len(envelope.get("actionable") or [])
    needs_review = len(envelope.get("needs_review") or [])
    parts = []
    if urgent:
        parts.append(f"{urgent} urgent")
    if actionable:
        parts.append(f"{actionable} actionable")
    if needs_review:
        parts.append(f"{needs_review} worth a closer look")
    summary = ", ".join(parts) if parts else "nothing urgent or actionable"
    coverage = f"{envelope.get('scanned', 0)} messages scanned"
    total_unread = envelope.get("total_unread")
    if isinstance(total_unread, int):
        coverage += f" · {total_unread} unread in your inbox"
    return f"Here's your inbox pre-scan — {summary}. {coverage}."


# ---------------------------------------------------------------------------
# Guard 4 — a calendar conflict/overlap verdict the model narrated itself
# instead of getting from detect_calendar_conflicts (#2571)
# ---------------------------------------------------------------------------


def find_ungrounded_calendar_conflict_claim(
    final_answer: Optional[str], conversation: Optional[List[Dict[str, Any]]]
) -> Optional[str]:
    """Return a reason string when ``final_answer`` narrates a calendar
    conflict/overlap verdict that this turn's own tool trace never computed;
    ``None`` when the claim is grounded or the answer makes no such claim.

    The actual detection is deterministic interval-adjacent text matching in
    ``calendar_tools.response_has_ungrounded_conflict_claim`` — two ways in:
    ``list_calendar_events`` ran and returned >=2 events (below that no
    conflict is even possible), or NEITHER calendar tool ran at all but the
    response still cites >=2 specific times alongside conflict language.
    ``detect_calendar_conflicts`` having run this turn always grounds the
    claim. Unlike the other guards in this module, the caller APPENDS a
    correction rather than replacing the answer outright (see
    ``append_conflict_grounding_correction``) — the listed events themselves
    came from a real tool call and stay useful; only the self-narrated
    verdict is unverified.
    """
    if not final_answer:
        return None
    tool_names = tools_called_this_turn(conversation)
    listed_event_count = _listed_event_count_from_conversation(conversation or [])
    if response_has_ungrounded_conflict_claim(
        final_answer, tool_names, listed_event_count
    ):
        return "narrates a calendar conflict verdict without detect_calendar_conflicts"
    return None


# ---------------------------------------------------------------------------
# Guard 5 — negative claims contradicted by the cached ATTENTION CARD (#2636)
# ---------------------------------------------------------------------------
#
# Unlike guard 2 above, the attention view is never a tool result in THIS
# turn's own trace: ``build_attention_view_impl`` has no ``@tool`` wrapper
# (#2582 built it purely for the TUI to render on open, via
# ``GET /v1/email/attention``), so the model generating ``final_answer`` has
# no way to see it and cannot ground itself against it on its own. The one
# honest source of "what the user is actually looking at right now" is the
# process-global cache that route already populated -- ``server.py`` mounts
# both that route and the agent's ``/query`` surface on one FastAPI app, and
# the sidecar is single-tenant by design (``_SessionRegistry``, one user's
# mailbox per process), so there is exactly one attention view to reconcile
# against, never one per session. This guard is therefore deliberately
# turn-INDEPENDENT: it does not require any tool call this turn, only that a
# cached view exists and is still fresh enough to trust.

_ATTENTION_CATEGORY_NOUNS = {
    "meeting_request": r"meetings?|meeting\s+proposals?",
    "waiting_on_you": r"(?:messages?\s+)?waiting\s+on\s+you",
    "needs_review": r"(?:messages?\s+(?:worth|needing)\s+(?:a\s+)?review|reviews?)",
    "action_item": r"action\s+items?",
}
_ATTENTION_CATEGORY_RE = {
    kind: re.compile(rf"\bno\b[^.!?]{{0,40}}\b(?:{noun})\b", re.IGNORECASE)
    for kind, noun in _ATTENTION_CATEGORY_NOUNS.items()
}

_ATTENTION_CATEGORY_LABELS = {
    "meeting_request": "meeting proposal",
    "waiting_on_you": "message waiting on you",
    "needs_review": "message worth a closer look",
    "action_item": "open action item",
}


def _attention_card_all_clear_claim(text: str) -> bool:
    return bool(
        _NO_URGENT_RE.search(text)
        or _NO_ACTIONABLE_RE.search(text)
        or _ALL_CLEAR_RE.search(text)
    )


def _attention_item_counts(cached: Dict[str, Any]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in cached.get("items") or []:
        kind = item.get("kind") if isinstance(item, dict) else None
        if kind:
            counts[kind] = counts.get(kind, 0) + 1
    return counts


def find_attention_card_contradiction(final_answer: Optional[str]) -> Optional[str]:
    """Return a reason when ``final_answer`` asserts absence of a category
    the cached attention card currently shows as non-empty, ``None`` when
    there is nothing to reconcile against or the claim is grounded.

    Declines to correct once the cache is older than
    ``ATTENTION_CACHE_TTL_SECONDS`` -- the same freshness window
    ``GET /v1/email/attention`` itself uses to decide a cached value is too
    old to serve without recomputing. Past that window the items the card
    showed may already be resolved, and asserting they are still open would
    be #2636's own dishonesty pointed the other way; declining is a refusal
    to assert something no longer supportable, not a silent fallback.
    """
    if not final_answer:
        return None
    cached = _peek_attention_cache()
    if cached is None:
        return None
    age = time.time() - cached["_computed_at"]
    if age > ATTENTION_CACHE_TTL_SECONDS:
        return None
    counts = _attention_item_counts(cached)
    if not counts:
        return None

    if _attention_card_all_clear_claim(final_answer):
        total = sum(counts.values())
        return f"claims no urgent/actionable items while the attention card has {total}"

    for kind, pattern in _ATTENTION_CATEGORY_RE.items():
        if counts.get(kind) and pattern.search(final_answer):
            return f"claims no {kind} while the attention card has {counts[kind]}"
    return None


def _attention_card_correction(cached: Dict[str, Any]) -> str:
    """A short correction naming the surface and its coverage, built
    straight from the cached envelope's own counts (#2636 AC 2)."""
    counts = _attention_item_counts(cached)
    parts = []
    for kind, label in _ATTENTION_CATEGORY_LABELS.items():
        n = counts.pop(kind, 0)
        if n:
            parts.append(f"{n} {label}{'s' if n != 1 else ''}")
    for kind, n in counts.items():
        if n:
            parts.append(f"{n} {kind}")
    listing = ", ".join(parts) if parts else "items still open"

    coverage = cached.get("coverage") or {}
    scanned = coverage.get("scanned")
    coverage_text = (
        f"{scanned} messages scanned"
        if isinstance(scanned, int)
        else "coverage unknown"
    )
    age = max(0.0, time.time() - cached["_computed_at"])
    age_text = f"{int(age)}s" if age < 60 else f"{int(age // 60)}m"

    return (
        f"(Your attention card — updated {age_text} ago, {coverage_text} — "
        f"still shows {listing}.)"
    )


# ---------------------------------------------------------------------------
# Guard 6 — an invite claimed as sent/received/confirmed (#2766)
# ---------------------------------------------------------------------------
#
# No tool in this package can currently confirm that a genuine calendar
# invite was sent or received — detect_meeting_request is a text heuristic
# for PROPOSALS, never a confirmation, and list_calendar_events /
# detect_calendar_conflicts return real events but an event existing is not
# evidence anyone emailed an invite for it (see calendar_tools' docstrings).
# A completion-framed invite claim is therefore always ungrounded today,
# with one exception: create_event_from_email's own mutation legitimately
# sends calendar invites to its attendees, so a turn that actually called it
# licenses the claim (mirrors guard 1's "grounded when a tool ran" shape).

_INVITE_CLAIM_RE = re.compile(
    r"\binvite[sd]?\b[^.!?]{0,40}\b(?:sent|received|confirmed)\b"
    r"|\b(?:sent|received)\b[^.!?]{0,40}\binvite[sd]?\b",
    re.IGNORECASE,
)

# A negation anywhere in the same clause turns a claim into a (correct)
# denial -- "no invite has been sent" must not be treated like "an invite
# has been sent". Deliberately broad (checked over the whole clause, not a
# fixed window) since a denial can front-load its negation far from "invite".
_INVITE_NEGATION_RE = re.compile(
    r"\b(?:no|not|n't|never|none|isn't|wasn't|hasn't|haven't|didn't|doesn't)\b",
    re.IGNORECASE,
)


def _clause_around(text: str, start: int, end: int) -> str:
    """The sentence-ish span of ``text`` containing ``[start, end)`` —
    bounded by the nearest ``.``/``!``/``?`` on either side (or the string's
    own edges). Shared by guards that need "same clause" context without
    crossing into an unrelated sentence.
    """
    clause_start = (
        max(
            text.rfind(".", 0, start),
            text.rfind("!", 0, start),
            text.rfind("?", 0, start),
        )
        + 1
    )
    ends = [
        idx
        for idx in (text.find(".", end), text.find("!", end), text.find("?", end))
        if idx != -1
    ]
    clause_end = min(ends) if ends else len(text)
    return text[clause_start:clause_end]


def find_ungrounded_invite_claim(
    final_answer: Optional[str], conversation: Optional[List[Dict[str, Any]]]
) -> Optional[str]:
    """Return a reason when ``final_answer`` claims a calendar invite was
    sent, received, or confirmed; ``None`` when the claim is grounded,
    negated, or absent.

    Proposals are not invites (#2766): "X proposed Thursday at 2pm" is fine,
    "X sent you an invite" is not, unless this turn actually created one.
    """
    if not final_answer:
        return None
    match = _INVITE_CLAIM_RE.search(final_answer)
    if not match:
        return None
    clause = _clause_around(final_answer, match.start(), match.end())
    if _INVITE_NEGATION_RE.search(clause):
        return None
    if "create_event_from_email" in tools_called_this_turn(conversation):
        return None
    return f"claims an invite was sent/received/confirmed: {match.group(0)!r}"


_INVITE_GROUNDING_CORRECTION = (
    "\n\nNote: I don't have a way to confirm a calendar invite was actually "
    "sent or received — what's described above may be a proposal, not a "
    "confirmed invite."
)


def append_invite_grounding_correction(response_text: str) -> str:
    """Append a correction notice to a response with an ungrounded invite
    claim. Never edits the original text — see ``find_ungrounded_invite_claim``
    for why this is an append, not a replace."""
    return (response_text or "") + _INVITE_GROUNDING_CORRECTION


# ---------------------------------------------------------------------------
# Guard 7 — an attendee/invitee named for a calendar event this turn's own
# tool result shows has none (#2766)
# ---------------------------------------------------------------------------
#
# list_calendar_events / detect_calendar_conflicts now report each event's
# real ``attendees`` (calendar_tools._extract_attendees) — [] when the
# calendar has no one beyond the organizer, which #2766's live probes show
# is true of every real event in the reference corpus. This guard is
# deliberately narrow: it does not try to catch every way a name could leak
# into prose (arbitrary proper-noun detection is not reliable), only the
# bounded, checkable case where the model uses attendee/invitee vocabulary
# for an event this turn's own tool result already proved carries none.

_ATTENDEE_CLAIM_RE = re.compile(r"\battendee[s]?\b|\binvitee[s]?\b", re.IGNORECASE)


def _any_listed_event_has_attendees(
    conversation: Optional[List[Dict[str, Any]]],
) -> Optional[bool]:
    """Across every ``list_calendar_events`` / ``detect_calendar_conflicts``
    result this turn: ``True`` if any listed event carries a non-empty
    ``attendees``, ``False`` if every one is empty, ``None`` if neither tool
    ran (nothing to reconcile against).
    """
    saw_any_tool = False
    for entry in _tool_entries(conversation):
        if entry.get("name") not in (
            "list_calendar_events",
            "detect_calendar_conflicts",
        ):
            continue
        payload = _parse_tool_payload(entry.get("content"))
        if not isinstance(payload, dict):
            continue
        events = payload.get("events")
        if events is None:
            events = payload.get("conflicts")
        if not isinstance(events, list):
            continue
        saw_any_tool = True
        for ev in events:
            if isinstance(ev, dict) and ev.get("attendees"):
                return True
    return False if saw_any_tool else None


def find_fabricated_attendee_claim(
    final_answer: Optional[str], conversation: Optional[List[Dict[str, Any]]]
) -> Optional[str]:
    """Return a reason when ``final_answer`` names attendees/invitees for a
    calendar event this turn's own tool result shows has none; ``None``
    when neither calendar tool ran, at least one listed event actually
    carries attendees, or the answer makes no attendee-shaped claim.
    """
    if not final_answer:
        return None
    if not _ATTENDEE_CLAIM_RE.search(final_answer):
        return None
    has_attendees = _any_listed_event_has_attendees(conversation)
    if has_attendees is None or has_attendees:
        return None
    return (
        "names an attendee/invitee for a calendar event whose own "
        "attendees list is empty"
    )


_ATTENDEE_GROUNDING_CORRECTION = (
    "\n\nNote: the calendar event(s) above don't list any attendees — I "
    "can't confirm who, if anyone, is attending."
)


def append_attendee_grounding_correction(response_text: str) -> str:
    """Append a correction notice to a response with a fabricated attendee
    claim. Never edits the original text — see
    ``find_fabricated_attendee_claim`` for why this is an append, not a
    replace."""
    return (response_text or "") + _ATTENDEE_GROUNDING_CORRECTION


# ---------------------------------------------------------------------------
# Orchestration — the single call site EmailTriageAgent.process_query uses
# ---------------------------------------------------------------------------


def ground_final_answer(result: Dict[str, Any]) -> Dict[str, Any]:
    """Apply every deterministic post-check to ``result["result"]`` in place.

    Order matters: scaffolding is stripped first (a pure, always-safe
    rewrite), then the two contradiction checks run against the cleaned
    text. Either contradiction check fully replaces the answer with a
    grounded fallback rather than attempting a partial text patch — a
    claim that has already been shown false is not a base worth editing
    from. A replaced answer is not re-scanned by the other check: each
    fallback is already, by construction, clean of the pattern it replaces.

    The calendar-conflict (#2571), attention-card (#2636), invite-claim, and
    fabricated-attendee (#2766) checks run last and, unlike the two above,
    APPEND a correction instead of replacing the answer — in every case the
    rest of the answer stays useful and only a specific clause is
    unverified/contradicted. They never run against text a prior
    contradiction check has already replaced (those checks already
    ``return``), but all four are independent of each other and MUST all be
    allowed to fire on the same turn, appending in sequence — none may
    short-circuit another.
    """
    final_answer = result.get("result")
    if not isinstance(final_answer, str) or not final_answer:
        return result

    conversation = result.get("conversation")

    if find_scaffolding_leak(final_answer):
        final_answer = strip_scaffolding_leaks(final_answer)

    success_claim = find_ungrounded_success_claim(final_answer, conversation)
    if success_claim:
        logger.warning(
            "email agent: dropped ungrounded success claim %r — no tool call "
            "recorded this turn",
            success_claim,
        )
        result["result"] = UNGROUNDED_SUCCESS_FALLBACK
        return result

    contradiction_reason = find_unqualified_negative_claim(
        final_answer, conversation
    ) or find_unlicensed_cross_mailbox_claim(final_answer, conversation)
    if contradiction_reason:
        logger.warning(
            "email agent: rewrote contradicted pre-scan claim — %s",
            contradiction_reason,
        )
        envelope = last_tool_payload(conversation, "pre_scan_inbox")
        result["result"] = _honest_prescan_summary(envelope or {})
        return result

    # Both remaining checks APPEND rather than replace (unlike the two
    # contradiction checks above): the false clause is typically one aside
    # inside an otherwise-useful answer (e.g. a drafted reply plus a wrong
    # note about "no action items", or a conflict verdict tacked onto a
    # correct event listing), so qualifying it costs the user less than
    # scrubbing the whole message. Both are independent and may fire on the
    # same turn — this must NOT be an if/elif or an early return, or one
    # correction silently suppresses the other.
    calendar_conflict_reason = find_ungrounded_calendar_conflict_claim(
        final_answer, conversation
    )
    if calendar_conflict_reason:
        logger.warning(
            "email agent: appended ungrounded calendar-conflict correction — %s",
            calendar_conflict_reason,
        )
        final_answer = append_conflict_grounding_correction(final_answer)

    attention_reason = find_attention_card_contradiction(final_answer)
    if attention_reason:
        logger.warning(
            "email agent: appended attention-card correction — %s",
            attention_reason,
        )
        cached = _peek_attention_cache()
        if cached is not None:
            final_answer = (
                final_answer.rstrip() + "\n\n" + _attention_card_correction(cached)
            )

    invite_reason = find_ungrounded_invite_claim(final_answer, conversation)
    if invite_reason:
        logger.warning(
            "email agent: appended ungrounded invite-claim correction — %s",
            invite_reason,
        )
        final_answer = append_invite_grounding_correction(final_answer)

    attendee_reason = find_fabricated_attendee_claim(final_answer, conversation)
    if attendee_reason:
        logger.warning(
            "email agent: appended fabricated-attendee correction — %s",
            attendee_reason,
        )
        final_answer = append_attendee_grounding_correction(final_answer)

    result["result"] = final_answer
    return result


__all__ = [
    "UNGROUNDED_SUCCESS_FALLBACK",
    "append_attendee_grounding_correction",
    "append_invite_grounding_correction",
    "decode_stray_unicode_escapes",
    "find_attention_card_contradiction",
    "find_fabricated_attendee_claim",
    "find_scaffolding_leak",
    "find_ungrounded_calendar_conflict_claim",
    "find_ungrounded_invite_claim",
    "find_ungrounded_success_claim",
    "find_unlicensed_cross_mailbox_claim",
    "find_unqualified_negative_claim",
    "ground_final_answer",
    "last_tool_payload",
    "strip_scaffolding_leaks",
    "tools_called_this_turn",
]
