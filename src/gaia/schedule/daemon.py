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


def _job(schedule: Schedule, store: ScheduleStore) -> None:
    # A failing job must not kill the daemon, but it must be loud (no silent
    # swallow): log with full traceback and keep the other schedules alive.
    try:
        runner.fire(schedule)
    except Exception:
        log.exception("schedule %r failed", schedule.name)
        return
    store.mark_run(
        schedule.name,
        datetime.now(timezone.utc).isoformat(),
        next_run=next_fire_time(schedule.cron),
    )


def build_scheduler(store: ScheduleStore) -> BackgroundScheduler:
    """Create a scheduler with one cron job per enabled schedule."""
    scheduler = BackgroundScheduler()
    schedules = store.load()
    armed = 0
    for schedule in schedules.values():
        if not schedule.enabled:
            log.info("skipping disabled schedule %r", schedule.name)
            continue
        scheduler.add_job(
            _job,
            trigger=CronTrigger.from_crontab(schedule.cron),
            args=[schedule, store],
            id=schedule.name,
            name=schedule.name,
            replace_existing=True,
        )
        armed += 1
    log.info("armed %d schedule(s)", armed)
    return scheduler


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
        stop.wait()
    finally:
        scheduler.shutdown(wait=False)
        log.info("schedule daemon stopped")


def next_fire_time(cron: str) -> Optional[str]:
    """Human-readable next fire time for a cron expression (for `list`)."""
    from datetime import datetime

    trigger = CronTrigger.from_crontab(cron)
    nxt = trigger.get_next_fire_time(None, datetime.now(trigger.timezone))
    return nxt.isoformat() if nxt else None
