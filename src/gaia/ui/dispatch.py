# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Lightweight dispatch queue for background tasks with status tracking.

Provides a DispatchQueue that runs tasks on a ThreadPoolExecutor,
tracks per-job status, and exposes visible jobs for frontend consumption.
Used by the UI backend lifespan for boot-time initialization.
"""

import asyncio
import enum
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

# Maximum time (seconds) to wait for a dependency job to reach a terminal state.
_DEPENDENCY_TIMEOUT = 60.0

# Polling interval (seconds) when waiting for a dependency.
_DEPENDENCY_POLL_INTERVAL = 0.2


class JobStatus(str, enum.Enum):
    """Lifecycle status of a dispatched job."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Job:
    """A tracked unit of work dispatched to the queue."""

    id: str = field(default_factory=lambda: uuid4().hex[:12])
    name: str = ""
    status: JobStatus = JobStatus.PENDING
    visible: bool = False
    error: Optional[str] = None
    depends_on: Optional[str] = None
    created_at: float = field(default_factory=time.monotonic)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None


class DispatchQueue:
    """Background task dispatcher with job tracking.

    All public methods that schedule work (``dispatch``) must be called from the
    asyncio event-loop thread.  Job status mutations happen exclusively on the
    event loop via ``_run`` coroutines — no additional locking is required for
    the ``_jobs`` dict.
    """

    def __init__(self, max_workers: int = 4, prune_after: float = 300.0):
        self._jobs: dict[str, Job] = {}
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._prune_after = prune_after
        # Anchors for converting monotonic timestamps to wall-clock.
        self._wall_epoch = time.time()
        self._mono_epoch = time.monotonic()

    # ── Public API ────────────────────────────────────────────────────────

    def dispatch(
        self,
        name: str,
        fn: Callable[..., Any],
        *args: Any,
        visible: bool = False,
        depends_on: Optional[str] = None,
    ) -> str:
        """Submit *fn* for execution on the thread-pool.

        Returns the job ID immediately (non-blocking).  Must be called from
        the running event-loop thread.
        """
        job = Job(name=name, visible=visible, depends_on=depends_on)
        self._jobs[job.id] = job
        loop = asyncio.get_running_loop()
        loop.create_task(self._run(job, fn, *args))
        return job.id

    def get_job(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def get_visible_jobs(self) -> List[Job]:
        return [j for j in self._jobs.values() if j.visible]

    def get_all_jobs(self) -> List[Job]:
        return list(self._jobs.values())

    def mono_to_wall(self, mono: float) -> float:
        """Convert a monotonic timestamp to a wall-clock epoch."""
        return self._wall_epoch + (mono - self._mono_epoch)

    async def shutdown(self) -> None:
        self._executor.shutdown(wait=False)

    # ── Internal ──────────────────────────────────────────────────────────

    async def _run(self, job: Job, fn: Callable[..., Any], *args: Any) -> None:
        """Execute *fn* in the thread-pool, handling dependencies and errors."""
        try:
            # Wait for dependency (if any) with a hard timeout.
            if job.depends_on:
                dep = self._jobs.get(job.depends_on)
                if dep is None:
                    job.status = JobStatus.FAILED
                    job.error = f"Dependency job '{job.depends_on}' not found"
                    job.completed_at = time.monotonic()
                    logger.warning(
                        "Boot init: %s → %s (dependency not found)",
                        job.name,
                        job.status.value,
                    )
                    return
                else:
                    deadline = time.monotonic() + _DEPENDENCY_TIMEOUT
                    while dep.status not in (JobStatus.DONE, JobStatus.FAILED):
                        if time.monotonic() >= deadline:
                            job.status = JobStatus.FAILED
                            job.error = f"Timed out waiting for dependency '{dep.name}'"
                            job.completed_at = time.monotonic()
                            logger.warning(
                                "Boot init: %s → %s (dependency timeout)",
                                job.name,
                                job.status.value,
                            )
                            return
                        await asyncio.sleep(_DEPENDENCY_POLL_INTERVAL)

                    if dep.status == JobStatus.FAILED:
                        job.status = JobStatus.FAILED
                        job.error = f"Dependency '{dep.name}' failed"
                        job.completed_at = time.monotonic()
                        logger.warning(
                            "Boot init: %s → %s (dependency failed)",
                            job.name,
                            job.status.value,
                        )
                        return

            # Execute the work on the thread-pool.
            job.status = JobStatus.RUNNING
            job.started_at = time.monotonic()

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._executor, fn, *args)

            job.status = JobStatus.DONE
        except Exception as exc:
            job.status = JobStatus.FAILED
            job.error = str(exc)
            logger.warning("Boot init: %s failed: %s", job.name, exc)
        finally:
            job.completed_at = job.completed_at or time.monotonic()
            elapsed = job.completed_at - job.created_at
            logger.info(
                "Boot init: %s → %s (%.1fs)",
                job.name,
                job.status.value,
                elapsed,
            )
            self._prune_old()

    def _prune_old(self) -> None:
        """Remove completed/failed jobs older than *prune_after* seconds."""
        now = time.monotonic()
        to_remove = [
            jid
            for jid, j in self._jobs.items()
            if j.status in (JobStatus.DONE, JobStatus.FAILED)
            and j.completed_at is not None
            and (now - j.completed_at) > self._prune_after
        ]
        for jid in to_remove:
            del self._jobs[jid]
