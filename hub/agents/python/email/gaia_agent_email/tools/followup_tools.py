# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Follow-up tracking tools mixin for ``EmailTriageAgent`` (#1606).

Tools: ``check_followups`` — scan the Sent folder and flag threads where the
latest message is still the user's own outbound mail (no inbound reply) past
a configurable window. The dropped thread is the inbox's biggest silent
failure mode; this surfaces it.

READ-ONLY BY DESIGN: this module detects and reports only. It never drafts,
never transmits mail, and never mutates a message — distinct from #555
(autonomous follow-up scheduling). ``tests/test_email_followups.py`` locks
this in both dynamically (backend call log) and statically (module source).

Detection semantics (per thread reached from a Sent-folder scan):

- The thread is *answered* when its latest message is inbound (``From`` is
  not the user). An inbound message that predates the user's last send does
  NOT count — only a reply AFTER the send suppresses the flag.
- The thread is *awaiting reply* when its latest message is outbound and its
  age is at least ``window_days``.
- Self-addressed-only mail (note-to-self) is skipped: no inbound reply can
  ever arrive, so flagging it would be permanent noise.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from email.utils import getaddresses
from typing import Any, Dict, List, Optional

from gaia_agent_email.tools.read_tools import (
    _envelope_err,
    _envelope_ok,
    extract_sender_email,
)
from gaia_agent_email.verbose import log_tool_call

from gaia.agents.base.tools import tool
from gaia.connectors.errors import ConnectorsError
from gaia.connectors.formatting import format_connector_error
from gaia.logger import get_logger

log = get_logger(__name__)

# Days without an inbound reply before a sent message is flagged, when the
# caller passes no explicit window (mirrors ``EmailAgentConfig.followup_window_days``).
DEFAULT_FOLLOWUP_WINDOW_DAYS = 3

# How many Sent-folder messages one scan enumerates. Each distinct thread
# costs a ``get_thread`` round-trip, so the budget bounds scan latency.
DEFAULT_MAX_SENT_SCAN = 50
MAX_SENT_SCAN_CAP = 200

_DAY_MS = 24 * 60 * 60 * 1000


def _timestamp_ms(msg: Dict[str, Any]) -> int:
    """Return a message's ``internalDate`` as epoch milliseconds.

    Gmail returns epoch-millis strings; the Outlook translation carries the
    Graph ISO-8601 ``receivedDateTime``. Anything else raises — an age
    computed from a guessed timestamp would silently mis-flag threads.
    """
    raw = msg.get("internalDate")
    if raw in (None, ""):
        raise ValueError(
            f"message {msg.get('id')!r} has no internalDate; cannot compute "
            "its follow-up age"
        )
    text = str(raw)
    try:
        return int(text)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"message {msg.get('id')!r} internalDate {raw!r} is neither epoch "
            "milliseconds nor ISO-8601"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _header_map(msg: Dict[str, Any]) -> Dict[str, str]:
    return {
        (h.get("name") or "").lower(): h.get("value", "")
        for h in (msg.get("payload") or {}).get("headers", [])
    }


def _recipient_addresses(to_header: str) -> List[str]:
    """Bare, lowercased addresses from a ``To`` header, order-preserving.

    ``getaddresses`` (not a naive comma split) so a quoted display name
    containing a comma ('"Doe, John" <j@x.com>') doesn't shed a garbage
    recipient.
    """
    out: List[str] = []
    for _name, addr in getaddresses([to_header or ""]):
        addr = addr.strip().lower()
        if addr and addr not in out:
            out.append(addr)
    return out


def check_followups_impl(
    gmail,
    *,
    window_days: int,
    max_sent: int = DEFAULT_MAX_SENT_SCAN,
    now_ms: Optional[int] = None,
    debug: bool = False,
) -> Dict[str, Any]:
    """Scan the Sent folder for threads still awaiting an inbound reply.

    Read-only: calls only ``get_user_email`` / ``list_messages`` /
    ``get_thread`` on the backend.

    Args:
        gmail: Any ``GmailBackend`` (live Gmail, live Outlook, or a fake).
        window_days: Flag a thread once the user's latest outbound message is
            at least this many days old. Must be positive.
        max_sent: Sent-folder enumeration budget (each distinct thread costs
            one ``get_thread`` call).
        now_ms: Injectable "now" in epoch milliseconds (tests); defaults to
            the current time.
        debug: Verbose tool-call logging.

    Returns::

        {
            "awaiting_reply": [
                {"message_id", "thread_id", "recipient", "recipients",
                 "subject", "sent_at", "age_days"},
                ...  # most overdue first
            ],
            "window_days": int,
            "sent_scanned": int,
            "scan_truncated": bool,
        }

    ``scan_truncated`` is True when the Sent-folder listing hit the
    ``max_sent`` ceiling (or the backend reports another page via
    ``nextPageToken``) — i.e. older sent messages exist that this scan never
    looked at, so the result may be missing overdue threads.
    """
    if not isinstance(window_days, int) or window_days <= 0:
        raise ValueError(
            f"check_followups window_days must be a positive number of days, "
            f"got {window_days!r}"
        )
    with log_tool_call(
        "check_followups",
        {"window_days": window_days, "max_sent": max_sent},
        debug=debug,
    ) as st:
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        user_email = (gmail.get_user_email() or "").strip().lower()
        if not user_email:
            raise ValueError(
                "the mail backend returned no user email address; cannot "
                "distinguish outbound mail from inbound replies"
            )

        listing = gmail.list_messages(label_ids=["SENT"], max_results=max_sent)
        stubs = listing.get("messages", [])
        # The listing is newest-first, so hitting the cap means the OLDEST
        # (most overdue) sends are the ones left unscanned — never let that
        # read as an exhaustive answer.
        scan_truncated = bool(listing.get("nextPageToken")) or len(stubs) >= max_sent
        thread_ids: List[str] = []
        for stub in stubs:
            tid = stub.get("threadId")
            if not tid:
                raise ValueError(
                    f"Sent-folder listing returned message {stub.get('id')!r} "
                    "without a threadId; cannot group it into a conversation"
                )
            if tid not in thread_ids:
                thread_ids.append(tid)

        flagged: List[tuple[int, Dict[str, Any]]] = []
        for tid in thread_ids:
            thread = gmail.get_thread(tid)
            messages = thread.get("messages", []) or []
            if not messages:
                raise ValueError(
                    f"thread {tid!r} from the Sent-folder listing came back "
                    "empty; the mail backend is inconsistent"
                )
            ordered = sorted(messages, key=_timestamp_ms)
            latest = ordered[-1]
            latest_from = extract_sender_email(_header_map(latest).get("from", ""))
            if latest_from != user_email:
                # Latest message is inbound — the thread has been answered.
                continue
            sent_ms = _timestamp_ms(latest)
            age_ms = now_ms - sent_ms
            if age_ms < window_days * _DAY_MS:
                continue
            headers = _header_map(latest)
            recipients = [
                a for a in _recipient_addresses(headers.get("to", "")) if a != user_email
            ]
            if not recipients:
                # Note-to-self (or no recipient) — an inbound reply can never
                # arrive, so a flag would be permanent noise.
                continue
            flagged.append(
                (
                    sent_ms,
                    {
                        "message_id": latest.get("id"),
                        "thread_id": tid,
                        "recipient": recipients[0],
                        "recipients": recipients,
                        "subject": headers.get("subject", ""),
                        "sent_at": headers.get("date", ""),
                        "age_days": int(age_ms // _DAY_MS),
                    },
                )
            )

        flagged.sort(key=lambda pair: pair[0])  # oldest send = most overdue first
        awaiting = [item for _, item in flagged]
        st["result_summary"] = {
            "awaiting": len(awaiting),
            "threads_checked": len(thread_ids),
            "scan_truncated": scan_truncated,
        }
        return {
            "awaiting_reply": awaiting,
            "window_days": window_days,
            "sent_scanned": len(stubs),
            "scan_truncated": scan_truncated,
        }


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------


class FollowupToolsMixin:
    """Mixin that registers the follow-up tracking tool.

    State-free at construction time (Critical CA-1) — relies on the agent
    having set ``self._backends``, ``self.config``, and the
    ``_remember_message_mailbox`` provenance helper before
    ``self._register_followup_tools()`` runs.
    """

    def _register_followup_tools(self) -> None:
        debug_flag = bool(getattr(self.config, "debug", False))
        agent = self  # captured for live access to backends + provenance map

        @tool
        def check_followups(window_days: int = 0, max_sent: int = 50) -> str:
            """Flag sent mail still awaiting a reply (follow-up tracking).

            Scans the Sent folder of every connected mailbox and returns the
            threads whose latest message is still the user's own outbound
            mail — i.e. nobody has replied — older than the window. Use this
            when the user asks what they're waiting on, which emails went
            unanswered, or who they should chase.

            READ-ONLY: this tool only detects and reports. It never composes
            or transmits a follow-up nudge — if the user wants to chase a
            thread, draft a reply as a separate, confirmed action.

            Args:
                window_days: Minimum age (days) of the unanswered message
                    before it is flagged. 0 (default) uses the configured
                    default (3 days).
                max_sent: How many Sent messages to scan per mailbox
                    (default 50, max 200).

            Returns:
                JSON envelope with ``{"awaiting_reply": [...]}`` — per item:
                message_id, thread_id, recipient, recipients, subject,
                sent_at, age_days, mailbox — sorted most overdue first, plus
                ``window_days``, ``sent_scanned``, and ``scan_truncated``.
                ``scan_truncated`` is true when the Sent-folder scan hit its
                ``max_sent`` ceiling in any connected mailbox — older sent
                threads exist beyond what was scanned, so tell the user the
                list may be incomplete.
            """
            try:
                effective_window = int(window_days or 0)
                if effective_window == 0:
                    effective_window = int(
                        getattr(
                            agent.config,
                            "followup_window_days",
                            DEFAULT_FOLLOWUP_WINDOW_DAYS,
                        )
                    )
                max_sent_budget = max(
                    1, min(int(max_sent or DEFAULT_MAX_SENT_SCAN), MAX_SENT_SCAN_CAP)
                )
                merged: List[Dict[str, Any]] = []
                sent_scanned = 0
                scan_truncated = False
                for provider, backend in agent._backends.items():
                    result = check_followups_impl(
                        backend,
                        window_days=effective_window,
                        max_sent=max_sent_budget,
                        debug=debug_flag,
                    )
                    for item in result["awaiting_reply"]:
                        item["mailbox"] = provider
                        agent._remember_message_mailbox(
                            item.get("message_id"), provider
                        )
                        agent._remember_message_mailbox(item.get("thread_id"), provider)
                        merged.append(item)
                    sent_scanned += result["sent_scanned"]
                    scan_truncated = scan_truncated or result["scan_truncated"]
                merged.sort(key=lambda item: item["age_days"], reverse=True)
                return _envelope_ok(
                    {
                        "awaiting_reply": merged,
                        "window_days": effective_window,
                        "sent_scanned": sent_scanned,
                        "scan_truncated": scan_truncated,
                    }
                )
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")
