# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Reconcile the legacy clocks into the single daemon clock (V2-15, #2156).

The one function that matters for the epic's flagged regression is
:func:`reconcile_jobs`: it adopts a batch of :class:`MigratableJob` records into
the daemon clock **exactly once**. Run it twice against the same source jobs and
the second pass is a no-op — the migration ledger's UNIQUE ``(source,
source_job_id)`` key makes double-adoption (and therefore the double-run this
issue exists to prevent) impossible.

Two loud failure modes, both required by CLAUDE.md's no-silent-fallbacks rule:

- an unschedulable job (bad kind, missing fire time/interval) raises
  :class:`ReconciliationError` *before* it can be silently dropped;
- a job the source still owns but that never reached the ledger is caught by
  :func:`assert_no_dropped`, which raises :class:`DroppedJobError` naming the
  ids — so a dropped brief fails the migration instead of vanishing.
"""

from __future__ import annotations

from typing import Iterable, List

from gaia.daemon.scheduler import store
from gaia.daemon.scheduler.models import (
    DroppedJobError,
    MigratableJob,
    MigrationResult,
)


def reconcile_jobs(db, jobs: Iterable[MigratableJob]) -> MigrationResult:
    """Adopt ``jobs`` into the daemon clock, exactly once each.

    Each job is validated loudly, then — only if the ledger has never seen its
    ``(source, source_job_id)`` — inserted into ``daemon_jobs`` and recorded in
    the ledger within a single transaction, so a crash mid-adoption never leaves
    a job row without its ledger guard (which would let a re-run double-adopt).

    Returns a :class:`MigrationResult` partitioning the batch into newly
    ``migrated`` ids and already-present ``skipped`` ids.

    Raises:
        ReconciliationError: if any job cannot be scheduled — the whole pass
            fails loudly rather than adopting a partial, lossy set.
    """
    migrated: List[str] = []
    skipped: List[str] = []

    # Validate the entire batch up front so a malformed job aborts before any
    # partial adoption — reconciliation is all-or-nothing per pass.
    materialized = list(jobs)
    for job in materialized:
        job.validate()

    for job in materialized:
        key = f"{job.source}:{job.source_job_id}"
        if store.ledger_entry(db, source=job.source, source_job_id=job.source_job_id):
            skipped.append(key)
            continue
        with db.transaction():
            daemon_job_id = store.register_job(
                db,
                source=job.source,
                source_job_id=job.source_job_id,
                kind=job.kind,
                payload=job.payload,
                fire_at=job.fire_at,
                interval_seconds=job.interval_seconds,
            )
            recorded = store.record_migration(
                db,
                source=job.source,
                source_job_id=job.source_job_id,
                daemon_job_id=daemon_job_id,
            )
        if recorded:
            migrated.append(key)
        else:
            # Lost the ledger race to a concurrent driver — the other pass owns
            # the adoption. Our just-inserted job row is the loser; drop it so
            # the winner's row is the only one that can fire (no double-run).
            store_db_delete(db, daemon_job_id)
            skipped.append(key)

    return MigrationResult(migrated=tuple(migrated), skipped=tuple(skipped))


def store_db_delete(db, daemon_job_id: str) -> None:
    """Remove a job row that lost the ledger race (internal to reconcile)."""
    db.delete("daemon_jobs", "job_id = :id", {"id": daemon_job_id})


def assert_no_dropped(db, *, source: str, source_job_ids: Iterable[str]) -> None:
    """Verify every still-owned source job reached the daemon ledger.

    Call this after a reconcile pass with the ids the source clock still holds.
    Any id without a ledger entry is a dropped job — the exact silent-loss the
    epic warns about (a reaped sidecar dropping its 8am brief) — and raises
    :class:`DroppedJobError` naming the offenders.
    """
    expected = list(source_job_ids)
    present = {row["source_job_id"] for row in store.list_ledger(db, source=source)}
    dropped = [sid for sid in expected if sid not in present]
    if dropped:
        raise DroppedJobError(
            f"{len(dropped)} job(s) from source {source!r} were not migrated "
            f"into the daemon clock: {dropped}. These would stop firing once "
            "the in-sidecar scheduler is gated. Re-run reconciliation or "
            "inspect the source store before enabling the idle reaper."
        )


__all__ = ["assert_no_dropped", "reconcile_jobs"]
