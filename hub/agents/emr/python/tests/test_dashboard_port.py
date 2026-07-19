# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for dashboard server port-conflict handling."""

import socket
import unittest
from unittest.mock import patch


class TestDashboardPortConflict(unittest.TestCase):
    """run_dashboard must fail loudly when its port is already taken."""

    def test_run_dashboard_raises_actionable_error_on_occupied_port(self):
        """An occupied port raises RuntimeError naming the port and a fix."""
        from gaia_agent_emr.dashboard.server import run_dashboard

        squatter = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        squatter.bind(("127.0.0.1", 0))
        squatter.listen(1)
        port = squatter.getsockname()[1]
        try:
            with self.assertRaises(RuntimeError) as ctx:
                run_dashboard(host="127.0.0.1", port=port)
            message = str(ctx.exception)
            self.assertIn(str(port), message)
            self.assertIn("--port", message)
        finally:
            squatter.close()

    def test_ipv6_host_occupied_port_raises_the_same_error(self):
        """The probe picks AF_INET6 for IPv6 hosts instead of misfiring."""
        from gaia_agent_emr.dashboard.server import _ensure_port_free

        squatter = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        try:
            squatter.bind(("::1", 0))
        except OSError:
            self.skipTest("IPv6 loopback unavailable")
        squatter.listen(1)
        port = squatter.getsockname()[1]
        try:
            with self.assertRaises(RuntimeError) as ctx:
                _ensure_port_free("::1", port)
            self.assertIn(str(port), str(ctx.exception))
        finally:
            squatter.close()

    def test_non_eaddrinuse_bind_errors_are_not_misreported(self):
        """A family/resolution error must not claim the port is in use."""
        from gaia_agent_emr.dashboard.server import _ensure_port_free

        # IPv6-shaped host forced onto a bad address: EINVAL/EADDRNOTAVAIL,
        # not EADDRINUSE — the probe must stay silent and defer to uvicorn.
        _ensure_port_free("::ffff:999.0.0.1", 0)  # no exception expected

    def test_cmd_dashboard_reports_port_conflict_and_exits_nonzero(self):
        """The CLI surfaces the conflict as an error instead of a traceback."""
        import argparse

        from gaia_agent_emr.cli import cmd_dashboard

        args = argparse.Namespace(
            watch_dir="./intake_forms",
            db="./data/patients.db",
            host="127.0.0.1",
            port=8080,
            no_open=True,
            browser=False,
        )
        with patch(
            "gaia_agent_emr.dashboard.server.run_dashboard",
            side_effect=RuntimeError("port 8080 already in use"),
        ):
            result = cmd_dashboard(args)
        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
