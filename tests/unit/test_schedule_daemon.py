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

    def test_refresh_tracks_add_pause_resume_and_remove(self, tmp_path):
        store = _store_with(tmp_path, _make_schedule("a"))
        scheduler = daemon.build_scheduler(store)
        store.set_enabled("a", False)
        store.add(_make_schedule("b"))
        daemon.refresh_schedules(scheduler, store)
        assert {job.id for job in scheduler.get_jobs()} == {"b"}

        store.set_enabled("a", True)
        store.remove("b")
        daemon.refresh_schedules(scheduler, store)
        assert {job.id for job in scheduler.get_jobs()} == {"a"}

    def test_refresh_updates_cron_without_resetting_unchanged_jobs(self, tmp_path):
        store = _store_with(tmp_path, _make_schedule("a"))
        scheduler = daemon.build_scheduler(store)
        scheduler.start(paused=True)
        try:
            original_fire = scheduler.get_job("a").next_run_time
            daemon.refresh_schedules(scheduler, store)
            assert scheduler.get_job("a").next_run_time == original_fire

            schedules = store.load()
            schedules["a"].cron = "0 17 * * *"
            store.save(schedules)
            daemon.refresh_schedules(scheduler, store)
            assert scheduler.get_job("a").next_run_time.hour == 17
            assert scheduler.get_job("a").args[0].cron == "0 17 * * *"
        finally:
            scheduler.shutdown()

    def test_reload_tick_survives_an_unparseable_store(self, tmp_path, caplog):
        """An unparseable cron on disk must not take the daemon down (#4143)."""
        store = _store_with(tmp_path, _make_schedule("a"))
        scheduler = daemon.build_scheduler(store)
        scheduler.start(paused=True)
        try:
            original_fire = scheduler.get_job("a").next_run_time

            schedules = store.load()
            schedules["a"].cron = "every day"  # not a valid crontab
            store.save(schedules)

            import logging

            with caplog.at_level(logging.ERROR):
                daemon._reload_or_keep_armed(scheduler, store)

            assert scheduler.get_job("a") is not None
            assert scheduler.get_job("a").next_run_time == original_fire
            assert "could not reload" in caplog.text
        finally:
            scheduler.shutdown()

    def test_reload_tick_survives_a_truncated_store_file(self, tmp_path, caplog):
        """A hand-edit caught mid-save must not take the daemon down (#4143)."""
        store = _store_with(tmp_path, _make_schedule("a"))
        scheduler = daemon.build_scheduler(store)
        scheduler.start(paused=True)
        try:
            original_fire = scheduler.get_job("a").next_run_time

            store.path.write_text("[schedules.a\n")  # torn write

            import logging

            with caplog.at_level(logging.ERROR):
                daemon._reload_or_keep_armed(scheduler, store)

            assert scheduler.get_job("a") is not None
            assert scheduler.get_job("a").next_run_time == original_fire
            assert "could not reload" in caplog.text
        finally:
            scheduler.shutdown()


class TestMisfires:
    """A job that fires late (sleep, busy machine) must still run (#4207)."""

    def test_every_job_tolerates_late_fires(self, tmp_path):
        store = _store_with(tmp_path, _make_schedule("a"), _make_schedule("b"))
        scheduler = daemon.build_scheduler(store)
        scheduler.start(paused=True)
        try:
            for job in scheduler.get_jobs():
                assert job.misfire_grace_time is not None
                assert job.misfire_grace_time >= 3600
                assert job.coalesce is True
        finally:
            scheduler.shutdown()

    def test_run_due_a_minute_ago_still_fires(self, mocker, tmp_path):
        import threading
        from datetime import timedelta, timezone

        store = _store_with(tmp_path, _make_schedule("a"))
        fired = threading.Event()
        mocker.patch.object(runner, "fire", side_effect=lambda _s: fired.set())
        scheduler = daemon.build_scheduler(store)
        scheduler.start(paused=True)
        try:
            late = datetime.now(timezone.utc) - timedelta(seconds=60)
            scheduler.modify_job("a", next_run_time=late)
            scheduler.resume()
            assert fired.wait(5), "a run 60s late was dropped as a misfire"
        finally:
            scheduler.shutdown()

    def test_job_added_on_reload_tolerates_late_fires(self, tmp_path):
        store = _store_with(tmp_path)
        scheduler = daemon.build_scheduler(store)
        scheduler.start(paused=True)
        try:
            store.add(_make_schedule("later"))
            daemon.refresh_schedules(scheduler, store)
            assert scheduler.get_job("later").misfire_grace_time >= 3600
        finally:
            scheduler.shutdown()

    def test_missed_run_is_logged_by_schedule_name(self, tmp_path, caplog):
        from datetime import timezone

        from apscheduler.events import EVENT_JOB_MISSED, JobExecutionEvent

        store = _store_with(tmp_path, _make_schedule("morning-brief"))
        scheduler = daemon.build_scheduler(store)
        due = datetime(2026, 1, 1, 7, 0, tzinfo=timezone.utc)
        event = JobExecutionEvent(EVENT_JOB_MISSED, "morning-brief", None, due)

        with caplog.at_level("WARNING", logger=daemon.log.name):
            scheduler._dispatch_event(event)

        assert "morning-brief" in caplog.text
        assert "missed" in caplog.text

    def test_overlapping_run_is_logged_by_schedule_name(self, tmp_path, caplog):
        from datetime import timezone

        from apscheduler.events import EVENT_JOB_MAX_INSTANCES, JobSubmissionEvent

        store = _store_with(tmp_path, _make_schedule("morning-brief"))
        scheduler = daemon.build_scheduler(store)
        due = datetime(2026, 1, 1, 7, 0, tzinfo=timezone.utc)
        event = JobSubmissionEvent(EVENT_JOB_MAX_INSTANCES, "morning-brief", None, [due])

        with caplog.at_level("WARNING", logger=daemon.log.name):
            scheduler._dispatch_event(event)

        assert "morning-brief" in caplog.text
        assert "still in progress" in caplog.text


# ===========================================================================
# 3. _job — success and failure paths
# ===========================================================================


class TestJob:

    @pytest.mark.parametrize("change", ["pause", "remove"])
    def test_does_not_fire_after_pause_or_remove(
        self, mocker, tmp_path, change, caplog
    ):
        store = _store_with(tmp_path, _make_schedule("a"))
        job = daemon.build_scheduler(store).get_job("a")
        if change == "pause":
            store.set_enabled("a", False)
        else:
            store.remove("a")
        fire = mocker.patch.object(runner, "fire")

        with caplog.at_level("DEBUG", logger=daemon.log.name):
            job.func(*job.args)

        fire.assert_not_called()
        # A skipped fire must leave a trace -- otherwise "my schedule didn't
        # run" has no diagnostic (#4143 nit).
        assert "skipping" in caplog.text and "a" in caplog.text

    def test_fires_current_prompt_after_store_edit(self, mocker, tmp_path):
        store = _store_with(tmp_path, _make_schedule("a"))
        job = daemon.build_scheduler(store).get_job("a")
        schedules = store.load()
        schedules["a"].prompt = "updated prompt"
        store.save(schedules)
        fire = mocker.patch.object(runner, "fire")

        job.func(*job.args)

        assert fire.call_args.args[0].prompt == "updated prompt"

    def test_does_not_fire_old_cron_before_refresh(self, mocker, tmp_path):
        store = _store_with(tmp_path, _make_schedule("a"))
        job = daemon.build_scheduler(store).get_job("a")
        schedules = store.load()
        schedules["a"].cron = "0 17 * * *"
        store.save(schedules)
        fire = mocker.patch.object(runner, "fire")

        job.func(*job.args)

        fire.assert_not_called()

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
        # The scheduler cannot run skills yet; fire must fail loudly, never
        # reach the agent or the sink.
        mock_sdk_cls = mocker.patch(_AGENT_SDK)
        mock_dispatch = mocker.patch.object(runner.sinks, "dispatch")

        sched = Schedule(name="s", cron="* * * * *", skill="my-skill")
        with pytest.raises(NotImplementedError, match="cannot run skills yet"):
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
        with pytest.raises(NotImplementedError, match="cannot run skills yet"):
            runner.resolve_input(sched)
