# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Follow-up tracking — flag sent mail still awaiting a reply (#1606).

Read-only DETECTION: scan the Sent folder, group by thread, and flag every
thread whose newest message is still the user's own once it is older than a
configurable window. Distinct from #555 (autonomous follow-up *sending*):
this module never drafts, schedules, or dispatches a nudge — it imports no
reply/send code path at all, a property the unit tests assert.

Gmail-only for now: the Microsoft Graph backend has no SENT label mapping
(``list_messages`` serves the *inbox* folder for unrecognized labels), so
scanning it would silently return wrong results. Per the no-silent-fallback
rule the tool refuses a Microsoft-only setup with a loud error instead.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from gaia_agent_email.tools.read_tools import extract_sender_email
from gaia_agent_email.verbose import log_tool_call

from gaia.agents.base.tools import tool
from gaia.connectors.errors import ConnectorsError
from gaia.connectors.formatting import format_connector_error
from gaia.logger import get_logger

log = get_logger(__name__)

_MS_PER_DAY = 86_400_000

# Per-call ceiling on Sent-folder stubs enumerated per backend. Bounds an
# interactive call the same way DEFAULT_INBOX_SCAN_CEILING bounds triage.
DEFAULT_SENT_SCAN_CEILING = 100


def _envelope_ok(data: Any) -> str:
    return json.dumps({"ok": True, "data": data}, default=str)


def _envelope_err(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


def _headers_of(msg: Dict[str, Any]) -> Dict[str, str]:
    return {
        (h.get("name") or "").lower(): h.get("value", "")
        for h in (msg.get("payload") or {}).get("headers", [])
    }


def _internal_date_ms(msg: Dict[str, Any], *, where: str) -> int:
    """Millis-since-epoch of a message; loud error when unusable.

    A message without a parseable ``internalDate`` cannot be aged, and
    guessing (e.g. treating it as 0) would mis-flag decade-old threads.
    """
    raw = msg.get("internalDate")
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError(
            f"message {msg.get('id')!r} in {where} has no usable "
            f"internalDate ({raw!r}); cannot compute reply age. The backend "
            "must return Gmail API v1 message shapes."
        ) from None


def find_awaiting_reply_impl(
    gmail,
    *,
    window_days: int,
    max_threads: int = 50,
    now_ms: Optional[int] = None,
    debug: bool = False,
) -> Dict[str, Any]:
    """Scan the Sent folder and return threads still awaiting a reply.

    A thread is *awaiting reply* when its chronologically newest message was
    sent by the user (no inbound message followed the user's last send) and
    that send is at least ``window_days`` old. Read-only: only
    ``get_user_email`` / ``list_messages`` / ``get_thread`` are called.

    Args:
        gmail:        Gmail backend (real or fake).
        window_days:  Minimum age in days before a sent message is flagged.
        max_threads:  Cap on distinct sent threads inspected per call.
        now_ms:       Clock override (millis since epoch) for deterministic
                      tests; ``None`` uses the real time.
        debug:        Pass-through to ``log_tool_call``.

    Returns:
        ``{"window_days", "threads_scanned", "count", "awaiting_reply":
        [{"message_id", "thread_id", "recipient", "subject", "sent_date",
        "age_days"}]}`` — most overdue first.
    """
    if window_days < 0:
        raise ValueError(f"window_days must be >= 0 (got {window_days})")
    me = (gmail.get_user_email() or "").strip().lower()
    if not me:
        raise ValueError(
            "backend returned an empty user email address; cannot tell the "
            "user's own sends apart from inbound replies. Reconnect the "
            "mailbox (see `gaia connectors`)."
        )
    if now_ms is None:
        now_ms = int(time.time() * 1000)

    with log_tool_call(
        "find_awaiting_reply",
        {"window_days": window_days, "max_threads": max_threads},
        debug=debug,
    ) as st:
        listing = gmail.list_messages(
            label_ids=["SENT"], max_results=DEFAULT_SENT_SCAN_CEILING
        )
        thread_ids: List[str] = []
        seen: set[str] = set()
        for stub in listing.get("messages", []):
            tid = stub.get("threadId")
            if tid and tid not in seen:
                seen.add(tid)
                thread_ids.append(tid)

        awaiting: List[Dict[str, Any]] = []
        scanned = 0
        for tid in thread_ids[:max_threads]:
            scanned += 1
            thread = gmail.get_thread(tid)
            messages = thread.get("messages", [])
            if not messages:
                raise ValueError(
                    f"thread {tid!r} from the Sent listing came back with no "
                    "messages; the backend thread view is inconsistent."
                )
            entries = sorted(
                (
                    (
                        _internal_date_ms(m, where=f"thread {tid!r}"),
                        extract_sender_email(_headers_of(m).get("from", "")) == me,
                        m,
                    )
                    for m in messages
                ),
                key=lambda e: e[0],
            )
            last_ms, last_from_me, last_msg = entries[-1]
            if not last_from_me:
                continue  # newest message is inbound — the thread was answered
            age_days = (now_ms - last_ms) / _MS_PER_DAY
            if age_days < window_days:
                continue
            headers = _headers_of(last_msg)
            awaiting.append(
                {
                    "message_id": last_msg.get("id"),
                    "thread_id": tid,
                    "recipient": headers.get("to", ""),
                    "subject": headers.get("subject", ""),
                    "sent_date": headers.get("date", ""),
                    "age_days": round(age_days, 1),
                }
            )

        awaiting.sort(key=lambda item: item["age_days"], reverse=True)
        st["result_summary"] = {"count": len(awaiting), "threads_scanned": scanned}
        return {
            "window_days": window_days,
            "threads_scanned": scanned,
            "count": len(awaiting),
            "awaiting_reply": awaiting,
        }


class FollowupToolsMixin:
    """Mixin that registers the read-only follow-up tracking tool.

    State-free at construction time — relies on the agent class having set
    ``self._gmail``, ``self._backends``, ``self.config``, and
    ``_remember_message_mailbox`` before ``self._register_followup_tools()``.
    """

    def _register_followup_tools(self) -> None:
        debug_flag = bool(getattr(self.config, "debug", False))
        agent = self  # captured for live access to config / backends

        @tool
        def find_awaiting_reply(window_days: int = 0, max_threads: int = 50) -> str:
            """Flag sent messages that never got a reply (read-only detection).

            Scans the Sent folder and returns every thread whose newest
            message is still the user's own — nobody replied — once the sent
            message is older than the window. Use when the user asks "who
            hasn't replied to me?", "what am I still waiting on?", or wants
            to chase overdue responses. Detection ONLY: this never drafts or
            dispatches a nudge; any actual follow-up email goes through the
            normal confirmation-gated reply tools at the user's request.

            Args:
                window_days: Minimum age in days before a sent message counts
                    as awaiting a reply. 0 (the default) uses the configured
                    ``followup_window_days`` (3 unless overridden).
                max_threads: Cap on how many sent threads to inspect
                    (default 50, max 100).

            Returns:
                JSON envelope with ``{"awaiting_reply": [{message_id,
                thread_id, recipient, subject, sent_date, age_days,
                mailbox}], "count", "window_days", "threads_scanned"}`` —
                most overdue first.
            """
            try:
                window = int(window_days or 0)
                if window <= 0:
                    window = int(getattr(agent.config, "followup_window_days", 3))
                max_threads = max(1, min(int(max_threads or 50), 100))
                backends = agent._backends
                google_backends = {
                    provider: backend
                    for provider, backend in backends.items()
                    if provider == "google"
                }
                if not google_backends:
                    return _envelope_err(
                        "follow-up tracking currently supports Gmail only — no "
                        "Google mailbox is connected. Connect one via "
                        "`gaia connectors`."
                    )
                merged: List[Dict[str, Any]] = []
                scanned = 0
                for provider, backend in google_backends.items():
                    result = find_awaiting_reply_impl(
                        backend,
                        window_days=window,
                        max_threads=max_threads,
                        debug=debug_flag,
                    )
                    scanned += result["threads_scanned"]
                    for item in result["awaiting_reply"]:
                        item["mailbox"] = provider
                        agent._remember_message_mailbox(
                            item.get("message_id"), provider
                        )
                        agent._remember_message_mailbox(
                            item.get("thread_id"), provider
                        )
                        merged.append(item)
                merged.sort(key=lambda item: item["age_days"], reverse=True)
                data: Dict[str, Any] = {
                    "window_days": window,
                    "threads_scanned": scanned,
                    "count": len(merged),
                    "awaiting_reply": merged,
                }
                skipped = sorted(set(backends) - set(google_backends))
                if skipped:
                    data["skipped_mailboxes"] = {
                        provider: "follow-up tracking is Gmail-only for now"
                        for provider in skipped
                    }
                return _envelope_ok(data)
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")
