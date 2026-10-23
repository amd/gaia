# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Start learning cycles in the background, on demand or from the daemon clock.

The daemon clock fires the ``learning`` job every
:data:`CHECK_INTERVAL_SECONDS`. Its executor only checks the cheap gates; when
they are open it starts ``gaia learn run --trigger scheduled`` as its own
process, so a training run of tens of minutes never holds the clock's thread.
"""

import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict

from gaia.learning.config import load_config
from gaia.learning.errors import LearningError
from gaia.learning.paths import learning_home
from gaia.logger import get_logger

log = get_logger(__name__)

JOB_SOURCE = "learning"
JOB_ID = "learning-nightly"
CHECK_INTERVAL_SECONDS = 15 * 60


def spawn_cycle(trigger: str) -> int:
    """Start ``gaia learn run`` detached; return its pid. Its output goes to
    ``<gaia_home>/learning/cycle.log``."""
    from gaia.utils.terminal import background_popen_kwargs

    home = learning_home()
    home.mkdir(parents=True, exist_ok=True)
    with open(home / "cycle.log", "a", encoding="utf-8") as out:
        out.write(f"\n=== {datetime.now(timezone.utc).isoformat()} trigger={trigger}\n")
        out.flush()
        # The child keeps its own copy of the log handle; it outlives this one.
        proc = subprocess.Popen(  # pylint: disable=consider-using-with
            [sys.executable, "-m", "gaia.cli", "learn", "run", "--trigger", trigger],
            stdout=out,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=dict(os.environ),
            **background_popen_kwargs(),
        )
    return proc.pid


def scheduled_executor(_job: Dict[str, Any], _db: Any) -> None:
    """Daemon-clock executor for the nightly learning job.

    The clock marks a job that raises as failed and never fires it again, so
    a failed check is recorded as a run the user sees instead, and the job
    stays scheduled for the next check.
    """
    try:
        _check_and_spawn()
    except (LearningError, OSError, sqlite3.Error) as e:
        log.error("learning: the nightly check failed: %s", e)
        _record_failed_check(f"{type(e).__name__}: {e}")


def _record_failed_check(error: str) -> None:
    from gaia.learning.runs import RunRecord, list_runs, new_run_id

    latest = list_runs(limit=1)
    if (
        latest
        and latest[0].get("trigger") == "check"
        and latest[0].get("error") == error
    ):
        return  # the same failure every 15 minutes is one record, not dozens
    run = RunRecord(new_run_id(), trigger="check")
    run.finish("failed", error=error, summary=f"The nightly check failed: {error}")


def _check_and_spawn() -> None:
    from gaia.learning.cycle import Deps
    from gaia.learning.gates import check_gates
    from gaia.learning.runs import last_scheduled_start
    from gaia.learning.sources import last_activity

    cfg = load_config()
    if not cfg.enabled:
        return
    deps = Deps()
    gate = check_gates(
        cfg,
        now=datetime.now(timezone.utc),
        last_activity=last_activity(deps.chat_db(), deps.memory_db()),
        last_scheduled_start=last_scheduled_start(),
        toolchain_ready=deps.toolchain_ready(),
        ac_power=deps.ac_power(),
    )
    if not gate.open:
        log.debug(
            "learning: not starting tonight's cycle yet: %s", "; ".join(gate.reasons)
        )
        return
    pid = spawn_cycle("scheduled")
    log.info("learning: started the nightly cycle (pid %s)", pid)


def register_nightly_job() -> str:
    """Add the nightly job to the daemon clock once. Idempotent."""
    from gaia.daemon.paths import scheduler_db_path
    from gaia.daemon.scheduler import store
    from gaia.daemon.scheduler.models import KIND_RECURRING, STATUS_PENDING
    from gaia.database.mixin import DatabaseMixin

    class _DB(DatabaseMixin):
        pass

    db = _DB()
    db.init_db(str(scheduler_db_path()), quiet=True)
    try:
        store.init_schema(db)
        job = store.get_job(db, job_id=JOB_ID)
        if job is not None and job["status"] != STATUS_PENDING:
            # Re-arm a job the clock gave up on.
            db.update(
                "daemon_jobs",
                {"status": STATUS_PENDING, "error": None, "fire_at": time.time()},
                "job_id = :id",
                {"id": JOB_ID},
            )
        elif job is None:
            store.register_job(
                db,
                source=JOB_SOURCE,
                source_job_id="nightly",
                kind=KIND_RECURRING,
                interval_seconds=CHECK_INTERVAL_SECONDS,
                fire_at=time.time(),
                job_id=JOB_ID,
            )
    finally:
        db.close_db()
    return JOB_ID
