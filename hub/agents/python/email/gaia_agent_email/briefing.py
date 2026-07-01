# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Scheduled daily inbox briefing (#1608).

The briefing CONTENT is the existing pre-scan envelope — this module adds
the SCHEDULED-JOB seam around ``pre_scan_inbox_impl``, not a new scan:

- :func:`load_schedule` / :func:`save_schedule` persist the user's
  :class:`~gaia_agent_email.contract.BriefingSchedule` (OFF by default) at
  ``~/.gaia/email/briefing.json``.
- :func:`run_scheduled_briefing` is the trigger a host scheduler invokes.
  It re-checks ``enabled`` itself, so a disabled schedule never touches the
  mailbox regardless of who fires the trigger.

The sidecar does NOT run a timer. Scheduling is owned by the host — the
autonomy engine (#555) once it lands, or any cron-like runner hitting
``POST /v1/email/briefing/run`` in the meantime. Push delivery is likewise
the host's job; until it exists, each run atomically persists its envelope
to ``~/.gaia/email/briefing_latest.json`` so a consumer can pull the most
recent briefing without having been the live caller.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import ValidationError

from gaia_agent_email.contract import BriefingSchedule

from gaia.logger import get_logger

log = get_logger(__name__)


class BriefingConfigError(ValueError):
    """Raised when the persisted briefing schedule is unreadable or invalid."""


def briefing_schedule_path() -> Path:
    """Where the briefing schedule persists.

    ``Path.home()`` resolves at call time so test HOME/tmp isolation is
    honored (same rationale as ``EmailAgentConfig.resolved_db_path``).
    """
    return Path.home() / ".gaia" / "email" / "briefing.json"


def latest_briefing_path() -> Path:
    """Where the most recent briefing envelope persists."""
    return Path.home() / ".gaia" / "email" / "briefing_latest.json"


def load_schedule(path: Optional[Path] = None) -> BriefingSchedule:
    """Load the persisted schedule; an absent file is the documented default
    (disabled — #1608 requires off-by-default).

    A file that exists but cannot be parsed or validated raises
    :class:`BriefingConfigError` — never a silent fall-back to defaults,
    which would quietly disable a briefing the user enabled (or worse,
    re-enable one they disabled by hand-editing the file badly).
    """
    p = Path(path) if path is not None else briefing_schedule_path()
    if not p.exists():
        return BriefingSchedule()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise BriefingConfigError(
            f"Briefing schedule at {p} is unreadable: {e}. Fix or delete the "
            "file, or overwrite it via PUT /v1/email/briefing/schedule."
        ) from e
    try:
        return BriefingSchedule.model_validate(raw)
    except ValidationError as e:
        raise BriefingConfigError(
            f"Briefing schedule at {p} is invalid: {e}. Expected "
            '{"enabled": bool, "time": "HH:MM", "max_messages": 1-100}. '
            "Overwrite it via PUT /v1/email/briefing/schedule."
        ) from e


def save_schedule(
    schedule: BriefingSchedule, path: Optional[Path] = None
) -> Path:
    """Persist ``schedule`` atomically (write-then-rename) and return the path."""
    p = Path(path) if path is not None else briefing_schedule_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(schedule.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(p)
    return p


def run_scheduled_briefing(
    backend: Any,
    *,
    schedule: Optional[BriefingSchedule] = None,
    schedule_path: Optional[Path] = None,
    latest_path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """The scheduled-job trigger: pre-scan the inbox into a briefing envelope.

    This is the seam a host scheduler (autonomy engine #555, cron, the REST
    trigger) invokes on its tick — no user prompt involved. When the schedule
    is disabled (the default) it returns ``None`` WITHOUT touching the
    mailbox: that is the documented contract of an off switch, logged so the
    skip is observable, not a silent degradation.

    When enabled, the briefing reuses ``pre_scan_inbox_impl`` — the exact
    classification path behind the agent-loop tool and ``POST
    /v1/email/prescan`` — wraps it with run metadata (``kind:
    "email_briefing"``), atomically persists it to :func:`latest_briefing_path`
    as the interim pull-based delivery surface, and returns it. Scan and
    write failures propagate to the caller.
    """
    sched = schedule if schedule is not None else load_schedule(schedule_path)
    if not sched.enabled:
        log.info(
            "daily briefing schedule is disabled — skipping (enable via "
            "PUT /v1/email/briefing/schedule)"
        )
        return None

    from gaia_agent_email.tools.read_tools import pre_scan_inbox_impl

    pre_scan = pre_scan_inbox_impl(backend, max_messages=sched.max_messages)
    stamp = now if now is not None else datetime.now(timezone.utc)
    envelope: Dict[str, Any] = {
        "kind": "email_briefing",
        "generated_at": stamp.isoformat(),
        "schedule": sched.model_dump(),
        "pre_scan": pre_scan,
    }

    lp = Path(latest_path) if latest_path is not None else latest_briefing_path()
    lp.parent.mkdir(parents=True, exist_ok=True)
    tmp = lp.with_name(lp.name + ".tmp")
    tmp.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    tmp.replace(lp)
    log.info("daily briefing generated and persisted to %s", lp)
    return envelope


__all__ = [
    "BriefingConfigError",
    "briefing_schedule_path",
    "latest_briefing_path",
    "load_schedule",
    "save_schedule",
    "run_scheduled_briefing",
]
