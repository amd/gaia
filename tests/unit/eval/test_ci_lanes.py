# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Guard: every scenario category is measured by exactly one CI lane.

The eval runs as parallel lanes and each lane builds its ``--category`` list from
``eval/ci_lanes.json``. That makes an orphan the dangerous edit: add a category
directory, forget the lane map, and nothing runs it — while every lane stays
green, because a lane only reports on the categories it was told about. Nothing
in the workflow can notice a category it was never handed.

So the mapping is checked against what is actually on disk: claimed by a lane, or
listed in ``excluded`` with a reason. ``vision`` and ``web_system`` must be in the
second group — they read the runner's screen, windows and clipboard into a
scorecard that uploads as a public artifact.
"""

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIO_DIR = REPO_ROOT / "eval" / "scenarios"
LANES_FILE = REPO_ROOT / "eval" / "ci_lanes.json"

# Never measurable in CI: both read the runner's screen/clipboard/windows into a
# scorecard that uploads as a public artifact.
SCREEN_READING = {"vision", "web_system"}


@pytest.fixture(scope="module")
def lanes_doc() -> dict:
    return json.loads(LANES_FILE.read_text(encoding="utf-8"))


def _categories_on_disk() -> set[str]:
    return {
        path.name
        for path in SCENARIO_DIR.iterdir()
        if path.is_dir() and any(path.glob("*.yaml"))
    }


def test_every_category_is_claimed_or_excluded(lanes_doc):
    claimed = {c for lane in lanes_doc["lanes"] for c in lane["categories"]}
    excluded = set(lanes_doc["excluded"])
    orphans = _categories_on_disk() - claimed - excluded
    assert not orphans, (
        f"No CI lane runs {sorted(orphans)}, and no entry in {LANES_FILE.name} "
        "says why. Add each to a lane's `categories`, or to `excluded` with the "
        "reason it cannot run in CI. Left as-is these scenarios stop being "
        "measured and every lane still reports green."
    )


def test_lane_map_names_no_category_that_does_not_exist(lanes_doc):
    on_disk = _categories_on_disk()
    named = {c for lane in lanes_doc["lanes"] for c in lane["categories"]}
    named |= set(lanes_doc["excluded"])
    missing = named - on_disk
    assert not missing, (
        f"{LANES_FILE.name} names {sorted(missing)}, which has no directory under "
        "eval/scenarios/. `gaia eval agent --category` on it measures nothing."
    )


def test_no_category_is_in_two_lanes(lanes_doc):
    seen: dict[str, str] = {}
    for lane in lanes_doc["lanes"]:
        for category in lane["categories"]:
            assert category not in seen, (
                f"`{category}` is in both the `{seen[category]}` and `{lane['lane']}` "
                "lanes, so it runs twice and burns a scarce runner slot for a "
                "duplicate scorecard."
            )
            seen[category] = lane["lane"]


def test_screen_reading_categories_are_never_in_a_lane(lanes_doc):
    claimed = {c for lane in lanes_doc["lanes"] for c in lane["categories"]}
    leaked = SCREEN_READING & claimed
    assert not leaked, (
        f"{sorted(leaked)} is in a CI lane. These read the runner's clipboard, "
        "windows and screen, and the lane uploads its scorecard and traces as a "
        "PUBLIC artifact. They belong in `excluded`."
    )
    assert SCREEN_READING <= set(lanes_doc["excluded"]), (
        "vision and web_system must stay listed in `excluded` with their reason, "
        "so a later edit has to read why before moving them."
    )


def test_lane_names_are_unique_and_artifact_safe(lanes_doc):
    names = [lane["lane"] for lane in lanes_doc["lanes"]]
    assert len(names) == len(set(names)), (
        f"Duplicate lane name in {names}. Lane names key the per-lane artifact, "
        "so two lanes sharing one would collide on upload."
    )
    for name in names:
        assert name and name.replace("-", "").replace("_", "").isalnum(), (
            f"Lane name `{name}` is not safe as an artifact name / concurrency "
            "group key; use letters, digits, dash or underscore."
        )


def test_every_lane_has_at_least_one_category(lanes_doc):
    for lane in lanes_doc["lanes"]:
        assert lane["categories"], (
            f"Lane `{lane['lane']}` has no categories, so its job would spend a "
            "runner booting Lemonade and measure nothing, then report success."
        )
