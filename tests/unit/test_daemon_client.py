# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for client-side daemon compatibility guidance."""

from __future__ import annotations

import pytest

from gaia.daemon import client
from gaia.daemon.errors import DaemonVersionError
from gaia.daemon.instance import DaemonInstance


def _instance(api_version: str) -> DaemonInstance:
    return DaemonInstance(pid=1, port=54321, token="token", api_version=api_version)


@pytest.mark.parametrize(
    ("check", "api_version"),
    [
        (client._check_version, "2.0"),
        (client._check_agents_floor, "1.0"),
    ],
)
def test_daemon_version_errors_guide_users_to_upgrade_the_installed_core(
    check, api_version
):
    with pytest.raises(DaemonVersionError) as exc_info:
        check(_instance(api_version))

    message = str(exc_info.value)
    assert "pip install --upgrade amd-gaia" in message
    assert "https://amd-gaia.ai" in message
    assert "gaia daemon restart` brings the same one back" in message
