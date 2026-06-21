# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the schedule daemon (``gaia.schedule.daemon``) and the
store-wiring of :func:`gaia.schedule.runner.fire`.

Covers:
  - ``next_fire_time`` returns an ISO string for a valid cron, None-safe shape.
  - ``build_scheduler`` arms only enabled schedules (one job per enabled).
  - ``_job`` marks the run on success and logs-but-does-not-raise on failure
    (a failing job must not kill the daemon, and must NOT mark the run).
  - ``runner.fire`` runs without a real LLM (AgentSDK mocked) and routes the
    agent output through ``sinks.dispatch``.

Hermetic: no network, no real LLM, no filesystem outside ``tmp_path``.
``AgentSDK``/``AgentConfig`` are imported lazily inside ``runner.fire`` so the
patch targets their source module ``gaia.chat.sdk``.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from gaia.schedule import daemon, runner
from gaia.schedule.store import Schedule, TomlScheduleStore

# runner.fire imports AgentSDK/AgentConfig lazily from gaia.chat.sdk.
_AGENT_SDK = "gaia.chat.sdk.AgentSDK"
_AGENT_CONFIG = "gaia.chat.sdk.AgentConfig"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_schedule(name: str = "daily", **overrides) -> Schedule:
    kwargs = {"name": name, "cron": "0 9 * * *", "prompt": "say hello"}
    kwargs.update(overrides)
    return Schedule(**kwargs)


def _store_with(tmp_path, *schedules) -> TomlScheduleStore:
    store = TomlScheduleStore(tmp_path / "schedules.toml")
    for s in schedules:
        store.add(s)
    return store


# ===========================================================================
# 1. next_fire_time
# ===========================================================================


class TestNextFireTime:

    def test_valid_cron_returns_iso_string(self):
        result = daemon.next_fire_time("0 9 * * *")
        assert isinstance(result, str)
        # Parseable as ISO8601 — the contract the store persists.
        datetime.fromisoformat(result)

    def test_every_minute_cron_returns_iso_string(self):
        result = daemon.next_fire_time("* * * * *")
        assert isinstance(result, str)
        datetime.fromisoformat(result)


# ===========================================================================
# 2. build_scheduler
# ===========================================================================


class TestBuildScheduler:

    def test_arms_only_enabled_schedules(self, tmp_path):
        store = _store_with(
            tmp_path,
            _make_schedule("on", enabled=True),
            _make_schedule("off", enabled=False),
        )
        scheduler = daemon.build_scheduler(store)
        jobs = scheduler.get_jobs()
        assert len(jobs) == 1
        assert jobs[0].id == "on"

    def test_empty_store_arms_no_jobs(self, tmp_path):
        store = _store_with(tmp_path)
        scheduler = daemon.build_scheduler(store)
        assert scheduler.get_jobs() == []

    def test_all_enabled_arms_each(self, tmp_path):
        store = _store_with(
            tmp_path,
            _make_schedule("a", enabled=True),
            _make_schedule("b", enabled=True),
        )
        scheduler = daemon.build_scheduler(store)
        assert {job.id for job in scheduler.get_jobs()} == {"a", "b"}


# ===========================================================================
# 3. _job — success and failure paths
# ===========================================================================


class TestJob:

    def test_success_marks_run(self, mocker, tmp_path):
        store = _store_with(tmp_path, _make_schedule("a"))
        sched = store.get("a")

        mock_fire = mocker.patch.object(runner, "fire", return_value="output")
        mock_mark = mocker.patch.object(store, "mark_run")

        daemon._job(sched, store)

        mock_fire.assert_called_once_with(sched)
        mock_mark.assert_called_once()
        # last_run (positional[1]) is an ISO timestamp; next_run is keyword.
        call = mock_mark.call_args
        assert call.args[0] == "a"
        datetime.fromisoformat(call.args[1])
        assert "next_run" in call.kwargs

    def test_success_persists_last_run_via_real_store(self, mocker, tmp_path):
        # End-to-end through the real store (only fire is mocked).
        store = _store_with(tmp_path, _make_schedule("a"))
        sched = store.get("a")
        mocker.patch.object(runner, "fire", return_value="output")

        daemon._job(sched, store)

        reloaded = store.get("a")
        assert reloaded.last_run is not None
        assert reloaded.next_run is not None

    def test_failure_does_not_raise(self, mocker, tmp_path):
        store = _store_with(tmp_path, _make_schedule("a"))
        sched = store.get("a")
        mocker.patch.object(runner, "fire", side_effect=RuntimeError("kaboom"))
        mock_mark = mocker.patch.object(store, "mark_run")

        # Must NOT propagate — the daemon stays alive.
        daemon._job(sched, store)

        # And must NOT mark the run on failure.
        mock_mark.assert_not_called()

    def test_failure_logs_exception(self, mocker, tmp_path):
        store = _store_with(tmp_path, _make_schedule("a"))
        sched = store.get("a")
        mocker.patch.object(runner, "fire", side_effect=RuntimeError("kaboom"))
        mock_log = mocker.patch.object(daemon.log, "exception")

        daemon._job(sched, store)

        # Loud failure: the traceback is logged (no silent swallow).
        mock_log.assert_called_once()

    def test_failure_leaves_store_unmodified(self, mocker, tmp_path):
        store = _store_with(tmp_path, _make_schedule("a"))
        sched = store.get("a")
        mocker.patch.object(runner, "fire", side_effect=RuntimeError("kaboom"))

        daemon._job(sched, store)

        reloaded = store.get("a")
        assert reloaded.last_run is None
        assert reloaded.next_run is None


# ===========================================================================
# 4. runner.fire — no real LLM, output routed to sink
# ===========================================================================


class TestRunnerFire:

    def test_fire_routes_agent_output_to_sink(self, mocker):
        mock_sdk_cls = mocker.patch(_AGENT_SDK)
        mocker.patch(_AGENT_CONFIG)
        mock_sdk_cls.return_value.send.return_value.text = "agent says hi"
        mock_dispatch = mocker.patch.object(runner.sinks, "dispatch")

        sched = _make_schedule("a", prompt="do the thing", sink="stdout", sink_args={})
        result = runner.fire(sched)

        assert result == "agent says hi"
        # Agent was driven with the schedule's prompt, fresh session.
        mock_sdk_cls.return_value.send.assert_called_once_with(
            "do the thing", no_history=True
        )
        # Output was delivered through the configured sink.
        mock_dispatch.assert_called_once_with("stdout", {}, "agent says hi")

    def test_fire_passes_sink_args_to_dispatch(self, mocker):
        mock_sdk_cls = mocker.patch(_AGENT_SDK)
        mocker.patch(_AGENT_CONFIG)
        mock_sdk_cls.return_value.send.return_value.text = "out"
        mock_dispatch = mocker.patch.object(runner.sinks, "dispatch")

        sched = _make_schedule(
            "a", prompt="p", sink="file", sink_args={"path": "/tmp/x.md"}
        )
        runner.fire(sched)

        mock_dispatch.assert_called_once_with("file", {"path": "/tmp/x.md"}, "out")

    def test_fire_skill_only_raises_not_implemented(self, mocker):
        # --skill resolution is blocked on #888; fire must fail loudly, never
        # reach the agent or the sink.
        mock_sdk_cls = mocker.patch(_AGENT_SDK)
        mock_dispatch = mocker.patch.object(runner.sinks, "dispatch")

        sched = Schedule(name="s", cron="* * * * *", skill="my-skill")
        with pytest.raises(NotImplementedError, match="#888"):
            runner.fire(sched)

        mock_sdk_cls.return_value.send.assert_not_called()
        mock_dispatch.assert_not_called()


# ===========================================================================
# 5. resolve_input
# ===========================================================================


class TestResolveInput:

    def test_prompt_returns_prompt_text(self):
        sched = _make_schedule("a", prompt="hello prompt")
        assert runner.resolve_input(sched) == "hello prompt"

    def test_skill_raises_not_implemented(self):
        sched = Schedule(name="s", cron="* * * * *", skill="sk")
        with pytest.raises(NotImplementedError, match="skill-format"):
            runner.resolve_input(sched)
