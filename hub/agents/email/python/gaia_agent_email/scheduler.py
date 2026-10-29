# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
One-shot job scheduler for scheduled send + snooze (#1609).

``EmailJobScheduler`` drives the persistent ``schedule_store`` jobs:

- ``fire_due_jobs()`` is the synchronous, deterministic entry point — it
  claims every due pending job and runs its kind's executor. Tests call it
  directly with an injected ``now``; the built-in polling thread calls it on
  an interval.
- ``start()`` / ``stop()`` manage the default driver: a daemon thread polling
  every ``poll_seconds``. Because jobs persist in SQLite, past-due jobs fire
  on the next ``fire_due_jobs`` after a restart ("at/after its time").

The scheduler opens its OWN SQLite connection per polling pass (from
``db_path``) rather than borrowing the agent's — sharing one sqlite3
connection across the agent thread and this thread is a use-after-close
segfault waiting for the agent to be torn down mid-poll. Executors receive
that per-pass connection for their audit writes.

Scheduler seam (#1371 / autonomy epic #555): when the ``gaia schedule`` cron
dispatcher lands, it can invoke ``fire_due_jobs()`` on its cadence instead of
``start()``-ing the thread. The store and executors don't change — only the
driver does. Kept email-scoped and minimal on purpose.

Daemon supervision (V2-15, #2156): under the GAIA daemon this thread is NOT
started (see :func:`gaia_agent_email.supervision.is_daemon_supervised`); the
daemon drives these jobs from its single reconciled clock after adopting them
via :func:`gaia_agent_email.daemon_migration.migrate_email_clocks`. Standalone
runs keep the thread — the seam above, realized. The atomic ``claim_job`` guard
means even if both drivers briefly poll the same store, each job fires once.

Fail-loudly contract: an executor failure marks the job ``failed`` with the
error message persisted on the row and logs at ERROR — a firing send must
never silently swallow a send failure. A job whose kind has no registered
executor is likewise marked failed, never quietly skipped.

Leases: a claim is held with a token and renewed every ``heartbeat_seconds``
while the executor runs, and only the holder can record the outcome. A process
crash, or a locked DB, between ``claim_job`` and ``mark_fired``/``mark_failed``
leaves the row in ``firing`` with a lease nobody renews. Once it has gone
``stale_firing_seconds`` without a heartbeat, and this scheduler has watched it
stay unrenewed for two more heartbeat intervals (which a live holder clears
even after a sleep/resume), a pass recovers it. A send is marked ``failed`` and
never re-fires, the safe side for email (no double-send); the failure is
listed by ``list_scheduled_jobs``. A snooze restore is safe to repeat, so it
goes back to ``pending``. If the holder was alive after all, its own outcome
still overwrites the recovery's guess.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional

from gaia_agent_email import schedule_store
from gaia_agent_email.lease import (
    DEFAULT_HEARTBEAT_SECONDS,
    DEFAULT_STALE_AFTER_SECONDS,
    Heartbeat,
    StaleLeases,
)

from gaia.database.mixin import DatabaseMixin
from gaia.logger import get_logger

log = get_logger(__name__)

# Executor signature: (job_row, db) -> None. ``db`` is the scheduler-owned
# per-pass connection; executors must use it for any store/audit writes.
JobExecutor = Callable[[Dict[str, Any], Any], None]

# How long a ``firing`` lease may go without a heartbeat before it is a
# recovery candidate.
DEFAULT_STALE_FIRING_SECONDS = DEFAULT_STALE_AFTER_SECONDS


class _SchedulerDB(DatabaseMixin):
    """Bare DatabaseMixin host for the scheduler's per-pass connection."""


class EmailJobScheduler:
    """Polls the one-shot job store and fires due jobs through executors.

    ``executors`` maps a job ``kind`` (e.g. ``schedule_store.KIND_SNOOZE``)
    to a ``JobExecutor``. Executors run on the polling thread; they must be
    self-contained (resolve their own backend from the job row) and use the
    passed-in db handle, never the agent's connection.
    """

    def __init__(
        self,
        db_path: str,
        executors: Dict[str, JobExecutor],
        *,
        poll_seconds: float = 30.0,
        stale_firing_seconds: float = DEFAULT_STALE_FIRING_SECONDS,
        heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError(f"poll_seconds must be > 0, got {poll_seconds}")
        if stale_firing_seconds <= 0:
            raise ValueError(
                f"stale_firing_seconds must be > 0, got {stale_firing_seconds}"
            )
        if not 0 < heartbeat_seconds < stale_firing_seconds:
            raise ValueError(
                f"heartbeat_seconds must be > 0 and below stale_firing_seconds "
                f"({stale_firing_seconds}), got {heartbeat_seconds}"
            )
        if db_path == ":memory:":
            raise ValueError(
                "EmailJobScheduler needs a file-backed SQLite path — an "
                "in-memory DB cannot be shared across connections, so jobs "
                "would silently never fire."
            )
        self._db_path = db_path
        self._executors = dict(executors)
        self._poll_seconds = poll_seconds
        self._stale_firing_seconds = stale_firing_seconds
        self._heartbeat_seconds = heartbeat_seconds
        # One wall clock for claims, heartbeats and recovery, injectable
        # so a test can move it the way a sleep/resume does.
        self._clock = clock
        self._stale_leases = StaleLeases(confirm_after=2 * heartbeat_seconds)
        self._schema_ready = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- Driving --------------------------------------------------------

    def fire_due_jobs(self, *, now: Optional[float] = None) -> Dict[str, List[str]]:
        """Claim and execute every due pending job. Returns fired/failed ids.

        The atomic ``claim_job`` guard makes each job fire exactly once even
        when two drivers (thread + external dispatcher) poll the same store.
        Abandoned leases are recovered first (see the module docstring): a
        failed send is reported in ``failed``, and a re-queued snooze fires in
        this same pass.
        """
        db = _SchedulerDB()
        db.init_db(self._db_path)
        try:
            if not self._schema_ready:
                # A driver other than the agent may call this first.
                schedule_store.init_schema(db)
                self._schema_ready = True
            return self._fire_due_jobs_on(db, now=now)
        finally:
            db.close_db()

    def _recover_abandoned(self, db, now: float) -> List[str]:
        candidates = schedule_store.find_stale_firing(
            db, now=now, stale_after=self._stale_firing_seconds
        )
        failed: List[str] = []
        for job in self._stale_leases.confirm(candidates, now):
            outcome = schedule_store.recover_claim(db, job, now=now)
            if outcome == "requeued":
                log.warning(
                    "email scheduler: snooze %s stopped renewing its lease while "
                    "firing; re-queued it",
                    job["job_id"],
                )
            elif outcome == "failed":
                log.error(
                    "email scheduler: job %s stopped renewing its lease while "
                    "firing; marked it failed instead of running it twice",
                    job["job_id"],
                )
                failed.append(job["job_id"])
        return failed

    def _renew(self, job_id: str, claim_id: str) -> bool:
        # Own connection: sqlite3 connections are not shared across threads.
        db = _SchedulerDB()
        db.init_db(self._db_path, quiet=True)
        try:
            return schedule_store.renew_claim(
                db, job_id=job_id, claim_id=claim_id, now=self._clock()
            )
        finally:
            db.close_db()

    def _record(self, recorded: bool, job: Dict[str, Any], outcome: str) -> None:
        if not recorded:
            log.warning(
                "email scheduler: %s job %s %s, but another driver had already "
                "claimed it again; that driver's outcome stands",
                job["kind"],
                job["job_id"],
                outcome,
            )

    def _fire_due_jobs_on(self, db, *, now: Optional[float]) -> Dict[str, List[str]]:
        now = now if now is not None else self._clock()
        fired: List[str] = []
        failed = self._recover_abandoned(db, now)
        for job in schedule_store.fetch_due(db, now=now):
            job_id = job["job_id"]
            claim_id = schedule_store.claim_job(db, job_id=job_id, now=now)
            if claim_id is None:
                continue  # another driver won the claim
            executor = self._executors.get(job["kind"])
            if executor is None:
                msg = (
                    f"no executor registered for job kind {job['kind']!r} — "
                    "the job cannot fire in this process"
                )
                schedule_store.mark_failed(
                    db, job_id=job_id, claim_id=claim_id, error=msg
                )
                log.error("email scheduler: job %s failed: %s", job_id, msg)
                failed.append(job_id)
                continue
            try:
                with Heartbeat(
                    lambda j=job_id, c=claim_id: self._renew(j, c),
                    self._heartbeat_seconds,
                    job_id,
                ):
                    executor(job, db)
            except Exception as exc:
                msg = f"{type(exc).__name__}: {exc}"
                self._record(
                    schedule_store.mark_failed(
                        db, job_id=job_id, claim_id=claim_id, error=msg
                    ),
                    job,
                    "failed",
                )
                log.exception(
                    "email scheduler: %s job %s failed to fire: %s",
                    job["kind"],
                    job_id,
                    msg,
                )
                failed.append(job_id)
                continue
            self._record(
                schedule_store.mark_fired(db, job_id=job_id, claim_id=claim_id),
                job,
                "fired",
            )
            log.info("email scheduler: %s job %s fired", job["kind"], job_id)
            fired.append(job_id)
        return {"fired": fired, "failed": failed}

    # -- Default driver: polling thread ---------------------------------

    def start(self) -> None:
        """Start the polling thread. Idempotent while running."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="email-job-scheduler", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the polling thread to exit and wait for it."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._poll_seconds + 5)
            self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        # Fire immediately on start so past-due jobs from a previous process
        # run fire without waiting a full poll interval.
        while True:
            try:
                self.fire_due_jobs()
            except Exception:
                # Per-job failures are handled inside fire_due_jobs; reaching
                # here means the store itself errored (e.g. the DB file is
                # gone). Log loudly and keep the thread alive — jobs stay
                # pending in SQLite and fire once the store recovers.
                log.exception("email scheduler: polling pass failed")
            if self._stop_event.wait(self._poll_seconds):
                return


__all__ = ["DEFAULT_STALE_FIRING_SECONDS", "EmailJobScheduler", "JobExecutor"]
