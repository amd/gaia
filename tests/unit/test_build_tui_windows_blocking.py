# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
A Windows-only TUI regression must turn the TUI check red.

The Windows test leg used to be advisory (``continue-on-error``) and skipped the
``tui/test`` integration suite, so a break that only showed on Windows merged
with every check green (#4791). These assertions keep it a blocking, full run.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOW = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "build_tui.yml"
)


@pytest.fixture(scope="module")
def test_job() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["test"]


def test_windows_is_a_matrix_leg(test_job):
    matrix = test_job["strategy"]["matrix"]
    oses = set(matrix.get("os", [])) | {i.get("os") for i in matrix.get("include", [])}
    assert {"ubuntu-latest", "macos-latest", "windows-latest"} <= oses


def test_no_leg_is_advisory(test_job):
    assert (
        "continue-on-error" not in test_job
    ), "a test leg can fail without failing the run"
    for leg in test_job["strategy"]["matrix"].get("include", []):
        assert not leg.get("advisory"), leg


def test_every_os_runs_the_whole_suite_under_race(test_job):
    run = next(s for s in test_job["steps"] if s.get("name") == "Run tests (race)")
    assert "if" not in run
    cmd = run["run"]
    assert "go test ./..." in cmd and "-race" in cmd
    assert "runner.os" not in cmd, "the test command branches per OS"
    assert "grep -v" not in cmd, "a package is filtered out of the run"
