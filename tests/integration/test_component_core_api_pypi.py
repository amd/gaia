# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Live check of util/check_component_core_api.py against the real PyPI index.

The unit tests stub PyPI; this proves the real index and wheel layout still
match what the guard parses. Skips only when PyPI is unreachable from this
machine or ``GAIA_SKIP_NETWORK_TESTS`` is set.
"""

import os
import socket
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

import check_component_core_api as guard  # noqa: E402


@pytest.fixture
def pypi_index():
    if os.environ.get("GAIA_SKIP_NETWORK_TESTS"):
        pytest.skip("GAIA_SKIP_NETWORK_TESTS is set")
    try:
        socket.getaddrinfo("pypi.org", 443)
    except OSError as e:
        pytest.skip(f"pypi.org did not resolve - offline dev box: {e}")
    return guard.load_pypi_index()


@pytest.mark.network
def test_real_index_lists_the_releases_the_guard_reasons_about(pypi_index):
    published = guard.released_versions(pypi_index)
    assert {(0, 22, 0), (0, 23, 0)} <= set(published)
    assert all(isinstance(raw, str) for raw in published.values())


@pytest.mark.network
def test_real_wheels_ship_the_daemon_api_the_unit_tests_assume(pypi_index):
    assert guard.fetch_released_daemon_api("0.21.2", pypi_index) is None
    assert guard.fetch_released_daemon_api("0.22.0", pypi_index) == "1"
    assert guard.fetch_released_daemon_api("0.23.0", pypi_index) == "1.1"


@pytest.mark.network
def test_committed_manifests_pass_against_real_pypi(pypi_index):
    assert guard.run().problems == []
