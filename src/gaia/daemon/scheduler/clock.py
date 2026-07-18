# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The single daemon-owned clock (V2-15, #2156).

``DaemonClock`` is the one scheduler that replaces the four that used to die
with their owning process. It drives :mod:`gaia.daemon.scheduler.store` jobs:

- :meth:`fire_due` is the synchronous, deterministic entry point — it claims
  every due job (atomic ``pending -> firing``) and runs its kind's executor.
  Tests call it directly with an injected ``now``; the polling thread calls it
  on an interval. The atomic claim is what makes a job fire exactly once even
  while a legacy in-sidecar driver is still briefly alive during transition.
- :meth:`start` / :meth:`stop` manage the default driver: one daemon thread
  polling every ``poll_seconds``. Because jobs persist in SQLite, past-due jobs
  fire on the next pass after a daemon restart.

Like the email ``EmailJobScheduler`` it models, the clock opens its OWN SQLite
connection per polling pass (from ``db_path``) rather than sharing one across
threads — a shared sqlite3 connection is a cross-thread use-after-close waiting
to happen. Executors receive that per-pass ``db`` handle for their own writes.

Fail-loudly contract: an executor failure marks the job ``failed`` with the
error persisted and logs at ERROR; a job whose kind has no registered executor
is likewise marked ``failed``, never quietly skipped.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional

from gaia.daemon.scheduler import store
from gaia.database.mixin import DatabaseMixin
from gaia.logger import get_logger

log = get_logger(__name__)

# Executor signature: (job_row, db) -> None. ``db`` is the clock-owned per-pass
# connection; executors must use it for any store writes, never a shared handle.
JobExecutor = Callable[[Dict[str, Any], Any], None]


class _ClockDB(DatabaseMixin):
    """Bare DatabaseMixin host for the clock's per-pass connection."""


class DaemonClock:
    """Fires the daemon's reconciled jobs through registered executors.

    ``executors`` maps a job ``kind`` (:data:`~gaia.daemon.scheduler.models.
    KIND_ONE_SHOT` / ``KIND_RECURRING`` — or a finer source-specific kind the
    caller registers) to a :data:`JobExecutor`. Executors run on the polling
    thread; they must be self-contained and use the passed-in db handle.
    """

    def __init__(
        self,
        db_path: str,
        executors: Dict[str, JobExecutor],
        *,
        poll_seconds: float = 30.0,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError(f"poll_seconds must be > 0, got {poll_seconds}")
        if db_path == ":memory:":
            raise ValueError(
                "DaemonClock needs a file-backed SQLite path — an in-memory DB "
                "cannot be shared across the per-pass connections, so jobs would "
                "silently never fire."
            )
        self._db_path = db_path
        self._executors = dict(executors)
        self._poll_seconds = poll_seconds
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- Store bootstrap ------------------------------------------------

    def _open_db(self) -> _ClockDB:
        db = _ClockDB()
        db.init_db(self._db_path)
        store.init_schema(db)
        return db

    # -- Driving --------------------------------------------------------

    def fire_due(self, *, now: Optional[float] = None) -> Dict[str, List[str]]:
        """Claim and execute every due job. Returns fired/failed daemon ids.

        The atomic ``claim_job`` guard makes each job fire exactly once even
        when a legacy driver polls the same store during the transition window.
        """
        db = self._open_db()
        try:
            return self._fire_due_on(db, now=now)
        finally:
            db.close_db()

    def _fire_due_on(self, db, *, now: Optional[float]) -> Dict[str, List[str]]:
        fired: List[str] = []
        failed: List[str] = []
        for job in store.fetch_due(db, now=now):
            job_id = job["job_id"]
            if not store.claim_job(db, job_id=job_id):
                continue  # another driver won the claim
            executor = self._executors.get(job["kind"])
            if executor is None:
                msg = (
                    f"no executor registered for job kind {job['kind']!r} "
                    f"(source {job['source']!r}) — the job cannot fire in the "
                    "daemon process"
                )
                store.mark_failed(db, job_id=job_id, error=msg)
                log.error("daemon clock: job %s failed: %s", job_id, msg)
                failed.append(job_id)
                continue
            try:
                executor(job, db)
            except Exception as exc:
                emsg = f"{type(exc).__name__}: {exc}"
                store.mark_failed(db, job_id=job_id, error=emsg)
                log.exception(
                    "daemon clock: %s job %s (source %s) failed to fire: %s",
                    job["kind"],
                    job_id,
                    job["source"],
                    emsg,
                )
                failed.append(job_id)
                continue
            store.mark_fired(db, job_id=job_id)
            log.info(
                "daemon clock: %s job %s (source %s) fired",
                job["kind"],
                job_id,
                job["source"],
            )
            fired.append(job_id)
        return {"fired": fired, "failed": failed}

    # -- Default driver: polling thread ---------------------------------

    def start(self) -> None:
        """Start the polling thread. Idempotent while running."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="gaia-daemon-clock", daemon=True
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
        # Fire immediately on start so past-due jobs from a previous daemon run
        # fire without waiting a full poll interval.
        while True:
            try:
                self.fire_due()
            except Exception:
                # Per-job failures are handled inside fire_due; reaching here
                # means the store itself errored (e.g. the DB file is gone).
                # Log loudly and keep the thread alive — jobs stay pending in
                # SQLite and fire once the store recovers.
                log.exception("daemon clock: polling pass failed")
            if self._stop_event.wait(self._poll_seconds):
                return


__all__ = ["DaemonClock", "JobExecutor"]
