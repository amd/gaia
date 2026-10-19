"""Long-running scheduler daemon.

Reads the schedule store, arms an APScheduler cron trigger per enabled schedule,
and blocks until interrupted. Each trigger fires :func:`runner.fire`.
"""

from __future__ import annotations

import signal
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from apscheduler.events import (
    EVENT_JOB_MAX_INSTANCES,
    EVENT_JOB_MISSED,
    EVENT_JOB_SUBMITTED,
    JobExecutionEvent,
    JobSubmissionEvent,
)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from gaia.logger import get_logger
from gaia.schedule import runner
from gaia.schedule.lock import daemon_lock
from gaia.schedule.store import (
    DEFAULT_STORE_PATH,
    MissedRuns,
    Schedule,
    ScheduleStore,
    TomlScheduleStore,
)

log = get_logger(__name__)
STORE_REFRESH_SECONDS = 1.0
# A run that comes due while the machine sleeps still fires if the daemon wakes
# within this window; older runs are skipped (and logged) rather than replayed.
MISFIRE_GRACE_SECONDS = 3600
JOB_DEFAULTS = {
    "misfire_grace_time": MISFIRE_GRACE_SECONDS,
    "coalesce": True,
    # One run at a time per schedule; an overlapping fire is skipped and logged.
    "max_instances": 1,
}


def _job(schedule: Schedule, store: ScheduleStore) -> None:
    # A failing job must not kill the daemon, but it must be loud (no silent
    # swallow): log with full traceback and keep the other schedules alive.
    try:
        current = store.load().get(schedule.name)
        if current is None or not current.enabled or current.cron != schedule.cron:
            log.debug("skipping %r: store changed since it was armed", schedule.name)
            return
        schedule = current
        runner.fire(schedule)
    except Exception:
        log.exception("schedule %r failed", schedule.name)
        return
    store.mark_run(
        schedule.name,
        datetime.now(timezone.utc).isoformat(),
        next_run=next_fire_time(schedule.cron),
    )


def _log_skipped_run(event: JobExecutionEvent | JobSubmissionEvent) -> None:
    if isinstance(event, JobExecutionEvent):
        log.warning(
            "schedule %r missed its run due at %s: "
            "the machine was asleep or busy for more than %ds past that time",
            event.job_id,
            event.scheduled_run_time,
            MISFIRE_GRACE_SECONDS,
        )
        return
    log.warning(
        "schedule %r skipped its run due at %s: the previous run is still "
        "in progress",
        event.job_id,
        ", ".join(str(t) for t in event.scheduled_run_times),
    )


#: Most due times counted for one missed stretch: an every-minute schedule
#: off for 69 days. Past it the count stops, and the log says so.
_MAX_MISSED_COUNTED = 100_000


def _utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _parse(schedule: Schedule, field: str) -> Optional[datetime]:
    stamp = getattr(schedule, field)
    if not stamp:
        return None
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError as e:
        raise ValueError(
            f"schedule {schedule.name!r} has an unreadable {field} {stamp!r} in "
            "the schedule store; fix or delete that line in schedules.toml"
        ) from e
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _missed_runs(
    schedule: Schedule, until: datetime, *, including_until: bool
) -> List[datetime]:
    """Due times of *schedule* before *until* that nothing ran.

    Counted from the run the daemon last expected (``next_run``), and never
    from before the schedule was created, last ran, was last resumed, or
    began counting misses.
    """
    trigger = CronTrigger.from_crontab(schedule.cron)
    floor = max(
        moment
        for moment in (
            _parse(schedule, "created_at"),
            _parse(schedule, "last_run"),
            _parse(schedule, "resumed_at"),
            _parse(schedule, "missed_since"),
        )
        if moment is not None
    )
    expected = _parse(schedule, "next_run")
    fire = (
        trigger.get_next_fire_time(None, expected)
        if expected is not None and expected > floor
        else trigger.get_next_fire_time(floor, floor)
    )
    missed: List[datetime] = []
    while fire is not None and len(missed) < _MAX_MISSED_COUNTED:
        if fire > until or (fire == until and not including_until):
            break
        if fire > floor:
            missed.append(fire)
        fire = trigger.get_next_fire_time(fire, fire)
    if len(missed) >= _MAX_MISSED_COUNTED:
        log.warning(
            "schedule %r: stopped counting missed runs at %d; more came due "
            "before %s",
            schedule.name,
            _MAX_MISSED_COUNTED,
            _utc(until),
        )
    return missed


def _account(
    store: ScheduleStore,
    name: str,
    until: datetime,
    *,
    including_until: bool,
) -> None:
    """Record what *name* missed before *until*; expect its next run after it."""

    def decide(schedule: Schedule) -> Optional[MissedRuns]:
        if not schedule.enabled:
            return None
        trigger = CronTrigger.from_crontab(schedule.cron)
        following = trigger.get_next_fire_time(until, until)
        next_run = _utc(following) if following else None
        if schedule.missed_since is None:
            # Saved before misses were counted: start counting from now.
            return MissedRuns([], next_run, since=_utc(until))
        missed = _missed_runs(schedule, until, including_until=including_until)
        if missed:
            log.warning(
                "schedule %r missed %d run(s), the latest due at %s: the PC "
                "was off or asleep, or the schedule daemon was not running",
                name,
                len(missed),
                _utc(missed[-1]),
            )
        return MissedRuns([_utc(due) for due in missed], next_run)

    store.account_missed(name, decide)


def catch_up_missed_runs(store: ScheduleStore, now: datetime) -> None:
    """At start: record the runs that came due while the daemon was not
    running. They are not run late; `gaia schedule show` lists them."""
    for name, schedule in store.load().items():
        if schedule.enabled:
            _account(store, name, now, including_until=True)


def _account_for_submission(store: ScheduleStore, event: JobSubmissionEvent) -> None:
    """Record the runs a long sleep skipped or folded into this submission.

    APScheduler submits the latest due run and drops the ones before it
    without an event. The latest one is dropped too when it is past the
    misfire grace; the executor then sends EVENT_JOB_MISSED from another
    thread, so it is decided here instead, once.
    """
    latest = max(event.scheduled_run_times)
    too_late = (datetime.now(timezone.utc) - latest).total_seconds() > (
        MISFIRE_GRACE_SECONDS
    )
    _account(store, event.job_id, latest, including_until=too_late)


def build_scheduler(store: ScheduleStore) -> BackgroundScheduler:
    """Create a scheduler with one cron job per enabled schedule.

    Runs that came due while the daemon was not running are recorded as
    missed first, and the runs a sleep skips are recorded as they happen.
    """
    scheduler = BackgroundScheduler(job_defaults=JOB_DEFAULTS)
    scheduler.add_listener(_log_skipped_run, EVENT_JOB_MISSED | EVENT_JOB_MAX_INSTANCES)
    scheduler.add_listener(
        lambda event: _account_for_submission(store, event), EVENT_JOB_SUBMITTED
    )
    refresh_schedules(scheduler, store)
    catch_up_missed_runs(store, datetime.now(timezone.utc))
    log.info("armed %d schedule(s)", len(scheduler.get_jobs()))
    return scheduler


def refresh_schedules(scheduler: BackgroundScheduler, store: ScheduleStore) -> None:
    """Reconcile running timers with the schedules currently persisted on disk."""
    schedules = store.load()
    enabled = {
        name: schedule for name, schedule in schedules.items() if schedule.enabled
    }
    # Validate all triggers before changing timers, so a bad edit fails loudly.
    triggers = {
        name: CronTrigger.from_crontab(schedule.cron)
        for name, schedule in enabled.items()
    }
    existing = {job.id: job for job in scheduler.get_jobs()}
    for name in existing.keys() - enabled.keys():
        scheduler.remove_job(name)
    for schedule in schedules.values():
        if not schedule.enabled:
            continue
        job = existing.get(schedule.name)
        if job is not None:
            if job.args[0].cron != schedule.cron:
                scheduler.reschedule_job(schedule.name, trigger=triggers[schedule.name])
                scheduler.modify_job(schedule.name, args=[schedule, store])
            continue
        scheduler.add_job(
            _job,
            trigger=triggers[schedule.name],
            args=[schedule, store],
            id=schedule.name,
            name=schedule.name,
            replace_existing=True,
        )


def _reload_or_keep_armed(scheduler: BackgroundScheduler, store: ScheduleStore) -> None:
    """One reload tick: reconcile timers, or stay on the last good read.

    A bad cron (``gaia schedule add`` rejects one now, but an existing store
    can still predate that check) or a store read mid-hand-edit must not take
    the whole daemon down with it -- keep the previously armed timers running
    and try again next tick, the same loud-but-alive contract ``_job`` already
    honours (#4143).
    """
    try:
        refresh_schedules(scheduler, store)
    except Exception:
        log.exception(
            "could not reload %s; keeping the schedules armed from the "
            "last good read",
            store.path,
        )


def run_daemon(store_path: Path = DEFAULT_STORE_PATH) -> None:
    """Start the scheduler and block until SIGINT/SIGTERM.

    Raises :class:`~gaia.schedule.lock.ScheduleLockError` if another daemon is
    already running against the same store.
    """
    with daemon_lock(store_path):
        store = TomlScheduleStore(store_path)
        scheduler = build_scheduler(store)
        scheduler.start()

        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())

        log.info("schedule daemon running (store=%s); press Ctrl-C to stop", store.path)
        try:
            while not stop.wait(STORE_REFRESH_SECONDS):
                _reload_or_keep_armed(scheduler, store)
        finally:
            scheduler.shutdown(wait=False)
            log.info("schedule daemon stopped")


def next_fire_time(cron: str) -> Optional[str]:
    """Human-readable next fire time for a cron expression (for `list`)."""
    trigger = CronTrigger.from_crontab(cron)
    nxt = trigger.get_next_fire_time(None, datetime.now(trigger.timezone))
    return nxt.isoformat() if nxt else None
