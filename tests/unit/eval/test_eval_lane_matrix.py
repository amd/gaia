# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The lane matrix eval_flagship.yml runs, and the environment each lane gets.

A wrong environment does not fail a lane — it measures something else and
reports it. ``gaia_memory`` once ran with memory disabled because the rule
matched only a category named exactly ``memory``, and every gaia_* lane ran
without the fixtures its scenarios read (#4423).
"""

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "eval_lane_matrix", REPO_ROOT / "util" / "eval_lane_matrix.py"
)
lane_matrix = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lane_matrix)

MEMORY_LIVE = {"memory_disabled": "0", "memory_admin": "1"}


@pytest.fixture(scope="module")
def real_matrix() -> list:
    doc = json.loads((REPO_ROOT / "eval" / "ci_lanes.json").read_text("utf-8"))
    on_disk = lane_matrix.categories_on_disk(REPO_ROOT / "eval" / "scenarios")
    return lane_matrix.build_matrix(doc, on_disk, "eval/ci_lanes.json")


def test_every_gaia_lane_gets_fixtures_and_live_memory(real_matrix):
    gaia_lanes = [lane for lane in real_matrix if "gaia_" in lane["categories"]]
    assert gaia_lanes, "no gaia_* lane in eval/ci_lanes.json"
    for lane in gaia_lanes:
        assert lane["gaia_fixtures"] == "1", lane
        assert {k: lane[k] for k in MEMORY_LIVE} == MEMORY_LIVE, lane


def test_gaia_memory_runs_with_memory_on(real_matrix):
    lane = next(
        each for each in real_matrix if "gaia_memory" in each["categories"].split()
    )
    assert lane["memory_disabled"] == "0"


def test_non_gaia_non_memory_lanes_run_with_memory_off(real_matrix):
    for lane in real_matrix:
        cats = lane["categories"].split()
        if "memory" in cats or any(c.startswith("gaia_") for c in cats):
            continue
        assert lane["memory_disabled"] == "1", lane
        assert lane["gaia_fixtures"] == "0", lane


def test_memory_lane_registers_the_mcp_read_tools(real_matrix):
    lane = next(
        each for each in real_matrix if each["categories"].split() == ["memory"]
    )
    assert lane["memory_mcp"] == "1"


def test_values_are_literal_strings(real_matrix):
    # A GitHub expression reads '0' as falsy, so booleans would be re-derived
    # wrongly downstream; the job must receive the literal env value.
    for lane in real_matrix:
        for key in ("memory_disabled", "memory_admin", "memory_mcp", "gaia_fixtures"):
            assert lane[key] in {"0", "1"}, (key, lane)


def test_a_lane_mixing_gaia_and_other_categories_is_refused():
    with pytest.raises(lane_matrix.LaneMapError, match="mixes gaia_"):
        lane_matrix.lane_env(["gaia_core", "rag_quality"])


@pytest.mark.parametrize(
    "doc, on_disk, match",
    [
        ({"lanes": []}, set(), "defines no lanes"),
        (
            {"lanes": [{"lane": "a", "categories": ["vision"]}]},
            {"vision"},
            "PUBLIC artifact",
        ),
        (
            {"lanes": [{"lane": "a", "categories": ["x"]}]},
            {"x", "orphan"},
            "No lane runs",
        ),
        (
            {"lanes": [{"lane": "a", "categories": ["x", "ghost"]}]},
            {"x"},
            "no directory",
        ),
    ],
)
def test_bad_lane_maps_are_refused(doc, on_disk, match):
    with pytest.raises(lane_matrix.LaneMapError, match=match):
        lane_matrix.build_matrix(doc, on_disk, "lanes.json")


def test_main_prints_a_github_output_line(capsys):
    assert lane_matrix.main() == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("matrix=")
    assert json.loads(out[len("matrix=") :])["include"]
