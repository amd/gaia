# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Run by tests/integration/test_daemon_lifetime.py in a child pytest: starts a
real daemon and never stops it, which the root conftest must catch."""

from gaia.daemon import client


def test_leaves_a_daemon_running(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / "host"))
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    inst = client.start_or_attach()
    print(f"LEAKED_PID={inst.pid}")
