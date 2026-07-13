# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Scheduled daily inbox briefing (#1608).

Generates the ``pre_scan_inbox`` briefing on a daily schedule — without a
user prompt — and delivers it by persisting the envelope where any surface
(``GET /v1/email/briefing``, the Agent UI, a headless consumer reading the
JSON file) can pick it up. Classification is NOT re-implemented here: the
job calls the agent's own :func:`pre_scan_inbox_impl`, so the scheduled
briefing is byte-for-byte the same ``email_pre_scan`` envelope the chat
surface renders.

Three layers, each independently usable:

- :class:`BriefingScheduleConfig` — explicit configuration, **off by
  default**. Sourced from environment variables on the sidecar process
  (set by whoever launches it); invalid values raise at startup, never
  silently coerce.
- :func:`run_briefing_job` — one-shot: resolve the mailbox, run the
  pre-scan, deliver the record. **This is the dispatcher seam for the
  ``gaia schedule`` cron dispatcher (#1371) and the autonomy engine
  (#555):** when either lands, it invokes this function directly and the
  in-process :class:`BriefingScheduler` below becomes redundant.
- :class:`BriefingScheduler` — a minimal asyncio daily timer that drives
  :func:`run_briefing_job` inside the email sidecar today, so the feature
  does not wait on #1371/#555.

Environment variables (read by :meth:`BriefingScheduleConfig.from_env`):

- ``GAIA_EMAIL_BRIEFING_ENABLED`` — ``true``/``1``/``yes`` to enable;
  unset/``false``/``0``/``no`` disables (the default). Anything else is a
  configuration error.
- ``GAIA_EMAIL_BRIEFING_TIME`` — 24h local ``HH:MM`` fire time
  (default ``08:00``).
- ``GAIA_EMAIL_BRIEFING_MAX_MESSAGES`` — inbox messages to scan, 1–100
  (default 25, matching ``EmailPreScanRequest``).
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from gaia.logger import get_logger

logger = get_logger(__name__)

DEFAULT_BRIEFING_TIME = "08:00"
DEFAULT_BRIEFING_MAX_MESSAGES = 25

_TRUE_VALUES = frozenset({"1", "true", "yes"})
_FALSE_VALUES = frozenset({"0", "false", "no", ""})

ENV_ENABLED = "GAIA_EMAIL_BRIEFING_ENABLED"
ENV_TIME = "GAIA_EMAIL_BRIEFING_TIME"
ENV_MAX_MESSAGES = "GAIA_EMAIL_BRIEFING_MAX_MESSAGES"


class BriefingConfigError(ValueError):
    """Raised when the briefing schedule configuration is invalid.

    Startup-time error — the sidecar refuses to start rather than run with
    a guessed schedule.
    """


class BriefingUnavailableError(RuntimeError):
    """Raised when a scheduled briefing cannot run (e.g. no mailbox
    connected). Carries the same actionable detail the REST pre-scan
    returns, so a log line or dispatcher surface tells the user what to fix.
    """


def _parse_time_of_day(value: str) -> dt_time:
    """Parse a 24h ``HH:MM`` string, raising :class:`BriefingConfigError`
    with an actionable message on anything else."""
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except (TypeError, ValueError) as e:
        raise BriefingConfigError(
            f"{ENV_TIME}={value!r} is not a valid 24h HH:MM time. "
            "Set it like GAIA_EMAIL_BRIEFING_TIME=08:00."
        ) from e
    return parsed.time()


@dataclass(frozen=True)
class BriefingScheduleConfig:
    """Explicit schedule configuration for the daily inbox briefing.

    ``enabled`` defaults to **False** — the briefing never runs unless the
    user (or the host launching the sidecar) opts in explicitly.
    """

    enabled: bool = False
    time_of_day: str = DEFAULT_BRIEFING_TIME
    max_messages: int = DEFAULT_BRIEFING_MAX_MESSAGES

    def validate(self) -> None:
        """Raise :class:`BriefingConfigError` on any invalid field."""
        _parse_time_of_day(self.time_of_day)
        if not 1 <= self.max_messages <= 100:
            raise BriefingConfigError(
                f"{ENV_MAX_MESSAGES}={self.max_messages!r} must be between "
                "1 and 100 (the pre-scan request bound)."
            )

    @classmethod
    def from_env(cls, environ: Optional[Dict[str, str]] = None) -> "BriefingScheduleConfig":
        """Build the config from environment variables, validating eagerly.

        Unset variables take the documented defaults (disabled, 08:00, 25).
        An unparseable value raises :class:`BriefingConfigError` — never a
        silent fallback to the default.
        """
        env = os.environ if environ is None else environ

        raw_enabled = env.get(ENV_ENABLED, "").strip().lower()
        if raw_enabled in _TRUE_VALUES:
            enabled = True
        elif raw_enabled in _FALSE_VALUES:
            enabled = False
        else:
            raise BriefingConfigError(
                f"{ENV_ENABLED}={env.get(ENV_ENABLED)!r} is not a valid "
                "boolean. Use 'true'/'1'/'yes' to enable the daily briefing "
                "or unset it (the briefing is off by default)."
            )

        time_of_day = env.get(ENV_TIME, DEFAULT_BRIEFING_TIME).strip()

        raw_max = env.get(ENV_MAX_MESSAGES, str(DEFAULT_BRIEFING_MAX_MESSAGES)).strip()
        try:
            max_messages = int(raw_max)
        except ValueError as e:
            raise BriefingConfigError(
                f"{ENV_MAX_MESSAGES}={raw_max!r} is not an integer. "
                "Set a value between 1 and 100."
            ) from e

        config = cls(
            enabled=enabled, time_of_day=time_of_day, max_messages=max_messages
        )
        config.validate()
        return config


# ---------------------------------------------------------------------------
# Delivery — persist the briefing where any surface can pick it up
# ---------------------------------------------------------------------------


def briefing_path() -> Path:
    """Where the latest briefing lives.

    ``Path.home()`` is resolved at call time so test home-isolation
    fixtures are honored (same rule as ``EmailAgentConfig.resolved_db_path``).
    """
    return Path.home() / ".gaia" / "email" / "briefing_latest.json"


def persist_briefing(record: Dict[str, Any], path: Optional[Path] = None) -> Path:
    """Atomically write ``record`` as the latest briefing and log a summary.

    The default delivery sink: write-then-rename so a concurrent reader
    never sees a partial file.
    """
    dest = path or briefing_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    tmp.replace(dest)
    totals = record.get("briefing", {}).get("totals") or {}
    logger.info(
        "Daily inbox briefing delivered to %s (urgent=%s actionable=%s "
        "informational=%s suggested_archives=%s)",
        dest,
        totals.get("urgent", 0),
        totals.get("actionable", 0),
        totals.get("informational", 0),
        totals.get("suggested_archives", 0),
    )
    return dest


def load_latest_briefing(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Return the latest persisted briefing record, or ``None`` if no
    briefing has been generated yet.

    A present-but-unreadable file raises with the offending path — never
    silently treated as "no briefing".
    """
    src = path or briefing_path()
    if not src.exists():
        return None
    try:
        return json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise BriefingUnavailableError(
            f"Persisted briefing at {src} is unreadable: {e}. Delete the "
            "file and let the next scheduled run regenerate it."
        ) from e


# ---------------------------------------------------------------------------
# The job — the #1371 / #555 dispatcher seam
# ---------------------------------------------------------------------------


def _resolve_briefing_backend() -> Any:
    """Resolve the single connected mailbox for a scheduled briefing.

    Reuses the REST pre-scan's fail-loud resolver (0 connected → error,
    2+ → error, 1 → live backend), translating its ``HTTPException`` into
    :class:`BriefingUnavailableError` so non-HTTP callers (the scheduler,
    a cron dispatcher) get a plain exception with the same actionable text.
    """
    from fastapi import HTTPException

    from gaia_agent_email.api_routes import get_prescan_backend

    try:
        return get_prescan_backend()
    except HTTPException as e:
        raise BriefingUnavailableError(str(e.detail)) from e


def run_briefing_job(
    backend: Any = None,
    *,
    max_messages: int = DEFAULT_BRIEFING_MAX_MESSAGES,
    sink: Optional[Callable[[Dict[str, Any]], Any]] = None,
) -> Dict[str, Any]:
    """Generate and deliver one inbox briefing. No user prompt involved.

    This is the one-shot entry the scheduler drives — and the seam the
    ``gaia schedule`` dispatcher (#1371) / autonomy engine (#555) will call
    when they land. The briefing content is the agent's own
    ``pre_scan_inbox_impl`` envelope (``kind == "email_pre_scan"``);
    nothing is re-classified here.

    ``backend`` and ``sink`` are injection seams for tests and dispatchers;
    they default to the live single-mailbox resolver and
    :func:`persist_briefing`.
    """
    from gaia_agent_email.tools.read_tools import pre_scan_inbox_impl

    if backend is None:
        backend = _resolve_briefing_backend()
    envelope = pre_scan_inbox_impl(backend, max_messages=max_messages)
    record = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "briefing": envelope,
    }
    (sink or persist_briefing)(record)
    return record


# ---------------------------------------------------------------------------
# In-process daily timer (until the #1371 dispatcher lands)
# ---------------------------------------------------------------------------


def seconds_until_next_run(time_of_day: str, now: datetime) -> float:
    """Seconds from ``now`` (local) until the next daily ``HH:MM`` fire.

    Pure so the schedule math is unit-testable. If today's fire time has
    already passed (or is exactly now), the next fire is tomorrow.
    """
    target = _parse_time_of_day(time_of_day)
    candidate = now.replace(
        hour=target.hour, minute=target.minute, second=0, microsecond=0
    )
    if candidate <= now:
        candidate += timedelta(days=1)
    return (candidate - now).total_seconds()


class BriefingScheduler:
    """Asyncio daily timer that runs :func:`run_briefing_job` in the email
    sidecar's event loop.

    A **disabled** config produces no task and therefore no briefing —
    :meth:`start` returns ``False`` and logs that the schedule is off.
    A failed run (mailbox disconnected, provider outage) is logged with its
    actionable message and the schedule continues; it is never retried
    silently or downgraded to a partial briefing.
    """

    def __init__(
        self,
        config: BriefingScheduleConfig,
        *,
        run_job: Callable[..., Dict[str, Any]] = run_briefing_job,
    ) -> None:
        config.validate()
        self.config = config
        self._run_job = run_job
        self._task: Optional[asyncio.Task] = None

    def start(self) -> bool:
        """Start the daily loop. Returns ``True`` if scheduling began,
        ``False`` when the schedule is disabled (the default)."""
        if not self.config.enabled:
            logger.info(
                "Daily inbox briefing is disabled (set %s=true on the email "
                "sidecar to enable it).",
                ENV_ENABLED,
            )
            return False
        if self._task is not None and not self._task.done():
            raise RuntimeError("BriefingScheduler is already running.")
        self._task = asyncio.get_running_loop().create_task(
            self._loop(), name="email-briefing-scheduler"
        )
        logger.info(
            "Daily inbox briefing scheduled at %s (local), scanning up to "
            "%d messages.",
            self.config.time_of_day,
            self.config.max_messages,
        )
        return True

    async def stop(self) -> None:
        """Cancel the loop (idempotent)."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        while True:
            delay = seconds_until_next_run(self.config.time_of_day, datetime.now())
            await asyncio.sleep(delay)
            try:
                await asyncio.to_thread(
                    self._run_job, max_messages=self.config.max_messages
                )
            except BriefingUnavailableError as e:
                logger.error("Scheduled inbox briefing skipped: %s", e)
            except Exception:
                logger.exception(
                    "Scheduled inbox briefing failed; next attempt at %s.",
                    self.config.time_of_day,
                )


__all__ = [
    "BriefingConfigError",
    "BriefingScheduleConfig",
    "BriefingScheduler",
    "BriefingUnavailableError",
    "ENV_ENABLED",
    "ENV_MAX_MESSAGES",
    "ENV_TIME",
    "briefing_path",
    "load_latest_briefing",
    "persist_briefing",
    "run_briefing_job",
    "seconds_until_next_run",
]
