# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA_HOME isolates the daemon too, not only config and Lemonade.

A UI started with a fresh GAIA_HOME attached to the machine's default daemon
and so to another home's Lemonade and models.
"""

from pathlib import Path

from gaia.daemon import paths


def test_gaia_home_moves_the_daemon_home(tmp_path, monkeypatch):
    monkeypatch.delenv("GAIA_DAEMON_HOME", raising=False)
    monkeypatch.setenv("GAIA_HOME", str(tmp_path))
    assert paths.host_dir() == tmp_path / "host"


def test_gaia_daemon_home_still_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / "daemon"))
    assert paths.host_dir() == tmp_path / "daemon"


def test_without_either_it_is_the_users_gaia_dir(monkeypatch):
    monkeypatch.delenv("GAIA_DAEMON_HOME", raising=False)
    monkeypatch.delenv("GAIA_HOME", raising=False)
    assert paths.host_dir() == Path.home() / ".gaia" / "host"
