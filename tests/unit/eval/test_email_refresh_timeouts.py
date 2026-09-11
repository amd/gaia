# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Execute the workflow's actual timeout calculation for repeat dispatches."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


@pytest.mark.parametrize(
    "limit,experiments,benchmark_minutes,job",
    [(250, 1, 210, 440), (250, 3, 630, 860), (25, 1, 45, 275), (25, 3, 135, 365)],
)
def test_repeat_budget_scales(limit, experiments, benchmark_minutes, job, tmp_path):
    workflow = (
        Path(__file__).resolve().parents[3]
        / ".github/workflows/email_scorecard_refresh.yml"
    )
    jobs = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]
    script = jobs["plan-timeouts"]["steps"][0]["run"]
    output = tmp_path / "output"
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=dict(
            os.environ,
            LIMIT=str(limit),
            EXPERIMENTS=str(experiments),
            GITHUB_OUTPUT=str(output),
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert output.read_text() == f"benchmark={benchmark_minutes}\njob={job}\n"
    assert jobs["refresh"]["needs"] == "plan-timeouts"
    assert (
        jobs["refresh"]["timeout-minutes"]
        == "${{ fromJSON(needs.plan-timeouts.outputs.job) }}"
    )
    step = next(
        step
        for step in jobs["refresh"]["steps"]
        if step["name"] == "Run the email-triage benchmark (real eval)"
    )
    assert (
        step["timeout-minutes"]
        == "${{ fromJSON(needs.plan-timeouts.outputs.benchmark) }}"
    )
