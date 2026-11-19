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

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
GUIDE = REPO / "docs" / "guides" / "eval-ci.mdx"
BASELINE = REPO / "tests" / "fixtures" / "eval_baselines" / "gaia-flagship"
SCENARIOS = REPO / "eval" / "scenarios"
UNIT_WORKFLOW = REPO / ".github" / "workflows" / "test_unit.yml"


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


def _trigger_filters():
    spec = importlib.util.spec_from_file_location(
        "select_tests", REPO / "util" / "select_tests.py"
    )
    select_tests = importlib.util.module_from_spec(spec)
    # Its dataclasses resolve their module through sys.modules.
    sys.modules.setdefault(spec.name, select_tests)
    spec.loader.exec_module(select_tests)
    wf = yaml.safe_load(UNIT_WORKFLOW.read_text(encoding="utf-8"))
    on = wf.get("on", wf.get(True))
    return {
        event: select_tests.path_filter(on[event]["paths"])
        for event in ("push", "pull_request")
    }


def _guide_categories():
    return sorted({c for *_, cats in _jobs_running_eval() for c in cats})


def test_guide_runs_at_least_one_category():
    assert _guide_categories(), "no example job in the guide names a --category"


@pytest.mark.parametrize("category", _guide_categories())
@pytest.mark.parametrize("event", ["push", "pull_request"])
def test_adding_a_scenario_reruns_this_check(category, event):
    # Adding a scenario raises the category's run time, so it must re-run this test.
    new_scenario = f"eval/scenarios/{category}/new_scenario.yaml"
    assert _trigger_filters()[event].match(new_scenario), (
        f"{UNIT_WORKFLOW.name} on.{event}.paths does not match {new_scenario}, so "
        f"adding a {category} scenario can push the guide's timeout below its run "
        f"time without this test noticing; add 'eval/scenarios/{category}/**'"
    )


def test_an_unrelated_category_is_not_matched():
    # The filter stays narrow: other categories' scenarios don't run the unit suite.
    filters = _trigger_filters()
    assert not filters["pull_request"].match("eval/scenarios/gaia_web/x.yaml")
