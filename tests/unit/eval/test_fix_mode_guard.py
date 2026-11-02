# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``--fix`` must not edit a dirty tree, and must stop on a failed fixer (#3514)."""

import json
import re
import shutil
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from gaia.eval import runner
from gaia.eval.runner import AgentEvalRunner

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "agent.py").write_text("PROMPT = 'v1'\n", encoding="utf-8")
    _git(root, "add", "agent.py")
    _git(
        root,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "commit",
        "-q",
        "-m",
        "init",
    )
    monkeypatch.setattr(runner, "REPO_ROOT", root)
    return root


# --- the clean-tree guard ----------------------------------------------------


@requires_git
def test_clean_tree_is_accepted(repo):
    runner.require_clean_tree_for_fix()


@requires_git
def test_untracked_files_do_not_block(repo):
    (repo / "notes.txt").write_text("scratch", encoding="utf-8")
    runner.require_clean_tree_for_fix()


@requires_git
@pytest.mark.parametrize("stage", [False, True], ids=["unstaged", "staged"])
def test_uncommitted_tracked_change_is_refused(repo, stage):
    (repo / "agent.py").write_text("PROMPT = 'mine'\n", encoding="utf-8")
    if stage:
        _git(repo, "add", "agent.py")
    with pytest.raises(RuntimeError) as exc:
        runner.require_clean_tree_for_fix()
    msg = str(exc.value)
    assert "agent.py" in msg and "1 uncommitted" in msg
    assert "git worktree add" in msg


@requires_git
def test_deleted_tracked_file_is_refused(repo):
    (repo / "agent.py").unlink()
    with pytest.raises(RuntimeError, match="uncommitted"):
        runner.require_clean_tree_for_fix()


@requires_git
def test_long_dirty_list_is_truncated(repo):
    for i in range(15):
        (repo / f"f{i}.py").write_text("x", encoding="utf-8")
    _git(repo, "add", ".")
    with pytest.raises(RuntimeError) as exc:
        runner.require_clean_tree_for_fix()
    assert "15 uncommitted" in str(exc.value)
    assert "and 5 more" in str(exc.value)


@requires_git
def test_not_a_checkout_is_refused(tmp_path, monkeypatch):
    plain = tmp_path / "site-packages"
    plain.mkdir()
    monkeypatch.setattr(runner, "REPO_ROOT", plain)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    with pytest.raises(RuntimeError, match="not one"):
        runner.require_clean_tree_for_fix()


def test_missing_git_is_refused(monkeypatch):
    def no_git(*_a, **_k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(runner.subprocess, "run", no_git)
    with pytest.raises(RuntimeError, match="needs git on PATH"):
        runner.require_clean_tree_for_fix()


def test_run_checks_the_tree_before_taking_the_lock(monkeypatch):
    def dirty():
        raise RuntimeError("dirty")

    locked = []
    monkeypatch.setattr(runner, "require_clean_tree_for_fix", dirty)
    monkeypatch.setattr(
        AgentEvalRunner, "_run_locked", lambda self, **k: locked.append(k)
    )
    with pytest.raises(RuntimeError, match="dirty"):
        AgentEvalRunner().run(category="x", fix_mode=True)
    assert locked == []


def test_plain_run_does_not_check_the_tree(monkeypatch):
    def boom():
        raise AssertionError("checked the tree without --fix")

    monkeypatch.setattr(runner, "require_clean_tree_for_fix", boom)
    monkeypatch.setattr(AgentEvalRunner, "_run_locked", lambda self, **k: {"ok": 1})
    assert AgentEvalRunner().run(category="x") == {"ok": 1}


# --- the fix loop ------------------------------------------------------------


def _verdict(sid, status):
    score = 9 if status == "PASS" else 2
    return {
        "type": "result",
        "is_error": False,
        "num_turns": 3,
        "total_cost_usd": 0.1,
        "usage": {"input_tokens": 10, "output_tokens": 10},
        "structured_output": {
            "scenario_id": sid,
            "session_id": f"sess-{sid}",
            "status": status,
            "overall_score": score,
            "turns": [{"turn": 1, "scores": {k: score for k in runner._SCORE_WEIGHTS}}],
        },
    }


@pytest.fixture
def loop(tmp_path, monkeypatch):
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    for sid in ("good", "bad"):
        data = {
            "id": sid,
            "category": "cat_a",
            "persona": "casual_user",
            "setup": {"index_documents": []},
            "turns": [{"turn": 1, "objective": "X", "ground_truth": {"answer": "A"}}],
        }
        (scenarios / f"{sid}.yaml").write_text(yaml.dump(data), encoding="utf-8")
    monkeypatch.setattr(runner, "SCENARIOS_DIR", scenarios)
    monkeypatch.setattr(runner, "USER_SCENARIOS_DIR", tmp_path / "absent")
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

    state = SimpleNamespace(calls=[], fixer=[], tmp=tmp_path)

    def driver(cmd, **kwargs):
        sid = re.search(r"^id: (\S+)$", kwargs["input"], re.MULTILINE).group(1)
        state.calls.append(sid)
        status = "PASS" if sid == "good" else "FAIL"
        return SimpleNamespace(
            returncode=0, stdout=json.dumps(_verdict(sid, status)), stderr=""
        )

    monkeypatch.setattr(runner, "_run_driver", driver)

    def use_fixer(result):
        def fixer(_scorecard, _run_dir, iteration):
            state.fixer.append(iteration)
            return {"iteration": iteration, **result}

        monkeypatch.setattr(runner, "run_fix_iteration", fixer)

    state.use_fixer = use_fixer
    return state


def _fix_run(state, iterations=3):
    return AgentEvalRunner(results_dir=state.tmp / "results")._run_locked(
        category="cat_a", fix_mode=True, max_fix_iterations=iterations
    )


@pytest.mark.parametrize(
    "result,reason",
    [
        ({"error": "Fixer timed out after 600s", "fixes": []}, "timed out"),
        ({"error": "boom", "fixes": []}, "boom"),
        ({"fixer_exit_code": 1, "fixes": []}, "exited 1"),
    ],
    ids=["timeout", "exception", "nonzero-exit"],
)
def test_failed_fixer_stops_the_loop(loop, result, reason):
    loop.use_fixer(result)
    with pytest.raises(RuntimeError) as exc:
        _fix_run(loop)
    assert reason in str(exc.value) and "git diff" in str(exc.value)
    # One fixer call, and the half-edited tree was never re-scored.
    assert loop.fixer == [1]
    assert sorted(loop.calls) == ["bad", "good"]
    run_dir = next((loop.tmp / "results").iterdir())
    history = json.loads((run_dir / "fix_history.json").read_text("utf-8"))
    assert [h["iteration"] for h in history] == [1]


def test_completed_fixer_iterations_continue(loop):
    loop.use_fixer({"fixer_exit_code": 0, "fixes": []})
    _fix_run(loop, iterations=2)
    assert loop.fixer == [1, 2]
    # Initial run of both, then only the failing scenario per iteration.
    assert sorted(loop.calls) == ["bad", "bad", "bad", "good"]


def test_fixer_timeout_is_reported_as_an_error(monkeypatch, tmp_path):
    seen = {}

    def slow(cmd, **kwargs):
        seen["cmd"], seen["kwargs"] = cmd, kwargs
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(runner.subprocess, "run", slow)
    out = runner.run_fix_iteration({"scenarios": []}, tmp_path, 1)
    assert "timed out" in out["error"]
    assert runner._fix_iteration_failure(out)
    assert seen["cmd"][1] == "-p" and "--dangerously-skip-permissions" in seen["cmd"]
    assert seen["kwargs"]["cwd"] == str(runner.REPO_ROOT)


@pytest.mark.parametrize(
    "result,failed",
    [
        ({"fixer_exit_code": 0}, False),
        ({}, False),
        ({"fixer_exit_code": 2}, True),
        ({"fixer_exit_code": -9}, True),
        ({"fixer_exit_code": 0, "error": "x"}, True),
        ({"error": ""}, False),
    ],
)
def test_fix_iteration_failure_classification(result, failed):
    assert bool(runner._fix_iteration_failure(result)) is failed
