# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The single daemon-owned clock and the reconciliation that feeds it (V2-15).

Public surface:

- :class:`~gaia.daemon.scheduler.clock.DaemonClock` — the one scheduler.
- :func:`~gaia.daemon.scheduler.migration.reconcile_jobs` /
  :func:`~gaia.daemon.scheduler.migration.assert_no_dropped` — exactly-once
  adoption of the legacy clocks' jobs and dropped-job detection.
- :class:`~gaia.daemon.scheduler.models.MigratableJob` and friends — the
  hub-safe vocabulary the email adapter produces.
"""

from __future__ import annotations

from gaia.daemon.scheduler.clock import DaemonClock, JobExecutor
from gaia.daemon.scheduler.migration import assert_no_dropped, reconcile_jobs
from gaia.daemon.scheduler.models import (
    KIND_ONE_SHOT,
    KIND_RECURRING,
    DroppedJobError,
    MigratableJob,
    MigrationResult,
    ReconciliationError,
    SchedulerError,
)

__all__ = [
    "DaemonClock",
    "DroppedJobError",
    "JobExecutor",
    "KIND_ONE_SHOT",
    "KIND_RECURRING",
    "MigratableJob",
    "MigrationResult",
    "ReconciliationError",
    "SchedulerError",
    "assert_no_dropped",
    "reconcile_jobs",
]
