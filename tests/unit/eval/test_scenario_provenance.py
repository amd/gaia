# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A scorecard records which scenario files it ran, so a local override shows (#3515)."""

import json
import logging
import re
from types import SimpleNamespace

import pytest
import yaml

from gaia.eval import runner
from gaia.eval.runner import AgentEvalRunner, compare_scorecards


def _scenario(sid, objective="X"):
    return {
        "id": sid,
        "category": "cat_a",
        "persona": "casual_user",
        "setup": {"index_documents": []},
        "turns": [{"turn": 1, "objective": objective, "ground_truth": {"answer": "A"}}],
    }


def _write(directory, sid, objective="X"):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{sid}.yaml"
    path.write_text(yaml.dump(_scenario(sid, objective)), encoding="utf-8")
    return path


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    builtin = repo / "eval" / "scenarios"
    _write(builtin, "alpha")
    _write(builtin, "bravo")
    user = tmp_path / "home" / ".gaia" / "eval" / "scenarios"
    monkeypatch.setattr(runner, "REPO_ROOT", repo)
    monkeypatch.setattr(runner, "SCENARIOS_DIR", builtin)
    monkeypatch.setattr(runner, "USER_SCENARIOS_DIR", user)
    return SimpleNamespace(repo=repo, builtin=builtin, user=user, tmp=tmp_path)


# --- provenance --------------------------------------------------------------


def test_builtin_only_run_is_marked_builtin_only(dirs):
    prov = runner.scenario_provenance(runner.find_scenarios())
    assert prov["builtin_only"] is True
    assert prov["overridden_builtins"] == []
    alpha = next(e for e in prov["scenarios"] if e["id"] == "alpha")
    assert alpha["source"] == "builtin"
    assert alpha["path"] == "eval/scenarios/alpha.yaml"
    assert alpha["sha256"] == runner._scenario_digest(_scenario("alpha"))


def test_user_dir_override_is_recorded_and_logged_as_warning(dirs, caplog):
    shadow = _write(dirs.user, "alpha", objective="EASIER")
    with caplog.at_level(logging.WARNING, logger="gaia.eval.runner"):
        scenarios = runner.find_scenarios()
    assert any("overrides" in r.getMessage() for r in caplog.records)
    prov = runner.scenario_provenance(scenarios)
    assert prov["overridden_builtins"] == ["alpha"]
    assert prov["builtin_only"] is False
    alpha = next(e for e in prov["scenarios"] if e["id"] == "alpha")
    assert alpha["source"] == "local" and alpha["overrides_builtin"] is True
    assert alpha["path"] == str(shadow)
    assert alpha["sha256"] == runner._scenario_digest(_scenario("alpha", "EASIER"))
    builtin = runner._scenario_digest(_scenario("alpha"))
    assert alpha["sha256"] != builtin


def test_scenario_dir_override_is_recorded(dirs):
    extra = dirs.tmp / "extra"
    _write(extra, "bravo", objective="OTHER")
    prov = runner.scenario_provenance(runner.find_scenarios(extra_dirs=[extra]))
    assert prov["overridden_builtins"] == ["bravo"]


def test_a_new_local_scenario_is_local_but_not_an_override(dirs):
    _write(dirs.user, "charlie")
    prov = runner.scenario_provenance(runner.find_scenarios())
    charlie = next(e for e in prov["scenarios"] if e["id"] == "charlie")
    assert charlie["source"] == "local"
    assert "overrides_builtin" not in charlie
    assert prov["overridden_builtins"] == []
    assert prov["builtin_only"] is False


def test_identical_copy_still_counts_as_an_override(dirs):
    # Same bytes today, but the scorecard cannot know it stays that way.
    (dirs.user).mkdir(parents=True)
    (dirs.user / "alpha.yaml").write_bytes((dirs.builtin / "alpha.yaml").read_bytes())
    prov = runner.scenario_provenance(runner.find_scenarios())
    assert prov["overridden_builtins"] == ["alpha"]


def test_digest_ignores_formatting_but_not_content():
    a = {"id": "x", "turns": [{"turn": 1, "objective": "A"}]}
    reordered = {"turns": [{"objective": "A", "turn": 1}], "id": "x"}
    assert runner._scenario_digest(a) == runner._scenario_digest(reordered)
    changed = {"id": "x", "turns": [{"turn": 1, "objective": "B"}]}
    assert runner._scenario_digest(a) != runner._scenario_digest(changed)


def test_missing_file_on_disk_still_records(dirs, tmp_path):
    # Provenance reads the parsed data; a path that is gone later is not fatal.
    ghost = tmp_path / "gone" / "alpha.yaml"
    prov = runner.scenario_provenance([(ghost, _scenario("alpha"))])
    assert prov["overridden_builtins"] == ["alpha"]


def test_empty_selection(dirs):
    prov = runner.scenario_provenance([])
    assert prov == {"scenarios": [], "overridden_builtins": [], "builtin_only": True}


# --- the run records it ------------------------------------------------------


def _verdict(sid):
    return {
        "type": "result",
        "is_error": False,
        "num_turns": 3,
        "total_cost_usd": 0.1,
        "usage": {"input_tokens": 10, "output_tokens": 10},
        "structured_output": {
            "scenario_id": sid,
            "session_id": f"sess-{sid}",
            "status": "PASS",
            "overall_score": 9,
            "turns": [{"turn": 1, "scores": {k: 9 for k in runner._SCORE_WEIGHTS}}],
        },
    }


def _fake_driver(monkeypatch):
    monkeypatch.setattr(runner, "preflight_check", lambda *a, **k: [])
    monkeypatch.setattr(runner, "_missing_requirements", lambda _sd, _m: [])
    monkeypatch.setattr(runner, "_stamp_agent_provenance", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_current_commit", lambda: "abc")
    monkeypatch.setattr(
        runner,
        "run_scripted_scenario",
        lambda *a, **k: runner.run_scenario_subprocess(
            *a, **{**k, "scenario_backend": None}
        ),
    )
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_SHA", raising=False)

    def driver(cmd, **kwargs):
        sid = re.search(r"^id: (\S+)$", kwargs["input"], re.MULTILINE).group(1)
        return SimpleNamespace(
            returncode=0, stdout=json.dumps(_verdict(sid)), stderr=""
        )

    monkeypatch.setattr(runner, "_run_driver", driver)


def test_scorecard_config_carries_provenance(dirs, monkeypatch, capsys):
    _write(dirs.user, "alpha", objective="EASIER")
    _fake_driver(monkeypatch)
    card = AgentEvalRunner(results_dir=dirs.tmp / "results")._run_locked(
        category="cat_a"
    )
    prov = card["config"]["scenario_provenance"]
    assert prov["overridden_builtins"] == ["alpha"]
    assert {e["id"] for e in prov["scenarios"]} == {"alpha", "bravo"}
    err = capsys.readouterr().err
    assert "1 built-in scenario(s) replaced by local copies" in err
    assert "alpha" in err
    # And it reaches the file a reviewer or CI reads.
    run_dir = next((dirs.tmp / "results").iterdir())
    on_disk = json.loads((run_dir / "scorecard.json").read_text("utf-8"))
    assert on_disk["config"]["scenario_provenance"] == prov


def test_resume_rechecks_the_scenarios_it_reruns(dirs, monkeypatch, capsys):
    _fake_driver(monkeypatch)
    card = AgentEvalRunner(results_dir=dirs.tmp / "results")._run_locked(
        category="cat_a"
    )
    assert card["config"]["scenario_provenance"]["builtin_only"] is True
    run_dir = next((dirs.tmp / "results").iterdir())
    on_disk = json.loads((run_dir / "scorecard.json").read_text("utf-8"))
    for s in on_disk["scenarios"]:
        if s["scenario_id"] == "bravo":
            s["status"] = runner.NOT_RUN
    (run_dir / "scorecard.json").write_text(json.dumps(on_disk), encoding="utf-8")
    # An override dropped in between the two sessions runs on resume.
    _write(dirs.user, "bravo", objective="EASIER")
    capsys.readouterr()

    resumed = AgentEvalRunner(results_dir=dirs.tmp / "results")._resume_locked(run_dir)
    prov = resumed["config"]["scenario_provenance"]
    assert prov["builtin_only"] is False
    assert prov["overridden_builtins"] == ["bravo"]
    bravo = next(e for e in prov["scenarios"] if e["id"] == "bravo")
    assert bravo["sha256"] == runner._scenario_digest(_scenario("bravo", "EASIER"))
    # The kept scenario keeps its first-session record.
    assert [e["id"] for e in prov["scenarios"]] == ["alpha", "bravo"]
    assert "changed since this run started" in capsys.readouterr().err


def test_merge_resumed_provenance_keeps_unrelated_entries():
    recorded = {
        "scenarios": [
            {"id": "a", "source": "builtin", "sha256": "1"},
            {"id": "b", "source": "builtin", "sha256": "2"},
        ],
        "overridden_builtins": [],
        "builtin_only": True,
    }
    fresh = {
        "scenarios": [{"id": "b", "source": "builtin", "sha256": "2"}],
        "overridden_builtins": [],
        "builtin_only": True,
    }
    assert runner.merge_resumed_provenance(recorded, fresh) == recorded


# --- compare flags it ---------------------------------------------------------


def _card(path, provenance=None):
    config = {"model": "m", "agent_model": "a"}
    if provenance is not None:
        config["scenario_provenance"] = provenance
    path.write_text(
        json.dumps(
            {
                "config": config,
                "summary": {"pass_rate": 1.0},
                "scenarios": [
                    {"scenario_id": "alpha", "status": "PASS", "overall_score": 9.0}
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _prov(digest="aaa", overridden=False):
    entry = {"id": "alpha", "source": "builtin", "path": "x", "sha256": digest}
    if overridden:
        entry.update(source="local", overrides_builtin=True)
    return {
        "scenarios": [entry],
        "overridden_builtins": ["alpha"] if overridden else [],
        "builtin_only": not overridden,
    }


def test_compare_flags_an_overridden_current_run(tmp_path, capsys):
    base = _card(tmp_path / "b.json", _prov())
    curr = _card(tmp_path / "c.json", _prov(digest="bbb", overridden=True))
    result = compare_scorecards(base, curr)
    out = capsys.readouterr().out
    assert any("current run replaced 1" in w for w in result["scenario_set_warnings"])
    assert any("differ between the runs" in w for w in result["scenario_set_warnings"])
    assert "[WARN] Different questions" in out


def test_compare_flags_an_overridden_baseline(tmp_path):
    base = _card(tmp_path / "b.json", _prov(overridden=True))
    curr = _card(tmp_path / "c.json", _prov())
    result = compare_scorecards(base, curr)
    assert any("baseline run replaced" in w for w in result["scenario_set_warnings"])


def test_compare_flags_an_edited_builtin(tmp_path):
    base = _card(tmp_path / "b.json", _prov(digest="aaa"))
    curr = _card(tmp_path / "c.json", _prov(digest="bbb"))
    result = compare_scorecards(base, curr)
    assert result["scenario_set_warnings"] == [
        "1 scenario file(s) differ between the runs: alpha"
    ]


@pytest.mark.parametrize(
    "base_prov,curr_prov",
    [(None, None), (None, "same"), ("same", None), ("same", "same")],
    ids=["neither", "old-baseline", "old-current", "identical"],
)
def test_compare_is_quiet_without_a_difference(tmp_path, capsys, base_prov, curr_prov):
    base = _card(tmp_path / "b.json", _prov() if base_prov else None)
    curr = _card(tmp_path / "c.json", _prov() if curr_prov else None)
    result = compare_scorecards(base, curr)
    assert result["scenario_set_warnings"] == []
    assert "Different questions" not in capsys.readouterr().out
