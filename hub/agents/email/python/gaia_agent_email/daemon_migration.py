# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Adapter: lift the email sidecar's in-process clock jobs into the daemon
clock (V2-15, #2156).

This is the email-specific half of the reconciliation. It reads the two
embedded clocks' durable state and maps it to the daemon's hub-safe
:class:`~gaia.daemon.scheduler.models.MigratableJob` vocabulary, then hands the
batch to the core reconciler. Living in the hub package keeps the dependency
direction legal: hub -> core is fine, and core never learns anything
email-specific.

Two sources fold in here:

- ``EmailJobScheduler`` / ``schedule_store`` (#1919) — persistent one-shot jobs
  (scheduled send, snooze) already keyed by a stable ``job_id``. Each pending
  job becomes a one-shot :class:`MigratableJob`.
- ``BriefingScheduler`` (#1918) — a recurring daily brief configured from env,
  with no per-job row. When enabled it contributes a single recurring
  :class:`MigratableJob` with a synthetic-but-stable ``source_job_id`` so a
  re-run migrates it exactly once.

The reconcile is idempotent: run it every time the daemon (re)adopts the email
sidecar; the ledger makes the second and later passes no-ops.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from gaia_agent_email import schedule_store
from gaia_agent_email.briefing import (
    BriefingScheduleConfig,
    seconds_until_next_run,
)

from gaia.daemon.scheduler import (
    KIND_ONE_SHOT,
    KIND_RECURRING,
    MigratableJob,
    MigrationResult,
    assert_no_dropped,
    reconcile_jobs,
)

# Source names recorded in the daemon migration ledger. Stable strings — they
# key the exactly-once guard, so they must never change once shipped.
SOURCE_ONE_SHOT = "email:schedule_store"
SOURCE_BRIEFING = "email:briefing"

# The briefing has no per-job row; this fixed id gives it a stable ledger key so
# re-adoption is idempotent. One daily brief per sidecar identity.
BRIEFING_JOB_ID = "daily_inbox_briefing"


def collect_one_shot_jobs(db) -> List[MigratableJob]:
    """Every still-pending one-shot email job as a :class:`MigratableJob`."""
    schedule_store.init_schema(db)
    jobs: List[MigratableJob] = []
    for row in schedule_store.list_jobs(db, status=schedule_store.STATUS_PENDING):
        jobs.append(
            MigratableJob(
                source=SOURCE_ONE_SHOT,
                source_job_id=row["job_id"],
                kind=KIND_ONE_SHOT,
                fire_at=row["due_at"],
                payload={
                    "kind": row["kind"],
                    "mailbox": row["mailbox"],
                    "payload": row["payload"],
                },
            )
        )
    return jobs


def collect_briefing_job(
    config: BriefingScheduleConfig, *, now: Optional[datetime] = None
) -> List[MigratableJob]:
    """The daily briefing as a recurring :class:`MigratableJob`, or [] when off.

    A disabled briefing contributes nothing — matching the embedded scheduler,
    which creates no task when disabled. The first ``fire_at`` is the next local
    occurrence of the configured time; the interval is a fixed 24h.
    """
    config.validate()
    if not config.enabled:
        return []
    reference = now or datetime.now()
    delay = seconds_until_next_run(config.time_of_day, reference)
    return [
        MigratableJob(
            source=SOURCE_BRIEFING,
            source_job_id=BRIEFING_JOB_ID,
            kind=KIND_RECURRING,
            interval_seconds=86400,
            fire_at=reference.timestamp() + delay,
            payload={
                "time_of_day": config.time_of_day,
                "max_messages": config.max_messages,
            },
        )
    ]


def migrate_email_clocks(
    db,
    *,
    briefing_config: Optional[BriefingScheduleConfig] = None,
    now: Optional[datetime] = None,
    verify_no_dropped: bool = True,
) -> MigrationResult:
    """Adopt both embedded email clocks into the daemon clock, exactly once.

    ``db`` is a ``DatabaseMixin`` handle open on the daemon clock's store (the
    same file the daemon drives). Pass ``briefing_config`` to fold the daily
    brief in; omit it to migrate only the one-shot jobs.

    When ``verify_no_dropped`` is set (the default) the one-shot batch is
    checked with :func:`assert_no_dropped` after the pass — every pending email
    job must have reached the ledger, or a :class:`DroppedJobError` is raised so
    the migration fails loudly instead of silently losing a scheduled send.
    """
    one_shot = collect_one_shot_jobs(db)
    briefing = (
        collect_briefing_job(briefing_config, now=now)
        if briefing_config is not None
        else []
    )
    result = reconcile_jobs(db, one_shot + briefing)

    if verify_no_dropped:
        assert_no_dropped(
            db,
            source=SOURCE_ONE_SHOT,
            source_job_ids=[j.source_job_id for j in one_shot],
        )
        if briefing:
            assert_no_dropped(
                db,
                source=SOURCE_BRIEFING,
                source_job_ids=[BRIEFING_JOB_ID],
            )
    return result


__all__: List[str] = [
    "BRIEFING_JOB_ID",
    "SOURCE_BRIEFING",
    "SOURCE_ONE_SHOT",
    "collect_briefing_job",
    "collect_one_shot_jobs",
    "migrate_email_clocks",
]
