# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Best-effort user notification for unattended scheduled-email failures.

The scheduler must keep durable failure state even when the UI is unavailable.
This helper therefore never changes the scheduler outcome: it emits a desktop
notification when the host supports the existing GAIA notification sink and
logs a warning when delivery is unavailable.
"""

from __future__ import annotations

from typing import Any, Mapping

from gaia.logger import get_logger

log = get_logger(__name__)


def notify_scheduled_failure(
    job: Mapping[str, Any], error: str, *, source: str
) -> None:
    """Tell the user about one failed unattended email job.

    ``source`` distinguishes the standalone email polling driver from daemon
    recovery in logs. Importing the sink lazily keeps the email sidecar usable
    in minimal/frozen environments where the core schedule package is absent.
    """
    payload = job.get("payload") or {}
    nested_payload = payload.get("payload") or {}
    kind = str(payload.get("kind") or job.get("kind") or "")
    subject = str(payload.get("subject") or nested_payload.get("subject") or "")
    is_recovery = "recovery" in source.lower() or "may already" in error.lower()
    if kind == "scheduled_send":
        detail = (
            f"the scheduled email ({subject})" if subject else "the scheduled email"
        )
        if is_recovery:
            title = "Scheduled email may not have been sent"
            message = (
                f"GAIA could not confirm {detail} was sent. It may already have "
                f"been sent; check your Sent folder before retrying. {error}"
            )
        else:
            title = "Scheduled email failed"
            message = f"GAIA could not send {detail}. {error}"
    elif kind == "snooze":
        detail = f"the snoozed email ({subject})" if subject else "the snoozed email"
        title = "Snoozed email restore failed"
        message = f"GAIA could not restore {detail}. {error}"
    else:
        detail = "the scheduled task"
        title = "Scheduled task failed"
        message = f"GAIA could not complete {detail}. {error}"
    try:
        from gaia.schedule import sinks

        sinks.dispatch("notification", {"title": title}, message)
    except Exception as exc:  # notification must never mask the durable failure
        log.warning(
            "email scheduler: could not notify user about failed job %s (%s): %s",
            job.get("job_id", "unknown"),
            source,
            exc,
        )


__all__ = ["notify_scheduled_failure"]
