# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for wiring the single daemon clock into the daemon process (#2379).

The daemon builds a `DaemonClock` but the current `server.py` never starts it —
scheduled jobs are registered in the store and then simply never fire. This file
pins the fixed contract for the fix:

- `gaia.daemon.paths.scheduler_db_path()` — the one file-backed path every
  `DaemonClock` in the daemon process must share (never `:memory:`).
- `gaia.daemon.server._build_clock` / `_build_register` / `_build_deregister` —
  the wiring seams: `_build_register`'s callable starts the refresher then the
  clock (rolling back both the refresher and `instance.json` if the clock start
  fails), and `_build_deregister`'s callable stops everything in a fixed order.
- `DaemonClock.stop()` must never silently claim success when its polling
  thread fails to join — it must log loudly and leave `running` reporting
  `True`.
- `GET /daemon/v1/status` gains an additive `"clock"` object only when a clock
  is passed to `create_app`.

Also carries two static guard tests protecting the intended scope of this fix:
no daemon file may import `gaia_agent_email`, and no daemon scheduler/server
file may reference the unrelated "autonomy" scheduler concept.
"""

from __future__ import annotations

import ast
import logging
import os
from pathlib import Path
from unittest import mock

import pytest

from gaia.daemon import instance as instance_mod
from gaia.daemon import paths
from gaia.daemon import server as daemon_server
from gaia.daemon.scheduler.clock import DaemonClock

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DAEMON_SRC = _REPO_ROOT / "src" / "gaia" / "daemon"


@pytest.fixture()
def daemon_home(tmp_path, monkeypatch):
    """Isolate all daemon on-disk state under a tmp dir for one test."""
    home = tmp_path / "host"
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(home))
    return home


# ---------------------------------------------------------------------------
# gaia.daemon.paths.scheduler_db_path
# ---------------------------------------------------------------------------


def test_scheduler_db_path_returns_host_dir_scheduler_db(daemon_home):
    path = paths.scheduler_db_path()

    assert path == paths.host_dir() / "scheduler.db"
    assert str(path) != ":memory:"


def test_scheduler_db_path_creates_host_dir_0700_and_file_0600(daemon_home):
    assert not daemon_home.exists()

    path = paths.scheduler_db_path()

    assert daemon_home.is_dir()
    assert path.exists()
    if os.name != "nt":
        assert (os.stat(daemon_home).st_mode & 0o777) == 0o700
        assert (os.stat(path).st_mode & 0o777) == 0o600


def test_scheduler_db_path_does_not_clobber_an_existing_file(daemon_home):
    first = paths.scheduler_db_path()
    first.write_bytes(b"not-actually-empty-sqlite-bytes")

    second = paths.scheduler_db_path()

    assert second == first
    assert second.read_bytes() == b"not-actually-empty-sqlite-bytes"


# ---------------------------------------------------------------------------
# gaia.daemon.server._build_clock
# ---------------------------------------------------------------------------


def test_build_clock_uses_scheduler_db_path(daemon_home):
    """`_build_clock` opens the shared scheduler_db_path file, and with no
    `executors` kwarg (the production call in `run()`) the mapping is empty —
    proven black-box by a due job failing with "no executor registered"."""
    from gaia.daemon.scheduler import store
    from gaia.daemon.scheduler.clock import _ClockDB
    from gaia.daemon.scheduler.models import KIND_ONE_SHOT, STATUS_FAILED

    db_path = str(paths.scheduler_db_path())
    db = _ClockDB()
    db.init_db(db_path)
    store.init_schema(db)
    job_id = store.register_job(db, source="test", kind=KIND_ONE_SHOT, fire_at=1.0)
    db.close_db()

    clock = daemon_server._build_clock(db_path)  # no executors kwarg at all

    result = clock.fire_due(now=10.0)

    assert result == {"fired": [], "failed": [job_id]}
    db2 = _ClockDB()
    db2.init_db(db_path)
    try:
        job = store.get_job(db2, job_id=job_id)
        assert job["status"] == STATUS_FAILED
        assert "no executor registered" in job["error"]
    finally:
        db2.close_db()


# ---------------------------------------------------------------------------
# gaia.daemon.server._build_register
# ---------------------------------------------------------------------------


def test_build_register_starts_refresher_then_clock_and_writes_instance(daemon_home):
    calls = []
    refresher = mock.Mock()
    refresher.start.side_effect = lambda: calls.append("refresher.start")
    clock = mock.Mock()
    clock.start.side_effect = lambda: calls.append("clock.start")

    register = daemon_server._build_register(
        specs={},
        pid=4242,
        port=55123,
        token="tok-xyz",
        host="127.0.0.1",
        started_at=100.0,
        refresher=refresher,
        clock=clock,
    )
    register()

    assert calls == ["refresher.start", "clock.start"]
    inst = instance_mod.read_instance()
    assert inst is not None
    assert inst.pid == 4242
    assert inst.port == 55123
    assert inst.token == "tok-xyz"
    assert inst.host == "127.0.0.1"
    assert inst.started_at == 100.0


def test_build_register_rolls_back_refresher_and_instance_on_clock_start_failure(
    daemon_home,
):
    refresher = mock.Mock()
    clock = mock.Mock()
    clock.start.side_effect = RuntimeError("clock exploded")

    register = daemon_server._build_register(
        specs={},
        pid=4343,
        port=55124,
        token="tok-abc",
        host="127.0.0.1",
        started_at=200.0,
        refresher=refresher,
        clock=clock,
    )

    with pytest.raises(RuntimeError, match="clock exploded"):
        register()

    refresher.stop.assert_called_once()
    # instance.json was written before the clock.start() failure — it must be
    # undone, never left stranded pointing at a daemon whose clock never ran.
    assert instance_mod.read_instance() is None


def test_build_register_rolls_back_instance_on_refresher_start_failure(daemon_home):
    """The same rollback must apply one line earlier: if refresher.start()
    itself raises (before clock.start() is ever reached), instance.json was
    already written and must still be undone — not just on a clock failure."""
    refresher = mock.Mock()
    refresher.start.side_effect = RuntimeError("refresher exploded")
    clock = mock.Mock()

    register = daemon_server._build_register(
        specs={},
        pid=4444,
        port=55125,
        token="tok-def",
        host="127.0.0.1",
        started_at=300.0,
        refresher=refresher,
        clock=clock,
    )

    with pytest.raises(RuntimeError, match="refresher exploded"):
        register()

    clock.start.assert_not_called()
    refresher.stop.assert_called_once()
    assert instance_mod.read_instance() is None


# ---------------------------------------------------------------------------
# gaia.daemon.server._build_deregister
# ---------------------------------------------------------------------------


def test_build_deregister_calls_in_exact_order(daemon_home):
    instance_mod.write_instance(instance_mod.DaemonInstance(pid=555, port=1, token="t"))

    calls = []
    refresher = mock.Mock()
    refresher.stop.side_effect = lambda: calls.append("refresher.stop")
    clock = mock.Mock()
    clock.stop.side_effect = lambda: calls.append("clock.stop")
    registry = mock.Mock()
    registry.shutdown_all.side_effect = lambda: calls.append("registry.shutdown_all")
    custody_store = mock.Mock()
    custody_store.close.side_effect = lambda: calls.append("custody_store.close")
    lemonade = mock.Mock()
    lemonade.shutdown.side_effect = lambda: calls.append("lemonade.shutdown")

    deregister = daemon_server._build_deregister(
        registry=registry,
        custody_store=custody_store,
        pid=555,
        refresher=refresher,
        clock=clock,
        lemonade=lemonade,
    )
    deregister()

    # The model server is reaped AFTER the sidecars, never before: an agent
    # mid-teardown may still be finishing a model call, and pulling the server
    # out from under it turns a clean shutdown into a wave of connection errors
    # in the logs.
    assert calls == [
        "refresher.stop",
        "clock.stop",
        "registry.shutdown_all",
        "lemonade.shutdown",
        "custody_store.close",
    ]
    assert instance_mod.read_instance() is None


# ---------------------------------------------------------------------------
# DaemonClock.stop() must never silently claim success on a hung thread
# ---------------------------------------------------------------------------


def test_stop_logs_error_and_running_stays_true_when_thread_wont_join(tmp_path, caplog):
    clock = DaemonClock(str(tmp_path / "clock.db"), executors={})

    class _HangingThread:
        """Stands in for a polling thread that never exits within the join
        timeout — deterministic and instant, no real hang required."""

        def join(self, timeout=None):
            return None

        def is_alive(self):
            return True

    clock._thread = _HangingThread()

    with caplog.at_level(logging.ERROR, logger="gaia.daemon.scheduler.clock"):
        clock.stop()

    assert any(r.levelno >= logging.ERROR for r in caplog.records), (
        "stop() must log loudly when the polling thread fails to join; "
        f"captured records: {[r.getMessage() for r in caplog.records]}"
    )
    assert clock.running is True, (
        "stop() must not clear internal thread state when the join failed — "
        "running must keep reporting True, never a false 'stopped'"
    )


# ---------------------------------------------------------------------------
# GET /daemon/v1/status: additive "clock" object
# ---------------------------------------------------------------------------


class _FakeClock:
    def __init__(self, *, running, last_poll_at, pending, failed):
        self.running = running
        self.last_poll_at = last_poll_at
        self._pending = pending
        self._failed = failed

    def job_counts(self):
        return {"pending": self._pending, "failed": self._failed}


def test_status_omits_clock_key_when_clock_is_none():
    from fastapi.testclient import TestClient

    from gaia.daemon.app import create_app

    app = create_app(token="tok-status-a", port=1234, pid=111, started_at=0.0)
    client = TestClient(app, raise_server_exceptions=False)

    r = client.get(
        "/daemon/v1/status", headers={"Authorization": "Bearer tok-status-a"}
    )

    assert r.status_code == 200
    assert "clock" not in r.json()


def test_status_includes_clock_object_when_clock_given():
    from fastapi.testclient import TestClient

    from gaia.daemon.app import create_app

    fake_clock = _FakeClock(running=True, last_poll_at=12345.5, pending=2, failed=1)
    app = create_app(
        token="tok-status-b",
        port=1234,
        pid=222,
        started_at=0.0,
        clock=fake_clock,
    )
    client = TestClient(app, raise_server_exceptions=False)

    r = client.get(
        "/daemon/v1/status", headers={"Authorization": "Bearer tok-status-b"}
    )

    assert r.status_code == 200
    body = r.json()
    assert body["clock"] == {
        "running": True,
        "last_poll_at": 12345.5,
        "pending_jobs": 2,
        "failed_jobs": 1,
    }


# ---------------------------------------------------------------------------
# Guard tests: keep this fix scoped to daemon-clock wiring, nothing else
# ---------------------------------------------------------------------------


def test_no_gaia_agent_email_import_under_daemon():
    """No file under src/gaia/daemon/ may actually import gaia_agent_email —
    the daemon core never imports a hub agent wheel. Comments mentioning the
    string (documenting a MUST-match constant) are fine; only real `import`/
    `from ... import` statements are forbidden."""
    offenders = []
    for path in sorted(_DAEMON_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "gaia_agent_email" or alias.name.startswith(
                        "gaia_agent_email."
                    ):
                        offenders.append(str(path))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "gaia_agent_email" or module.startswith(
                    "gaia_agent_email."
                ):
                    offenders.append(str(path))

    assert offenders == [], (
        "src/gaia/daemon/ must never import gaia_agent_email (core cannot "
        f"depend on a hub agent wheel); found real import statements in: "
        f"{sorted(set(offenders))}"
    )


def test_no_autonomy_reference_in_scheduler_wiring():
    """Neither server.py nor anything under daemon/scheduler/ may reference
    the unrelated 'autonomy' scheduler concept — this fix wires the existing
    DaemonClock only."""
    targets = [_DAEMON_SRC / "server.py"] + sorted(
        (_DAEMON_SRC / "scheduler").rglob("*.py")
    )
    offenders = [
        str(path)
        for path in targets
        if path.is_file() and "autonomy" in path.read_text(encoding="utf-8").lower()
    ]

    assert offenders == [], (
        "found a forbidden 'autonomy' reference (case-insensitive) in: " f"{offenders}"
    )
