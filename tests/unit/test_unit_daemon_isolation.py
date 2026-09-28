# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The unit suite must never see or spawn the developer's real daemon."""

from pathlib import Path

from gaia.daemon import client, paths


def test_daemon_home_is_not_the_real_host_dir():
    real = Path.home() / ".gaia" / "host"
    assert paths.host_dir() != real
    assert paths.instance_path().parent == paths.host_dir()


def test_daemon_spawn_is_guarded():
    # Calling the guard would fail this test at teardown, so check it is installed.
    assert client._spawn_and_wait.__module__ != client.__name__
