# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A Claude outage stops the run at once, and ``--resume`` finishes it later.

On v0.25.1-rc2 the Claude accounts ran dry part-way: 68 scenarios then errored
within seconds each, with zero tokens, and the run stayed green. These tests
drive the real runner loop and ``run_scenario_subprocess`` against a fake
``claude -p`` that runs out of quota midway, and pin that:

* nothing is launched after the outage, and the rest are ``NOT_RUN``, never
  PASS and never an ordinary error;
* the run exits non-zero naming the account, the reset time and the resume
  command;
* ``--resume`` runs only the scenarios without a result and merges them into
  the same scorecard, each stamped with its own commit and time;
* a genuine agent failure or a timeout never stops a run.
"""

import json
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

from gaia.eval import runner
from gaia.eval.claude_outage import (
    CREDENTIAL,
    QUOTA,
    UNREACHABLE,
    classify_driver_failure,
)
from gaia.eval.integrity_gate import main as gate_main
from gaia.eval.integrity_gate import zero_token_errors
from gaia.eval.runner import EXIT_CLAUDE_OUTAGE, AgentEvalRunner, compare_scorecards
from gaia.eval.scorecard import build_scorecard

IDS = ["alpha", "bravo", "charlie", "delta", "echo"]


def _usage(tokens):
    return {
        "input_tokens": tokens,
        "output_tokens": tokens,
        "cache_creation_input_tokens": tokens,
        "cache_read_input_tokens": tokens,
    }


def _ok(sid, status="PASS", score=9):
    turn_score = {"PASS": 9, "FAIL": 2}[status]
    return {
        "type": "result",
        "is_error": False,
        "duration_api_ms": 4000,
        "num_turns": 6,
        "total_cost_usd": 0.12,
        "usage": _usage(100),
        "structured_output": {
            "scenario_id": sid,
            "session_id": f"sess-{sid}",
            "status": status,
            "overall_score": score,
            "turns": [
                {
                    "turn": 1,
                    "scores": {k: turn_score for k in runner._SCORE_WEIGHTS},
                }
            ],
        },
    }


# What the subscription CLI prints once the weekly window is spent.
QUOTA_HIT = {
    "type": "result",
    "subtype": "success",
    "is_error": True,
    "duration_api_ms": 0,
    "num_turns": 1,
    "total_cost_usd": 0,
    "api_error_status": None,
    "result": "You've hit your weekly limit · resets 3pm (UTC)",
    "usage": _usage(0),
}


class FakeClaude:
    """A ``claude -p`` that answers ``budget`` scenarios, then is out of quota."""

    def __init__(self, budget, verdicts=None, timeout_ids=(), unanswered_ids=()):
        self.budget = budget
        self.verdicts = verdicts or {}
        self.timeout_ids = set(timeout_ids)
        self.unanswered_ids = set(unanswered_ids)
        self.calls = []

    def __call__(
        self, cmd, *, input, timeout, cwd
    ):  # pylint: disable=redefined-builtin
        sid = re.search(r"^id: (\S+)$", input, re.MULTILINE).group(1)
        self.calls.append(sid)
        if sid in self.timeout_ids:
            raise subprocess.TimeoutExpired(cmd, timeout)
        if sid in self.unanswered_ids:
            # Zero tokens, no quota or credential wording.
            refused = {
                **QUOTA_HIT,
                "result": "API Error: 400 bad request",
                "api_error_status": 400,
            }
            return SimpleNamespace(returncode=1, stdout=json.dumps(refused), stderr="")
        if len(self.calls) > self.budget:
            return SimpleNamespace(
                returncode=1, stdout=json.dumps(QUOTA_HIT), stderr=""
            )
        payload = _ok(sid, self.verdicts.get(sid, "PASS"))
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """Five scenarios, a stubbed backend, and a pluggable fake Claude."""
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    for sid in IDS:
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
    monkeypatch.setattr(
        runner,
        "run_scripted_scenario",
        lambda *a, **k: runner.run_scenario_subprocess(
            *a, **{**k, "scenario_backend": None}
        ),
    )
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_ACCOUNT", "secondary")
    monkeypatch.delenv("GITHUB_SHA", raising=False)

    state = SimpleNamespace(tmp=tmp_path, fake=None, commit="aaa111")
    monkeypatch.setattr(runner, "_current_commit", lambda: state.commit)

    def use(fake):
        state.fake = fake
        monkeypatch.setattr(runner, "_run_driver", fake)

    state.use = use
    return state


def _stopped_run(harness, capsys, budget=2):
    harness.use(FakeClaude(budget=budget))
    eval_runner = AgentEvalRunner(results_dir=harness.tmp / "results")
    with pytest.raises(SystemExit) as exc:
        eval_runner._run_locked(category="cat_a")
    run_dir = next((harness.tmp / "results").iterdir())
    card = json.loads((run_dir / "scorecard.json").read_text("utf-8"))
    return exc.value.code, run_dir, card, capsys.readouterr()


# --- stopping ----------------------------------------------------------------


def test_quota_midway_stops_launching_and_marks_the_rest_not_run(harness, capsys):
    code, _run_dir, card, _out = _stopped_run(harness, capsys)

    assert code == EXIT_CLAUDE_OUTAGE
    # Two answered, the third hit the wall, and nothing after it was launched.
    assert harness.fake.calls == ["alpha", "bravo", "charlie"]
    statuses = [(s["scenario_id"], s["status"]) for s in card["scenarios"]]
    assert statuses == [
        ("alpha", "PASS"),
        ("bravo", "PASS"),
        ("charlie", "INFRA_ERROR"),
        ("delta", "NOT_RUN"),
        ("echo", "NOT_RUN"),
    ]
    charlie, delta = card["scenarios"][2], card["scenarios"][3]
    assert charlie["claude_outage"]["kind"] == QUOTA
    assert delta["not_run_reason"] == QUOTA
    assert delta["overall_score"] is None

    summary = card["summary"]
    assert summary["passed"] == 2
    assert summary["not_run"] == 2
    assert summary["errored"] == 0
    assert summary["by_category"]["cat_a"]["not_run"] == 2
    # The cut-off scenario never counts toward the judged pass rate.
    assert summary["judged_pass_rate"] == 1.0


def test_the_stop_names_account_reset_and_how_to_resume(harness, capsys):
    _code, run_dir, card, out = _stopped_run(harness, capsys)

    stopped = card["stopped"]
    assert stopped["kind"] == QUOTA
    assert stopped["resets"] == "3pm (UTC)"
    assert stopped["after_scenario"] == "charlie"
    assert stopped["not_run"] == ["charlie", "delta", "echo"]
    assert "CLAUDE_CODE_OAUTH_TOKEN" in stopped["account"]
    assert "CLAUDE_ACCOUNT=secondary" in stopped["account"]

    err = out.err
    assert "Claude quota exhausted during charlie (scenario 3 of 5)" in err
    assert "Resets:  3pm (UTC)" in err
    assert f'gaia eval agent --resume "{run_dir}"' in err
    assert "-f scenarios=charlie,delta,echo" in err
    # The token itself never reaches a log.
    assert "test-token" not in err + out.out

    md = (run_dir / "summary.md").read_text("utf-8")
    # The banner leads, ahead of any pass rate.
    assert "> **Stopped: Claude quota exhausted**" in md.split("## Summary")[0]
    assert "- **Not run (Claude unavailable):** 2" in md


def test_an_outage_on_the_first_scenario_runs_nothing_else(harness, capsys):
    code, _run_dir, card, _out = _stopped_run(harness, capsys, budget=0)
    assert code == EXIT_CLAUDE_OUTAGE
    assert harness.fake.calls == ["alpha"]
    assert [s["status"] for s in card["scenarios"]].count("NOT_RUN") == 4


def test_a_genuine_failure_or_timeout_never_stops_the_run(harness, capsys):
    harness.use(
        FakeClaude(budget=99, verdicts={"bravo": "FAIL"}, timeout_ids={"delta"})
    )
    eval_runner = AgentEvalRunner(results_dir=harness.tmp / "results")
    card = eval_runner._run_locked(category="cat_a")
    assert harness.fake.calls == IDS
    statuses = [s["status"] for s in card["scenarios"]]
    assert statuses == ["PASS", "FAIL", "PASS", "TIMEOUT", "PASS"]
    assert "stopped" not in card
    assert card["summary"]["not_run"] == 0


def test_one_unexplained_zero_token_failure_does_not_stop_the_run(harness, capsys):
    harness.use(FakeClaude(budget=99, unanswered_ids={"bravo"}))
    card = AgentEvalRunner(results_dir=harness.tmp / "results")._run_locked(
        category="cat_a"
    )
    assert harness.fake.calls == IDS
    bravo = card["scenarios"][1]
    assert bravo["status"] == "INFRA_ERROR"
    assert bravo["claude_outage"]["kind"] == UNREACHABLE
    assert "stopped" not in card
    assert "stops if the next scenario fails the same way" in capsys.readouterr().out


def test_two_in_a_row_stop_the_run(harness, capsys):
    harness.use(FakeClaude(budget=99, unanswered_ids={"bravo", "charlie"}))
    with pytest.raises(SystemExit) as exc:
        AgentEvalRunner(results_dir=harness.tmp / "results")._run_locked(
            category="cat_a"
        )
    assert exc.value.code == EXIT_CLAUDE_OUTAGE
    assert harness.fake.calls == ["alpha", "bravo", "charlie"]
    run_dir = next((harness.tmp / "results").iterdir())
    card = json.loads((run_dir / "scorecard.json").read_text("utf-8"))
    # Both unexplained failures are resumed, along with what never started.
    assert card["stopped"]["not_run"] == ["bravo", "charlie", "delta", "echo"]
    assert "Claude never answered during charlie" in capsys.readouterr().err


def test_an_outage_inside_iterations_stops_and_keeps_all_usage(harness, capsys):
    harness.use(FakeClaude(budget=3))
    eval_runner = AgentEvalRunner(results_dir=harness.tmp / "results", iterations=2)
    with pytest.raises(SystemExit):
        eval_runner._run_locked(category="cat_a")
    run_dir = next((harness.tmp / "results").iterdir())
    card = json.loads((run_dir / "scorecard.json").read_text("utf-8"))
    # alpha: 2 attempts; bravo: attempt 1 answered, attempt 2 hit the wall.
    assert harness.fake.calls == ["alpha", "alpha", "bravo", "bravo"]
    bravo = card["scenarios"][1]
    assert bravo["status"] == "INFRA_ERROR"
    assert bravo["claude_usage"]["roles"]["driver"]["input_tokens"] == 100
    assert [s["status"] for s in card["scenarios"][2:]] == ["NOT_RUN"] * 3


@pytest.mark.parametrize(
    "forged",
    [
        {"claude_outage": {"kind": QUOTA}},
        {"status": "NOT_RUN", "not_run_reason": QUOTA},
    ],
)
def test_the_driver_cannot_fake_an_outage(harness, capsys, monkeypatch, forged):
    def driver(cmd, *, input, timeout, cwd):  # pylint: disable=redefined-builtin
        sid = re.search(r"^id: (\S+)$", input, re.MULTILINE).group(1)
        payload = _ok(sid)
        payload["structured_output"].update(forged)
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(runner, "_run_driver", driver)
    card = AgentEvalRunner(results_dir=harness.tmp / "results")._run_locked(
        category="cat_a"
    )
    assert "stopped" not in card
    assert card["summary"]["not_run"] == 0
    assert not any(s.get("claude_outage") for s in card["scenarios"])


# --- resuming ----------------------------------------------------------------


def test_resume_runs_only_what_is_missing_and_merges_one_scorecard(harness, capsys):
    _code, run_dir, first, _out = _stopped_run(harness, capsys)

    harness.commit = "bbb222"
    harness.use(FakeClaude(budget=99))
    card = AgentEvalRunner()._resume_locked(run_dir)

    assert harness.fake.calls == ["charlie", "delta", "echo"]
    assert card["run_id"] == first["run_id"]
    assert [s["status"] for s in card["scenarios"]] == ["PASS"] * 5
    assert "stopped" not in card
    by_id = {s["scenario_id"]: s for s in card["scenarios"]}
    assert by_id["alpha"]["commit"] == "aaa111"
    assert by_id["alpha"]["ran_at"] == first["scenarios"][0]["ran_at"]
    assert by_id["echo"]["commit"] == "bbb222"
    assert card["config"]["resumes"][0]["scenarios"] == ["charlie", "delta", "echo"]
    assert card["config"]["resumes"][0]["commit"] == "bbb222"
    # The scorecard on disk is the merged one, and the usage covers both sessions.
    on_disk = json.loads((run_dir / "scorecard.json").read_text("utf-8"))
    assert on_disk["summary"]["passed"] == 5
    assert on_disk["claude_usage"]["scenarios_with_usage"] == 5
    assert "mixes commits" in capsys.readouterr().out


def test_a_resume_that_hits_the_limit_again_stops_again(harness, capsys):
    _code, run_dir, _first, _out = _stopped_run(harness, capsys)
    harness.use(FakeClaude(budget=1))
    with pytest.raises(SystemExit) as exc:
        AgentEvalRunner()._resume_locked(run_dir)
    assert exc.value.code == EXIT_CLAUDE_OUTAGE
    assert harness.fake.calls == ["charlie", "delta"]
    card = json.loads((run_dir / "scorecard.json").read_text("utf-8"))
    assert [s["status"] for s in card["scenarios"]] == [
        "PASS",
        "PASS",
        "PASS",
        "INFRA_ERROR",
        "NOT_RUN",
    ]
    assert card["stopped"]["not_run"] == ["delta", "echo"]


def test_resume_keeps_the_runs_own_model(harness, capsys):
    _code, run_dir, _first, _out = _stopped_run(harness, capsys)
    harness.use(FakeClaude(budget=99))
    with pytest.raises(SystemExit) as exc:
        AgentEvalRunner()._resume_locked(run_dir, model="claude-other")
    assert exc.value.code == 1
    assert harness.fake.calls == []
    assert "A resumed run keeps its own --model" in capsys.readouterr().err


def test_resume_of_a_finished_run_runs_nothing(harness, capsys):
    harness.use(FakeClaude(budget=99))
    AgentEvalRunner(results_dir=harness.tmp / "results")._run_locked(category="cat_a")
    run_dir = next((harness.tmp / "results").iterdir())
    harness.fake.calls.clear()
    card = AgentEvalRunner()._resume_locked(run_dir)
    assert harness.fake.calls == []
    assert card["summary"]["passed"] == 5


@pytest.mark.parametrize(
    "card, message",
    [
        (None, "has no scorecard.json"),
        ({"config": {"model": "m"}, "scenarios": []}, "without --resume support"),
    ],
)
def test_resume_refuses_what_it_cannot_reconstruct(tmp_path, capsys, card, message):
    if card is not None:
        (tmp_path / "scorecard.json").write_text(json.dumps(card), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        AgentEvalRunner()._resume_locked(tmp_path)
    assert exc.value.code == 1
    assert message in capsys.readouterr().err


def test_cli_refuses_resume_with_a_new_selection(tmp_path):
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "gaia.cli",
            "eval",
            "agent",
            "--resume",
            str(tmp_path),
            "--category",
            "rag_quality",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 2
    assert "cannot be combined with --category" in proc.stderr


# --- classifying a failed driver ---------------------------------------------


def _failed(result, status=None, tokens=0, **extra):
    return {
        "is_error": True,
        "result": result,
        "api_error_status": status,
        "duration_api_ms": 1200 if tokens else 0,
        "usage": _usage(tokens),
        **extra,
    }


@pytest.mark.parametrize(
    "payload, kind, resets",
    [
        (QUOTA_HIT, QUOTA, "3pm (UTC)"),
        (_failed("Session limit reached ∙ resets 11am"), QUOTA, "11am"),
        (
            _failed("Claude AI usage limit reached|1761062400"),
            QUOTA,
            "2025-10-21 16:00 UTC",
        ),
        (_failed("API Error: 429 rate_limit_error", 429, tokens=5000), QUOTA, None),
        (_failed("Credit balance is too low", 400), QUOTA, None),
        (_failed("Invalid API key · Please run /login", 401), CREDENTIAL, None),
        (_failed("OAuth token has expired"), CREDENTIAL, None),
        (_failed("API Error: 400 model not found", 400), UNREACHABLE, None),
    ],
)
def test_outage_signatures(payload, kind, resets):
    outage = classify_driver_failure(payload, 1)
    assert outage is not None and outage.kind == kind
    assert outage.resets == resets


@pytest.mark.parametrize(
    "payload, returncode",
    [
        # A judged run, even one whose transcript talks about rate limits.
        (_ok("x") | {"result": "waited until a rate limit resets"}, 0),
        # The scenario's own budget cap is BUDGET_EXCEEDED, not the account's.
        (_failed("budget exceeded", subtype="error_max_budget_usd", tokens=900), 1),
        # Claude answered for a while, then the run broke for its own reasons.
        (_failed("Tool call failed: CONNECTION_CLOSED", tokens=40000), 1),
    ],
)
def test_what_is_not_an_outage(payload, returncode):
    assert classify_driver_failure(payload, returncode) is None


def test_non_json_cli_output_is_classified_from_its_text():
    outage = classify_driver_failure(
        None, 1, stdout="", stderr="Error: Invalid API key · Please run /login"
    )
    assert outage.kind == CREDENTIAL
    assert classify_driver_failure(None, 1, stderr="boom: unexpected") is None


# --- downstream readers ------------------------------------------------------


def _card(*results):
    return build_scorecard("r", list(results), {})


def test_gate_counts_not_run_as_unmeasured_and_as_claude_unavailable(tmp_path, capsys):
    current = _card(
        {"scenario_id": "a", "status": "PASS", "category": "c", "overall_score": 9},
        {
            "scenario_id": "b",
            "status": "INFRA_ERROR",
            "category": "c",
            "claude_outage": {"kind": QUOTA},
        },
        {"scenario_id": "c", "status": "NOT_RUN", "category": "c"},
    )
    assert zero_token_errors(current) == ["b", "c"]
    baseline_dir, results_dir = tmp_path / "base", tmp_path / "res"
    (results_dir / "c").mkdir(parents=True)
    baseline_dir.mkdir()
    (results_dir / "c" / "scorecard.json").write_text(json.dumps(current), "utf-8")
    (baseline_dir / "scorecard_c.json").write_text(json.dumps(current), "utf-8")
    code = gate_main(
        [
            "--baseline-dir",
            str(baseline_dir),
            "--results-dir",
            str(results_dir),
            "--category",
            "c",
            "--fail-on-zero-token-errors",
        ]
    )
    assert code == 1
    out = capsys.readouterr().out
    assert "not_run=1" in out
    assert "Claude was unavailable: b, c" in out


def test_gate_fails_on_an_outage_even_with_a_stale_summary(tmp_path, capsys):
    current = {
        "config": {},
        "summary": {},
        "scenarios": [
            {"scenario_id": "a", "status": "PASS"},
            {"scenario_id": "b", "status": "NOT_RUN", "not_run_reason": QUOTA},
        ],
    }
    (tmp_path / "res" / "c").mkdir(parents=True)
    (tmp_path / "base").mkdir()
    for path in (
        tmp_path / "res" / "c" / "scorecard.json",
        tmp_path / "base" / "scorecard_c.json",
    ):
        path.write_text(json.dumps(current), "utf-8")
    argv = [
        "--baseline-dir",
        str(tmp_path / "base"),
        "--results-dir",
        str(tmp_path / "res"),
        "--category",
        "c",
    ]
    assert gate_main(argv) == 0
    assert gate_main(argv + ["--fail-on-zero-token-errors"]) == 1


def test_compare_never_reads_not_run_as_a_regression(tmp_path):
    base = _card({"scenario_id": "a", "status": "PASS", "overall_score": 9})
    curr = _card({"scenario_id": "a", "status": "NOT_RUN", "overall_score": None})
    (tmp_path / "b.json").write_text(json.dumps(base), "utf-8")
    (tmp_path / "c.json").write_text(json.dumps(curr), "utf-8")
    result = compare_scorecards(tmp_path / "b.json", tmp_path / "c.json")
    assert not result["regressed"]
    assert [e["scenario_id"] for e in result["unmeasured"]] == ["a"]
