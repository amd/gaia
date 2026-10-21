# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""eval_flagship_gemma_stx.yml scores Gemma on Strix Point, and only Gemma.

Each check guards a way the workflow could quietly measure something else, or
crowd the small Strix Point pool: a hardware-picked model, a Halo runner, a
baseline for another model, a hand-written baseline number, both machines held.
"""

import json
from pathlib import Path

import yaml

from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _load(name):
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


CALLER = _load("eval_flagship_gemma_stx.yml")
LANE = _load("eval_scenario_lane.yml")
CALL = CALLER["jobs"]["agent-eval"]
BASELINE_DIR = REPO_ROOT / CALL["with"]["baseline-dir"]
META = json.loads((BASELINE_DIR / "meta.json").read_text(encoding="utf-8"))


def test_it_runs_on_dispatch_only_until_a_tag_trigger_is_agreed():
    # PyYAML reads the bare `on:` key as True.
    assert set(CALLER[True]) == {"workflow_dispatch"}


def test_it_scores_the_model_smaller_pcs_run():
    assert CALL["with"]["agent-model"] == DEFAULT_MODEL_NAME


def test_its_baseline_is_declared_for_that_model():
    assert META["model"] == CALL["with"]["agent-model"]
    assert META["eval_model"], "the driver and judge must be pinned too"


def test_it_runs_on_windows_strix_point_and_never_on_halo():
    labels = json.loads(CALL["with"]["runs-on"])
    assert {"self-hosted", "Windows", "xsj-dispatch", "strix-point"} <= set(labels)
    assert "strix-halo" not in labels


def test_it_never_holds_more_than_one_strix_point_machine():
    assert CALL["strategy"]["max-parallel"] == 1
    assert "${{" not in CALLER["concurrency"]["group"], "one run at a time, any ref"


def test_the_lane_job_timeout_leaves_room_to_upload():
    assert (
        CALL["with"]["job-timeout-minutes"] >= CALL["with"]["eval-timeout-minutes"] + 30
    )


def test_the_lane_pins_the_model_and_asks_the_eval_to_verify_it():
    steps = {s.get("name"): s for s in LANE["jobs"]["lane"]["steps"]}
    run = steps["Run this lane's categories serially"]["run"]
    assert "eval_gaia_home.py pin-model" in run
    assert '--agent-model "$env:AGENT_MODEL"' in run
    assert run.index("pin-model") < run.index("gaia.ui.server")


def test_the_lane_checks_the_baseline_model_before_using_the_pool():
    names = [s.get("name") for s in LANE["jobs"]["lane"]["steps"]]
    assert names.index("Check the baseline directory is for this model") < names.index(
        "Install Lemonade Server"
    )


def test_a_refused_compare_fails_the_lane():
    steps = {s.get("name"): s for s in LANE["jobs"]["lane"]["steps"]}
    run = steps["Compare each category against its committed baseline"]["run"]
    assert "3 {" in run and "NOT COMPARABLE" in run
    assert "NO BASELINE YET" in run


def test_committed_scorecards_come_from_this_model_and_driver():
    scorecards = sorted(BASELINE_DIR.glob("scorecard_*.json"))
    for path in scorecards:
        config = json.loads(path.read_text(encoding="utf-8"))["config"]
        assert config.get("agent_model") == META["model"], path.name
        assert config.get("model") == META["eval_model"], path.name
    if not scorecards:
        # No run yet, so no numbers: a baseline figure nobody measured looks
        # exactly like a verdict.
        for key in ("source_run", "scenarios", "passed", "pass_rate"):
            assert META[key] is None, key
