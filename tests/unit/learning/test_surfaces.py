# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Agent UI router, the ``gaia learn`` CLI, the config file and the
daemon's nightly job."""

import json
import os
import sys
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.learning import config as lconfig
from gaia.learning.analyze import Opportunity, Report
from gaia.learning.dataset import Corpus
from gaia.learning.errors import LearningError
from gaia.learning.registry import AdapterRegistry
from gaia.learning.runs import RunRecord

from .conftest import write_gguf

MODEL = "Gemma-4-E4B-it-GGUF"


def _adapter(aid, status="candidate"):
    reg = AdapterRegistry()
    write_gguf(reg.new_dir(aid) / "adapter.gguf")
    reg.save({"id": aid, "base_model": MODEL, "status": status, "created_at": aid})
    return reg


def _item(oid="concept:a"):
    Corpus().merge(
        Report(
            opportunities=[
                Opportunity(
                    oid,
                    "concept",
                    "Bluefleet is staging",
                    ["Bluefleet is staging"],
                    examples=[
                        [
                            {"role": "user", "content": "q"},
                            {"role": "assistant", "content": "a"},
                        ]
                    ],
                )
            ],
            turns_read=0,
            facts_read=0,
            tool_calls_read=0,
        ),
        "r1",
    )


# -- config -----------------------------------------------------------------


def test_config_defaults_off_and_round_trips(lhome):
    cfg = lconfig.load_config()
    assert cfg.enabled is False
    lconfig.update_config(enabled=True, window_start="23:30", window_end="05:00")
    again = lconfig.load_config()
    assert (again.enabled, again.window_start, again.window_end) == (
        True,
        "23:30",
        "05:00",
    )


@pytest.mark.parametrize(
    "change, match",
    [
        ({"window_start": "25:00"}, "HH:MM"),
        ({"rank": 0}, "positive"),
        ({"backend": "cuda"}, "backend"),
        ({"learning_rate": 3}, "between"),
    ],
)
def test_bad_config_values_name_the_field(lhome, change, match):
    with pytest.raises(LearningError, match=match):
        lconfig.update_config(**change)


def test_unknown_config_keys_are_an_error(lhome):
    path = lhome / "config.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"enabeld": True}), encoding="utf-8")
    with pytest.raises(LearningError, match="unknown keys"):
        lconfig.load_config()


# -- router -----------------------------------------------------------------


@pytest.fixture
def api(lhome, monkeypatch):
    from gaia.ui.routers import learning

    monkeypatch.setattr(learning, "_model", lambda: MODEL)
    monkeypatch.setattr(learning, "_reload", lambda model: None)
    app = FastAPI()
    app.include_router(learning.router)
    return TestClient(app)


def test_status_and_config_update(api):
    s = api.get("/api/learning/status").json()
    assert s["config"]["enabled"] is False and s["base_model_trainable"] is True
    with patch("gaia.learning.launcher.register_nightly_job") as reg:
        r = api.put(
            "/api/learning/config", json={"enabled": True, "window_start": "02:00"}
        )
    assert r.status_code == 200 and r.json()["window_start"] == "02:00"
    reg.assert_called_once()
    assert (
        api.put("/api/learning/config", json={"window_end": "nope"}).status_code == 400
    )


def test_adapter_management(api):
    _adapter("20260101-010000")
    _adapter("20260102-010000")
    assert [a["id"] for a in api.get("/api/learning/adapters").json()] == [
        "20260102-010000",
        "20260101-010000",
    ]
    assert (
        api.post("/api/learning/adapters/20260101-010000/activate").json()["status"]
        == "active"
    )
    assert (
        api.get("/api/learning/status").json()["active_adapter"]["id"]
        == "20260101-010000"
    )
    assert (
        api.delete("/api/learning/adapters/20260101-010000").status_code == 400
    )  # active
    assert (
        api.post("/api/learning/deactivate").json()["deactivated"] == "20260101-010000"
    )
    assert api.delete("/api/learning/adapters/20260101-010000").status_code == 200
    assert api.get("/api/learning/adapters/nope").status_code == 404


def test_items_and_forget(api):
    _item()
    [item] = api.get("/api/learning/items").json()
    assert item["id"] == "concept:a" and item["examples"] == 1
    assert api.delete("/api/learning/items/concept:a").status_code == 200
    assert api.delete("/api/learning/items/concept:a").status_code == 404


def test_runs_report_a_dead_process_as_failed(api):
    rec = RunRecord("20260101-010000", pid=99999999)
    rec.update()
    run = api.get("/api/learning/runs").json()[0]
    assert run["status"] == "failed" and "exited" in run["summary"]


def test_run_now_and_stop(api, lhome):
    with patch("gaia.learning.launcher.spawn_cycle", return_value=1234) as spawn:
        assert api.post("/api/learning/run").json() == {"started": True, "pid": 1234}
    spawn.assert_called_once_with("manual")
    RunRecord("20260101-010000", pid=os.getpid()).update()
    assert api.post("/api/learning/run").status_code == 409
    assert api.post("/api/learning/stop").json() == {"stopping": True}
    assert (lhome / "STOP").exists()


def test_router_is_mounted_in_the_ui_server():
    from gaia.ui import server

    src = open(server.__file__, encoding="utf-8").read()
    assert "app.include_router(learning_router_mod.router)" in src


# -- CLI --------------------------------------------------------------------


def _cli(*argv):
    from gaia.cli import main

    with patch.object(sys, "argv", ["gaia", "learn", *argv]):
        try:
            main()
        except SystemExit as e:
            return e.code or 0
    return 0


def test_cli_config_items_and_adapters(lhome, capsys, monkeypatch):
    monkeypatch.setattr("gaia.learning.cli._model", lambda: MODEL)
    monkeypatch.setattr("gaia.learning.cli._reload", lambda model: None)
    assert _cli("config", "--window", "00:30-04:00", "--allow-battery") == 0
    cfg = lconfig.load_config()
    assert (cfg.window_start, cfg.window_end, cfg.require_ac_power) == (
        "00:30",
        "04:00",
        False,
    )
    _item()
    capsys.readouterr()
    _cli("items")
    assert "concept:a" in capsys.readouterr().out
    _adapter("20260101-010000")
    assert _cli("activate", "20260101-010000") == 0
    _cli("list")
    assert "active" in capsys.readouterr().out
    assert _cli("delete", "20260101-010000") == 1  # active: refused with a reason
    assert "Deactivate it" in capsys.readouterr().err


def test_cli_bad_window_is_a_clean_error(lhome, capsys):
    assert _cli("config", "--window", "0100") == 1
    assert "START-END" in capsys.readouterr().err


# -- daemon job -------------------------------------------------------------


def test_nightly_job_registers_once(lhome, tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / "daemon"))
    from gaia.daemon.paths import scheduler_db_path
    from gaia.daemon.scheduler import store
    from gaia.daemon.scheduler.clock import _ClockDB
    from gaia.learning.launcher import JOB_ID, register_nightly_job

    register_nightly_job()
    register_nightly_job()
    db = _ClockDB()
    db.init_db(str(scheduler_db_path()))
    try:
        jobs = [j for j in store.list_jobs(db) if j["source"] == "learning"]
    finally:
        db.close_db()
    assert [j["job_id"] for j in jobs] == [JOB_ID]
    assert jobs[0]["kind"] == "recurring"


def test_scheduled_executor_spawns_only_through_open_gates(lhome, monkeypatch):
    from gaia.learning import launcher

    spawned = []
    monkeypatch.setattr(
        launcher, "spawn_cycle", lambda trigger: spawned.append(trigger) or 1
    )
    monkeypatch.setattr("gaia.learning.sources.last_activity", lambda *a: None)
    with patch("gaia.learning.cycle.Deps") as deps_cls:
        deps = deps_cls.return_value
        deps.toolchain_ready.return_value = True
        deps.ac_power.return_value = True
        launcher.scheduled_executor({}, None)  # learning is off
        assert spawned == []
        # A window of 00:00-00:00 is always open.
        lconfig.update_config(enabled=True, window_start="00:00", window_end="00:00")
        deps.toolchain_ready.return_value = False
        launcher.scheduled_executor({}, None)
        assert spawned == []
        deps.toolchain_ready.return_value = True
        launcher.scheduled_executor({}, None)
    assert spawned == ["scheduled"]


def test_daemon_dispatches_learning_jobs_by_source():
    from gaia.daemon import server

    seen = []
    dispatch = server._by_source(
        {"learning": lambda job, db: seen.append(job["job_id"])}
    )
    dispatch({"job_id": "j", "source": "learning", "kind": "recurring"}, None)
    assert seen == ["j"]
    with pytest.raises(RuntimeError, match="no executor"):
        dispatch({"job_id": "k", "source": "email", "kind": "recurring"}, None)
    assert "recurring" in server._production_executors()


def test_a_failing_check_is_recorded_once_and_keeps_the_job(lhome, monkeypatch):
    from gaia.learning import launcher
    from gaia.learning.runs import list_runs

    def broken():
        raise OSError("disk unplugged")

    monkeypatch.setattr(launcher, "_check_and_spawn", broken)
    launcher.scheduled_executor(
        {}, None
    )  # must not raise: the clock would drop the job
    launcher.scheduled_executor({}, None)
    runs = list_runs()
    assert (
        len(runs) == 1
        and runs[0]["status"] == "failed"
        and "disk unplugged" in runs[0]["summary"]
    )


def test_enable_rearms_a_job_the_clock_gave_up_on(lhome, tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / "daemon"))
    from gaia.daemon.paths import scheduler_db_path
    from gaia.daemon.scheduler import store
    from gaia.daemon.scheduler.clock import _ClockDB
    from gaia.learning.launcher import JOB_ID, register_nightly_job

    register_nightly_job()
    db = _ClockDB()
    db.init_db(str(scheduler_db_path()))
    db.update(
        "daemon_jobs",
        {"status": "failed", "error": "x"},
        "job_id = :id",
        {"id": JOB_ID},
    )
    db.close_db()
    register_nightly_job()
    db = _ClockDB()
    db.init_db(str(scheduler_db_path()))
    try:
        assert store.get_job(db, job_id=JOB_ID)["status"] == "pending"
    finally:
        db.close_db()
