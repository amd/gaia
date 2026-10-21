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


# ---------------------------------------------------------------------------
# --categories: a subset, regrouped by environment (eval_flagship_gemma_stx.yml)
# ---------------------------------------------------------------------------


def _subset(categories):
    doc = json.loads((REPO_ROOT / "eval" / "ci_lanes.json").read_text("utf-8"))
    on_disk = lane_matrix.categories_on_disk(REPO_ROOT / "eval" / "scenarios")
    return lane_matrix.subset_matrix(doc, on_disk, "eval/ci_lanes.json", categories)


def test_the_gemma_default_set_is_one_memory_off_lane():
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "eval_flagship_gemma_stx.yml"
    ).read_text(encoding="utf-8")
    import yaml

    default = yaml.safe_load(workflow)[True]["workflow_dispatch"]["inputs"][
        "categories"
    ]["default"]
    (lane,) = _subset(default.split())
    assert lane["lane"] == "core"
    assert lane["categories"] == default
    assert lane["memory_disabled"] == "1" and lane["gaia_fixtures"] == "0"


def test_memory_gets_its_own_lane_and_does_not_switch_memory_on_beside_it():
    lanes = {lane["lane"]: lane for lane in _subset(["tool_selection", "memory"])}

    assert lanes["memory"]["categories"] == "memory"
    assert lanes["memory"]["memory_mcp"] == "1"
    assert lanes["core"]["memory_disabled"] == "1"


def test_gaia_categories_share_one_fixture_lane():
    lanes = {lane["lane"]: lane for lane in _subset(["gaia_core", "gaia_shell"])}

    assert list(lanes) == ["gaia"]
    assert lanes["gaia"]["gaia_fixtures"] == "1"


@pytest.mark.parametrize("category", sorted(lane_matrix.SCREEN_READING))
def test_a_screen_reading_category_is_refused_with_its_reason(category):
    with pytest.raises(lane_matrix.LaneMapError, match="public artifact"):
        _subset(["tool_selection", category])


def test_an_unknown_category_is_refused():
    with pytest.raises(lane_matrix.LaneMapError, match="not in any lane"):
        _subset(["tool_selecton"])


@pytest.mark.parametrize("categories", [[], [""]])
def test_an_empty_request_is_refused(categories):
    with pytest.raises(lane_matrix.LaneMapError, match="No categories"):
        _subset(categories)


def test_a_repeated_category_runs_once():
    (lane,) = _subset(["error_recovery", "error_recovery"])
    assert lane["categories"] == "error_recovery"


def test_the_cli_prints_a_matrix_for_a_subset(capsys):
    assert lane_matrix.main(["--categories", "error_recovery"]) == 0
    out = capsys.readouterr().out
    matrix = json.loads(out.split("matrix=", 1)[1])
    assert [lane["lane"] for lane in matrix["include"]] == ["core"]


def test_the_cli_fails_on_a_bad_subset(capsys):
    assert lane_matrix.main(["--categories", "vision"]) == 1
    assert "::error::" in capsys.readouterr().err
