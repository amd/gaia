# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
The eval-ci guide's example jobs must not time out before the categories they run finish.

Readers copy these workflows verbatim. A ``timeout-minutes`` below the measured wall
clock kills the job mid-eval, so each example job's timeout is checked against the
committed flagship baseline, scaled to the category's current scenario count
(scenarios run one at a time, so a category's time is the sum of its scenarios).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
GUIDE = REPO / "docs" / "guides" / "eval-ci.mdx"
BASELINE = REPO / "tests" / "fixtures" / "eval_baselines" / "gaia-flagship"
SCENARIOS = REPO / "eval" / "scenarios"


def _example_workflows() -> dict[str, dict]:
    text = GUIDE.read_text(encoding="utf-8")
    blocks = re.findall(r'```yaml title="([^"]+)"\n(.*?)\n```', text, re.S)
    assert blocks, f"no titled yaml examples found in {GUIDE}"
    return {title: yaml.safe_load(body) for title, body in blocks}


def _expected_minutes(category: str) -> float:
    card = json.loads(
        (BASELINE / f"scorecard_{category}.json").read_text(encoding="utf-8")
    )
    elapsed = [s["elapsed_s"] for s in card["scenarios"] if s.get("elapsed_s")]
    assert elapsed, f"baseline for {category} has no scenarios"
    count = len(list((SCENARIOS / category).glob("*.yaml")))
    return sum(elapsed) / len(elapsed) * count / 60


def _jobs_running_eval():
    for title, wf in _example_workflows().items():
        for job_id, job in wf["jobs"].items():
            cmds = " ".join(
                step.get("run", "") for step in job.get("steps", []) if "run" in step
            )
            if "gaia eval agent" not in cmds or "--audit-only" in cmds:
                continue
            yield title, job_id, job, re.findall(r"--category (\w+)", cmds)


def test_guide_has_eval_jobs():
    assert list(_jobs_running_eval()), "found no example job that runs gaia eval agent"


@pytest.mark.parametrize(
    "title, job_id, job, categories",
    [pytest.param(*j, id=f"{j[0]}:{j[1]}") for j in _jobs_running_eval()],
)
def test_job_timeout_covers_its_categories(title, job_id, job, categories):
    if not categories:
        pytest.skip("full benchmark: sized by hosted-job limits, not one category")
    needed = sum(_expected_minutes(c) for c in categories)
    timeout = job.get("timeout-minutes")
    assert timeout is not None, f"{title}:{job_id} sets no timeout-minutes"
    assert timeout >= needed, (
        f"{title}:{job_id} times out at {timeout} min but {categories} take "
        f"~{needed:.0f} min at the flagship baseline's per-scenario pace"
    )
