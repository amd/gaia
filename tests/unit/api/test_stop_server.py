# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Regression tests for ``stop_server`` (``python -m gaia.api.app stop``).

``stop_server`` carried its own copy of the port matcher that
``kill_process_by_port`` had: ``f":{port}" in line`` over a whole ``netstat``
line, so ``--port 80`` also selected a listener on ``:8080`` and rows whose
*foreign* address ended in ``:80``, then ``taskkill /F``'d them with no check
on who owned the process. It now shares :mod:`gaia.ports` with every other
"stop what's on this port" path.
"""

from __future__ import annotations

import pytest

from gaia.api.app import stop_server
from gaia.ports import parse_windows_netstat_listeners

# A `netstat -ano` excerpt with the two rows the old substring match wrongly
# selected for port 80: an :8080 listener and an :80 foreign address.
NETSTAT_WITH_8080 = """
  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:8080           0.0.0.0:0              LISTENING       7100
  TCP    192.168.1.10:60122     52.109.12.23:80        ESTABLISHED     18040
  TCP    127.0.0.1:80           0.0.0.0:0              LISTENING       4242
"""


class TestPortTargeting:
    def test_port_80_does_not_select_the_8080_listener(self):
        assert parse_windows_netstat_listeners(NETSTAT_WITH_8080, 80) == [4242]

    def test_port_8080_selects_only_its_own_listener(self):
        assert parse_windows_netstat_listeners(NETSTAT_WITH_8080, 8080) == [7100]


class TestStopServer:
    def test_stops_a_gaia_listener(self, mocker, capsys):
        mocker.patch(
            "gaia.ports.listeners_on_port", return_value=[(9001, "python.exe")]
        )
        mocker.patch("sys.platform", "linux")
        killer = mocker.patch("gaia.api.app.os.kill")

        stop_server(8080)

        killer.assert_called_once()
        assert killer.call_args[0][0] == 9001
        out = capsys.readouterr().out
        assert "Stopped API server process (PID: 9001)" in out
        assert "✅ API server stopped" in out

    def test_refuses_a_process_that_is_not_ours(self, mocker, capsys):
        """The old code taskkill /F'd whatever the substring match hit."""
        mocker.patch("gaia.ports.listeners_on_port", return_value=[(7100, "nginx")])
        mocker.patch("sys.platform", "linux")
        killer = mocker.patch("gaia.api.app.os.kill")

        stop_server(8080)

        killer.assert_not_called()
        out = capsys.readouterr().out
        assert "Refusing to stop PID 7100 (nginx)" in out
        assert "✅ API server stopped" not in out

    def test_no_listener_reports_nothing_running(self, mocker, capsys):
        mocker.patch("gaia.ports.listeners_on_port", return_value=[])
        killer = mocker.patch("gaia.api.app.os.kill")

        stop_server(8080)

        killer.assert_not_called()
        assert "No API server found running on port 8080" in capsys.readouterr().out

    def test_missing_tooling_prints_manual_instructions(self, mocker, capsys):
        mocker.patch(
            "gaia.ports.listeners_on_port",
            side_effect=FileNotFoundError(2, "not found", "lsof"),
        )

        stop_server(8080)

        out = capsys.readouterr().out
        assert "Required command not found" in out
        assert "To stop manually" in out

    @pytest.mark.parametrize("port", [80, 8080])
    def test_only_the_exact_port_is_looked_up(self, mocker, port):
        lookup = mocker.patch("gaia.ports.listeners_on_port", return_value=[])

        stop_server(port)

        lookup.assert_called_once_with(port)
