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

At-most-once trade-off (deliberate): a process crash, or a locked DB, between
``claim_job`` and ``mark_fired``/``mark_failed`` leaves the row in ``firing``.
A send never re-fires, which is the safe side for email (no double-send):
once the claim is older than ``stale_firing_seconds`` a pass marks it ``failed``
with the reason, so it shows up as a failure to check. A snooze restore is safe
to repeat, so it goes back to ``pending`` and fires again.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional

from gaia_agent_email import schedule_store

from gaia.database.mixin import DatabaseMixin
from gaia.logger import get_logger

log = get_logger(__name__)

# Executor signature: (job_row, db) -> None. ``db`` is the scheduler-owned
# per-pass connection; executors must use it for any store/audit writes.
JobExecutor = Callable[[Dict[str, Any], Any], None]

# How long a ``firing`` claim may go without an outcome before a pass fails it.
# Longer than any send or snooze restore takes, so a live fire is never failed.
DEFAULT_STALE_FIRING_SECONDS = 3600.0


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
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError(f"poll_seconds must be > 0, got {poll_seconds}")
        if stale_firing_seconds <= 0:
            raise ValueError(
                f"stale_firing_seconds must be > 0, got {stale_firing_seconds}"
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
        self._schema_ready = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- Driving --------------------------------------------------------

    def fire_due_jobs(self, *, now: Optional[float] = None) -> Dict[str, List[str]]:
        """Claim and execute every due pending job. Returns fired/failed ids.

        The atomic ``claim_job`` guard makes each job fire exactly once even
        when two drivers (thread + external dispatcher) poll the same store.
        Claims left ``firing`` past ``stale_firing_seconds`` are recovered
        first: a failed send is reported in ``failed``, and a re-queued snooze
        fires in this same pass.
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

    def _fire_due_jobs_on(self, db, *, now: Optional[float]) -> Dict[str, List[str]]:
        fired: List[str] = []
        recovered = schedule_store.recover_stale_firing(
            db,
            now=now if now is not None else time.time(),
            stale_after=self._stale_firing_seconds,
        )
        for job_id in recovered["requeued"]:
            log.warning(
                "email scheduler: snooze %s was left firing with no outcome for "
                "over %.0fs; re-queued it",
                job_id,
                self._stale_firing_seconds,
            )
        for job_id in recovered["failed"]:
            log.error(
                "email scheduler: job %s was left firing with no outcome for "
                "over %.0fs; marked it failed instead of running it twice",
                job_id,
                self._stale_firing_seconds,
            )
        failed: List[str] = list(recovered["failed"])
        for job in schedule_store.fetch_due(db, now=now):
            job_id = job["job_id"]
            if not schedule_store.claim_job(db, job_id=job_id, now=now):
                continue  # another driver won the claim
            executor = self._executors.get(job["kind"])
            if executor is None:
                msg = (
                    f"no executor registered for job kind {job['kind']!r} — "
                    "the job cannot fire in this process"
                )
                schedule_store.mark_failed(db, job_id=job_id, error=msg)
                log.error("email scheduler: job %s failed: %s", job_id, msg)
                failed.append(job_id)
                continue
            try:
                executor(job, db)
            except Exception as exc:
                msg = f"{type(exc).__name__}: {exc}"
                schedule_store.mark_failed(db, job_id=job_id, error=msg)
                log.exception(
                    "email scheduler: %s job %s failed to fire: %s",
                    job["kind"],
                    job_id,
                    msg,
                )
                failed.append(job_id)
                continue
            schedule_store.mark_fired(db, job_id=job_id)
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
