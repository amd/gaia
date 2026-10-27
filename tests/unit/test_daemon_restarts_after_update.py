# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""An update that replaces GAIA restarts the daemon still running the old one (#5695).

After the desktop app updated, the previous release's daemon and agents kept
running, so the new app drove old code until a reboot or `gaia daemon restart`.
The daemon now records its release and the version.py it read it from; a client
starting up compares that with what is on disk now.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

from gaia import version
from gaia.daemon import client, instance
from gaia.daemon.errors import DaemonError
from gaia.daemon.instance import DaemonInstance, runs_replaced_install


def _version_file(tmp_path, text='__version__ = "0.25.1"\n'):
    path = tmp_path / "gaia" / "version.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _inst(pid=4242, **fields):
    return DaemonInstance(pid=pid, port=5151, token="t", started_at=100.0, **fields)


def test_a_daemon_whose_release_is_still_installed_is_current(tmp_path):
    path = _version_file(tmp_path)
    assert (
        runs_replaced_install(_inst(gaia_version="0.25.1", version_file=str(path)))
        is None
    )


def test_a_daemon_whose_release_was_replaced_is_out_of_date(tmp_path):
    path = _version_file(tmp_path, '__version__ = "0.25.2"\n')
    why = runs_replaced_install(_inst(gaia_version="0.25.1", version_file=str(path)))
    assert why == "it runs GAIA 0.25.1, and 0.25.2 is installed"


def test_a_daemon_whose_install_is_gone_is_out_of_date(tmp_path):
    gone = tmp_path / "old-venv" / "gaia" / "version.py"
    why = runs_replaced_install(_inst(gaia_version="0.25.1", version_file=str(gone)))
    assert why and "is gone" in why


def test_the_record_round_trips_through_instance_json(tmp_path, monkeypatch):
    monkeypatch.setattr(instance.paths, "instance_path", lambda: tmp_path / "i.json")
    monkeypatch.setattr(instance.paths, "ensure_host_dir", lambda: None)
    recorded = _inst(gaia_version="0.25.1", version_file="/x/gaia/version.py")
    instance.write_instance(recorded)
    assert json.loads((tmp_path / "i.json").read_text())["gaia_version"] == "0.25.1"
    assert instance.read_instance(tmp_path / "i.json") == recorded


class _Process:
    def __init__(self, cmdline, started):
        self._cmdline = cmdline
        self._started = started

    def cmdline(self):
        return self._cmdline

    def create_time(self):
        return self._started


@pytest.fixture
def launched_with(monkeypatch):
    import psutil

    def use(cmdline, started):
        monkeypatch.setattr(psutil, "Process", lambda pid: _Process(cmdline, started))

    return use


def _installed_at():
    installed = os.stat(version.__file__)
    return max(installed.st_mtime, installed.st_ctime)


def test_an_older_daemon_from_this_python_is_judged_by_its_install_time(launched_with):
    legacy = DaemonInstance(pid=1, port=1, token="t")

    launched_with([sys.executable, "-m", "gaia.daemon"], started=_installed_at() + 60)
    assert runs_replaced_install(legacy) is None

    launched_with([sys.executable, "-m", "gaia.daemon"], started=_installed_at() - 60)
    why = runs_replaced_install(legacy)
    assert why and "installed after it started" in why


def test_the_process_start_time_wins_over_the_recorded_one(launched_with):
    # A daemon started after the install but recording an older start is current.
    launched_with([sys.executable, "-m", "gaia.daemon"], started=_installed_at() + 60)
    assert (
        runs_replaced_install(DaemonInstance(pid=1, port=1, token="t", started_at=0.0))
        is None
    )


def test_an_older_daemon_from_another_python_is_left_alone(launched_with):
    launched_with(["/opt/other-venv/bin/python", "-m", "gaia.daemon"], started=0.0)
    assert runs_replaced_install(DaemonInstance(pid=1, port=1, token="t")) is None


@pytest.fixture
def running(monkeypatch, tmp_path):
    """A live, answering daemon; records what start_or_attach does to it."""
    calls = {"shutdown": [], "spawned": 0}
    path = _version_file(tmp_path, '__version__ = "0.25.2"\n')
    state = {"inst": _inst(gaia_version="0.25.2", version_file=str(path))}

    monkeypatch.setattr(client, "attach", lambda *a, **k: state["inst"])
    monkeypatch.setattr(client, "read_instance", lambda *a: state["inst"])
    monkeypatch.setattr(client, "_answers", lambda *a, **k: True)
    monkeypatch.setattr(client, "missing_daemon_deps", lambda: [])
    monkeypatch.setattr(
        client,
        "request_shutdown",
        lambda inst: calls["shutdown"].append(inst.pid) or True,
    )
    monkeypatch.setattr(client, "wait_until_gone", lambda inst, timeout=0: True)
    monkeypatch.setattr(client, "outdated_daemon_busy", lambda inst: state.get("busy"))
    monkeypatch.setattr(client, "_DEFERRED_NOTED", set())

    def spawn(timeout, on_progress=None, drop_env=()):
        calls["spawned"] += 1
        return _inst(pid=5353, gaia_version="0.25.2", version_file=str(path))

    monkeypatch.setattr(client, "_spawn_and_wait", spawn)
    return state, calls, path


def test_a_current_daemon_is_attached_to(running):
    state, calls, _path = running
    assert client.start_or_attach(timeout=1) is state["inst"]
    assert calls == {"shutdown": [], "spawned": 0}


def test_an_out_of_date_daemon_is_restarted(running):
    state, calls, path = running
    state["inst"] = _inst(gaia_version="0.25.1", version_file=str(path))

    fresh = client.start_or_attach(timeout=1)

    assert calls == {"shutdown": [4242], "spawned": 1}
    assert fresh.pid == 5353


def test_an_out_of_date_daemon_that_will_not_stop_says_what_to_run(
    running, monkeypatch
):
    state, calls, path = running
    state["inst"] = _inst(gaia_version="0.25.1", version_file=str(path))
    monkeypatch.setattr(client, "wait_until_gone", lambda inst, timeout=0: False)

    with pytest.raises(DaemonError, match="gaia daemon restart"):
        client.start_or_attach(timeout=1)
    assert calls["spawned"] == 0


def test_a_busy_out_of_date_daemon_keeps_running_and_says_so_once(running, caplog):
    state, calls, path = running
    state["inst"] = _inst(gaia_version="0.25.1", version_file=str(path))
    state["busy"] = "the GAIA terminal app is still using it"

    with caplog.at_level("WARNING", logger="gaia.daemon.client"):
        assert client.start_or_attach(timeout=1) is state["inst"]
        assert client.start_or_attach(timeout=1) is state["inst"]

    assert calls == {"shutdown": [], "spawned": 0}
    notices = [
        r.getMessage() for r in caplog.records if "keeps running" in r.getMessage()
    ]
    assert len(notices) == 1
    assert "terminal app is still using it" in notices[0]
    assert "gaia daemon restart" in notices[0]


def test_a_daemon_that_cannot_be_asked_to_stop_says_what_to_run(running, monkeypatch):
    state, calls, path = running
    state["inst"] = _inst(gaia_version="0.25.1", version_file=str(path))

    def unreachable(inst):
        raise DaemonError("could not reach the daemon at http://127.0.0.1:5151.")

    monkeypatch.setattr(client, "request_shutdown", unreachable)

    with pytest.raises(DaemonError, match="gaia daemon restart"):
        client.start_or_attach(timeout=1)
    assert calls["spawned"] == 0


class _Response:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


@pytest.fixture
def daemon_reports(monkeypatch, tmp_path):
    """Answers the clients route; the job store lives in a scratch host dir."""
    import requests

    answers = {
        "clients": _Response(200, {"clients": [], "seconds_since_request": None}),
    }
    monkeypatch.setattr(requests, "get", lambda url, **kwargs: answers["clients"])
    monkeypatch.setattr(client, "pid_alive", lambda pid: pid != 999999)
    monkeypatch.setattr(client.paths, "host_dir", lambda: tmp_path)
    return answers


def _job(tmp_path, status, fire_at=None, claimed_at=None):
    import sqlite3
    import time as _time

    from gaia.daemon.scheduler.store import DAEMON_JOBS_DDL

    with sqlite3.connect(tmp_path / "scheduler.db") as db:
        db.executescript(DAEMON_JOBS_DDL)
        db.execute(
            "INSERT INTO daemon_jobs (job_id, source, kind, fire_at, "
            "interval_seconds, payload_json, status, created_at, claimed_at) "
            "VALUES (?, 'test', 'recurring', ?, 86400, '{}', ?, ?, ?)",
            (f"j{_time.monotonic_ns()}", fire_at, status, _time.time(), claimed_at),
        )


def test_an_idle_daemon_is_free_to_restart(daemon_reports):
    assert client.outdated_daemon_busy(_inst()) is None


def test_a_recurring_job_waiting_for_tomorrow_does_not_keep_it(
    daemon_reports, tmp_path
):
    import time as _time

    _job(tmp_path, "pending", fire_at=_time.time() + 86400)
    assert client.outdated_daemon_busy(_inst()) is None


def test_a_running_scheduled_job_keeps_the_daemon(daemon_reports, tmp_path):
    import time as _time

    _job(tmp_path, "firing", claimed_at=_time.time())
    assert "1 scheduled job(s) are running" in client.outdated_daemon_busy(_inst())


def test_a_job_due_now_keeps_the_daemon(daemon_reports, tmp_path):
    import time as _time

    _job(tmp_path, "pending", fire_at=_time.time() - 5)
    assert "due to run" in client.outdated_daemon_busy(_inst())


def test_a_firing_row_left_by_a_crash_does_not_keep_it(daemon_reports, tmp_path):
    _job(tmp_path, "firing", claimed_at=1.0)
    assert client.outdated_daemon_busy(_inst()) is None


def test_another_running_front_end_keeps_the_daemon(daemon_reports):
    daemon_reports["clients"] = _Response(
        200, {"clients": [{"name": "gaia-tui", "pids": [4343]}]}
    )
    assert "gaia-tui is still using it" in client.outdated_daemon_busy(_inst())


def test_this_process_and_exited_front_ends_do_not_keep_it(daemon_reports):
    daemon_reports["clients"] = _Response(
        200,
        {
            "clients": [
                {"name": "this", "pids": [os.getpid()]},
                {"name": "parent", "pids": [os.getppid()]},
                {"name": "gone", "pids": [999999]},
            ]
        },
    )
    assert client.outdated_daemon_busy(_inst()) is None


def test_a_model_request_just_now_keeps_the_daemon(daemon_reports):
    daemon_reports["clients"] = _Response(
        200, {"clients": [], "seconds_since_request": 4.0}
    )
    assert "4s ago" in client.outdated_daemon_busy(_inst())


def test_a_daemon_without_the_clients_route_is_judged_by_its_jobs(daemon_reports):
    daemon_reports["clients"] = _Response(404)
    assert client.outdated_daemon_busy(_inst()) is None


def test_a_daemon_that_cannot_be_checked_is_kept(daemon_reports, monkeypatch):
    import requests

    def down(url, **kwargs):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(requests, "get", down)
    assert "could not check" in client.outdated_daemon_busy(_inst())


def _old_store(tmp_path, status, fire_at=None):
    """A job store from before 0.25.1, which had no heartbeat column."""
    import sqlite3

    with sqlite3.connect(tmp_path / "scheduler.db") as db:
        db.execute(
            "CREATE TABLE daemon_jobs (job_id TEXT PRIMARY KEY, status TEXT, fire_at REAL)"
        )
        db.execute("INSERT INTO daemon_jobs VALUES ('j1', ?, ?)", (status, fire_at))


def test_an_older_store_with_a_future_job_does_not_keep_it(daemon_reports, tmp_path):
    import time as _time

    _old_store(tmp_path, "pending", fire_at=_time.time() + 86400)
    assert client.outdated_daemon_busy(_inst()) is None


def test_an_older_store_with_a_running_job_keeps_it(daemon_reports, tmp_path):
    _old_store(tmp_path, "firing")
    assert "running" in client.outdated_daemon_busy(_inst())


def test_a_store_without_the_jobs_table_does_not_keep_it(daemon_reports, tmp_path):
    import sqlite3

    sqlite3.connect(tmp_path / "scheduler.db").close()
    assert client.outdated_daemon_busy(_inst()) is None
