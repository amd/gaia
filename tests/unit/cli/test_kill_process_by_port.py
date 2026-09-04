# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Regression tests for ``gaia kill --port`` / ``gaia api stop`` targeting.

The port matcher used to substring-match ``f":{port}"`` against a whole
``netstat`` line, so ``--port 80`` also matched a ``:8009`` foreign address and
every ESTABLISHED / TIME_WAIT row, then killed the first pid it found. amd/gaia#789
fixed the ``shell=True`` half of the same routine; the substring match survived the
rewrite verbatim.

The fixtures below are real ``netstat`` output shapes, trimmed.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from gaia.cli import kill_process_by_port
from gaia.ports import (
    is_killable_process,
    listeners_on_port,
    parse_unix_netstat_listeners,
    parse_windows_netstat_listeners,
)

# ---------------------------------------------------------------------------
# Fixture output
# ---------------------------------------------------------------------------

# Captured from a real `netstat -ano` on a Windows dev box. The two :8009 rows
# are why `gaia kill --port 80` used to taskkill /F an unrelated browser.
WINDOWS_NETSTAT = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       2540
  TCP    127.0.0.1:4200         0.0.0.0:0              LISTENING       9001
  TCP    0.0.0.0:8080           0.0.0.0:0              LISTENING       7100
  TCP    192.168.1.178:51799    192.168.1.48:8009      ESTABLISHED     18040
  TCP    192.168.1.178:52576    192.168.1.53:8009      ESTABLISHED     18040
  TCP    192.168.1.178:49179    160.79.104.10:443      ESTABLISHED     22808
  TCP    192.168.1.178:60122    52.109.12.23:80        TIME_WAIT       0
  TCP    [::]:4200              [::]:0                 LISTENING       9001
  UDP    0.0.0.0:4200           *:*                                    5555
"""

UNIX_NETSTAT = """\
Active Internet connections (only servers)
Proto Recv-Q Send-Q Local Address           Foreign Address         State       PID/Program name
tcp        0      0 0.0.0.0:22              0.0.0.0:*               LISTEN      1234/sshd
tcp        0      0 127.0.0.1:4200          0.0.0.0:*               LISTEN      9001/python3
tcp        0      0 0.0.0.0:8080            0.0.0.0:*               LISTEN      7100/nginx
tcp        0      0 10.0.0.5:51799          10.0.0.9:8009           ESTABLISHED 18040/chrome
tcp6       0      0 :::13305                :::*                    LISTEN      4242/lemonade-server
udp        0      0 0.0.0.0:68              0.0.0.0:*                           900/dhclient
"""


# ---------------------------------------------------------------------------
# Windows netstat -ano
# ---------------------------------------------------------------------------


class TestWindowsNetstatParsing:
    def test_port_80_does_not_match_an_8009_foreign_address(self):
        """':80' is a substring of ':8009' and of ':8080'."""
        assert parse_windows_netstat_listeners(WINDOWS_NETSTAT, 80) == []

    def test_time_wait_row_is_not_a_listener(self):
        """The TIME_WAIT row above has local port 60122 and foreign port 80."""
        pids = parse_windows_netstat_listeners(WINDOWS_NETSTAT, 60122)
        assert pids == [], "a TIME_WAIT socket owns no server to kill"

    def test_established_client_socket_is_not_a_listener(self):
        assert parse_windows_netstat_listeners(WINDOWS_NETSTAT, 51799) == []
        assert parse_windows_netstat_listeners(WINDOWS_NETSTAT, 443) == []

    def test_exact_listening_port_is_matched(self):
        assert parse_windows_netstat_listeners(WINDOWS_NETSTAT, 8080) == [7100]
        assert parse_windows_netstat_listeners(WINDOWS_NETSTAT, 135) == [2540]

    def test_ipv4_and_ipv6_rows_for_one_listener_dedupe_to_one_pid(self):
        assert parse_windows_netstat_listeners(WINDOWS_NETSTAT, 4200) == [9001]

    def test_udp_row_is_ignored(self):
        """The UDP :4200 row has pid 5555 and must never be selected."""
        assert 5555 not in parse_windows_netstat_listeners(WINDOWS_NETSTAT, 4200)

    def test_localized_state_column_still_detects_a_listener(self):
        """netstat localizes 'LISTENING'; a zero foreign port identifies it anyway."""
        german = (
            "  Proto  Lokale Adresse         Remoteadresse          Status    PID\n"
            "  TCP    0.0.0.0:4200           0.0.0.0:0              ABHÖREN   9001\n"
        )
        assert parse_windows_netstat_listeners(german, 4200) == [9001]

    def test_pid_zero_is_never_returned(self):
        idle = (
            "  TCP    0.0.0.0:4200           0.0.0.0:0              LISTENING       0\n"
        )
        assert parse_windows_netstat_listeners(idle, 4200) == []

    def test_header_and_blank_lines_are_ignored(self):
        assert parse_windows_netstat_listeners(WINDOWS_NETSTAT, 0) == []


# ---------------------------------------------------------------------------
# Unix netstat -tulpn fallback
# ---------------------------------------------------------------------------


class TestUnixNetstatParsing:
    def test_port_80_does_not_match_8009_or_8080(self):
        assert parse_unix_netstat_listeners(UNIX_NETSTAT, 80) == []

    def test_established_row_is_not_a_listener(self):
        assert parse_unix_netstat_listeners(UNIX_NETSTAT, 51799) == []

    def test_listener_returns_pid_and_program_name(self):
        assert parse_unix_netstat_listeners(UNIX_NETSTAT, 4200) == [(9001, "python3")]
        assert parse_unix_netstat_listeners(UNIX_NETSTAT, 13305) == [
            (4242, "lemonade-server")
        ]

    def test_udp_row_is_ignored(self):
        assert parse_unix_netstat_listeners(UNIX_NETSTAT, 68) == []


# ---------------------------------------------------------------------------
# Ownership allowlist
# ---------------------------------------------------------------------------


class TestKillableProcessAllowlist:
    @pytest.mark.parametrize(
        "name",
        ["gaia.exe", "lemonade-server", "lemond", "python3", "python.exe", "node"],
    )
    def test_gaia_and_lemonade_processes_are_killable(self, name):
        assert is_killable_process(name)

    @pytest.mark.parametrize("name", ["svchost.exe", "chrome.exe", "nginx", "sshd", ""])
    def test_unrelated_processes_are_not_killable(self, name):
        assert not is_killable_process(name)


# ---------------------------------------------------------------------------
# kill_process_by_port end to end
# ---------------------------------------------------------------------------


class TestKillProcessByPort:
    def test_refuses_to_kill_a_process_that_is_not_ours(self, mocker):
        mocker.patch("gaia.cli.listeners_on_port", return_value=[(2540, "svchost.exe")])
        terminate = mocker.patch("gaia.cli.terminate_pid")

        result = kill_process_by_port(135)

        terminate.assert_not_called()
        assert result["success"] is False
        assert "Refusing to kill 2540 (svchost.exe)" in result["message"]

    def test_kills_a_gaia_listener(self, mocker):
        mocker.patch("gaia.cli.listeners_on_port", return_value=[(9001, "python.exe")])
        terminate = mocker.patch("gaia.cli.terminate_pid")

        result = kill_process_by_port(4200)

        terminate.assert_called_once_with(9001)
        assert result["success"] is True
        assert "9001" in result["message"]

    def test_no_listener_reports_failure(self, mocker):
        mocker.patch("gaia.cli.listeners_on_port", return_value=[])
        terminate = mocker.patch("gaia.cli.terminate_pid")

        result = kill_process_by_port(80)

        terminate.assert_not_called()
        assert result["success"] is False
        assert "No process is listening on port 80" in result["message"]

    def test_invalid_port_is_rejected_before_any_lookup(self, mocker):
        lookup = mocker.patch("gaia.cli.listeners_on_port")

        result = kill_process_by_port("not-a-port")

        lookup.assert_not_called()
        assert result["success"] is False
        assert "Invalid port number" in result["message"]

    def test_missing_tooling_is_reported_as_such_not_as_no_process(self, mocker):
        mocker.patch(
            "gaia.cli.listeners_on_port",
            side_effect=FileNotFoundError(2, "not found", "netstat"),
        )

        result = kill_process_by_port(4200)

        assert result["success"] is False
        assert "Cannot inspect port 4200" in result["message"]
        assert "netstat" in result["message"]

    def test_undecodable_netstat_output_does_not_surface_as_an_error(self, mocker):
        """OEM-codepage bytes used to raise UnicodeDecodeError and exit 1."""
        mocker.patch("sys.platform", "win32")
        oem = b"  TCP    0.0.0.0:4200    0.0.0.0:0    \xc4BH\xd6REN    9001\n"
        mocker.patch(
            "gaia.ports.subprocess.check_output",
            side_effect=lambda *a, **kw: oem.decode("utf-8", errors=kw["errors"]),
        )
        mocker.patch("gaia.ports.process_image_name", return_value="python.exe")
        terminate = mocker.patch("gaia.cli.terminate_pid")

        result = kill_process_by_port(4200)

        terminate.assert_called_once_with(9001)
        assert result["success"] is True


class TestListenerLookupCallShape:
    """Mocks prove we called the tool; these assert the call would be valid."""

    def test_windows_netstat_decodes_leniently(self, mocker):
        mocker.patch("sys.platform", "win32")
        check_output = mocker.patch(
            "gaia.ports.subprocess.check_output", return_value=""
        )

        listeners_on_port(4200)

        args, kwargs = check_output.call_args
        assert args[0] == ["netstat", "-ano"]
        assert kwargs["errors"] == "replace", "OEM codepage output must not raise"

    def test_lsof_is_restricted_to_listening_sockets(self, mocker):
        """Without -sTCP:LISTEN, lsof returns both ends of every connection and
        kill -9 takes out the Agent UI backend and any client of the port."""
        mocker.patch("sys.platform", "linux")
        run = mocker.patch(
            "gaia.ports.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout="9001\n", stderr=""),
        )
        mocker.patch("gaia.ports.process_image_name", return_value="python3")

        assert listeners_on_port(4200) == [(9001, "python3")]

        argv = run.call_args[0][0]
        assert argv[:2] == ["lsof", "-nP"]
        assert "-iTCP:4200" in argv
        assert "-sTCP:LISTEN" in argv
        assert "-t" in argv
        assert "-ti:4200" not in argv, "the old form also lists connected clients"
        assert run.call_args[1]["errors"] == "replace"

    def test_lsof_finding_nothing_is_not_a_missing_lsof(self, mocker):
        """lsof exits 1 when no socket matches. That is an answer, not a failure —
        falling through to `netstat -tulpn` breaks macOS, where that is not valid
        BSD syntax."""
        mocker.patch("sys.platform", "linux")
        mocker.patch(
            "gaia.ports.subprocess.run",
            return_value=SimpleNamespace(returncode=1, stdout="", stderr=""),
        )
        netstat = mocker.patch("gaia.ports.subprocess.check_output")

        assert listeners_on_port(4200) == []
        netstat.assert_not_called()

    def test_a_real_lsof_failure_is_not_swallowed(self, mocker):
        mocker.patch("sys.platform", "linux")
        mocker.patch(
            "gaia.ports.subprocess.run",
            return_value=SimpleNamespace(returncode=127, stdout="", stderr="boom"),
        )

        with pytest.raises(subprocess.CalledProcessError):
            listeners_on_port(4200)

    def test_falls_back_to_netstat_when_lsof_is_missing(self, mocker):
        mocker.patch("sys.platform", "linux")
        mocker.patch("gaia.ports.subprocess.run", side_effect=FileNotFoundError("lsof"))
        netstat = mocker.patch(
            "gaia.ports.subprocess.check_output", return_value=UNIX_NETSTAT
        )

        assert listeners_on_port(13305) == [(4242, "lemonade-server")]
        assert netstat.call_args[0][0] == ["netstat", "-tulpn"]
