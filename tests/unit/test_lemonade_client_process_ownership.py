# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A LemonadeClient stops only the server process it launched itself.

Garbage-collecting a client whose ``server_process`` was faked by a test used
to ``taskkill /T`` the fake pid and then stop every Lemonade listening on the
client's port -- the developer's own server.
"""

import atexit
import gc
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.llm.lemonade_client import LemonadeClient, OwnedServerProcess
from gaia.llm.lemonade_launcher import LemonadeTooling, StartSpec

_SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]


@pytest.fixture
def kill_paths():
    """Every way the client could stop a process, recorded instead of run."""
    with (
        patch("gaia.llm.lemonade_client.subprocess.run") as run,
        patch("gaia.llm.lemonade_client.terminate_pid") as terminate_pid,
        patch("gaia.llm.lemonade_client.listeners_on_port") as listeners,
    ):
        yield {"run": run, "terminate_pid": terminate_pid, "listeners": listeners}


def _assert_nothing_killed(kill_paths):
    kill_paths["run"].assert_not_called()
    kill_paths["terminate_pid"].assert_not_called()
    kill_paths["listeners"].assert_not_called()


def _launch(client, argv, background="silent"):
    """Run the real launch_server with only the Lemonade-specific edges stubbed."""
    tooling = LemonadeTooling(
        found=True, kind="modern", client_path="lemonade", server_launcher="x"
    )
    with (
        patch.object(client, "health_check", side_effect=ConnectionError("down")),
        patch("gaia.llm.lemonade_client.gaia_runs_lemonade", return_value=False),
        patch.object(client, "_classify_port_listeners", return_value=([], [])),
        patch("gaia.llm.lemonade_client.resolve_lemonade", return_value=tooling),
        patch(
            "gaia.llm.lemonade_client.build_start_command",
            return_value=StartSpec(argv=list(argv), env={}),
        ),
        patch("gaia.llm.lemonade_client.socket.create_connection"),
        patch("gaia.llm.lemonade_client.time.sleep"),
    ):
        client.launch_server(background=background)


def test_gc_of_a_client_with_a_fake_process_kills_nothing(kill_paths):
    client = LemonadeClient(host="localhost", port=13305, verbose=False)
    client.server_process = MagicMock(pid=12345)
    del client
    gc.collect()
    _assert_nothing_killed(kill_paths)


def test_gc_of_a_client_that_launched_through_a_mocked_popen_kills_nothing(
    kill_paths,
):
    client = LemonadeClient(host="localhost", port=13305, verbose=False)
    with patch("subprocess.Popen", return_value=MagicMock(pid=12345)):
        _launch(client, ["lemonade-server", "serve"])
    assert client.server_process is not None
    del client
    gc.collect()
    _assert_nothing_killed(kill_paths)


def test_close_never_stops_a_process_the_client_did_not_launch(kill_paths):
    fake = MagicMock(pid=12345)
    client = LemonadeClient(host="localhost", port=13305, verbose=False)
    client.server_process = fake
    client.close()
    _assert_nothing_killed(kill_paths)
    fake.kill.assert_not_called()
    fake.terminate.assert_not_called()
    assert client.server_process is None


def test_a_client_attached_to_a_running_server_never_stops_it(kill_paths):
    client = LemonadeClient(host="localhost", port=13305, verbose=False)
    with (
        patch.object(client, "health_check", return_value={"status": "ok"}),
        patch("gaia.llm.lemonade_client.gaia_runs_lemonade", return_value=False),
        patch("subprocess.Popen") as popen,
    ):
        client.launch_server(background="silent")
    popen.assert_not_called()
    with client:
        pass
    client.terminate_server()
    _assert_nothing_killed(kill_paths)


def test_close_stops_the_server_the_client_launched(tmp_path):
    client = LemonadeClient(host="localhost", port=1, verbose=False)
    _launch(client, _SLEEPER)
    proc = client.server_process
    assert isinstance(proc, subprocess.Popen) and proc.poll() is None
    try:
        with patch.object(atexit, "unregister", wraps=atexit.unregister) as unreg:
            client.close()
        assert proc.wait(timeout=15) is not None
        unreg.assert_called_once()
        assert client.server_process is None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_keep_alive_leaves_the_launched_server_running_on_close():
    client = LemonadeClient(host="localhost", port=1, verbose=False, keep_alive=True)
    _launch(client, _SLEEPER)
    proc = client.server_process
    try:
        client.close()
        assert proc.poll() is None
        owned = client._owned_server
        owned.stop_at_exit()  # what atexit would run: keep_alive is honoured
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait(timeout=5)
        atexit.unregister(client._owned_server.stop_at_exit)


def test_a_reused_pid_is_not_killed():
    """Same pid, different start time: it is no longer the process we launched."""
    proc = subprocess.Popen(_SLEEPER)
    try:
        owned = OwnedServerProcess.record(proc)
        assert owned is not None and owned.is_alive()
        owned.create_time -= 1000.0
        with patch("gaia.llm.lemonade_client.subprocess.run") as run:
            owned.kill_tree(MagicMock())
        run.assert_not_called()
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_a_replaced_server_process_is_not_killed(kill_paths):
    """Swapping ``server_process`` after launch does not transfer ownership."""
    client = LemonadeClient(host="localhost", port=1, verbose=False)
    _launch(client, _SLEEPER)
    launched = client.server_process
    try:
        client.server_process = MagicMock(pid=12345)
        client.close()
        _assert_nothing_killed(kill_paths)
        assert launched.poll() is None
    finally:
        launched.kill()
        launched.wait(timeout=5)


def test_mock_processes_are_never_recorded_as_owned():
    assert OwnedServerProcess.record(MagicMock(pid=os.getpid())) is None
    assert OwnedServerProcess.record(None) is None


def test_silent_launch_logs_to_gaia_log_dir_not_cwd(tmp_path, monkeypatch):
    from gaia import config

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(config, "GAIA_CONFIG_DIR", tmp_path / "gaia")
    client = LemonadeClient(host="localhost", port=1, verbose=False)
    with patch("subprocess.Popen", return_value=MagicMock(pid=12345)) as popen:
        _launch(client, ["lemonade-server", "serve"])
    try:
        log_file = Path(popen.call_args.kwargs["stdout"].name)
        assert log_file == tmp_path / "gaia" / "logs" / "lemonade.log"
        assert "lemonade-server serve" in log_file.read_text(encoding="utf-8")
        assert not (cwd / "lemonade.log").exists()
    finally:
        client.terminate_server()


def _guard_attempts():
    for module in sys.modules.values():
        path = getattr(module, "__file__", "") or ""
        if hasattr(module, "_REAL_LEMONADE_ATTEMPTS") and path.endswith("conftest.py"):
            return module._REAL_LEMONADE_ATTEMPTS
    raise AssertionError("tests/unit/conftest.py real-Lemonade guard not loaded")


def test_the_unit_suite_refuses_to_spawn_a_real_lemonade():
    attempts = _guard_attempts()
    before = len(attempts)
    try:
        with pytest.raises(RuntimeError, match="real Lemonade server"):
            subprocess.Popen(["LemonadeServer.exe", "--silent"])
        with pytest.raises(RuntimeError, match="Lemonade by name"):
            subprocess.run(["taskkill", "/F", "/IM", "LemonadeServer.exe"])
    finally:
        del attempts[before:]


def test_the_unit_suite_points_lemonade_at_an_unroutable_address():
    assert os.environ["LEMONADE_BASE_URL"] == "http://127.0.0.1:1"
    assert "LEMONADE_HOST" not in os.environ
    assert "LEMONADE_PORT" not in os.environ
