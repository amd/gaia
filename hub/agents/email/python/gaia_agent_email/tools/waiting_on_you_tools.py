# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Waiting-on-you detection tools mixin for ``EmailTriageAgent`` (#2581).

``check_followups`` (#1606) answers "what did I send that nobody answered".
This module answers the opposite, and arguably more urgent, direction:
which INBOUND mail is sitting there waiting on the user's reply or
decision. A colleague asking "did you get a chance to look at this? can we
meet Thursday?" is invisible to the outbound-only detector.

READ-ONLY BY DESIGN: this module detects and reports only. It never
drafts, sends, labels, archives, or otherwise mutates a message.

Precision is the entire difficulty here, not recall. Measured against the
email-triage adversarial corpus (``tests/fixtures/email/vendor_corpus_seed.jsonl``,
104 PROMOTIONAL rows / 42 of them adversarially crafted to defeat naive
heuristics): sender shape is not a usable gate (only 1 of 42 adversarial
rows has an automated-sender keyword — the rest use human-looking sender
names on purpose), and 47 of 104 PROMOTIONAL rows carry a literal ``?``
from a non-automated-looking sender. A detector that fires on "direct
question" or "human sender" alone would flag a large share of marketing
copy as "someone is waiting on you".

Qualification therefore requires BOTH of:

1. A direct-ask signal — genuine ask phrasing (``text_signals.
   has_direct_ask_signal``) or an informal meeting-time proposal
   (``text_signals.has_meeting_time_signal``, or the existing calendar
   heuristic gated on ``is_meeting_request and confidence == "high"`` —
   never confidence alone, since the heuristic returns
   ``confidence="high"`` on its no-signal branch too).
2. Corroboration that this is genuine, ongoing correspondence:
   - the thread already contains earlier outbound message(s) from the
     user (a real back-and-forth), or
   - the sender is a known correspondent — someone the user has
     genuinely corresponded with before, in any thread.

   "Addressed to the user specifically, not a bulk recipient list" is
   deliberately NOT used as a corroboration path: the adversarial corpus
   entries are crafted as one-to-one, human-named, single-recipient cold
   outreach specifically to look targeted (e.g. corpus ids ``4fae4338``,
   ``567fa520``, ``1677f2af``, ``5f720d3d`` all address exactly one
   recipient by name and contain genuine-looking ask phrasing). Treating
   single-recipient targeting as corroboration would let precisely the
   messages this detector exists to reject qualify.

   IMPORTANT (checkpoint fix): "a prior message exists" is NOT itself
   sufficient corroboration — an adversarial verifier proved that a single
   one-line prior contact (an unsubscribe reply, or one unrelated cold
   outreach to the same address) manufactures corroboration for every
   subsequent marketing message from that sender. Real correspondence
   requires either (a) more than one prior exchange, or (b) a single prior
   message with real substance (``text_signals.is_substantive_text`` — a
   floor against one-line dismissals/inquiries like "Please remove me from
   this list." or "what's the pricing?"). A prior message where the user
   told the sender to stop contacting them (``text_signals.
   is_opt_out_reply``) never counts as corroboration and instead suppresses
   that sender from ever qualifying — it is evidence of wanting LESS
   contact, not more.

3. An independent veto: if the existing category heuristic
   (``triage_heuristics.classify_category_heuristic``, unmodified — see
   that module's own docstring) confidently classifies the candidate
   message as PROMOTIONAL, it never qualifies regardless of signal or
   corroboration. Corroboration proves "we've had contact before"; it must
   not be read as "therefore this specific message is personal", which is
   an independent question the category heuristic already answers when it
   is confident.

Threads whose latest message is the user's own (i.e. already replied) are
excluded, as are bulk/automated senders (reusing
``triage_heuristics._AUTOMATED_SENDER_KEYWORDS`` — the existing signal,
not a new list).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Set

from gaia_agent_email.gmail_backend import decode_message_body
from gaia_agent_email.tools.calendar_tools import detect_meeting_request_heuristic
from gaia_agent_email.tools.envelope import _envelope_err, _envelope_ok
from gaia_agent_email.tools.followup_tools import (
    _header_map,
    _recipient_addresses,
    _timestamp_ms,
)
from gaia_agent_email.tools.read_tools import extract_sender_email
from gaia_agent_email.tools.text_signals import (
    has_direct_ask_signal,
    has_meeting_time_signal,
    is_opt_out_reply,
    is_substantive_text,
)
from gaia_agent_email.tools.triage_heuristics import (
    _AUTOMATED_SENDER_KEYWORDS,
    CATEGORY_PROMOTIONAL,
    classify_category_heuristic,
)
from gaia_agent_email.verbose import log_tool_call

from gaia.agents.base.tools import tool
from gaia.connectors.errors import ConnectorsError
from gaia.connectors.formatting import format_connector_error
from gaia.logger import get_logger

log = get_logger(__name__)

# How many INBOX messages one scan enumerates. Each distinct thread costs a
# ``get_thread`` round-trip, so the budget bounds scan latency (mirrors
# ``followup_tools.DEFAULT_MAX_SENT_SCAN``).
DEFAULT_MAX_INBOX_SCAN = 50
MAX_INBOX_SCAN_CAP = 200

# How many SENT messages are scanned to build the "known correspondent" set
# used as one of the two corroboration signals.
DEFAULT_MAX_SENT_LOOKBACK = 50
MAX_SENT_LOOKBACK_CAP = 200

_DAY_MS = 24 * 60 * 60 * 1000

SIGNAL_DIRECT_ASK = "direct_ask"
SIGNAL_MEETING_TIME = "meeting_time"

CORROBORATION_THREAD_REPLY = "thread_reply_history"
CORROBORATION_KNOWN_CORRESPONDENT = "known_correspondent"


def _is_automated_sender(sender: str) -> bool:
    """Reuses the existing automated-sender keyword list — no new list."""
    sender_lower = (sender or "").lower()
    return any(kw in sender_lower for kw in _AUTOMATED_SENDER_KEYWORDS)


def _has_gated_meeting_signal(subject: str, body: str) -> bool:
    """Meeting signal via the existing calendar heuristic, correctly gated.

    ``detect_meeting_request_heuristic`` returns ``confidence="high"`` on
    its no-signal branch too (a confident negative), so gating on
    confidence alone would treat "no meeting signal at all" as a positive.
    Only ``is_meeting_request and confidence == "high"`` counts.
    """
    detection = detect_meeting_request_heuristic(subject, body)
    return bool(detection.is_meeting_request and detection.confidence == "high")


def _has_meeting_signal(subject: str, body: str, subject_lower: str, body_lower: str) -> bool:
    if has_meeting_time_signal(subject_lower, body_lower):
        return True
    return _has_gated_meeting_signal(subject, body)


def _is_genuine_exchange(bodies: List[str]) -> bool:
    """True when a list of prior message bodies is real corroboration.

    A single one-line message ("what's the pricing?", "Please remove me
    from this list.") is not evidence of an ongoing relationship — it is
    exactly what an adversarial verifier used to manufacture corroboration
    for every subsequent marketing message from that address. Two or more
    prior exchanges, OR one message with real substance, is the bar
    (mirrors the plan's checkpoint-fix direction: "more than one, or a
    substantive reply rather than a dismissal").
    """
    if len(bodies) >= 2:
        return True
    if len(bodies) == 1:
        return is_substantive_text(bodies[0])
    return False


def _known_correspondents(
    gmail, *, user_email: str, max_sent: int
) -> tuple:
    """Addresses the user has genuinely corresponded with before.

    Returns ``(bodies_by_address, opted_out)``:

    - ``bodies_by_address``: address -> list of non-opt-out message bodies
      the user sent to it (any thread). Callers apply
      ``_is_genuine_exchange`` — a single one-line prior contact is not
      itself sufficient corroboration (checkpoint fix).
    - ``opted_out``: addresses the user has told, in any scanned message,
      to stop contacting them. These must never qualify regardless of any
      other signal, in this call or a later one against the same address.

    One bounded SENT-folder scan (mirrors ``check_followups_impl``'s scan
    shape) so this stays a cheap, single-purpose corroboration signal —
    not a per-candidate lookup.
    """
    listing = gmail.list_messages(label_ids=["SENT"], max_results=max_sent)
    stubs = listing.get("messages", [])
    thread_ids: List[str] = []
    for stub in stubs:
        tid = stub.get("threadId")
        if tid and tid not in thread_ids:
            thread_ids.append(tid)

    bodies_by_address: Dict[str, List[str]] = {}
    opted_out: Set[str] = set()
    for tid in thread_ids:
        thread = gmail.get_thread(tid)
        for msg in thread.get("messages", []) or []:
            headers = _header_map(msg)
            frm = extract_sender_email(headers.get("from", ""))
            if frm != user_email:
                continue
            body, _attachments = decode_message_body(msg.get("payload") or {})
            body_lower = body.lower()
            recipients = [
                a for a in _recipient_addresses(headers.get("to", "")) if a != user_email
            ]
            if is_opt_out_reply(body_lower):
                opted_out.update(recipients)
                continue
            for addr in recipients:
                bodies_by_address.setdefault(addr, []).append(body)
    return bodies_by_address, opted_out


def detect_waiting_on_you_impl(
    gmail,
    *,
    max_inbox: int = DEFAULT_MAX_INBOX_SCAN,
    max_sent_lookback: int = DEFAULT_MAX_SENT_LOOKBACK,
    min_age_hours: int = 0,
    now_ms: Optional[int] = None,
    debug: bool = False,
) -> Dict[str, Any]:
    """Scan the inbox for messages awaiting the user's reply.

    Read-only: calls only ``get_user_email`` / ``list_messages`` /
    ``get_thread`` on the backend.

    Args:
        gmail: Any ``GmailBackend`` (live Gmail, live Outlook, or a fake).
        max_inbox: INBOX-folder enumeration budget (each distinct thread
            costs one ``get_thread`` round-trip).
        max_sent_lookback: SENT-folder scan budget used only to build the
            known-correspondent corroboration set.
        min_age_hours: Skip messages younger than this (0 = no minimum).
        now_ms: Injectable "now" in epoch milliseconds (tests); defaults to
            the current time.
        debug: Verbose tool-call logging.

    Returns::

        {
            "waiting_on_you": [
                {"message_id", "thread_id", "sender", "subject",
                 "received_at", "age_days", "signal", "corroboration"},
                ...  # oldest (most overdue) first
            ],
            "inbox_scanned": int,
            "scan_truncated": bool,
        }

    ``scan_truncated`` is True when the INBOX listing hit the ``max_inbox``
    ceiling (or the backend reports another page) — older inbound messages
    exist beyond what this scan looked at.
    """
    if min_age_hours < 0:
        raise ValueError(
            f"detect_waiting_on_you min_age_hours must be >= 0, got {min_age_hours!r}"
        )
    with log_tool_call(
        "detect_waiting_on_you",
        {"max_inbox": max_inbox, "max_sent_lookback": max_sent_lookback},
        debug=debug,
    ) as st:
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        user_email = (gmail.get_user_email() or "").strip().lower()
        if not user_email:
            raise ValueError(
                "the mail backend returned no user email address; cannot "
                "distinguish inbound mail from the user's own replies"
            )

        known_correspondent_bodies, opted_out_senders = _known_correspondents(
            gmail, user_email=user_email, max_sent=max_sent_lookback
        )

        listing = gmail.list_messages(label_ids=["INBOX"], max_results=max_inbox)
        stubs = listing.get("messages", [])
        scan_truncated = bool(listing.get("nextPageToken")) or len(stubs) >= max_inbox
        thread_ids: List[str] = []
        for stub in stubs:
            tid = stub.get("threadId")
            if not tid:
                raise ValueError(
                    f"Inbox listing returned message {stub.get('id')!r} without "
                    "a threadId; cannot group it into a conversation"
                )
            if tid not in thread_ids:
                thread_ids.append(tid)

        min_age_ms = min_age_hours * 60 * 60 * 1000
        flagged: List[tuple] = []
        for tid in thread_ids:
            thread = gmail.get_thread(tid)
            messages = thread.get("messages", []) or []
            if not messages:
                raise ValueError(
                    f"thread {tid!r} from the Inbox listing came back empty; "
                    "the mail backend is inconsistent"
                )
            ordered = sorted(messages, key=_timestamp_ms)
            latest = ordered[-1]
            latest_headers = _header_map(latest)
            latest_from = extract_sender_email(latest_headers.get("from", ""))
            if latest_from == user_email:
                # The user's own message is the latest in the thread — the
                # ball is back in the other person's court, not ours.
                continue

            sender_raw = latest_headers.get("from", "")
            if _is_automated_sender(sender_raw):
                continue

            sender_email = extract_sender_email(sender_raw)
            if sender_email in opted_out_senders:
                # The user has already told this address to stop contacting
                # them — evidence of wanting LESS contact, never
                # corroboration for MORE. Suppressed unconditionally.
                continue

            subject = latest_headers.get("subject", "")
            body, _attachments = decode_message_body(latest.get("payload") or {})
            subject_lower = subject.lower()
            body_lower = body.lower()

            # Independent veto: an existing, unmodified classifier that
            # confidently calls this message PROMOTIONAL means prior contact
            # cannot turn it into a personal ask — that is a separate
            # question from "have we corresponded before".
            category_verdict = classify_category_heuristic(
                subject=subject,
                sender=sender_raw,
                label_ids=latest.get("labelIds", []),
                body=body,
            )
            if (
                category_verdict.category == CATEGORY_PROMOTIONAL
                and category_verdict.confident
            ):
                continue

            if has_direct_ask_signal(subject_lower, body_lower):
                signal = SIGNAL_DIRECT_ASK
            elif _has_meeting_signal(subject, body, subject_lower, body_lower):
                signal = SIGNAL_MEETING_TIME
            else:
                continue

            # Corroboration: a prior message existing is not enough on its
            # own (checkpoint fix) — either the thread shows real depth
            # (>=2 prior messages) or a single prior outbound is itself
            # substantive; same bar applies to cross-thread correspondence.
            prior_messages = ordered[:-1]
            prior_outbound_bodies: List[str] = []
            thread_has_opt_out = False
            for m in prior_messages:
                m_headers = _header_map(m)
                if extract_sender_email(m_headers.get("from", "")) != user_email:
                    continue
                m_body, _ = decode_message_body(m.get("payload") or {})
                if is_opt_out_reply(m_body.lower()):
                    thread_has_opt_out = True
                    continue
                prior_outbound_bodies.append(m_body)

            if thread_has_opt_out:
                # The user asked THIS sender to stop, in this very thread —
                # suppress regardless of everything else.
                continue

            has_earlier_outbound = bool(prior_outbound_bodies) and (
                len(prior_messages) >= 2 or _is_genuine_exchange(prior_outbound_bodies)
            )
            if has_earlier_outbound:
                corroboration = CORROBORATION_THREAD_REPLY
            elif _is_genuine_exchange(known_correspondent_bodies.get(sender_email, [])):
                corroboration = CORROBORATION_KNOWN_CORRESPONDENT
            else:
                # No corroboration that this is genuine correspondence —
                # sender shape / phrasing alone is not enough (see module
                # docstring). Skip rather than qualify a cold-outreach guess.
                continue

            sent_ms = _timestamp_ms(latest)
            age_ms = now_ms - sent_ms
            if age_ms < min_age_ms:
                continue

            flagged.append(
                (
                    age_ms,
                    {
                        "message_id": latest.get("id"),
                        "thread_id": tid,
                        "sender": sender_email,
                        "subject": subject,
                        "received_at": latest_headers.get("date", ""),
                        "age_days": int(age_ms // _DAY_MS),
                        "signal": signal,
                        "corroboration": corroboration,
                    },
                )
            )

        flagged.sort(key=lambda pair: pair[0], reverse=True)  # most overdue first
        waiting = [item for _, item in flagged]
        st["result_summary"] = {
            "waiting": len(waiting),
            "threads_checked": len(thread_ids),
            "scan_truncated": scan_truncated,
        }
        return {
            "waiting_on_you": waiting,
            "inbox_scanned": len(stubs),
            "scan_truncated": scan_truncated,
        }


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------


class WaitingOnYouToolsMixin:
    """Mixin that registers the waiting-on-you detection tool.

    State-free at construction time — relies on the agent having set
    ``self._backends``, ``self.config``, and the
    ``_remember_message_mailbox`` provenance helper before
    ``self._register_waiting_on_you_tools()`` runs.
    """

    def _register_waiting_on_you_tools(self) -> None:
        debug_flag = bool(getattr(self.config, "debug", False))
        agent = self  # captured for live access to backends + provenance map

        @tool
        def list_waiting_on_you(
            max_inbox: int = 0, min_age_hours: int = 0
        ) -> str:
            """Flag inbound mail that is waiting on the user's reply.

            Scans the inbox of every connected mailbox for messages that
            ask directly for a reply, decision, or meeting time, AND show
            corroboration that they are genuine correspondence (an
            existing back-and-forth in the thread, or a sender the user
            has emailed before). Use this when the user asks what they
            haven't gotten to, who's waiting on them, or what needs a
            reply.

            A bare question mark or a human-looking sender name is NOT
            enough to qualify a message here — marketing and cold-outreach
            mail routinely use both. Nor is a single one-line prior
            contact (an old unsubscribe reply, a one-off cold outreach) —
            real corroboration needs more than one exchange, or one
            genuinely substantive message. A message an existing category
            heuristic confidently calls promotional never qualifies
            regardless of any of the above, and a sender the user has
            told to stop contacting them is suppressed unconditionally.

            READ-ONLY: this tool only detects and reports. It never
            drafts, sends, labels, or archives anything.

            Args:
                max_inbox: How many INBOX messages to scan per mailbox
                    (0 uses the default of 50, max 200).
                min_age_hours: Skip messages younger than this many hours
                    (0 = no minimum).

            Returns:
                JSON envelope with ``{"waiting_on_you": [...]}`` — per
                item: message_id, thread_id, sender, subject, received_at,
                age_days, signal (``"direct_ask"`` or ``"meeting_time"``),
                corroboration (``"thread_reply_history"`` or
                ``"known_correspondent"``), and mailbox — sorted most
                overdue first, plus ``inbox_scanned`` and
                ``scan_truncated``. ``scan_truncated`` is true when the
                INBOX scan hit its ceiling in any connected mailbox — older
                inbound messages may exist beyond what was scanned.
            """
            try:
                inbox_budget = max(
                    1, min(int(max_inbox or DEFAULT_MAX_INBOX_SCAN), MAX_INBOX_SCAN_CAP)
                )
                merged: List[Dict[str, Any]] = []
                inbox_scanned = 0
                scan_truncated = False
                for provider, backend in agent._backends.items():
                    result = detect_waiting_on_you_impl(
                        backend,
                        max_inbox=inbox_budget,
                        min_age_hours=int(min_age_hours or 0),
                        debug=debug_flag,
                    )
                    for item in result["waiting_on_you"]:
                        item["mailbox"] = provider
                        agent._remember_message_mailbox(
                            item.get("message_id"), provider
                        )
                        agent._remember_message_mailbox(item.get("thread_id"), provider)
                        merged.append(item)
                    inbox_scanned += result["inbox_scanned"]
                    scan_truncated = scan_truncated or result["scan_truncated"]
                merged.sort(key=lambda item: item["age_days"], reverse=True)
                return _envelope_ok(
                    {
                        "waiting_on_you": merged,
                        "inbox_scanned": inbox_scanned,
                        "scan_truncated": scan_truncated,
                    }
                )
            except ValueError as exc:
                return _envelope_err(str(exc))
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")
