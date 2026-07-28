# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Scheduled send + snooze tools (#1609).

``schedule_send`` is declared in the email agent's
``CONFIRMATION_REQUIRED_TOOLS`` (#1440) — the user confirms the LITERAL
``to``/``subject``/``body`` and
the fire time AT CREATION (#1264); the send then fires unattended at/after
that time. The body is persisted as a backend draft (visible in the user's
mail client), never in SQLite — the job row carries only the draft_id and a
subject line, mirroring the ``action_store`` body-preview privacy rule.

``snooze_message`` composes archive-now + re-surface-later: the message
leaves INBOX immediately and a one-shot job re-adds it (via
``unarchive_message`` with the prior label set) at the chosen time. Snooze is
organize-tier — reversible, no per-action confirmation.

Ordering invariant (Adversarial B2): backend call FIRST, job row only on
success — a job for an action that never happened is a state-corruption
class. Cancelling is a loud operation: cancelling a job that already fired
(or doesn't exist) is an error, never a silent no-op.

Relative-time resolution (#2526): ``send_at``/``until`` accept common
relative phrases ("tomorrow morning", "next monday", "in 3 hours", "this
evening") resolved agent-side by :func:`_resolve_relative_time` BEFORE
``_parse_future_ts`` ever sees them — the agent must own turning natural
language into a timestamp, not hand that problem back to the user in chat.
Resolution is anchored to "local time", which here means exactly what naive
ISO-8601 timestamps already meant in this module: the OS ``TZ`` of the
machine/process the agent runs on (``datetime.now()`` / ``time.time()``),
never UTC and never a per-user profile setting — there is no such setting in
this codebase today. A phrase that resolves to neither a known relative
pattern nor strict ISO-8601 fails with an actionable error that proposes a
concrete, always-future time (tomorrow 09:00 local) instead of demanding
ISO-8601 from a person in a chat window.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from gaia_agent_email.tools.envelope import _envelope_err, _envelope_ok
from gaia_agent_email import action_store, schedule_store
from gaia_agent_email.verbose import log_tool_call

from gaia.agents.base.tools import tool
from gaia.connectors.errors import ConnectorsError
from gaia.connectors.formatting import format_connector_error
from gaia.logger import get_logger

log = get_logger(__name__)


# Default clock times for a bare time-of-day word, 24h local.
_TIME_OF_DAY = {
    "morning": (9, 0),
    "noon": (12, 0),
    "afternoon": (14, 0),
    "evening": (18, 0),
    "night": (20, 0),
    "midnight": (0, 0),
}

_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_RELATIVE_UNIT_SECONDS = {
    "second": 1,
    "sec": 1,
    "minute": 60,
    "min": 60,
    "hour": 3600,
    "hr": 3600,
    "day": 86400,
}

# Small ordinals/positions so "cancel the second one" can refer to the
# most recently shown listing without the user knowing a raw job id (#2526).
_POSITION_WORDS = {
    "first": 1,
    "1st": 1,
    "second": 2,
    "2nd": 2,
    "third": 3,
    "3rd": 3,
    "fourth": 4,
    "4th": 4,
    "fifth": 5,
    "5th": 5,
}


def _at_time_of_day(base: datetime, hour: int, minute: int = 0) -> datetime:
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _parse_clock(
    hour_str: str, minute_str: Optional[str], meridiem: Optional[str]
) -> Tuple[int, int]:
    hour = int(hour_str)
    minute = int(minute_str) if minute_str else 0
    if meridiem:
        meridiem = meridiem.lower()
        if meridiem == "pm" and hour != 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
    # Bare hour, no am/pm: treat as the common casual-speech default (a
    # morning/daytime hour), e.g. "tomorrow at 7" -> 07:00, not 19:00.
    return hour % 24, minute


def _resolve_relative_time(raw: str, now: datetime) -> Optional[datetime]:
    """Resolve a relative-time phrase into a concrete local ``datetime``.

    Returns ``None`` when ``raw`` matches no known pattern — the caller then
    falls back to strict ISO-8601 parsing. ``now`` must be the SAME local
    wall-clock convention the rest of this module uses for naive timestamps
    (machine/process ``TZ``); callers pass an injected ``now`` in tests so
    resolution is deterministic, never dependent on the real clock.
    """
    text = raw.strip().lower()
    if not text:
        return None

    m = re.fullmatch(
        r"in\s+(\d+)\s*(second|sec|minute|min|hour|hr|day)s?", text
    )
    if m:
        amount = int(m.group(1))
        unit_seconds = _RELATIVE_UNIT_SECONDS[m.group(2)]
        return now + timedelta(seconds=amount * unit_seconds)

    m = re.fullmatch(
        r"(tomorrow|today|tonight)"
        r"(?:\s+(morning|afternoon|evening|night|noon|midnight))?",
        text,
    )
    if m:
        day_word, period = m.group(1), m.group(2)
        base = now + timedelta(days=1) if day_word == "tomorrow" else now
        if day_word == "tonight":
            period = period or "night"
        hour, minute = _TIME_OF_DAY.get(period or "morning", (9, 0))
        return _at_time_of_day(base, hour, minute)

    m = re.fullmatch(
        r"(tomorrow|today)\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text
    )
    if m:
        day_word, hour_str, minute_str, meridiem = m.groups()
        base = now + timedelta(days=1) if day_word == "tomorrow" else now
        hour, minute = _parse_clock(hour_str, minute_str, meridiem)
        return _at_time_of_day(base, hour, minute)

    m = re.fullmatch(r"this\s+(morning|afternoon|evening|night)", text)
    if m:
        hour, minute = _TIME_OF_DAY[m.group(1)]
        candidate = _at_time_of_day(now, hour, minute)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    m = re.fullmatch(
        r"next\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
        r"(?:\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?)?",
        text,
    )
    if m:
        weekday_name, hour_str, minute_str, meridiem = m.groups()
        target_weekday = _WEEKDAYS[weekday_name]
        days_ahead = (target_weekday - now.weekday()) % 7
        # "next X" means a week out even said on X itself, not "later today".
        days_ahead = days_ahead or 7
        base = now + timedelta(days=days_ahead)
        if hour_str:
            hour, minute = _parse_clock(hour_str, minute_str, meridiem)
        else:
            hour, minute = _TIME_OF_DAY["morning"]
        return _at_time_of_day(base, hour, minute)

    return None


def _propose_time(now: datetime) -> datetime:
    """A concrete, always-future fallback time: tomorrow 09:00 local.

    Never a stale/past example — computed off the injected/actual ``now``,
    not a hardcoded literal (the observed defect suggested a timestamp that
    was already in the past by the time the user read it).
    """
    return _at_time_of_day(now + timedelta(days=1), 9, 0)


def _parse_future_ts(value: str, *, now: Optional[float] = None) -> float:
    """Resolve a relative phrase or ISO-8601 timestamp into epoch seconds;
    must be in the future.

    Tries :func:`_resolve_relative_time` first ("tomorrow morning", "next
    monday", "in 3 hours", ...); falls back to strict ISO-8601. Naive
    timestamps (relative or ISO) are interpreted in the machine's local
    timezone (the scheduler compares against local ``time.time()``). Raises
    ``ValueError`` with an actionable message — proposing a concrete future
    time — on bad format or a non-future time.
    """
    raw = (value or "").strip()
    now_s = time.time() if now is None else now
    now_dt = datetime.fromtimestamp(now_s)
    proposal = _propose_time(now_dt).isoformat(timespec="minutes")

    if not raw:
        raise ValueError(
            "no time given. Try a phrase like 'tomorrow morning' or 'in 3 "
            "hours', or an ISO-8601 timestamp, e.g. "
            f"'{proposal}'."
        )

    resolved = _resolve_relative_time(raw, now_dt)
    if resolved is not None:
        ts = resolved.timestamp()
    else:
        iso = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            raise ValueError(
                f"couldn't work out a time from {value!r}. Try a phrase "
                "like 'tomorrow morning', 'next monday', 'in 3 hours', or "
                f"'this evening' — or an exact ISO-8601 timestamp like "
                f"'{proposal}'. If tomorrow at 09:00 ({proposal}) works, "
                "just say so and I'll use that."
            ) from None
        ts = dt.timestamp()  # naive -> local time; aware -> exact instant

    if ts <= now_s:
        raise ValueError(
            f"{value!r} is not in the future (it is at or before now). "
            "Pick a later time, or use send_now / leave the message in "
            "INBOX instead."
        )
    return ts


def _iso_local(ts: float) -> str:
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Pure impls
# ---------------------------------------------------------------------------


def schedule_send_impl(
    gmail,
    db,
    *,
    to: str,
    subject: str,
    body: str,
    send_at: str,
    mailbox: Optional[str] = None,
    now: Optional[float] = None,
    debug: bool = False,
) -> Dict[str, Any]:
    """Create a backend draft now; schedule a one-shot job to send it at T.

    The draft-then-send composition (reusing ``send_draft`` at fire time)
    keeps the full body out of SQLite and leaves the pending send visible in
    the user's own mail client, matching how mail providers surface their
    native schedule-send.
    """
    with log_tool_call(
        "schedule_send",
        {"to": to, "subject": subject, "send_at": send_at},
        debug=debug,
    ) as st:
        due_at = _parse_future_ts(send_at, now=now)
        # Backend call first (ordering invariant): draft in the mailbox,
        # then the audit + job rows.
        draft = gmail.create_draft(to=to, subject=subject, body=body)
        draft_id = draft["id"]
        action_store.record_draft(
            db, draft_id=draft_id, to=to, subject=subject, body=body
        )
        job_id = schedule_store.create_job(
            db,
            kind=schedule_store.KIND_SCHEDULED_SEND,
            due_at=due_at,
            payload={"draft_id": draft_id, "to": to, "subject": subject},
            mailbox=mailbox,
        )
        st["result_summary"] = {"job_id": job_id, "due_at": due_at}
        return {
            "job_id": job_id,
            "draft_id": draft_id,
            "to": to,
            "subject": subject,
            "send_at": _iso_local(due_at),
            "cancellable_via": "cancel_scheduled_job",
        }


def snooze_message_impl(
    gmail,
    db,
    *,
    message_id: str,
    until: str,
    mailbox: Optional[str] = None,
    now: Optional[float] = None,
    debug: bool = False,
) -> Dict[str, Any]:
    """Remove a message from INBOX now; schedule its return at ``until``."""
    with log_tool_call(
        "snooze_message",
        {"message_id": message_id, "until": until},
        debug=debug,
    ) as st:
        due_at = _parse_future_ts(until, now=now)
        prior = gmail.get_message(message_id)
        prior_labels = list(prior.get("labelIds", []))
        if "INBOX" not in prior_labels:
            raise ValueError(
                f"message {message_id!r} is not in INBOX — nothing to snooze. "
                "Snooze only applies to inbox messages."
            )
        # Backend call first (ordering invariant), then the job row.
        result = gmail.archive_message(message_id)
        # Folder-based backends (Outlook) return a new id on the move;
        # label-based backends (Gmail) keep the same id.
        post_archive_id = (result or {}).get("id") or message_id
        try:
            job_id = schedule_store.create_job(
                db,
                kind=schedule_store.KIND_SNOOZE,
                due_at=due_at,
                payload={
                    "message_id": message_id,
                    "post_archive_id": post_archive_id,
                    "prior_labels": prior_labels,
                },
                mailbox=mailbox,
            )
        except Exception as exc:
            # Unlike a scheduled send (whose orphan is a harmless draft), an
            # archived message with no re-surface job silently vanishes from
            # INBOX. Roll the archive back before failing; if that also
            # fails, say exactly what state the message is in.
            try:
                gmail.unarchive_message(post_archive_id, prior_labels)
            except Exception as undo_exc:
                raise RuntimeError(
                    f"snooze failed to persist its re-surface job "
                    f"({type(exc).__name__}: {exc}) AND the rollback "
                    f"un-archive also failed ({type(undo_exc).__name__}: "
                    f"{undo_exc}) — message {message_id!r} is archived with "
                    "no scheduled return. Restore it via your mail client "
                    "or restore_message."
                ) from exc
            raise RuntimeError(
                f"snooze failed to persist its re-surface job "
                f"({type(exc).__name__}: {exc}); the message was restored "
                "to INBOX — nothing is scheduled."
            ) from exc
        st["result_summary"] = {"job_id": job_id, "due_at": due_at}
        return {
            "job_id": job_id,
            "message_id": message_id,
            "returns_at": _iso_local(due_at),
            "cancellable_via": "cancel_scheduled_job",
        }


def _resolve_cancel_target(db, ref: str) -> str:
    """Resolve a cancel target: an exact job_id, or a 1-based position /
    ordinal ("2", "second") into the CURRENT pending listing — the same
    due_at order ``list_scheduled_jobs`` shows (#2526). A user who just saw
    a listing has no way to know the raw job id; letting them say "cancel
    the second one" avoids demanding it.
    """
    text = (ref or "").strip()
    position: Optional[int] = None
    if text.isdigit() and len(text) <= 3:
        position = int(text)
    elif text.lower() in _POSITION_WORDS:
        position = _POSITION_WORDS[text.lower()]
    if position is None:
        return text
    pending = schedule_store.list_jobs(db, status=schedule_store.STATUS_PENDING)
    if 1 <= position <= len(pending):
        return pending[position - 1]["job_id"]
    raise ValueError(
        f"there is no job at position {position} in the current pending "
        f"list ({len(pending)} pending). Call list_scheduled_jobs to see "
        "current jobs and their ids/positions."
    )


def cancel_scheduled_job_impl(db, *, job_id: str, debug: bool = False) -> Dict[str, Any]:
    """Cancel a pending job. ``job_id`` may be an exact id OR a 1-based
    position/ordinal from the most recent listing. Loud on anything not
    cancellable."""
    with log_tool_call("cancel_scheduled_job", {"job_id": job_id}, debug=debug) as st:
        resolved_id = _resolve_cancel_target(db, job_id)
        if schedule_store.cancel_job(db, job_id=resolved_id):
            st["result_summary"] = {"cancelled": resolved_id}
            return {"job_id": resolved_id, "cancelled": True}
        job = schedule_store.get_job(db, job_id=resolved_id)
        if job is None:
            raise ValueError(
                f"no scheduled job with id {resolved_id!r}. Use "
                "list_scheduled_jobs to see pending jobs."
            )
        raise ValueError(
            f"job {resolved_id!r} is {job['status']} and can no longer be "
            "cancelled — only pending jobs can be."
        )


def list_scheduled_jobs_impl(db, *, debug: bool = False) -> Dict[str, Any]:
    """List pending one-shot jobs with their cancel handles."""
    with log_tool_call("list_scheduled_jobs", {}, debug=debug) as st:
        jobs = []
        for job in schedule_store.list_jobs(db, status=schedule_store.STATUS_PENDING):
            entry: Dict[str, Any] = {
                "job_id": job["job_id"],
                "kind": job["kind"],
                "due_at": _iso_local(job["due_at"]),
                "mailbox": job["mailbox"],
            }
            if job["kind"] == schedule_store.KIND_SCHEDULED_SEND:
                entry["to"] = job["payload"].get("to")
                entry["subject"] = job["payload"].get("subject")
            else:
                entry["message_id"] = job["payload"].get("message_id")
            jobs.append(entry)
        st["result_summary"] = {"pending": len(jobs)}
        return {"pending": jobs, "count": len(jobs)}


# ---------------------------------------------------------------------------
# Fire-time executors (run by EmailJobScheduler, off the tool loop)
# ---------------------------------------------------------------------------


def execute_scheduled_send_impl(gmail, db, *, job: Dict[str, Any]) -> None:
    """Fire a scheduled send: send the draft created at schedule time.

    Reuses ``send_draft_impl`` so the audit row is marked sent exactly like
    an interactive send. Any backend failure propagates — the scheduler marks
    the job failed with the error; a send failure is never swallowed.
    """
    from gaia_agent_email.tools.reply_tools import send_draft_impl

    draft_id = job["payload"].get("draft_id")
    if not draft_id:
        raise ValueError(
            f"scheduled_send job {job['job_id']!r} has no draft_id in its "
            "payload — cannot send"
        )
    send_draft_impl(gmail, db, draft_id=draft_id)


def execute_snooze_impl(gmail, *, job: Dict[str, Any]) -> None:
    """Fire a snooze: re-surface the message into INBOX.

    Uses ``unarchive_message`` with the prior label set — the same
    provider-correct restore the undo path uses (Gmail re-adds INBOX;
    Outlook moves the message back via the post-archive id).
    """
    payload = job["payload"]
    restore_id = payload.get("post_archive_id") or payload.get("message_id")
    if not restore_id:
        raise ValueError(
            f"snooze job {job['job_id']!r} has no message id in its payload — "
            "cannot re-surface"
        )
    gmail.unarchive_message(restore_id, list(payload.get("prior_labels") or []))


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------


class ScheduleToolsMixin:
    def _register_schedule_tools(self) -> None:
        db = self
        agent = self
        debug_flag = bool(getattr(self.config, "debug", False))

        @tool
        def schedule_send(
            to: str, subject: str, body: str, send_at: str, mailbox: str = ""
        ) -> str:
            """Schedule an email to be sent at a future time. Requires user
            confirmation at creation; the send then fires unattended.

            ``send_at`` accepts a relative phrase in the user's own words —
            'tomorrow morning', 'next monday', 'in 3 hours', 'this evening',
            'tomorrow at 7' — or an ISO-8601 timestamp (e.g.
            '2026-07-02T09:00'); either way it must resolve to a future
            local time. Resolve the phrase yourself; never ask the user for
            ISO-8601. ``mailbox`` (optional) chooses which account sends
            when multiple are connected. Cancellable before it fires via
            cancel_scheduled_job.
            """
            try:
                backend = agent._send_backend(mailbox or None)
                provider = agent._provider_for_backend(backend)
                return _envelope_ok(
                    schedule_send_impl(
                        backend,
                        db,
                        to=to,
                        subject=subject,
                        body=body,
                        send_at=send_at,
                        mailbox=provider,
                        debug=debug_flag,
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def snooze_message(message_id: str, until: str, mailbox: str = "") -> str:
            """Snooze a message: remove it from INBOX now and bring it back at
            a future time.

            ``until`` accepts a relative phrase in the user's own words —
            'tomorrow morning', 'next monday', 'in 3 hours', 'this evening',
            'tomorrow at 7' — or an ISO-8601 timestamp (e.g.
            '2026-07-02T09:00'); either way it must resolve to a future
            local time. Resolve the phrase yourself; never ask the user for
            ISO-8601. ``mailbox`` (optional) routes when multiple mailboxes
            are connected. Cancellable before it fires via
            cancel_scheduled_job (cancelling keeps the message archived).
            """
            try:
                provider = agent._provider_for_message(message_id, mailbox or None)
                return _envelope_ok(
                    snooze_message_impl(
                        agent._backends[provider],
                        db,
                        message_id=message_id,
                        until=until,
                        mailbox=provider,
                        debug=debug_flag,
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def cancel_scheduled_job(job_id: str) -> str:
            """Cancel a pending scheduled send or snooze before it fires.

            ``job_id`` may be the exact job id, OR a 1-based position/ordinal
            ('2', 'second') from the most recently shown
            list_scheduled_jobs listing — the user rarely knows the raw id.
            Cancelling a scheduled send leaves the draft in the mailbox;
            cancelling a snooze leaves the message archived.
            """
            try:
                return _envelope_ok(
                    cancel_scheduled_job_impl(db, job_id=job_id, debug=debug_flag)
                )
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def list_scheduled_jobs() -> str:
            """List pending scheduled sends and snoozes with their job ids
            (the cancel handles) and fire times."""
            try:
                return _envelope_ok(list_scheduled_jobs_impl(db, debug=debug_flag))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

    # -- Fire-time executors (EmailJobScheduler registry) -------------------

    def _schedule_backend_for_job(self, job: Dict[str, Any]):
        """Resolve the backend a job fires against, failing loud when the
        recorded mailbox is no longer connected."""
        provider = job.get("mailbox")
        if provider is None:
            if len(self._backends) == 1:
                return next(iter(self._backends.values()))
            raise ValueError(
                f"scheduled job {job['job_id']!r} has no mailbox recorded and "
                f"multiple mailboxes are connected ({', '.join(self._backends)})"
            )
        backend = self._backends.get(provider)
        if backend is None:
            raise ValueError(
                f"scheduled job {job['job_id']!r} targets mailbox {provider!r}, "
                f"which is not connected. Connected: "
                f"{', '.join(self._backends) or 'none'}."
            )
        return backend

    def _execute_scheduled_send(self, job: Dict[str, Any], db: Any) -> None:
        # ``db`` is the scheduler's own per-pass connection — the audit write
        # must NOT go through the agent's connection from the polling thread.
        execute_scheduled_send_impl(self._schedule_backend_for_job(job), db, job=job)

    def _execute_snooze_restore(self, job: Dict[str, Any], db: Any) -> None:
        del db  # re-surfacing is a pure backend call; no store write needed
        execute_snooze_impl(self._schedule_backend_for_job(job), job=job)
