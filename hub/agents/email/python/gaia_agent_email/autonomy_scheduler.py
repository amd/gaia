# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Scheduled driver for the full-autonomy cycle (#1115 / #557).

The email agent's ``run_autonomy_cycle`` is the observe→decide→act pass; this
module is what *drives* it on a cadence so the user never has to trigger it by
hand — the "handle all my email" half of full autonomy.

Three layers, mirroring ``briefing.py`` so the two schedulers behave alike:

- :class:`AutonomyScheduleConfig` — explicit, **off by default**. Sourced from
  environment variables on the sidecar process; an invalid value raises at
  startup rather than silently coercing to a guess.
- :func:`run_autonomy_job` — one cycle: build (or accept an injected) agent at
  the configured level, run ``run_autonomy_cycle``, return the report. **This is
  the dispatcher seam** the daemon clock (#2156) and any cron driver call —
  exactly as ``run_briefing_job`` is for the briefing.
- :class:`AutonomyScheduler` — a minimal asyncio interval timer that drives
  :func:`run_autonomy_job` inside a standalone sidecar today.

Daemon supervision (V2-15, #2156): when the GAIA daemon spawns this sidecar it
drives autonomy from its single reconciled clock, so ``server.py`` does NOT
start this in-process timer (see
:func:`gaia_agent_email.supervision.is_daemon_supervised`). Standalone /
bare-integrator runs keep the timer live — the same seam the briefing uses.

An agent is built per run and torn down; all trust state lives on-disk in the
agent's ``state.db`` ledger, so learning accumulates across runs exactly as a
long-lived agent would — the same statelessness ``run_briefing_job`` relies on.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from gaia.logger import get_logger

from gaia_agent_email.trust import AUTONOMY_LEVELS, LEVEL_EARN_TRUST, LEVEL_OFF

logger = get_logger(__name__)

DEFAULT_AUTONOMY_INTERVAL_MINUTES = 15
DEFAULT_AUTONOMY_MAX_MESSAGES = 25

_TRUE_VALUES = frozenset({"1", "true", "yes"})
_FALSE_VALUES = frozenset({"0", "false", "no", ""})

ENV_ENABLED = "GAIA_EMAIL_AUTONOMY_ENABLED"
ENV_LEVEL = "GAIA_EMAIL_AUTONOMY_LEVEL"
ENV_INTERVAL = "GAIA_EMAIL_AUTONOMY_INTERVAL_MINUTES"
ENV_MAX_MESSAGES = "GAIA_EMAIL_AUTONOMY_MAX_MESSAGES"


class AutonomyConfigError(ValueError):
    """Raised when the autonomy schedule configuration is invalid.

    Startup-time error — the sidecar refuses to start rather than run
    autonomy on a guessed cadence or level.
    """


@dataclass(frozen=True)
class AutonomyScheduleConfig:
    """Explicit schedule configuration for the autonomy cycle.

    ``enabled`` defaults to **False** — autonomy never runs on a timer unless
    the user (or the host launching the sidecar) opts in explicitly, matching
    the safe-by-default posture of the whole feature.
    """

    enabled: bool = False
    level: str = LEVEL_EARN_TRUST
    interval_minutes: int = DEFAULT_AUTONOMY_INTERVAL_MINUTES
    max_messages: int = DEFAULT_AUTONOMY_MAX_MESSAGES

    def validate(self) -> None:
        """Raise :class:`AutonomyConfigError` on any invalid field."""
        if self.level not in AUTONOMY_LEVELS:
            raise AutonomyConfigError(
                f"{ENV_LEVEL}={self.level!r} must be one of {list(AUTONOMY_LEVELS)}."
            )
        if self.enabled and self.level == LEVEL_OFF:
            raise AutonomyConfigError(
                f"{ENV_ENABLED} is true but {ENV_LEVEL}=off — that would schedule "
                "a cycle that does nothing. Pick 'suggest', 'earn_trust', or "
                "'full', or leave autonomy disabled."
            )
        if self.interval_minutes < 1:
            raise AutonomyConfigError(
                f"{ENV_INTERVAL}={self.interval_minutes!r} must be a positive "
                "number of minutes."
            )
        if not 1 <= self.max_messages <= 100:
            raise AutonomyConfigError(
                f"{ENV_MAX_MESSAGES}={self.max_messages!r} must be between 1 and 100."
            )

    @classmethod
    def from_env(
        cls, environ: Optional[Dict[str, str]] = None
    ) -> "AutonomyScheduleConfig":
        """Build the config from environment variables, validating eagerly.

        Unset variables take the documented defaults (disabled, earn_trust,
        15 min, 25). An unparseable value raises — never a silent fallback.
        """
        env = os.environ if environ is None else environ

        raw_enabled = env.get(ENV_ENABLED, "").strip().lower()
        if raw_enabled in _TRUE_VALUES:
            enabled = True
        elif raw_enabled in _FALSE_VALUES:
            enabled = False
        else:
            raise AutonomyConfigError(
                f"{ENV_ENABLED}={env.get(ENV_ENABLED)!r} is not a valid boolean. "
                "Use 'true'/'1'/'yes' to enable, or unset it (off by default)."
            )

        level = env.get(ENV_LEVEL, LEVEL_EARN_TRUST).strip().lower()

        raw_interval = env.get(
            ENV_INTERVAL, str(DEFAULT_AUTONOMY_INTERVAL_MINUTES)
        ).strip()
        try:
            interval_minutes = int(raw_interval)
        except ValueError as e:
            raise AutonomyConfigError(
                f"{ENV_INTERVAL}={raw_interval!r} is not an integer number of "
                "minutes."
            ) from e

        raw_max = env.get(ENV_MAX_MESSAGES, str(DEFAULT_AUTONOMY_MAX_MESSAGES)).strip()
        try:
            max_messages = int(raw_max)
        except ValueError as e:
            raise AutonomyConfigError(
                f"{ENV_MAX_MESSAGES}={raw_max!r} is not an integer."
            ) from e

        config = cls(
            enabled=enabled,
            level=level,
            interval_minutes=interval_minutes,
            max_messages=max_messages,
        )
        config.validate()
        return config


# ---------------------------------------------------------------------------
# The job — the dispatcher seam (mirrors run_briefing_job)
# ---------------------------------------------------------------------------


def _build_autonomy_agent(level: str) -> Any:
    """Build a headless EmailTriageAgent at the given autonomy level.

    All trust state is on-disk (``state.db`` ledger, ``memory.db`` preferences),
    so a per-run agent accumulates learning across runs like a long-lived one.
    """
    from gaia_agent_email.agent import EmailTriageAgent
    from gaia_agent_email.config import EmailAgentConfig

    return EmailTriageAgent(config=EmailAgentConfig(autonomy_level=level))


def run_autonomy_job(
    agent: Any = None,
    *,
    level: str = LEVEL_EARN_TRUST,
    max_messages: int = DEFAULT_AUTONOMY_MAX_MESSAGES,
    build_agent: Callable[[str], Any] = _build_autonomy_agent,
) -> Dict[str, Any]:
    """Run one autonomy cycle and return its report. No user prompt involved.

    This is the one-shot entry the scheduler drives and the seam the daemon
    clock / a cron dispatcher call. ``agent`` and ``build_agent`` are injection
    seams for tests; by default a fresh agent is built at ``level`` and torn
    down after the run so the job holds no resources between fires.
    """
    owned = agent is None
    if owned:
        agent = build_agent(level)
    try:
        return agent.run_autonomy_cycle({"max_messages": max_messages})
    finally:
        if owned:
            close = getattr(agent, "close_db", None)
            if callable(close):
                close()


# ---------------------------------------------------------------------------
# In-process interval timer (until the daemon clock drives it)
# ---------------------------------------------------------------------------


class AutonomyScheduler:
    """Asyncio interval timer that runs :func:`run_autonomy_job` in the email
    sidecar's event loop.

    A **disabled** config produces no task — :meth:`start` returns ``False`` and
    logs that autonomy is off. A failed run (mailbox disconnected, provider
    outage) is logged with its actionable message and the schedule continues; it
    is never retried silently. When the daemon supervises the sidecar it owns the
    clock, so ``server.py`` does not start this timer.
    """

    def __init__(
        self,
        config: AutonomyScheduleConfig,
        *,
        run_job: Callable[..., Dict[str, Any]] = run_autonomy_job,
    ) -> None:
        config.validate()
        self.config = config
        self._run_job = run_job
        self._task: Optional["asyncio.Task"] = None
        # Seconds between fires. Held as an instance attr (not recomputed in the
        # loop) so a test can shrink it without waiting a real interval.
        self._delay_seconds = config.interval_minutes * 60

    def start(self) -> bool:
        """Start the interval loop. Returns ``True`` if scheduling began,
        ``False`` when autonomy is disabled (the default)."""
        if not self.config.enabled:
            logger.info(
                "Email autonomy is disabled (set %s=true on the sidecar to "
                "enable it).",
                ENV_ENABLED,
            )
            return False
        if self._task is not None and not self._task.done():
            raise RuntimeError("AutonomyScheduler is already running.")
        self._task = asyncio.get_running_loop().create_task(
            self._loop(), name="email-autonomy-scheduler"
        )
        logger.info(
            "Email autonomy scheduled every %d min at level %r (up to %d "
            "messages/cycle).",
            self.config.interval_minutes,
            self.config.level,
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
            await asyncio.sleep(self._delay_seconds)
            try:
                report = await asyncio.to_thread(
                    self._run_job,
                    level=self.config.level,
                    max_messages=self.config.max_messages,
                )
                logger.info(
                    "Autonomy cycle: %d executed, %d proposed, %d skipped.",
                    len(report.get("executed", [])),
                    len(report.get("proposals", [])),
                    report.get("skipped", 0),
                )
            except Exception:
                logger.exception(
                    "Scheduled autonomy cycle failed; next attempt in %d min.",
                    self.config.interval_minutes,
                )


__all__ = [
    "AutonomyConfigError",
    "AutonomyScheduleConfig",
    "AutonomyScheduler",
    "ENV_ENABLED",
    "ENV_INTERVAL",
    "ENV_LEVEL",
    "ENV_MAX_MESSAGES",
    "run_autonomy_job",
]
