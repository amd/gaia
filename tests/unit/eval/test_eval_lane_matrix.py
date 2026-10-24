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


def _outputs(capsys) -> dict:
    lines = capsys.readouterr().out.strip().splitlines()
    return dict(line.split("=", 1) for line in lines)


def test_main_prints_github_output_lines(capsys, monkeypatch):
    monkeypatch.delenv("EVAL_SELECT_CATEGORIES", raising=False)
    monkeypatch.delenv("EVAL_SELECT_SCENARIOS", raising=False)
    assert lane_matrix.main() == 0
    out = _outputs(capsys)
    assert json.loads(out["matrix"])["include"]
    assert out["has_lanes"] == "true"
    assert out["targeted"] == "false"
    assert out["outcomes"] == "true"


# --- dispatch selection ------------------------------------------------------


@pytest.fixture(scope="module")
def index() -> dict:
    return lane_matrix.scenario_index(REPO_ROOT / "eval" / "scenarios")


@pytest.fixture(scope="module")
def excluded() -> dict:
    return json.loads((REPO_ROOT / "eval" / "ci_lanes.json").read_text("utf-8"))[
        "excluded"
    ]


def _select(real_matrix, index, excluded, categories="", scenarios=""):
    return lane_matrix.select(
        real_matrix,
        index,
        lane_matrix.parse_list(categories),
        lane_matrix.parse_list(scenarios),
        excluded,
    )


def test_index_reads_every_scenario_as_yaml_does(index):
    yaml = pytest.importorskip("yaml")
    files = sorted((REPO_ROOT / "eval" / "scenarios").rglob("*.yaml"))
    assert len(index) == len(files)
    for path in files:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert index[data["id"]] == {
            "category": data["category"],
            "tags": list(data.get("tags") or []),
        }, path


def test_no_selection_leaves_the_tag_run_matrix_untouched(real_matrix, index, excluded):
    chosen = _select(real_matrix, index, excluded)
    assert chosen["include"] is real_matrix
    assert chosen["targeted"] is False
    assert chosen["outcomes"] is True
    assert all("scenarios" not in lane for lane in real_matrix)


def test_scenarios_alone_pick_their_lanes_and_categories(real_matrix, index, excluded):
    chosen = _select(
        real_matrix,
        index,
        excluded,
        scenarios="empty_file, no_sycophancy,large_document",
    )
    assert chosen["targeted"] is True
    assert chosen["outcomes"] is False
    assert chosen["include"] == [
        {
            "lane": "conversation",
            "categories": "personality adversarial",
            "memory_disabled": "1",
            "memory_admin": "0",
            "memory_mcp": "0",
            "gaia_fixtures": "0",
            "scenarios": "personality=no_sycophancy adversarial=empty_file,large_document",
        }
    ]
    assert chosen["description"] == (
        "TARGETED run: 3 scenario(s) in 2 category(ies); lanes conversation; "
        "outcomes job skipped"
    )


def test_categories_alone_run_every_scenario_in_them(real_matrix, index, excluded):
    chosen = _select(real_matrix, index, excluded, categories="memory,gaia_honesty")
    assert [(lane["lane"], lane["categories"]) for lane in chosen["include"]] == [
        ("memory", "memory"),
        ("gaia-signal", "gaia_honesty"),
    ]
    assert all("scenarios" not in lane for lane in chosen["include"])
    # A narrowed lane keeps the environment its categories need.
    assert chosen["include"][1]["gaia_fixtures"] == "1"
    assert chosen["include"][0]["memory_mcp"] == "1"


def test_outcomes_only_runs_no_scenario_lane(real_matrix, index, excluded):
    chosen = _select(real_matrix, index, excluded, categories="outcomes")
    assert chosen["include"] == []
    assert chosen["outcomes"] is True


@pytest.mark.parametrize(
    "categories, scenarios, match",
    [
        ("memroy", "", r"'memroy' is not a category \| Valid categories: "),
        ("vision", "", "never runs in CI: Reads the runner's screen"),
        (
            "",
            "no_such_scenario",
            r"not scenario ids: no_such_scenario \| Valid scenario ids: ",
        ),
        ("memory", "empty_file", "which the categories input leaves out"),
        (
            "",
            "web_live_search_canary",
            r"carries \['live'\], which the gaia_\* lanes exclude",
        ),
    ],
)
def test_a_bad_selection_is_refused_with_the_valid_ids(
    real_matrix, index, excluded, categories, scenarios, match
):
    with pytest.raises(lane_matrix.LaneMapError, match=match):
        _select(real_matrix, index, excluded, categories, scenarios)


def test_the_valid_id_list_covers_only_runnable_scenarios(real_matrix, index, excluded):
    with pytest.raises(lane_matrix.LaneMapError) as exc:
        _select(real_matrix, index, excluded, scenarios="nope")
    listed = str(exc.value).split("Valid scenario ids: ")[1].split(", ")
    assert "empty_file" in listed
    assert not [sid for sid in listed if index[sid]["category"] in excluded]


def test_main_refuses_a_bad_dispatch(capsys, monkeypatch):
    monkeypatch.setenv("EVAL_SELECT_SCENARIOS", "empty_file,typo_id")
    assert lane_matrix.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "::error::The dispatch selection cannot run as asked" in captured.err


def test_main_emits_a_targeted_matrix(capsys, monkeypatch):
    monkeypatch.setenv("EVAL_SELECT_CATEGORIES", "adversarial")
    monkeypatch.setenv("EVAL_SELECT_SCENARIOS", "empty_file")
    assert lane_matrix.main() == 0
    out = _outputs(capsys)
    include = json.loads(out["matrix"])["include"]
    assert [lane["scenarios"] for lane in include] == ["adversarial=empty_file"]
    assert out["targeted"] == "true"
    assert out["outcomes"] == "false"
    assert out["selection"].startswith("TARGETED run: 1 scenario(s)")


def test_exclude_tags_match_the_workflow_run_step():
    workflow = (REPO_ROOT / ".github" / "workflows" / "eval_flagship.yml").read_text(
        "utf-8"
    )
    expected = ", ".join(
        f"'--exclude-tag', '{tag}'" for tag in lane_matrix.GAIA_LANE_EXCLUDE_TAGS
    )
    assert f"$extraArgs = @({expected})" in workflow
