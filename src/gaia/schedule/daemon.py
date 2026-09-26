"""Long-running scheduler daemon.

Reads the schedule store, arms an APScheduler cron trigger per enabled schedule,
and blocks until interrupted. Each trigger fires :func:`runner.fire`.
"""

from __future__ import annotations

import signal
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from apscheduler.events import (
    EVENT_JOB_MAX_INSTANCES,
    EVENT_JOB_MISSED,
    JobExecutionEvent,
    JobSubmissionEvent,
)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from gaia.logger import get_logger
from gaia.schedule import runner
from gaia.schedule.store import (
    DEFAULT_STORE_PATH,
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


def build_scheduler(store: ScheduleStore) -> BackgroundScheduler:
    """Create a scheduler with one cron job per enabled schedule."""
    scheduler = BackgroundScheduler(job_defaults=JOB_DEFAULTS)
    scheduler.add_listener(_log_skipped_run, EVENT_JOB_MISSED | EVENT_JOB_MAX_INSTANCES)
    refresh_schedules(scheduler, store)
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
    """Start the scheduler and block until SIGINT/SIGTERM."""
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
