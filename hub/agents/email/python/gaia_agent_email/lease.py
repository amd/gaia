# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Leases for scheduled jobs that are firing.

A driver that claims a job (``pending -> firing``) holds a lease on it: a
``claim_id`` token plus a ``claimed_at`` heartbeat it renews while the
executor runs. Another driver sharing the store may recover the job only when
the lease has gone ``stale_after`` seconds without a heartbeat, and only after
it has watched that same unrenewed lease for ``confirm_after`` more seconds of
its own passes. The second condition matters after a sleep/resume: every
lease looks hours old at the first pass after wake-up, but a live holder's
next heartbeat lands well within ``confirm_after`` and clears it.

A copy of :mod:`gaia.daemon.scheduler.lease`, kept in the email package so it
runs on any supported amd-gaia core (the core module is newer than its floor).
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from gaia.logger import get_logger

log = get_logger(__name__)

#: A lease with no heartbeat for this long is a recovery candidate.
DEFAULT_STALE_AFTER_SECONDS = 600.0
#: How often a firing driver renews its lease.
DEFAULT_HEARTBEAT_SECONDS = 60.0


class Heartbeat:
    """Renew a lease from a background thread while a job's executor runs.

    ``beat`` performs one renewal and returns False once the lease is no
    longer ours (another driver recovered it); the heartbeat then stops and
    says so. A renewal that raises is logged and retried at the next tick,
    because one locked write must not end a lease that is still live.
    """

    def __init__(self, beat: Callable[[], bool], interval: float, job_id: str) -> None:
        self._beat = beat
        self._interval = interval
        self._job_id = job_id
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def __enter__(self) -> "Heartbeat":
        self._thread = threading.Thread(
            target=self._run, name=f"lease-heartbeat-{self._job_id}", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval + 5)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                if not self._beat():
                    log.warning(
                        "Lost the lease on firing job %s: another driver "
                        "recovered it",
                        self._job_id,
                    )
                    return
            except Exception:  # noqa: BLE001 - logged; the next tick retries
                log.exception("Could not renew the lease on job %s", self._job_id)


class StaleLeases:
    """Decide which stale leases have been unrenewed long enough to recover.

    One instance per driver. ``confirm`` is given the rows a store reports as
    stale on this pass; it returns those whose ``(claim_id, claimed_at)`` has
    not changed across ``confirm_after`` seconds of this driver's passes.
    """

    def __init__(self, confirm_after: float) -> None:
        self._confirm_after = confirm_after
        self._first_seen: Dict[str, Tuple[Tuple[Any, Any], float]] = {}

    def confirm(self, rows: List[Mapping[str, Any]], now: float) -> List[Mapping]:
        due: List[Mapping[str, Any]] = []
        seen: Dict[str, Tuple[Tuple[Any, Any], float]] = {}
        for row in rows:
            lease = (row["claim_id"], row["claimed_at"])
            previous = self._first_seen.get(row["job_id"])
            if previous is None or previous[0] != lease:
                seen[row["job_id"]] = (lease, now)
                continue
            seen[row["job_id"]] = previous
            if now - previous[1] >= self._confirm_after:
                due.append(row)
        self._first_seen = seen
        return due


__all__ = [
    "DEFAULT_HEARTBEAT_SECONDS",
    "DEFAULT_STALE_AFTER_SECONDS",
    "Heartbeat",
    "StaleLeases",
]
