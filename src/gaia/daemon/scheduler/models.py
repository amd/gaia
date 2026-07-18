# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Data model and errors for the single daemon-owned clock (V2-15, #2156).

The daemon reconciles the four clocks that used to die with their owning
process — the UI backend's ``Scheduler``, the ``gaia schedule`` CLI, and the
email sidecar's two in-process clocks (``BriefingScheduler`` #1918 and
``EmailJobScheduler`` #1919) — into ONE clock the daemon owns. The pieces here
are the vocabulary that reconciliation speaks in; they carry no store or driver
logic so they stay import-cheap and hub-safe (core NEVER imports a hub wheel —
the email adapter that produces :class:`MigratableJob` records lives in the
email package and imports *this*, never the other way around).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

# Job cadence kinds understood by the daemon clock.
KIND_ONE_SHOT = "one_shot"  # fire once at ``fire_at`` (scheduled send, snooze)
KIND_RECURRING = "recurring"  # fire every ``interval_seconds`` (briefing, UI)

_VALID_KINDS = frozenset({KIND_ONE_SHOT, KIND_RECURRING})

# Job lifecycle. ``claim_due`` performs the atomic pending -> firing transition
# that makes each fire happen exactly once even with two drivers polling.
STATUS_PENDING = "pending"
STATUS_FIRING = "firing"
STATUS_FIRED = "fired"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"


class SchedulerError(RuntimeError):
    """Base class for daemon-clock failures. Always actionable, never silent."""


class ReconciliationError(SchedulerError):
    """A job could not be migrated into the daemon clock.

    Raised loudly (CLAUDE.md: no silent fallbacks) — a job that cannot be
    scheduled must surface, never be dropped, so an always-on brief or a
    scheduled send is never lost in the migration.
    """


class DroppedJobError(SchedulerError):
    """A source clock's job vanished across reconciliation.

    Raised by :func:`gaia.daemon.scheduler.migration.assert_no_dropped` when a
    job the source still owns has no ledger entry after a reconcile pass — the
    exact regression the epic flags (a reaped sidecar dropping its 8am brief).
    """


@dataclass(frozen=True)
class MigratableJob:
    """A single periodic job lifted out of one of the legacy clocks.

    ``source`` + ``source_job_id`` is the stable identity the migration ledger
    keys on — the same pair reconciled twice migrates exactly once. ``source``
    names the origin clock (e.g. ``"email"``); ``source_job_id`` is that clock's
    own primary key for the job (e.g. the email ``schedule_store`` ``job_id``).
    """

    source: str
    source_job_id: str
    kind: str
    payload: Dict[str, Any] = field(default_factory=dict)
    fire_at: Optional[float] = None  # epoch seconds; required for one-shot
    interval_seconds: Optional[int] = None  # required for recurring

    def validate(self) -> None:
        """Raise :class:`ReconciliationError` on any field that would produce a
        job the daemon cannot fire. Called before the job touches the store."""
        if not self.source:
            raise ReconciliationError(
                f"MigratableJob is missing a source clock name "
                f"(source_job_id={self.source_job_id!r}); reconciliation cannot "
                "attribute or de-duplicate it."
            )
        if not self.source_job_id:
            raise ReconciliationError(
                f"MigratableJob from source {self.source!r} has no "
                "source_job_id; without a stable id the migration ledger "
                "cannot guarantee exactly-once and the job would double-fire."
            )
        if self.kind not in _VALID_KINDS:
            raise ReconciliationError(
                f"MigratableJob {self.source}:{self.source_job_id} has unknown "
                f"kind {self.kind!r}; expected one of {sorted(_VALID_KINDS)}."
            )
        if self.kind == KIND_ONE_SHOT and self.fire_at is None:
            raise ReconciliationError(
                f"one-shot job {self.source}:{self.source_job_id} has no "
                "fire_at; a fire time is required or the daemon cannot know "
                "when to run it."
            )
        if self.kind == KIND_RECURRING and (
            self.interval_seconds is None or self.interval_seconds <= 0
        ):
            raise ReconciliationError(
                f"recurring job {self.source}:{self.source_job_id} has "
                f"interval_seconds={self.interval_seconds!r}; a positive "
                "interval is required or the job would never fire again."
            )


@dataclass(frozen=True)
class MigrationResult:
    """Outcome of one reconcile pass. ``migrated`` are jobs newly adopted by the
    daemon clock; ``skipped`` were already in the ledger (a re-run is a no-op —
    the exactly-once guarantee)."""

    migrated: tuple = ()
    skipped: tuple = ()

    @property
    def total(self) -> int:
        return len(self.migrated) + len(self.skipped)


__all__ = [
    "DroppedJobError",
    "KIND_ONE_SHOT",
    "KIND_RECURRING",
    "MigratableJob",
    "MigrationResult",
    "ReconciliationError",
    "SchedulerError",
    "STATUS_CANCELLED",
    "STATUS_FAILED",
    "STATUS_FIRED",
    "STATUS_FIRING",
    "STATUS_PENDING",
]
