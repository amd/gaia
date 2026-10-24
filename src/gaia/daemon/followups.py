# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Follow-ups an agent schedules for itself, fired by the daemon clock.

"Check on this job in two hours" becomes a one-shot job on the daemon's single
clock (docs/plans/autonomy-engine.mdx §5). When it comes due, the daemon asks
the agent to do the task in a fresh run, keeps the answer, and posts a desktop
notification; the agent's next turn on any front end then says what came back.

One-shot only, at most :data:`MAX_PENDING` waiting and :data:`MAX_DELAY_SECONDS`
ahead: an agent that wants to keep checking schedules its next follow-up when
this one fires, so a misjudging agent cannot wind up a runaway clock. The fired
run has no one to approve a gated tool, so those are refused, as in any
unattended run.
"""

from __future__ import annotations

import json
import math
import re
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from gaia.daemon.scheduler import store
from gaia.daemon.scheduler.models import STATUS_PENDING
from gaia.logger import get_logger

log = get_logger(__name__)

KIND = "agent_follow_up"
SOURCE = "agent"
MIN_DELAY_SECONDS = 60
MAX_DELAY_SECONDS = 7 * 24 * 3600
MAX_PENDING = 10
MAX_TASK_CHARS = 2000

RESULTS_DDL = """
CREATE TABLE IF NOT EXISTS daemon_follow_up_results (
    job_id      TEXT PRIMARY KEY,
    agent_id    TEXT NOT NULL,
    task        TEXT NOT NULL,
    answer      TEXT,
    error       TEXT,
    note        TEXT,
    finished_at REAL NOT NULL,
    seen_at     REAL
);
"""


class FollowUpError(ValueError):
    """A follow-up that cannot be scheduled as asked, with what to do instead."""


def init_schema(db) -> None:
    store.init_schema(db)
    db.execute(RESULTS_DDL)


# -- When -----------------------------------------------------------------

_UNITS = {
    "minute": 60,
    "min": 60,
    "hour": 3600,
    "hr": 3600,
    "day": 86400,
    "week": 7 * 86400,
}
_IN = re.compile(r"^in\s+(\d+|an?|one)\s+(minute|min|hour|hr|day|week)s?$")
_AT = re.compile(
    r"^(?:(today|tomorrow)\s+)?(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?"
    r"(?:\s+(today|tomorrow))?$"
)


def parse_when(text: str, now: Optional[datetime] = None) -> datetime:
    """The local time *text* names: "in 2 hours", "tomorrow at 9am", "at 16:30",
    or an ISO 8601 timestamp. Raises :class:`FollowUpError` when it names none,
    or a time outside :data:`MIN_DELAY_SECONDS`..:data:`MAX_DELAY_SECONDS`.
    """
    now = now or datetime.now().astimezone()
    phrase = " ".join((text or "").strip().lower().split())
    when = _parse(phrase, now)
    if when is None:
        raise FollowUpError(
            f"Can't tell when {text!r} is. Use a phrase like 'in 2 hours', "
            "'tomorrow at 9am', 'at 16:30', or an ISO time like "
            "'2026-10-24T09:00'."
        )
    delay = (when - now).total_seconds()
    if delay < MIN_DELAY_SECONDS:
        raise FollowUpError(
            f"{text!r} is {'in the past' if delay < 0 else 'under a minute away'}"
            "; a follow-up needs to be at least a minute ahead."
        )
    if delay > MAX_DELAY_SECONDS:
        raise FollowUpError(
            f"{text!r} is more than 7 days away. Schedule a nearer follow-up and "
            "set the next one when it fires, or ask the user to add a scheduled "
            "task for something recurring."
        )
    return when


def _parse(phrase: str, now: datetime) -> Optional[datetime]:
    if not phrase:
        return None
    m = _IN.match(phrase)
    if m:
        n = 1 if m.group(1) in ("a", "an", "one") else int(m.group(1))
        return now + timedelta(seconds=n * _UNITS[m.group(2)])
    m = _AT.match(phrase)
    if m and (m.group(1) or m.group(5) or phrase.startswith("at ") or m.group(4)):
        hour, minute = int(m.group(2)), int(m.group(3) or 0)
        meridiem = m.group(4)
        if meridiem:
            if not 1 <= hour <= 12:
                return None
            hour = hour % 12 + (12 if meridiem == "pm" else 0)
        if hour > 23 or minute > 59:
            return None
        day = m.group(1) or m.group(5)
        when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if day == "tomorrow":
            when += timedelta(days=1)
        elif day is None and when <= now:
            when += timedelta(days=1)
        return when
    try:
        when = datetime.fromisoformat(phrase.upper().replace(" ", "T"))
    except ValueError:
        return None
    return when if when.tzinfo else when.astimezone()


# -- The jobs ---------------------------------------------------------------


def create(
    db,
    *,
    agent_id: str,
    task: str,
    fire_at: float,
    origin: Optional[Dict[str, Any]] = None,
    model: Optional[str] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Register a follow-up; raises :class:`FollowUpError` over a limit."""
    task = (task or "").strip()
    if not task:
        raise FollowUpError("A follow-up needs a task saying what to check or do.")
    if len(task) > MAX_TASK_CHARS:
        raise FollowUpError(
            f"The task is {len(task)} characters; keep it under {MAX_TASK_CHARS}."
        )
    now = time.time() if now is None else now
    if not MIN_DELAY_SECONDS <= fire_at - now <= MAX_DELAY_SECONDS:
        raise FollowUpError("A follow-up must be between a minute and 7 days ahead.")
    waiting = pending(db, agent_id=agent_id)
    if len(waiting) >= MAX_PENDING:
        raise FollowUpError(
            f"{MAX_PENDING} follow-ups are already waiting. Cancel one with "
            "cancel_follow_up before scheduling another."
        )
    payload = {
        "agent_id": agent_id,
        "task": task,
        "created_at": now,
        "origin": origin or {},
        # The chat's model, so a cloud user's follow-up does not load a local one.
        "model": model,
    }
    job_id = store.register_job(
        db,
        source=SOURCE,
        kind=KIND,
        payload=payload,
        source_job_id=uuid.uuid4().hex,
        fire_at=fire_at,
    )
    return _view(store.get_job(db, job_id=job_id))


def pending(db, *, agent_id: str) -> List[Dict[str, Any]]:
    return [
        _view(j)
        for j in store.list_jobs(db, status=STATUS_PENDING)
        if j["kind"] == KIND and j["payload"].get("agent_id") == agent_id
    ]


def cancel(db, *, agent_id: str, job_id: str) -> bool:
    """Cancel a waiting follow-up of *agent_id*; False when none is waiting."""
    job = store.get_job(db, job_id=job_id)
    if (
        job is None
        or job["kind"] != KIND
        or job["status"] != STATUS_PENDING
        or job["payload"].get("agent_id") != agent_id
    ):
        return False
    return store.cancel_job(db, job_id=job_id)


def unseen_results(db, *, agent_id: str) -> List[Dict[str, Any]]:
    rows = db.query(
        "SELECT * FROM daemon_follow_up_results WHERE agent_id = :a "
        "AND seen_at IS NULL ORDER BY finished_at",
        {"a": agent_id},
    )
    return [dict(r) for r in rows or ()]


def mark_seen(
    db, *, agent_id: str, job_ids: List[str], now: Optional[float] = None
) -> int:
    seen = 0
    for job_id in job_ids:
        seen += db.update(
            "daemon_follow_up_results",
            {"seen_at": time.time() if now is None else now},
            "job_id = :id AND agent_id = :a AND seen_at IS NULL",
            {"id": job_id, "a": agent_id},
        )
    return seen


def _view(job: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": job["job_id"],
        "task": job["payload"].get("task", ""),
        "fire_at": job["fire_at"],
        "status": job["status"],
        "created_at": job["payload"].get("created_at"),
    }


# -- Firing ---------------------------------------------------------------

#: ``(agent_id, prompt, model) -> answer``. Raises when the agent gave no answer.
AskAgent = Callable[[str, str, Optional[str]], str]
#: ``(title, text) -> None``. Raises when the notification could not be posted.
Notify = Callable[[str, str], None]


def prompt_for(payload: Dict[str, Any], now: Optional[float] = None) -> str:
    created = datetime.fromtimestamp(payload.get("created_at") or time.time())
    current = datetime.fromtimestamp(time.time() if now is None else now)
    return (
        f"This is a follow-up you scheduled at {created:%a %b %d %H:%M}; it is "
        f"now {current:%a %b %d %H:%M}. The task you left yourself:\n\n"
        f"{payload.get('task', '')}\n\n"
        "Do it now and report the result to the user in a few sentences. Nobody "
        "is watching this run, so every tool that needs the user's approval is "
        "refused: shell commands, running code, writing files. Use the tools that "
        "need none: search for files, read them, list folders, fetch web pages. "
        "If the task cannot be done without approval, say what the user should "
        "run instead. If it is still not finished, you may schedule another "
        "follow-up."
    )


def make_executor(
    ask_agent: AskAgent, notify: Notify
) -> Callable[[Dict[str, Any], Any], None]:
    """The clock executor for :data:`KIND`: run the task, keep the answer, notify."""

    def execute(job: Dict[str, Any], db) -> None:
        payload = job["payload"]
        agent_id = payload["agent_id"]
        task = payload.get("task", "")
        try:
            answer = ask_agent(agent_id, prompt_for(payload), payload.get("model"))
        except Exception as e:
            # Kept so the next turn says the follow-up failed; the clock marks it too.
            _record(db, job, agent_id, task, answer=None, error=str(e), note=None)
            raise
        note = None
        try:
            notify("GAIA follow-up", _first_line(answer) or task)
        except Exception as e:  # pylint: disable=broad-except
            # The answer is kept and shown on the next turn; say why no banner came.
            note = f"desktop notification failed: {e}"
            log.warning("follow-up %s: %s", job["job_id"], note)
        _record(db, job, agent_id, task, answer=answer, error=None, note=note)

    return execute


def _record(db, job, agent_id, task, *, answer, error, note) -> None:
    # A recovered stale lease can run a job twice; its newest result wins.
    db.delete("daemon_follow_up_results", "job_id = :id", {"id": job["job_id"]})
    db.insert(
        "daemon_follow_up_results",
        {
            "job_id": job["job_id"],
            "agent_id": agent_id,
            "task": task,
            "answer": answer,
            "error": error,
            "note": note,
            "finished_at": time.time(),
            "seen_at": None,
        },
    )


def _first_line(text: str, limit: int = 200) -> str:
    line = next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")
    return line if len(line) <= limit else line[: limit - 1] + "…"


def ask_sidecar(registry, *, read_timeout: float) -> AskAgent:
    """Ask *agent_id*'s sidecar one question over its own ``/query`` stream."""
    import requests

    from gaia.daemon.agent_query import _iter_sse_events

    def ask(agent_id: str, prompt: str, model: Optional[str]) -> str:
        registry.ensure(agent_id)
        base_url, token = registry.connection(agent_id)
        body: Dict[str, Any] = {
            "query": prompt,
            "run_id": str(uuid.uuid4()),
            "context": [],
        }
        if model:
            body["model"] = model
        with requests.post(
            f"{base_url}/v1/{agent_id}/query",
            headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
            json=body,
            stream=True,
            timeout=(10, None if math.isinf(read_timeout) else read_timeout),
        ) as response:
            if response.status_code != 200:
                raise RuntimeError(
                    f"the {agent_id} agent refused the follow-up "
                    f"(HTTP {response.status_code}): {response.text[:300]}"
                )
            for event in _iter_sse_events(response):
                if event.get("type") == "final":
                    answer = (event.get("answer") or "").strip()
                    if not answer:
                        raise RuntimeError(f"the {agent_id} agent gave an empty answer")
                    return answer
                if event.get("type") == "error":
                    raise RuntimeError(
                        f"the {agent_id} agent failed: {event.get('message') or json.dumps(event)[:300]}"
                    )
        raise RuntimeError(f"the {agent_id} agent's stream ended without an answer")

    return ask


def desktop_notify(title: str, text: str) -> None:
    from gaia.schedule import sinks

    sinks.dispatch("notification", {"title": title}, text)
