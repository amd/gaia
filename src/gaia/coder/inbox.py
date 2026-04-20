# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""EM inbox helpers for ``gaia-coder`` (§4.5).

Thin, stateless CRUD over the ``em_inbox.db`` store from
:mod:`gaia.coder.stores.em_inbox`. This module does not open or own
connections — callers pass an already-open connection in. That keeps the
inbox testable without the file-system layer and makes it easy for the
daemon's heartbeat to keep a single connection hot.

Public surface (mirrors the §4 EM CLI verbs):

* :func:`enqueue` — land a new message (``info`` / ``question`` / ``critical``).
* :func:`auto_ack` — post the 5-second acknowledgement per §4.5. Non-LLM; the
  template is fixed so the ack can never be delayed by a model call.
* :func:`mark_seen` / :func:`mark_answered` / :func:`escalate` — state
  transitions per the §15.1 ``em_inbox`` schema.
* :func:`poll_at_breakpoint` — drain the pending queue at a ReAct-loop
  breakpoint.

Severities and channels are both bounded by CHECK constraints in the SQL
DDL; we raise :class:`InboxError` loudly on mismatch rather than letting
SQLite surface an opaque ``IntegrityError``.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional

from gaia.coder.stores import em_inbox, feedback

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Severities accepted by the ``em_inbox.severity`` CHECK constraint (§15.1).
VALID_SEVERITIES: frozenset[str] = frozenset({"info", "question", "critical"})

#: Channels accepted by the ``em_inbox.channel`` CHECK constraint (§15.1).
VALID_CHANNELS: frozenset[str] = frozenset(
    {"cli", "tui", "gh-comment", "email", "daily-standup-reply"}
)

#: Map from ``em_inbox.severity`` to ``feedback.severity`` per §7.3. The two
#: tables use different ladders (``info|question|critical`` vs
#: ``low|med|high|critical``) on purpose — the inbox surfaces real-time user
#: interaction classes, feedback surfaces work priorities. We translate at
#: escalation time.
_ESCALATE_SEVERITY_MAP: dict[str, str] = {
    "info": "low",
    "question": "med",
    "critical": "critical",
}

#: The 5-second auto-ack template (§4.5 "non-LLM auto-ack"). Single-line so
#: it fits inside a GitHub comment, standup reply, or CLI print on one row.
_AUTO_ACK_TEMPLATE: str = (
    "I see your message; will respond at next breakpoint "
    "(current task ETA: ~{eta_minutes} min)."
)

#: Dispatch callable signature: ``(channel, message) -> None``. Called by
#: :func:`auto_ack` to post the templated acknowledgement back on the
#: channel the message came in on. Implementations talk to CLI stdout, the
#: GitHub API, the EM's email, etc. — this module is channel-agnostic.
Dispatch = Callable[[str, str], None]

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class InboxError(Exception):
    """Inbox invariant violated (bad severity/channel, missing row, etc.)."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    """ISO-8601 UTC timestamp with trailing ``Z`` — matches ``em_inbox`` schema."""
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def _new_id() -> str:
    """Fresh UUIDv4 string for ``em_inbox.id``.

    §15.1 specifies UUIDv7; Python's stdlib doesn't yet ship a v7 generator
    (added in 3.13). We use v4 — collision probability is negligible at our
    scale, and monotonicity-on-timestamp is nice-to-have but not load-bearing
    since every row also stores ``received_at``. A future Phase can swap to
    v7 in one place.
    """
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def enqueue(
    conn: sqlite3.Connection,
    *,
    severity: str,
    body: str,
    from_handle: str,
    channel: str,
    received_at: Optional[str] = None,
) -> str:
    """Insert a new ``em_inbox`` row and return its id.

    Args:
        conn: Open ``em_inbox.db`` connection (from :func:`em_inbox.open_store`).
        severity: ``info`` / ``question`` / ``critical``. Any other value
            raises :class:`InboxError` (faster than letting the CHECK
            constraint surface an ``IntegrityError``).
        body: Free-text message body. Not trimmed or length-capped — the EM
            may paste a long snippet, and silently truncating is worse than
            taking the disk cost.
        from_handle: GitHub handle of the sender. For CLI invocations this
            is usually the bound EM; we do not validate against
            ``em.toml.em_handle`` here because enqueue is the transport
            layer — validation belongs at a higher level.
        channel: One of :data:`VALID_CHANNELS`.
        received_at: Optional override. Defaults to the current UTC time.

    Returns:
        The new row's primary-key id.
    """
    if severity not in VALID_SEVERITIES:
        raise InboxError(
            f"invalid severity {severity!r}; expected one of "
            f"{sorted(VALID_SEVERITIES)}"
        )
    if channel not in VALID_CHANNELS:
        raise InboxError(
            f"invalid channel {channel!r}; expected one of " f"{sorted(VALID_CHANNELS)}"
        )
    if not body or not body.strip():
        raise InboxError("body is required (empty messages have no meaning)")

    row = em_inbox.EmInboxRow(
        id=_new_id(),
        received_at=received_at or _utc_now_iso(),
        from_handle=from_handle,
        channel=channel,
        severity=severity,
        body=body,
    )
    em_inbox.insert_row(conn, row)
    return row.id


def auto_ack(
    conn: sqlite3.Connection,
    msg_id: str,
    eta_minutes: int,
    *,
    dispatch: Optional[Dispatch] = None,
    now: Optional[str] = None,
) -> str:
    """Post the §4.5 5-second acknowledgement for *msg_id*.

    The ack text is rendered from :data:`_AUTO_ACK_TEMPLATE` — no LLM call,
    no conditional logic, just string interpolation. That's the whole point:
    the ack must be cheap enough that even a daemon mid-expensive-turn can
    emit it inside the 5-second SLA.

    Args:
        conn: Open ``em_inbox.db`` connection.
        msg_id: Row to acknowledge. Must exist; missing id raises.
        eta_minutes: Estimated minutes until the agent can answer.
        dispatch: Optional callable ``(channel, text) -> None`` that posts the
            ack back on the original channel. When ``None`` we only update
            the database — useful for tests and for channels that are
            read-only (e.g. replaying an old message).
        now: Override timestamp for the ``ack_sent_at`` column. Mostly for
            tests.

    Returns:
        The ack text that was written/dispatched. Callers that need to log
        or display the exact ack have it without a second DB round-trip.

    Raises:
        InboxError: if *msg_id* does not exist.
    """
    row = em_inbox.get_row(conn, msg_id)
    if row is None:
        raise InboxError(
            f"cannot auto-ack unknown inbox id {msg_id!r}; " "enqueue the message first"
        )

    ack_text = _AUTO_ACK_TEMPLATE.format(eta_minutes=int(eta_minutes))
    em_inbox.update_row(conn, msg_id, {"ack_sent_at": now or _utc_now_iso()})

    if dispatch is not None:
        dispatch(row.channel, ack_text)

    return ack_text


def mark_seen(
    conn: sqlite3.Connection,
    msg_id: str,
) -> None:
    """Transition state ``pending`` → ``seen`` for *msg_id*.

    Idempotent from higher states (``seen`` / ``answered`` / ``escalated`` /
    ``closed``) — calling it on an already-seen row is a no-op. The update is
    a simple patch so re-applying it produces the same row. This matches the
    spec's intent ("the agent reads silently at breakpoint") — there's no
    value in raising when the heartbeat hits the same row twice.
    """
    row = em_inbox.get_row(conn, msg_id)
    if row is None:
        raise InboxError(f"cannot mark-seen unknown inbox id {msg_id!r}")
    if row.state == "pending":
        em_inbox.update_row(conn, msg_id, {"state": "seen"})


def mark_answered(
    conn: sqlite3.Connection,
    msg_id: str,
    answer_text: str,
    *,
    now: Optional[str] = None,
) -> None:
    """Record the agent's answer and transition the row to ``answered``.

    Writes both ``answer`` and ``answered_at`` so the metric in §10.6
    (EM-inbox ack-to-answer latency) can be computed in one SELECT.
    """
    row = em_inbox.get_row(conn, msg_id)
    if row is None:
        raise InboxError(f"cannot mark-answered unknown inbox id {msg_id!r}")
    if not answer_text or not answer_text.strip():
        raise InboxError("answer_text is required (empty answers are not answers)")
    em_inbox.update_row(
        conn,
        msg_id,
        {
            "state": "answered",
            "answer": answer_text,
            "answered_at": now or _utc_now_iso(),
        },
    )


def escalate(
    conn: sqlite3.Connection,
    msg_id: str,
    feedback_conn: sqlite3.Connection,
    *,
    fix_class: Optional[str] = None,
    context_url: Optional[str] = None,
) -> str:
    """Move an inbox row into the §7.3 feedback queue and return the feedback id.

    Creates a new ``feedback`` row copying the inbox row's body, from_handle,
    channel, and severity (translated via :data:`_ESCALATE_SEVERITY_MAP`),
    then updates the inbox row's ``state`` to ``escalated`` and writes the
    new feedback id into ``escalated_to``. Close-out of the inbox row to
    ``closed`` is the caller's decision (the §4.5 table shows ``escalated``
    as a terminal-ish state that may be followed by ``closed`` once the
    feedback record resolves).

    Args:
        conn: Open ``em_inbox.db`` connection.
        msg_id: Inbox row to escalate.
        feedback_conn: Open ``feedback.db`` connection.
        fix_class: Optional pre-classified fix class. When ``None`` the
            feedback row's ``fix_class`` stays NULL — the §7.4 triage loop
            will classify it later.
        context_url: Optional URL (PR / issue / commit) for the feedback row.

    Returns:
        The new feedback row's id.

    Raises:
        InboxError: if *msg_id* does not exist.
    """
    row = em_inbox.get_row(conn, msg_id)
    if row is None:
        raise InboxError(f"cannot escalate unknown inbox id {msg_id!r}")

    feedback_id = _new_id()
    feedback_row = feedback.FeedbackRow(
        id=feedback_id,
        received_at=row.received_at,
        from_handle=row.from_handle,
        channel=row.channel,
        severity=_ESCALATE_SEVERITY_MAP[row.severity],
        body=row.body,
        context_url=context_url,
        fix_class=fix_class,
    )
    feedback.insert_row(feedback_conn, feedback_row)
    em_inbox.update_row(
        conn,
        msg_id,
        {"state": "escalated", "escalated_to": feedback_id},
    )
    return feedback_id


def poll_at_breakpoint(
    conn: sqlite3.Connection,
) -> list[em_inbox.EmInboxRow]:
    """Return pending inbox rows oldest-first for the loop to service.

    Called at natural ReAct-loop breakpoints per §4.5. A breakpoint is:
    between ReAct turns, after ``declare_done``, after a tool call returns,
    before opening a PR, before scheduling a wake-up, before merging an
    auto-mergeable self-fix. Every such site calls this function, reads the
    (usually empty) list, and routes per severity:

    * ``info`` → :func:`mark_seen`
    * ``question`` → answer inline via :func:`mark_answered` or emit a
      "full answer after the current sub-task" note then :func:`mark_seen`
    * ``critical`` → caller pauses the current task and switches context

    The sort order is ``received_at`` ascending so the caller sees the
    oldest pending message first — the opposite of :func:`em_inbox.list_rows`
    which returns newest-first for CLI display.
    """
    rows = em_inbox.list_rows(conn, filter={"state": "pending"})
    rows.sort(key=lambda r: r.received_at)
    return rows


def recent(
    conn: sqlite3.Connection,
    *,
    limit: int = 20,
    states: Optional[Iterable[str]] = None,
) -> list[em_inbox.EmInboxRow]:
    """Return the N most-recent rows matching any of *states* for CLI display.

    Used by ``gaia-coder inbox``. When *states* is ``None`` every state is
    included. ``limit`` is applied in Python because the stores layer's
    :func:`em_inbox.list_rows` does not accept ``LIMIT`` — a slice is
    adequate at the scales this table sees (hundreds of rows, not millions).
    """
    if states is None:
        rows = em_inbox.list_rows(conn, filter=None)
    else:
        allowed = set(states)
        rows = [r for r in em_inbox.list_rows(conn, filter=None) if r.state in allowed]
    return rows[:limit]
