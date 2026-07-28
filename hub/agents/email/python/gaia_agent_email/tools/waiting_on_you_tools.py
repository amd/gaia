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
   - the thread already contains an earlier outbound message from the
     user (a real back-and-forth), or
   - the sender is a known correspondent — someone the user has sent
     mail to before, in any thread.

   "Addressed to the user specifically, not a bulk recipient list" is
   deliberately NOT used as a corroboration path: the adversarial corpus
   entries are crafted as one-to-one, human-named, single-recipient cold
   outreach specifically to look targeted (e.g. corpus ids ``4fae4338``,
   ``567fa520``, ``1677f2af``, ``5f720d3d`` all address exactly one
   recipient by name and contain genuine-looking ask phrasing). Treating
   single-recipient targeting as corroboration would let precisely the
   messages this detector exists to reject qualify.

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
)
from gaia_agent_email.tools.triage_heuristics import _AUTOMATED_SENDER_KEYWORDS
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


def _known_correspondents(
    gmail, *, user_email: str, max_sent: int
) -> Set[str]:
    """Bare addresses the user has sent mail to before, in any thread.

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

    correspondents: Set[str] = set()
    for tid in thread_ids:
        thread = gmail.get_thread(tid)
        for msg in thread.get("messages", []) or []:
            headers = _header_map(msg)
            frm = extract_sender_email(headers.get("from", ""))
            if frm != user_email:
                continue
            for addr in _recipient_addresses(headers.get("to", "")):
                if addr != user_email:
                    correspondents.add(addr)
    return correspondents


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

        known_correspondents = _known_correspondents(
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

            subject = latest_headers.get("subject", "")
            body, _attachments = decode_message_body(latest.get("payload") or {})
            subject_lower = subject.lower()
            body_lower = body.lower()

            if has_direct_ask_signal(subject_lower, body_lower):
                signal = SIGNAL_DIRECT_ASK
            elif _has_meeting_signal(subject, body, subject_lower, body_lower):
                signal = SIGNAL_MEETING_TIME
            else:
                continue

            sender_email = extract_sender_email(sender_raw)
            has_earlier_outbound = any(
                extract_sender_email(_header_map(m).get("from", "")) == user_email
                for m in ordered[:-1]
            )
            if has_earlier_outbound:
                corroboration = CORROBORATION_THREAD_REPLY
            elif sender_email in known_correspondents:
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
            mail routinely use both. This tool requires the corroboration
            above before it will say someone is waiting on you.

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
