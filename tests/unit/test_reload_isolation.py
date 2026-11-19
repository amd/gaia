# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests that reload ``gaia.config`` must not leak new classes into later tests.

A leaked reload left ``gaia.config.UnsafeGaiaHomeError`` a different class from
the one ``gaia.ui.utils`` raises, so the Agent UI's unsafe-GAIA_HOME refusal
fell into its generic handler and exited 1 instead of 64.
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Every test here reloads gaia.config; the last one needs the original classes.
_ORDERED_TESTS = [
    "tests/unit/test_gaia_home.py::test_gaia_home_carries_config_json",
    "tests/unit/test_cli_config.py::TestConfigPathEnvOverride",
    "tests/unit/test_ui_fault_log.py::"
    "test_an_unsafe_gaia_home_is_refused_before_the_fault_log_is_opened",
]


def test_config_reload_tests_leave_the_original_classes_in_place():
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:randomly",
            "-p",
            "no:xdist",
            "-p",
            "no:cacheprovider",
            "-q",
            *_ORDERED_TESTS,
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    assert "4 passed" in result.stdout, result.stdout[-4000:]
