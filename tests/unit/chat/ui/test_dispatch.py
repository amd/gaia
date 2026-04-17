# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for the DispatchQueue background task system."""

import asyncio
import time

import pytest

from gaia.ui.dispatch import DispatchQueue, JobStatus


@pytest.fixture
def queue():
    """Create a DispatchQueue for testing."""
    q = DispatchQueue(max_workers=4, prune_after=1.0)
    yield q
    # Shutdown synchronously for test cleanup
    q._executor.shutdown(wait=True)


# ── Job Lifecycle ─────────────────────────────────────────────────────────


async def test_job_completes_successfully(queue):
    """A dispatched job transitions through pending → running → done."""
    job_id = queue.dispatch("test job", time.sleep, 0.01, visible=True)
    job = queue.get_job(job_id)
    assert job is not None
    assert job.name == "test job"

    # Wait for completion
    for _ in range(100):
        if job.status == JobStatus.DONE:
            break
        await asyncio.sleep(0.05)

    assert job.status == JobStatus.DONE
    assert job.error is None
    assert job.started_at is not None
    assert job.completed_at is not None
    assert job.completed_at >= job.started_at


async def test_job_failure_captures_error(queue):
    """A job that raises transitions to FAILED with the error message."""

    def _fail():
        raise ValueError("test error")

    job_id = queue.dispatch("failing job", _fail, visible=True)
    job = queue.get_job(job_id)

    for _ in range(100):
        if job.status == JobStatus.FAILED:
            break
        await asyncio.sleep(0.05)

    assert job.status == JobStatus.FAILED
    assert "test error" in job.error
    assert job.completed_at is not None


# ── Dependency Handling ───────────────────────────────────────────────────


async def test_depends_on_waits_for_predecessor(queue):
    """A dependent job waits for its predecessor to complete."""
    order = []

    def _first():
        time.sleep(0.1)
        order.append("first")

    def _second():
        order.append("second")

    first_id = queue.dispatch("first", _first)
    queue.dispatch("second", _second, depends_on=first_id)

    # Wait for both to complete
    for _ in range(100):
        jobs = queue.get_all_jobs()
        if all(j.status in (JobStatus.DONE, JobStatus.FAILED) for j in jobs):
            break
        await asyncio.sleep(0.05)

    assert order == ["first", "second"]


async def test_depends_on_fails_when_predecessor_fails(queue):
    """If a predecessor fails, the dependent job also fails."""

    def _fail():
        raise RuntimeError("predecessor failed")

    first_id = queue.dispatch("failing first", _fail)
    second_id = queue.dispatch("dependent", lambda: None, depends_on=first_id)

    for _ in range(100):
        second = queue.get_job(second_id)
        if second.status == JobStatus.FAILED:
            break
        await asyncio.sleep(0.05)

    assert second.status == JobStatus.FAILED
    assert "Dependency" in second.error


# ── Visibility Filter ────────────────────────────────────────────────────


async def test_visible_filter(queue):
    """get_visible_jobs returns only visible=True jobs."""
    queue.dispatch("visible", lambda: None, visible=True)
    queue.dispatch("hidden", lambda: None, visible=False)

    # Wait for completion
    await asyncio.sleep(0.2)

    visible = queue.get_visible_jobs()
    all_jobs = queue.get_all_jobs()

    assert len(visible) == 1
    assert visible[0].name == "visible"
    assert len(all_jobs) == 2


# ── Concurrency ──────────────────────────────────────────────────────────


async def test_independent_jobs_run_concurrently(queue):
    """Two independent jobs run in parallel, not sequentially."""

    def _sleep():
        time.sleep(0.2)

    t0 = time.monotonic()
    queue.dispatch("a", _sleep)
    queue.dispatch("b", _sleep)

    for _ in range(100):
        jobs = queue.get_all_jobs()
        if all(j.status == JobStatus.DONE for j in jobs):
            break
        await asyncio.sleep(0.05)

    elapsed = time.monotonic() - t0
    # Both 0.2s jobs in parallel should take < 0.4s total
    assert elapsed < 0.4, f"Jobs ran sequentially: {elapsed:.2f}s"


# ── Pruning ──────────────────────────────────────────────────────────────


async def test_prune_removes_old_completed_jobs(queue):
    """Completed jobs older than prune_after are removed."""
    # Queue has prune_after=1.0s from fixture
    job_id = queue.dispatch("prunable", lambda: None)

    # Wait for completion
    for _ in range(50):
        if queue.get_job(job_id).status == JobStatus.DONE:
            break
        await asyncio.sleep(0.05)

    assert queue.get_job(job_id) is not None

    # Wait past prune threshold, then trigger pruning via another job
    await asyncio.sleep(1.1)
    queue.dispatch("trigger", lambda: None)
    await asyncio.sleep(0.2)

    assert queue.get_job(job_id) is None, "Old job should have been pruned"


# ── Mono-to-Wall Conversion ─────────────────────────────────────────────


def test_mono_to_wall(queue):
    """mono_to_wall converts monotonic timestamps to wall-clock."""
    mono_now = time.monotonic()
    wall_now = time.time()
    converted = queue.mono_to_wall(mono_now)
    # Should be within 1 second of actual wall time
    assert abs(converted - wall_now) < 1.0
