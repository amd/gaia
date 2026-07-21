# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Autonomy scheduler / driver tests (#1115 / #557).

Covers the config (env parsing + eager validation), the ``run_autonomy_job``
dispatcher seam (agent ownership + teardown), and the scheduler's enable gate.
Mirrors the briefing scheduler's test posture — no Lemonade, no Gmail.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.autonomy_scheduler import (  # noqa: E402
    ENV_ENABLED,
    ENV_INTERVAL,
    ENV_LEVEL,
    ENV_MAX_MESSAGES,
    AutonomyConfigError,
    AutonomyScheduleConfig,
    AutonomyScheduler,
    run_autonomy_job,
)

# ---------------------------------------------------------------------------
# Config: from_env + validate
# ---------------------------------------------------------------------------


def test_defaults_are_disabled():
    cfg = AutonomyScheduleConfig.from_env({})
    assert cfg.enabled is False
    assert cfg.level == "earn_trust"
    assert cfg.interval_minutes == 15
    assert cfg.max_messages == 25


def test_from_env_enables_and_parses():
    cfg = AutonomyScheduleConfig.from_env(
        {
            ENV_ENABLED: "true",
            ENV_LEVEL: "full",
            ENV_INTERVAL: "5",
            ENV_MAX_MESSAGES: "40",
        }
    )
    assert cfg.enabled is True
    assert cfg.level == "full"
    assert cfg.interval_minutes == 5
    assert cfg.max_messages == 40


def test_bad_enabled_raises():
    with pytest.raises(AutonomyConfigError):
        AutonomyScheduleConfig.from_env({ENV_ENABLED: "maybe"})


def test_bad_level_raises():
    with pytest.raises(AutonomyConfigError):
        AutonomyScheduleConfig.from_env({ENV_ENABLED: "true", ENV_LEVEL: "turbo"})


def test_enabled_with_off_level_raises():
    with pytest.raises(AutonomyConfigError):
        AutonomyScheduleConfig.from_env({ENV_ENABLED: "true", ENV_LEVEL: "off"})


def test_bad_interval_and_max_raise():
    with pytest.raises(AutonomyConfigError):
        AutonomyScheduleConfig.from_env({ENV_ENABLED: "true", ENV_INTERVAL: "0"})
    with pytest.raises(AutonomyConfigError):
        AutonomyScheduleConfig.from_env({ENV_MAX_MESSAGES: "999"})


# ---------------------------------------------------------------------------
# run_autonomy_job — the dispatcher seam
# ---------------------------------------------------------------------------


class _FakeAgent:
    def __init__(self):
        self.calls = []
        self.closed = False

    def run_autonomy_cycle(self, context=None):
        self.calls.append(context)
        return {"level": "full", "executed": [], "proposals": [], "skipped": 0}

    def close_db(self):
        self.closed = True


def test_run_job_builds_and_tears_down_owned_agent():
    built = {}

    def _factory(level):
        agent = _FakeAgent()
        built["agent"] = agent
        built["level"] = level
        return agent

    report = run_autonomy_job(level="full", max_messages=10, build_agent=_factory)
    assert report["executed"] == []
    assert built["level"] == "full"
    assert built["agent"].calls == [{"max_messages": 10}]
    assert built["agent"].closed is True  # owned agent is torn down


def test_run_job_does_not_close_injected_agent():
    agent = _FakeAgent()
    run_autonomy_job(agent, max_messages=7)
    assert agent.calls == [{"max_messages": 7}]
    assert agent.closed is False  # caller owns an injected agent


def test_run_job_closes_owned_agent_even_on_error():
    class _Boom(_FakeAgent):
        def run_autonomy_cycle(self, context=None):
            raise RuntimeError("mailbox down")

    boom = _Boom()
    with pytest.raises(RuntimeError):
        run_autonomy_job(build_agent=lambda level: boom)
    assert boom.closed is True


# ---------------------------------------------------------------------------
# Scheduler enable gate + fire
# ---------------------------------------------------------------------------


def test_disabled_scheduler_does_not_start():
    async def _run():
        sched = AutonomyScheduler(AutonomyScheduleConfig(enabled=False))
        assert sched.start() is False
        await sched.stop()  # idempotent when never started

    asyncio.run(_run())


def test_enabled_scheduler_fires_the_job():
    """The loop fires run_job with the configured level/budget. The delay seam
    is shrunk so the test doesn't wait a real minute; the job signals an event
    and the test stops the scheduler after the first fire."""
    fired = []
    done = asyncio.Event()

    def _job(*, level, max_messages):
        fired.append((level, max_messages))
        done.set()
        return {"executed": [], "proposals": [], "skipped": 0}

    async def _run():
        cfg = AutonomyScheduleConfig(
            enabled=True, level="earn_trust", interval_minutes=1, max_messages=25
        )
        sched = AutonomyScheduler(cfg, run_job=_job)
        sched._delay_seconds = 0.01  # test seam: fire almost immediately
        assert sched.start() is True
        await asyncio.wait_for(done.wait(), timeout=2.0)
        await sched.stop()

    asyncio.run(_run())
    assert fired[0] == ("earn_trust", 25)
