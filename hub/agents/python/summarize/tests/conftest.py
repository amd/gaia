# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shared fixtures for the summarizer test suite.

Most of these tests drive real LLM inference through the CLI and need a
running Lemonade server; they are marked ``lemonade`` and skip cleanly when
the server is unreachable (mirrors the ``require_lemonade`` convention in
the repo-root tests/conftest.py) so the LLM-free tests still run in CI.
"""

import pytest
import requests

LEMONADE_HEALTH_URL = "http://localhost:13305/api/v1/health"


def _lemonade_available() -> bool:
    try:
        return requests.get(LEMONADE_HEALTH_URL, timeout=5).status_code == 200
    except requests.RequestException:
        return False


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "lemonade: test needs a running Lemonade server (real LLM inference)",
    )


def pytest_collection_modifyitems(config, items):
    if _lemonade_available():
        return
    skip = pytest.mark.skip(
        reason=f"Lemonade server not reachable at {LEMONADE_HEALTH_URL}"
    )
    for item in items:
        if "lemonade" in item.keywords:
            item.add_marker(skip)
