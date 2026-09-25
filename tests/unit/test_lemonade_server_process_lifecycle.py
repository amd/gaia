# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Launching and stopping a local Lemonade server must only touch that server.

``terminate_server`` signals the server's whole process group so Lemonade's own
children (llama-server) go with it. That is only safe when the server leads its
own group; sharing the caller's group took the caller and its shell down too.
The port cleanup around launch/terminate must also leave non-GAIA listeners
alone.
"""

import os
import shutil
import signal
import socket
import subprocess
import sys
import textwrap
import time
from unittest.mock import MagicMock, patch

import pytest

from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError
from gaia.ports import listeners_on_port

posix_only = pytest.mark.skipif(
    sys.platform.startswith("win"), reason="process groups are POSIX-only"
)

# Launches `sleep 30` through the real launch_server Popen shape, then stops it.
# Runs in its own session so a group-wide kill can't reach pytest.
_REPRODUCER = textwrap.dedent("""
    import os, sys
    from unittest.mock import patch

    from gaia.llm.lemonade_client import LemonadeClient
    from gaia.llm.lemonade_launcher import LemonadeTooling, StartSpec

    background = sys.argv[1]
    tooling = LemonadeTooling(
        found=True, kind="modern", client_path="lemonade",
        server_launcher="/bin/sleep",
    )
    client = LemonadeClient(host="localhost", port=1, verbose=False)
    with patch.object(client, "health_check", return_value=None), \\
         patch("gaia.llm.lemonade_client.resolve_lemonade", return_value=tooling), \\
         patch(
             "gaia.llm.lemonade_client.build_start_command",
             return_value=StartSpec(argv=["sleep", "30"], env={}),
         ), \\
         patch("gaia.llm.lemonade_client.socket.create_connection"), \\
         patch("gaia.llm.lemonade_client.time.sleep"):
        client.launch_server(background=background)
        proc = client.server_process
        print(f"CHILD_PGID {os.getpgid(proc.pid)} OWN_PGID {os.getpgrp()}", flush=True)
        client.terminate_server()
    print(f"CHILD_RC {proc.poll()}", flush=True)
    print("SURVIVED", flush=True)
""")


@posix_only
@pytest.mark.parametrize("background", ["none", "silent", "terminal"])
def test_terminate_server_spares_the_caller(tmp_path, background):
    script = tmp_path / "reproducer.py"
    script.write_text(_REPRODUCER, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(script), background],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        start_new_session=True,
        check=False,
    )

    assert result.returncode == 0, (
        f"caller died with {result.returncode} "
        f"(-{int(signal.SIGTERM)} means terminate_server signalled its own group)\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "SURVIVED" in result.stdout
    pgids = next(
        line.split() for line in result.stdout.splitlines() if "CHILD_PGID" in line
    )
    assert pgids[1] != pgids[3], "server must lead its own process group"
    rc_line = next(line for line in result.stdout.splitlines() if "CHILD_RC" in line)
    assert rc_line != "CHILD_RC None", "server process was left running"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _launch_with_mocked_server(client):
    from gaia.llm.lemonade_launcher import LemonadeTooling, StartSpec

    tooling = LemonadeTooling(
        found=True, kind="legacy", client_path="x", server_launcher="x"
    )
    real_popen = subprocess.Popen

    def popen(argv, *args, **kwargs):
        # Only the server launch is faked; the port lookup (lsof) runs for real.
        if argv[0] == "lemonade-server":
            return MagicMock(pid=999999)
        return real_popen(argv, *args, **kwargs)

    with (
        patch.object(client, "health_check", return_value=None),
        patch("gaia.llm.lemonade_client.resolve_lemonade", return_value=tooling),
        patch(
            "gaia.llm.lemonade_client.build_start_command",
            return_value=StartSpec(argv=["lemonade-server", "serve"], env={}),
        ),
        patch("subprocess.Popen", side_effect=popen) as launched,
        patch("gaia.llm.lemonade_client.open", MagicMock(), create=True),
        patch("gaia.llm.lemonade_client.socket.create_connection"),
        patch("gaia.llm.lemonade_client.time.sleep"),
    ):
        client.launch_server(background="silent")
    return launched


@posix_only
@pytest.mark.skipif(shutil.which("nc") is None, reason="needs nc for a listener")
def test_launch_server_leaves_a_non_gaia_listener_running():
    port = _free_port()
    listener = subprocess.Popen(
        ["nc", "-l", str(port)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5
        while not listeners_on_port(port):
            if time.monotonic() > deadline or listener.poll() is not None:
                pytest.skip("nc did not come up listening on the port")
            time.sleep(0.1)

        client = LemonadeClient(host="localhost", port=port, verbose=False)
        with pytest.raises(LemonadeClientError, match="not a GAIA or Lemonade"):
            _launch_with_mocked_server(client)

        assert listener.poll() is None, "launch_server killed a non-GAIA listener"
    finally:
        listener.kill()
        listener.wait(timeout=5)


def test_launch_server_refuses_a_foreign_listener_without_killing_it():
    client = LemonadeClient(host="localhost", port=13305, verbose=False)
    with (
        patch(
            "gaia.llm.lemonade_client.listeners_on_port",
            return_value=[(4242, "nginx")],
        ),
        patch("gaia.llm.lemonade_client.terminate_pid") as kill,
    ):
        with pytest.raises(LemonadeClientError, match="PID 4242"):
            _launch_with_mocked_server(client)
    kill.assert_not_called()


def test_launch_server_frees_the_port_from_a_stale_lemonade():
    client = LemonadeClient(host="localhost", port=13305, verbose=False)
    with (
        patch(
            "gaia.llm.lemonade_client.listeners_on_port",
            return_value=[(4242, "llama-server"), (os.getpid(), "python")],
        ),
        patch("gaia.llm.lemonade_client.terminate_pid") as kill,
    ):
        _launch_with_mocked_server(client)
    kill.assert_called_once_with(4242)


@pytest.mark.parametrize(
    "platform, expected", [("linux", True), ("darwin", True), ("win32", None)]
)
def test_new_session_is_requested_only_off_windows(platform, expected):
    # CPython raises on start_new_session=True under Windows; taskkill /T covers it.
    client = LemonadeClient(host="localhost", port=13305, verbose=False)
    with (
        patch("gaia.llm.lemonade_client.listeners_on_port", return_value=[]),
        patch("gaia.llm.lemonade_client.sys.platform", platform),
    ):
        launched = _launch_with_mocked_server(client)
    assert launched.call_args.kwargs.get("start_new_session") is expected


def test_terminate_server_leaves_a_foreign_listener_running():
    client = LemonadeClient(host="localhost", port=13305, verbose=False)
    client.server_process = MagicMock(spec=["join", "terminate"])
    with (
        patch(
            "gaia.llm.lemonade_client.listeners_on_port",
            return_value=[(4242, "nginx")],
        ),
        patch("gaia.llm.lemonade_client.terminate_pid") as kill,
    ):
        client.terminate_server()
    kill.assert_not_called()
    assert client.server_process is None
