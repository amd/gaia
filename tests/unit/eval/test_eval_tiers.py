# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The flagship eval tiers: which runs when, on which model, and that none drops a scenario.

A tier that silently stops running a scenario looks exactly like a pass, so the
coverage check works from the matrices the workflow would actually build
(util/eval_tiers.py), not from the tier lists.
"""

import copy
import json
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "util"))

import eval_backlog_trend as trend  # noqa: E402
import eval_lane_matrix as lanes  # noqa: E402
import eval_tiers as tiers  # noqa: E402

SCENARIOS = REPO_ROOT / "eval" / "scenarios"
QWEN = "Qwen3.6-35B-A3B-GGUF"
GEMMA = "Gemma-4-E4B-it-GGUF"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "eval_flagship.yml"


@pytest.fixture(scope="module")
def ctx():
    doc = json.loads((REPO_ROOT / "eval" / "ci_tiers.json").read_text("utf-8"))
    lanes_doc = json.loads((REPO_ROOT / "eval" / "ci_lanes.json").read_text("utf-8"))
    return doc, lanes_doc, lanes.scenario_index(SCENARIOS)


def _check(doc, lanes_doc, index):
    return tiers.check(doc, lanes_doc, index, SCENARIOS)


# --- coverage: nothing silently drops out ------------------------------------


def test_smoke_full_and_backlog_together_cover_every_runnable_scenario(ctx):
    doc, lanes_doc, index = ctx
    cov = _check(doc, lanes_doc, index)
    assert cov.smoke | cov.full | cov.backlog == set(cov.runnable)
    # Every scenario in the suite is either covered or named with its reason.
    assert set(cov.runnable) | set(cov.not_in_ci) == set(index)
    assert "every one covered" in cov.report()


def test_only_the_backlog_scenarios_leave_the_full_sweep(ctx):
    doc, lanes_doc, index = ctx
    cov = _check(doc, lanes_doc, index)
    assert set(cov.runnable) - cov.full == cov.leaves_full
    assert cov.leaves_full <= cov.backlog


def test_no_protected_scenario_leaves_the_full_sweep(ctx):
    doc, lanes_doc, index = ctx
    cov = _check(doc, lanes_doc, index)
    protected = {s for s in cov.runnable if tiers.is_protected(s, index[s])}
    assert protected <= cov.full
    assert len(protected) >= 70  # #5493 counts 75; a big drop means the rule broke


def test_smoke_tier_is_30_scenarios_13_protected_on_3_machines(ctx):
    doc, _, index = ctx
    ids = [e["id"] for _, e in tiers.tier_entries(doc, "smoke")]
    assert len(ids) == 30
    assert sum(tiers.is_protected(s, index[s]) for s in ids) == 13
    assert len(doc["tiers"]["smoke"]["machines"]) <= doc["max_machines"] == 3
    assert len(doc["tiers"]["backlog"]["machines"]) <= 3


def test_the_protected_set_covers_the_must_not_regress_areas(ctx):
    doc, _, index = ctx
    smoke = {index[e["id"]]["category"] for _, e in tiers.tier_entries(doc, "smoke")}
    # error_recovery has no stable scenario (#5493 6.6); it stays in the full sweep.
    for area in ("adversarial", "gaia_honesty", "gaia_resilience"):
        assert area in smoke
    ids = {e["id"] for _, e in tiers.tier_entries(doc, "smoke")}
    assert any("injection" in s for s in ids)
    assert any("security" in index[s]["tags"] for s in ids)


def _broken(doc, edit):
    doc = copy.deepcopy(doc)
    edit(doc)
    return doc


def _first(doc, tier, sid=None):
    for machine in doc["tiers"][tier]["machines"]:
        for entry in machine["scenarios"]:
            if sid is None or entry["id"] == sid:
                return machine, entry
    raise AssertionError(sid)


@pytest.mark.parametrize(
    "edit, message",
    [
        (
            lambda d: _first(d, "backlog", "task_feed_injection")[1].update(
                leaves_full=True
            ),
            "is protected and may never leave the full sweep",
        ),
        (
            lambda d: d["tiers"]["smoke"]["machines"].append(
                {
                    "machine": "smoke-4",
                    "scenarios": [{"id": "no_tools_needed", "why": "x"}],
                }
            ),
            "needs 4 machines; the limit is 3",
        ),
        (
            lambda d: _first(d, "smoke", "honesty_empty_result")[0]["scenarios"].append(
                {"id": "no_tools_needed", "why": "x"}
            ),
            "mixes gaia_* and other categories",
        ),
        (
            lambda d: _first(d, "smoke")[1].update(id="not_a_scenario"),
            "'not_a_scenario' is not a scenario id",
        ),
        (
            lambda d: _first(d, "smoke")[1].update(id="screenshot_capture"),
            "never runs in CI",
        ),
        (
            lambda d: _first(d, "smoke")[1].update(why=""),
            "has no `why`",
        ),
        (
            lambda d: _first(d, "smoke")[1].update(leaves_full=True),
            "sets leaves_full outside backlog",
        ),
        (
            lambda d: _first(d, "smoke")[0].update(machine="tools"),
            "is missing or reused",
        ),
        (
            lambda d: d["baselines"].update({QWEN: d["baselines"][GEMMA]}),
            "A run would be compared with another model",
        ),
        (lambda d: d.update(halo_model="Nope"), "has no entry in baselines"),
    ],
)
def test_check_refuses_a_broken_tier_file(ctx, edit, message):
    doc, lanes_doc, index = ctx
    with pytest.raises(tiers.TierError, match="ci_tiers.json is not usable") as err:
        _check(_broken(doc, edit), lanes_doc, index)
    assert message in str(err.value)


def test_a_matrix_that_drops_a_scenario_is_a_gap(ctx, monkeypatch):
    doc, lanes_doc, index = ctx
    real = tiers.full_matrix

    def lossy(*args):
        include = real(*args)
        return [e for e in include if e["lane"] != "conversation"]

    monkeypatch.setattr(tiers, "full_matrix", lossy)
    with pytest.raises(tiers.TierError, match="would stop being measured"):
        _check(doc, lanes_doc, index)


# --- the matrices ------------------------------------------------------------


def test_smoke_machines_carry_their_scenarios_and_environment(ctx):
    doc, _, index = ctx
    include = tiers.tier_matrix(doc, index, "smoke")
    assert [e["lane"] for e in include] == ["smoke-1", "smoke-2", "smoke-3"]
    by_lane = {e["lane"]: e for e in include}
    mixed = by_lane["smoke-3"]
    assert "memory" in mixed["categories"].split()
    # memory shares a machine: the job runs with the store off, and only the
    # memory category turns it on.
    assert mixed["memory_categories"] == "memory"
    assert mixed["memory_disabled"] == "1" and mixed["gaia_fixtures"] == "0"
    for lane in ("smoke-1", "smoke-2"):
        assert by_lane[lane]["gaia_fixtures"] == "1"
        assert by_lane[lane]["memory_categories"] == ""
    for entry in include:
        picked = dict(p.split("=", 1) for p in entry["scenarios"].split())
        assert sorted(picked) == sorted(entry["categories"].split())


def test_full_matrix_is_every_lane_without_the_backlog_scenarios(ctx):
    doc, lanes_doc, index = ctx
    full_lanes = lanes.build_matrix(lanes_doc, lanes.categories_on_disk(SCENARIOS), "x")
    include = tiers.full_matrix(doc, lanes_doc, index, full_lanes)
    assert [e["lane"] for e in include] == [lane["lane"] for lane in lanes_doc["lanes"]]
    gone = tiers.leaves_full(doc)
    for entry in include:
        assert not set(entry["scenarios"].replace("=", ",").split(",")) & gone
        # Untouched categories run whole: no --scenario list for them.
        for pair in entry["scenarios"].split():
            category, ids = pair.split("=", 1)
            assert any(index[s]["category"] == category for s in gone)


def test_memory_cannot_share_a_machine_with_gaia():
    with pytest.raises(tiers.TierError, match="memory cannot share"):
        tiers.machine_env(["memory", "gaia_core"])


# --- the model under test -----------------------------------------------------


def test_the_halo_default_is_qwen_with_its_own_baseline(ctx):
    doc, _, _ = ctx
    assert tiers.resolve_model(doc, "") == (
        QWEN,
        "tests/fixtures/eval_baselines/gaia-flagship-qwen",
    )
    assert tiers.resolve_model(doc, GEMMA) == (
        GEMMA,
        "tests/fixtures/eval_baselines/gaia-flagship",
    )
    with pytest.raises(tiers.TierError, match="has no baseline directory"):
        tiers.resolve_model(doc, "Llama-9B")


def test_the_qwen_baseline_is_an_empty_contract_until_a_real_run_fills_it():
    base = REPO_ROOT / "tests/fixtures/eval_baselines/gaia-flagship-qwen"
    meta = json.loads((base / "meta.json").read_text("utf-8"))
    gemma = json.loads(
        (REPO_ROOT / "tests/fixtures/eval_baselines/gaia-flagship/meta.json").read_text(
            "utf-8"
        )
    )
    assert meta["model"] == QWEN
    assert meta["eval_model"] == gemma["eval_model"]
    # Never hand-authored: scorecards arrive only from a run, with its source.
    scorecards = list(base.glob("scorecard_*.json"))
    assert bool(scorecards) == bool(meta["source_run"])
    if not scorecards:
        assert meta["passed"] is None and meta["pass_rate"] is None


def test_a_targeted_gemma_rerun_keeps_the_gemma_baseline(ctx):
    """How v0.25.1-rc3 re-runs rc2's lost scenarios from a ref with this change."""
    doc, lanes_doc, index = ctx
    env = {
        "EVAL_TIER": "targeted",
        "EVAL_AGENT_MODEL": GEMMA,
        "EVAL_SELECT_SCENARIOS": "empty_file,honesty_empty_result",
    }
    out = tiers.build(env, doc, lanes_doc, index, SCENARIOS)
    assert out["agent_model"] == GEMMA
    assert out["baseline_dir"] == "tests/fixtures/eval_baselines/gaia-flagship"
    assert out["targeted"] and not out["outcomes"]
    assert {e["lane"] for e in out["include"]} == {"conversation", "gaia-signal"}


@pytest.mark.parametrize(
    "tier, lanes_, outcomes, partial",
    [
        ("smoke", 3, False, True),
        ("backlog", 3, False, True),
        ("full", 12, True, False),
        ("verify", 0, False, False),
        ("none", 0, False, False),
    ],
)
def test_build_per_tier(ctx, tier, lanes_, outcomes, partial):
    doc, lanes_doc, index = ctx
    out = tiers.build({"EVAL_TIER": tier}, doc, lanes_doc, index, SCENARIOS)
    assert len(out["include"]) == lanes_
    assert out["outcomes"] is outcomes and out["targeted"] is partial
    assert out["agent_model"] == QWEN


def test_build_refuses_an_unknown_tier(ctx):
    doc, lanes_doc, index = ctx
    with pytest.raises(tiers.TierError, match="unknown tier"):
        tiers.build({"EVAL_TIER": "everything"}, doc, lanes_doc, index, SCENARIOS)


# --- which tier ---------------------------------------------------------------


@pytest.mark.parametrize(
    "event, tag, otype, message, dispatch, targeted, busy, tier",
    [
        ("push", "v0.25.2-rc1", "tag", "rc1", "", False, None, "smoke"),
        ("push", "v0.25.2-rc3", "tag", "rc3 [full eval]", "", False, None, "full"),
        (
            "push",
            "v0.25.2-rc3",
            "tag",
            "rc3 [FULL EVAL]\n\nnotes",
            "",
            False,
            None,
            "full",
        ),
        # A lightweight tag's contents are the commit message; never honour it.
        ("push", "v0.25.2-rc3", "commit", "fix [full eval]", "", False, None, "smoke"),
        ("push", "v0.25.2", "tag", "v0.25.2", "", False, None, "verify"),
        ("push", "v0.25.2", "tag", "v0.25.2 [full eval]", "", False, None, "verify"),
        ("schedule", "main", "", "", "", False, [], "backlog"),
        ("schedule", "main", "", "", "", False, ["run 1 (push, v0.25.2-rc1)"], "none"),
        ("workflow_dispatch", "main", "", "", "", False, None, "smoke"),
        ("workflow_dispatch", "main", "", "", "full", False, None, "full"),
        ("workflow_dispatch", "main", "", "", "backlog", False, None, "backlog"),
        ("workflow_dispatch", "main", "", "", "full", True, None, "targeted"),
    ],
)
def test_decide(event, tag, otype, message, dispatch, targeted, busy, tier):
    ref_type = "tag" if event == "push" else "branch"
    got = tiers.decide(event, ref_type, tag, otype, message, dispatch, targeted, busy)
    assert got.tier == tier


def test_decide_says_why_on_tags_and_skips():
    assert (
        "nominated"
        in tiers.decide("push", "tag", "v1.0.0-rc2", "tag", "[full eval]").notice
    )
    assert (
        "[full eval]" in tiers.decide("push", "tag", "v1.0.0-rc2", "tag", "rc2").notice
    )
    final = tiers.decide("push", "tag", "v1.0.0", "tag", "[full eval]").notice
    assert "not re-run" in final and "ignored" in final
    skipped = tiers.decide(
        "schedule", "branch", "main", busy=["run 9 (push, v1.0.0-rc1)"]
    )
    assert "run 9" in skipped.notice


def test_decide_refuses_what_it_cannot_place():
    with pytest.raises(tiers.TierError, match="tier input"):
        tiers.decide("workflow_dispatch", "branch", "main", dispatch_tier="huge")
    with pytest.raises(tiers.TierError, match="no tier for event"):
        tiers.decide("pull_request", "branch", "main")


def test_busy_runs_ignores_schedules_and_itself():
    pages = {
        "in_progress": [
            {"id": 1, "event": "schedule", "head_branch": "main"},
            {"id": 2, "event": "push", "head_branch": "v0.25.2-rc1"},
            {"id": 7, "event": "workflow_dispatch", "head_branch": "main"},
        ],
        "queued": [{"id": 3, "event": "workflow_dispatch", "head_branch": "main"}],
    }

    def api(path):
        status = path.split("status=")[1].split("&")[0]
        return {"workflow_runs": pages[status]}

    busy = tiers.busy_runs("amd/gaia", "7", api)
    assert [b.split()[1] for b in busy] == ["2", "3"]


# --- the final tag ------------------------------------------------------------

LS_REMOTE = "\n".join(
    [
        "aaa\trefs/tags/v0.25.2-rc1",
        "t2\trefs/tags/v0.25.2-rc2",
        "bbb\trefs/tags/v0.25.2-rc2^{}",
        "ccc\trefs/tags/v0.25.20-rc1",
        "ddd\trefs/tags/v0.25.2-rc10",
    ]
)


def test_rc_tag_shas_peels_annotated_tags_and_keeps_only_this_release():
    assert tiers.rc_tag_shas("v0.25.2", LS_REMOTE) == {
        "v0.25.2-rc1": "aaa",
        "v0.25.2-rc2": "bbb",
        "v0.25.2-rc10": "ddd",
    }


def _fake_api(runs_by_sha, markers):
    def api(path):
        if "head_sha=" in path:
            sha = path.split("head_sha=")[1].split("&")[0]
            return {"workflow_runs": runs_by_sha.get(sha, [])}
        run = int(path.split("/runs/")[1].split("/")[0])
        return {"artifacts": [{"name": tiers.FULL_MARKER}] if run in markers else []}

    return api


def _run(rid, status="completed"):
    return {
        "id": rid,
        "status": status,
        "event": "push",
        "conclusion": "success",
        "html_url": f"https://x/runs/{rid}",
    }


def test_verify_final_passes_on_a_full_sweep_of_the_same_commit():
    api = _fake_api({"fff": [_run(1), _run(2)]}, markers={2})
    got = tiers.verify_final(
        "amd/gaia", "v0.25.2", "fff", {"v0.25.2-rc1": "aaa"}, "99", api
    )
    assert got.ok
    assert "https://x/runs/2" in "\n".join(got.lines)
    assert "never evaluated" not in "\n".join(got.lines)


def test_verify_final_accepts_the_nominated_rc_commit_and_says_it_differs():
    api = _fake_api({"bbb": [_run(5)]}, markers={5})
    got = tiers.verify_final(
        "amd/gaia", "v0.25.2", "fff", {"v0.25.2-rc2": "bbb"}, "99", api
    )
    assert got.ok
    text = "\n".join(got.lines)
    assert "via v0.25.2-rc2" in text and "never evaluated" in text


def test_verify_final_fails_loudly_without_a_full_sweep():
    # A smoke run (no marker), an unfinished full sweep, and this run itself.
    api = _fake_api(
        {"fff": [_run(1), _run(2, status="in_progress"), _run(99)]}, markers={2, 99}
    )
    got = tiers.verify_final(
        "amd/gaia", "v0.25.2", "fff", {"v0.25.2-rc1": "aaa"}, "99", api
    )
    assert not got.ok
    text = "\n".join(got.lines)
    assert "**No.**" in text and "-f tier=full" in text


# --- the workflow wiring ------------------------------------------------------


@pytest.fixture(scope="module")
def workflow():
    return yaml.load(WORKFLOW.read_text("utf-8"), Loader=yaml.BaseLoader)


def test_the_dispatch_model_choices_are_exactly_the_baselines(ctx, workflow):
    doc, _, _ = ctx
    model = workflow["on"]["workflow_dispatch"]["inputs"]["model"]
    assert sorted(model["options"]) == sorted(doc["baselines"])
    assert model["default"] == doc["halo_model"]
    tier = workflow["on"]["workflow_dispatch"]["inputs"]["tier"]
    assert tier["options"] == list(tiers.DISPATCH_TIERS) and tier["default"] == "smoke"


def test_the_weekly_schedule_matches_the_tier_file(ctx, workflow):
    doc, _, _ = ctx
    cron = workflow["on"]["schedule"][0]["cron"]
    assert cron == "17 6 * * 0"
    assert any("Sunday 06:17 UTC" in line for line in doc["$comment"])


def test_every_lane_pins_and_checks_the_model_under_test(workflow):
    job = workflow["jobs"]["agent-eval"]
    assert job["env"]["AGENT_MODEL"] == "${{ needs.lanes.outputs.agent_model }}"
    assert job["env"]["BASELINE_DIR"] == "${{ needs.lanes.outputs.baseline_dir }}"
    assert job["env"]["FAIL_ON_ZERO_TOKENS"] == "true"
    run = next(
        s["run"]
        for s in job["steps"]
        if s.get("name") == "Run this lane's categories serially"
    )
    assert (
        "util/eval_gaia_home.py pin-model --home $catHome --model $env:AGENT_MODEL"
        in run
    )
    assert '--agent-model "$env:AGENT_MODEL"' in run
    assert run.index("pin-model") < run.index('Start-Isolated @("-m","gaia.ui.server"')
    compare = next(
        s["run"] for s in job["steps"] if s.get("name", "").startswith("Compare each")
    )
    assert "3 {" in compare  # the cross-model refusal is fatal


def test_the_lanes_job_builds_the_matrix_from_the_tiers(workflow):
    lanes_job = workflow["jobs"]["lanes"]
    step = next(s for s in lanes_job["steps"] if s.get("id") == "build")
    assert step["run"].startswith("python util/eval_tiers.py matrix")
    assert step["env"]["EVAL_TIER"] == "${{ needs.eval-gate.outputs.tier }}"
    gate = workflow["jobs"]["eval-gate"]
    assert any("util/eval_tiers.py decide" in s.get("run", "") for s in gate["steps"])


def test_only_a_final_tag_runs_verify_final_and_it_can_escalate(workflow):
    verify = workflow["jobs"]["verify-final"]
    assert "needs.eval-gate.outputs.tier == 'verify'" in verify["if"]
    assert verify["permissions"]["actions"] == "read"
    escalation = workflow["jobs"]["release-escalation"]
    assert "verify-final" in escalation["needs"]
    assert "needs.verify-final.result" in escalation["if"]
    # A tier that leaves a job out (skipped) must not file an issue.
    assert "!= 'success'" not in escalation["if"]


def test_the_backlog_trend_rolls_forward(workflow):
    job = workflow["jobs"]["backlog-trend"]
    assert "needs.lanes.outputs.tier == 'backlog'" in job["if"]
    upload = next(s for s in job["steps"] if s.get("name") == "Upload the trend")
    assert upload["with"]["name"] == "flagship-backlog-trend"
    assert upload["with"]["retention-days"] == "90"


# --- the trend artifact -------------------------------------------------------


def _artifacts(tmp_path, statuses):
    card = {
        "scenarios": [{"scenario_id": s, "status": st} for s, st in statuses.items()]
    }
    path = tmp_path / "art" / "eval-backlog-1" / "gaia_git" / "scorecard.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(card), "utf-8")
    return tmp_path / "art"


def _trend_args(tmp_path, art, previous=None):
    args = [
        "--artifacts-dir",
        str(art),
        "--tiers",
        str(REPO_ROOT / "eval" / "ci_tiers.json"),
        "--out",
        str(tmp_path / "out" / "trend.json"),
        "--run-url",
        "https://x/runs/1",
        "--sha",
        "abcdef1234",
        "--date",
        "2026-10-25",
        "--model",
        QWEN,
    ]
    if previous:
        args += ["--previous", str(previous)]
    return args


def test_trend_appends_this_week_and_flags_a_backlog_pass(tmp_path, capsys):
    art = _artifacts(
        tmp_path, {"git_t3_merge_conflict": "PASS", "git_t2_branch_compare": "FAIL"}
    )
    previous = tmp_path / "prev.json"
    previous.write_text(
        json.dumps(
            [
                {
                    "date": "2026-10-18",
                    "sha": "0" * 9,
                    "run": "r",
                    "model": QWEN,
                    "passed": 0,
                    "total": 41,
                    "statuses": {},
                }
            ]
        ),
        "utf-8",
    )
    assert trend.main(_trend_args(tmp_path, art, previous)) == 0
    saved = json.loads((tmp_path / "out" / "trend.json").read_text("utf-8"))
    assert [w["date"] for w in saved] == ["2026-10-18", "2026-10-25"]
    week = saved[-1]
    assert week["passed"] == 1 and week["total"] == 41
    assert week["statuses"]["git_t2_branch_compare"] == "FAIL"
    assert week["statuses"]["skills_hub_search"] == "NOT_RUN"
    out = capsys.readouterr().out
    assert "**1 of 41**" in out
    assert "`git_t3_merge_conflict`" in out.split("left the full sweep")[1]


def test_trend_with_no_results_fails_and_keeps_the_history(tmp_path):
    art = tmp_path / "empty"
    art.mkdir()
    previous = tmp_path / "prev.json"
    previous.write_text("[]", "utf-8")
    assert trend.main(_trend_args(tmp_path, art, previous)) == 1
    assert json.loads((tmp_path / "out" / "trend.json").read_text("utf-8")) == []
