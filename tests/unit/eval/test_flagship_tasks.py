# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The flagship task harness: the tasks are sound, and the gate means what it says."""

import argparse
import collections
import csv
import datetime
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from gaia.eval import flagship_tasks as ft

ALL_TASKS = ft.load_suite("full")
MECHANICAL = [t for t in ALL_TASKS if t.check == "mechanical"]
STATED = [t for t in ALL_TASKS if t.check == "stated"]


def _copy_fixture(tmp_path: Path) -> Path:
    workdir = tmp_path / "toybox"
    shutil.copytree(ft.FIXTURE, workdir, ignore=shutil.ignore_patterns(*ft._IGNORED))
    return workdir


# ---------------------------------------------------------------------------
# Reference solutions: plain, correct work. A probe that rejects one is wrong.
# ---------------------------------------------------------------------------


def _bugfix(d: Path) -> None:
    dates = d / "toybox" / "dates.py"
    dates.write_text(
        dates.read_text().replace('if v.endswith("Z"):', 'if v[-1:] in ("Z", "z"):')
    )
    tests = d / "tests" / "test_dates.py"
    tests.write_text(
        tests.read_text()
        + "\n\ndef test_updated_lowercase_z():\n"
        + "    assert parse_updated('2026-01-02 03:04:05z').minute == 4\n"
    )


def _weekly_report(d: Path) -> None:
    weeks = collections.defaultdict(list)
    with open(d / "build_times.csv", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            day = datetime.date.fromisoformat(row["date"])
            start = day - datetime.timedelta(days=day.weekday())
            weeks[start].append(float(row["build_seconds"]))
    with open(d / "weekly_build_times.csv", "w", newline="", encoding="utf-8") as fh:
        out = csv.writer(fh)
        out.writerow(["week_start", "runs", "avg_seconds"])
        for start, values in sorted(weeks.items()):
            out.writerow(
                [start.isoformat(), len(values), f"{sum(values) / len(values):.1f}"]
            )


def _config_env(d: Path) -> None:
    cli = d / "toybox" / "cli.py"
    src = cli.read_text()
    src = src.replace("import argparse\n", "import argparse\nimport os\n")
    src = src.replace(
        'p.add_argument("--config", required=True)',
        'p.add_argument("--config", default=os.environ.get("TOYBOX_CONFIG"))',
    )
    src = src.replace(
        "    if args.limit < 1:",
        "    if not args.config:\n"
        '        print("--config or TOYBOX_CONFIG is required", file=sys.stderr)\n'
        "        return 2\n"
        "    if args.limit < 1:",
    )
    cli.write_text(src)
    readme = d / "README.md"
    readme.write_text(
        readme.read_text() + "\nSet `TOYBOX_CONFIG` to skip `--config`.\n"
    )


def _refactor(d: Path) -> None:
    (d / "toybox" / "dates.py").write_text(
        '"""Date helpers, sharing one parse."""\n'
        "from datetime import datetime\n\n\n"
        "def _parse(value):\n"
        '    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")\n\n\n'
        "def parse_created(value):\n"
        "    return _parse(value.strip())\n\n\n"
        "def parse_updated(value):\n"
        "    v = value.strip()\n"
        '    return _parse(v[:-1] if v.endswith("Z") else v)\n\n\n'
        "def parse_deleted(value):\n"
        '    return _parse(value.strip().replace("T", " "))\n'
    )


def _write_tests(d: Path) -> None:
    (d / "tests" / "test_cli.py").write_text(
        "import json\n\nfrom toybox.cli import main\n\n\n"
        "def test_prints_sorted_key_value_lines(tmp_path, capsys):\n"
        '    cfg = tmp_path / "c.json"\n'
        '    cfg.write_text(json.dumps({"b": 2, "a": 1}))\n'
        '    assert main(["--config", str(cfg)]) == 0\n'
        '    assert capsys.readouterr().out.splitlines() == ["a = 1", "b = 2"]\n\n\n'
        "def test_rejects_a_non_positive_limit(tmp_path):\n"
        '    cfg = tmp_path / "c.json"\n'
        '    cfg.write_text("{}")\n'
        '    assert main(["--config", str(cfg), "--limit", "0"]) == 2\n'
    )


SOLUTIONS = {
    "02-bugfix": _bugfix,
    "b6-weekly-report": _weekly_report,
    "b6-config-env": _config_env,
    "01-refactor": _refactor,
    "b6-write-tests": _write_tests,
}


# ---------------------------------------------------------------------------
# The task set
# ---------------------------------------------------------------------------


def test_every_suite_loads_and_core_is_a_subset_of_full():
    suites = {name: ft.load_suite(name) for name in ft.suite_names()}
    assert suites["core"] and suites["full"]
    assert {t.id for t in suites["core"]} <= {t.id for t in suites["full"]}


def test_every_mechanical_task_has_a_reference_solution():
    assert {t.id for t in MECHANICAL} == set(SOLUTIONS)


def test_every_stated_task_has_a_reference_answer():
    assert all(t.genuine_answer for t in STATED)


def test_the_fixture_suite_passes_untouched(tmp_path):
    workdir = _copy_fixture(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("task", MECHANICAL, ids=lambda t: t.id)
def test_a_mechanical_task_fails_on_the_untouched_project(task, tmp_path):
    passed, why = ft.evaluate(task, _copy_fixture(tmp_path))
    assert not passed, f"{task.id} passes with no work done ({why})"


@pytest.mark.parametrize("task", MECHANICAL, ids=lambda t: t.id)
def test_a_mechanical_task_accepts_a_correct_solution(task, tmp_path):
    workdir = _copy_fixture(tmp_path)
    SOLUTIONS[task.id](workdir)
    passed, why = ft.evaluate(task, workdir)
    assert passed, f"{task.id} rejects a correct solution: {why}"


@pytest.mark.parametrize("task", STATED, ids=lambda t: t.id)
def test_a_stated_task_accepts_its_answer_and_rejects_silence(task):
    assert ft.stated_verdict(task, task.genuine_answer)[0]
    assert not ft.stated_verdict(task, "")[0]


@pytest.mark.parametrize(
    "raw, message",
    [
        ({"id": "x", "check": "vibes", "prompt": "p", "max_steps": 5}, "check must"),
        ({"id": "x", "check": "mechanical", "prompt": "p", "max_steps": 5}, "expect"),
        (
            {"id": "x", "check": "stated", "prompt": "p", "max_steps": 5},
            "must_mention_groups",
        ),
        (
            {
                "id": "x",
                "check": "mechanical",
                "prompt": "p",
                "max_steps": 5,
                "expect": {"unchanged": ["a"]},
            },
            "unknown expect keys",
        ),
    ],
)
def test_a_malformed_task_is_refused(tmp_path, raw, message):
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps({"suites": {"s": ["x"]}, "tasks": [raw]}))
    with pytest.raises(ValueError, match=message):
        ft.load_suite("s", path)


def test_an_unknown_suite_names_the_real_ones():
    with pytest.raises(ValueError, match="core"):
        ft.load_suite("nope")


def test_the_diff_shows_edits_and_new_files_only(tmp_path):
    workdir = _copy_fixture(tmp_path)
    assert ft.workspace_diff(workdir) == "(no changes to the workspace)"
    _bugfix(workdir)
    (workdir / "notes.txt").write_text("new\n")
    (workdir / "__pycache__").mkdir()
    (workdir / "__pycache__" / "x.pyc").write_text("junk")
    diff = ft.workspace_diff(workdir)
    assert "b/toybox/dates.py" in diff and "b/notes.txt" in diff
    assert "__pycache__" not in diff


# ---------------------------------------------------------------------------
# Running the agent — against a stand-in, so no model is needed
# ---------------------------------------------------------------------------


class _FakeAgent:
    """Records what the harness hands the agent, then acts like one."""

    seen = []
    behaviour = staticmethod(lambda workdir: "done")
    error_history = []

    def __init__(self, config):
        self.config = config
        self.console = SimpleNamespace(auto_approve_gated_tools=False)
        self.error_history = list(type(self).error_history)

    def process_query(self, prompt):
        type(self).seen.append(
            {
                "credentials": {n: os.environ.get(n) for n in ft.JUDGE_CREDENTIALS},
                "cwd": os.getcwd(),
                "approve": self.console.auto_approve_gated_tools,
                "memory_db": os.environ.get("GAIA_MEMORY_DB"),
                "prompt": prompt,
                "config": self.config,
            }
        )
        answer = type(self).behaviour(Path.cwd())
        return {
            "result": answer,
            "steps_taken": 3,
            "input_tokens": 1000,
            "output_tokens": 50,
            "conversation": [{"role": "tool", "content": "x"}] * 2,
        }


@pytest.fixture
def fake_agent(monkeypatch, tmp_path):
    _FakeAgent.seen = []
    _FakeAgent.behaviour = staticmethod(lambda workdir: "done")
    _FakeAgent.error_history = []
    module = SimpleNamespace(GaiaAgent=_FakeAgent, GaiaAgentConfig=lambda **kw: kw)
    monkeypatch.setitem(sys.modules, "gaia_agent.agent", module)
    tasks = tmp_path / "tasks.json"
    source = json.loads(ft.TASKS_FILE.read_text(encoding="utf-8"))
    source["suites"]["one"] = ["02-bugfix"]
    tasks.write_text(json.dumps(source))
    return tasks


def test_the_agent_never_sees_the_judges_credentials(fake_agent, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key-secret")
    ft.run_suite("one", "some-model", tmp_path / "out", tasks_file=fake_agent)
    assert _FakeAgent.seen[0]["credentials"] == {
        "CLAUDE_CODE_OAUTH_TOKEN": None,
        "ANTHROPIC_API_KEY": None,
    }


def test_a_run_is_headless_isolated_and_restores_the_process(fake_agent, tmp_path):
    before = os.getcwd()
    ft.run_suite("one", "some-model", tmp_path / "out", tasks_file=fake_agent)
    seen = _FakeAgent.seen[0]
    assert seen["approve"] is True
    assert seen["cwd"] != before and Path(seen["cwd"]).name == "toybox"
    assert (
        seen["memory_db"] and Path(seen["memory_db"]).parent == Path(seen["cwd"]).parent
    )
    assert f"You are working in {seen['cwd']}" in seen["prompt"]
    assert seen["config"]["model_id"] == "some-model"
    assert seen["config"]["streaming"] is False
    assert seen["config"]["allowed_paths"] == [seen["cwd"]]
    assert os.getcwd() == before


def test_a_run_scores_the_work_and_records_its_cost(fake_agent, tmp_path):
    def solve(workdir):
        _bugfix(workdir)
        return "Fixed lowercase z and added a test."

    _FakeAgent.behaviour = staticmethod(solve)
    card = ft.run_suite("one", "m", tmp_path / "out", tasks_file=fake_agent)
    task = card["tasks"][0]
    assert task["passed"], task["why"]
    assert (task["steps"], task["tool_calls"]) == (3, 2)
    assert (task["input_tokens"], task["output_tokens"]) == (1000, 50)
    assert json.loads((tmp_path / "out" / "scorecard.json").read_text()) == card
    transcript = json.loads(
        (tmp_path / "out" / "02-bugfix" / "transcript.json").read_text()
    )
    assert transcript["answer"] == "Fixed lowercase z and added a test."
    assert (
        "b/toybox/dates.py"
        in (tmp_path / "out" / "02-bugfix" / "workspace.diff").read_text()
    )


def test_a_crashed_agent_fails_its_task_without_ending_the_run(fake_agent, tmp_path):
    def crash(workdir):
        raise RuntimeError("context length exceeded")

    _FakeAgent.behaviour = staticmethod(crash)
    card = ft.run_suite("one", "m", tmp_path / "out", tasks_file=fake_agent)
    task = card["tasks"][0]
    assert not task["passed"]
    assert task["error"] == "RuntimeError: context length exceeded"


def test_a_backend_failure_is_an_error_not_a_verdict(fake_agent, tmp_path):
    _FakeAgent.error_history = [{"type": "llm_connection_error", "error": "refused"}]
    card = ft.run_suite("one", "m", tmp_path / "out", tasks_file=fake_agent)
    assert card["tasks"][0]["error"] == "model backend failed: refused"


# ---------------------------------------------------------------------------
# The judge
# ---------------------------------------------------------------------------


def _envelope(text, **extra):
    return json.dumps({"result": text, "total_cost_usd": 0.01, **extra})


GRADE = (
    '{"instruction_compliance": 5, "work_quality": 4, "reasoning": 4, '
    '"fabrication_free": 5, "one_line": "fine"}'
)


@pytest.mark.parametrize(
    "text", [GRADE, f"```json\n{GRADE}\n```", f"Here is my grade:\n{GRADE}\nThanks."]
)
def test_a_grade_is_read_however_it_is_wrapped(text):
    grade = ft.parse_judgement(_envelope(text))
    assert grade["work_quality"] == 4 and grade["one_line"] == "fine"


@pytest.mark.parametrize(
    "stdout",
    [
        "not json",
        _envelope("I cannot grade this."),
        _envelope(GRADE.replace('"reasoning": 4', '"reasoning": 9')),
        _envelope(GRADE, is_error=True),
    ],
)
def test_an_unusable_grade_is_an_error_not_a_score(stdout):
    with pytest.raises(ft.JudgeError):
        ft.parse_judgement(stdout)


def test_the_judge_has_no_tools_and_uses_the_key_only_when_given(monkeypatch):
    monkeypatch.setattr(ft.shutil, "which", lambda name: "/bin/claude")
    oauth = ft.judge_command("claude-opus-5", {"CLAUDE_CODE_OAUTH_TOKEN": "t"})
    assert oauth[oauth.index("--tools") + 1] == ""
    assert "--dangerously-skip-permissions" not in oauth
    assert "--bare" not in oauth
    assert "--bare" in ft.judge_command("claude-opus-5", {"ANTHROPIC_API_KEY": "k"})


def test_a_missing_claude_cli_is_named(monkeypatch):
    monkeypatch.setattr(ft.shutil, "which", lambda name: None)
    with pytest.raises(
        FileNotFoundError, match="npm install -g @anthropic-ai/claude-code"
    ):
        ft.judge_command("m", {})


def test_the_judge_runs_outside_the_repo_with_only_the_given_env(monkeypatch):
    monkeypatch.setattr(ft.shutil, "which", lambda name: "/bin/claude")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(returncode=0, stdout=_envelope(GRADE), stderr="")

    monkeypatch.setattr(ft.subprocess, "run", fake_run)
    ft.judge_one(
        "the task", "the answer", "the diff", "m", {"CLAUDE_CODE_OAUTH_TOKEN": "t"}
    )
    kwargs = calls[0]
    # No tools, so the judge gets the original project to check claims against.
    assert "--- toybox/dates.py ---" in kwargs["input"]
    assert "def parse_updated" in kwargs["input"]
    assert Path(kwargs["cwd"]).resolve() != ft.REPO_ROOT.resolve()
    assert kwargs["env"] == {"CLAUDE_CODE_OAUTH_TOKEN": "t"}
    assert all(
        part in kwargs["input"] for part in ("the task", "the answer", "the diff")
    )


def _run_dir(tmp_path, ids=("a", "b")):
    run_dir = tmp_path / "run"
    tasks = []
    for task_id in ids:
        (run_dir / task_id).mkdir(parents=True)
        (run_dir / task_id / "transcript.json").write_text(
            json.dumps({"prompt": f"do {task_id}", "answer": "ok"})
        )
        (run_dir / task_id / "workspace.diff").write_text(
            "(no changes to the workspace)"
        )
        tasks.append(asdict_task(task_id, passed=True, steps=5, tokens=(10000, 500)))
    ft.write_scorecard(run_dir, {"suite": "core", "model": "m", "tasks": tasks})
    return run_dir


def asdict_task(task_id, passed=True, steps=5, tokens=(10000, 500), judge=None):
    return {
        "id": task_id,
        "passed": passed,
        "why": "",
        "error": "",
        "wall_seconds": 10.0,
        "steps": steps,
        "tool_calls": 2,
        "input_tokens": tokens[0],
        "output_tokens": tokens[1],
        "judge": judge or {},
    }


def test_a_failed_grade_is_retried_when_asked_then_recorded(tmp_path, monkeypatch):
    attempts = collections.Counter()

    def fake_judge(prompt, answer, diff, model, env):
        attempts[prompt] += 1
        if prompt == "do b":
            raise ft.JudgeError("no JSON")
        return ft.parse_judgement(_envelope(GRADE))

    monkeypatch.setattr(ft, "judge_one", fake_judge)
    card = ft.judge_run(_run_dir(tmp_path), "judge-model", {}, attempts=2)
    grades = {t["id"]: t["judge"] for t in card["tasks"]}
    assert grades["a"]["work_quality"] == 4 and attempts["do a"] == 1
    assert "no JSON" in grades["b"]["error"] and attempts["do b"] == 2
    assert card["judge_model"] == "judge-model"


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

_GOOD = {
    "instruction_compliance": 5,
    "work_quality": 4,
    "reasoning": 4,
    "fabrication_free": 5,
}


def _card(*tasks):
    return {"suite": "core", "model": "m", "judge_model": "j", "tasks": list(tasks)}


EXPECTED = {
    "suite": "core",
    "model": "m",
    "judge_model": "j",
    "min_passed": 1,
    "min_quality": 4.0,
    "max_misreported": 0,
    "max_total_tokens": 25000,
    "max_steps": 12,
}


def test_a_run_that_meets_every_expectation_passes_the_gate():
    card = _card(asdict_task("a", judge=_GOOD), asdict_task("b", judge=_GOOD))
    assert all(check.ok for check in ft.gate(card, EXPECTED))


@pytest.mark.parametrize(
    "tasks, failing",
    [
        (
            [
                asdict_task("a", passed=False, judge=_GOOD),
                asdict_task("b", passed=False, judge=_GOOD),
            ],
            "Tasks passed",
        ),
        (
            [
                asdict_task("a", judge={**_GOOD, "work_quality": 1}),
                asdict_task("b", judge={**_GOOD, "reasoning": 1}),
            ],
            "Quality (1-5)",
        ),
        (
            [
                asdict_task("a", judge={**_GOOD, "fabrication_free": 3}),
                asdict_task("b", judge=_GOOD),
            ],
            "Tasks it misreported",
        ),
        (
            [
                asdict_task("a", tokens=(30000, 0), judge=_GOOD),
                asdict_task("b", judge=_GOOD),
            ],
            "Total tokens",
        ),
        (
            [asdict_task("a", steps=20, judge=_GOOD), asdict_task("b", judge=_GOOD)],
            "Agent steps",
        ),
    ],
)
def test_each_expectation_can_fail_on_its_own(tasks, failing):
    missed = [c.metric for c in ft.gate(_card(*tasks), EXPECTED) if not c.ok]
    assert missed == [failing]


def test_an_unjudged_task_fails_the_quality_checks_instead_of_skipping_them():
    card = _card(asdict_task("a", judge=_GOOD), asdict_task("b", judge={"error": "x"}))
    missed = {c.metric for c in ft.gate(card, EXPECTED) if not c.ok}
    assert missed == {"Quality (1-5)", "Tasks it misreported"}


@pytest.mark.parametrize("key", ["suite", "model", "judge_model"])
def test_expectations_measured_differently_are_refused(key):
    with pytest.raises(ValueError, match=key):
        ft.gate(_card(asdict_task("a", judge=_GOOD)), {**EXPECTED, key: "other"})


def test_proposed_expectations_leave_headroom_for_one_noisy_run():
    card = _card(
        asdict_task("a", judge=_GOOD, steps=10, tokens=(10000, 0)),
        asdict_task("b", judge=_GOOD, steps=10, tokens=(10000, 0)),
    )
    proposal = ft.propose_expectations(card)
    assert proposal["min_passed"] == 1
    assert proposal["min_quality"] == 4.0
    assert proposal["max_misreported"] == 1
    assert proposal["max_total_tokens"] == 27000
    assert proposal["max_steps"] == 27
    assert all(check.ok for check in ft.gate(card, proposal))


def test_expectations_are_not_proposed_from_an_unjudged_run():
    with pytest.raises(ValueError, match="judge the run"):
        ft.propose_expectations(_card(asdict_task("a")))


# ---------------------------------------------------------------------------
# The CLI gate: report by default, fail only when enforcing
# ---------------------------------------------------------------------------


def _gate_args(run_dir, expect=None, enforce=False, propose=None):
    return argparse.Namespace(
        tasks_action="gate",
        run_dir=str(run_dir),
        expect=str(expect) if expect else None,
        enforce=enforce,
        propose=str(propose) if propose else None,
    )


def _judged_run_dir(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ft.write_scorecard(
        run_dir, _card(asdict_task("a", judge=_GOOD), asdict_task("b", judge=_GOOD))
    )
    return run_dir


def test_missing_expectations_report_but_fail_when_enforced(tmp_path, monkeypatch):
    from gaia.cli import _handle_eval_tasks

    monkeypatch.setattr(ft, "EXPECTATIONS_DIR", tmp_path / "none")
    run_dir = _judged_run_dir(tmp_path)
    _handle_eval_tasks(_gate_args(run_dir))
    with pytest.raises(SystemExit) as exc:
        _handle_eval_tasks(_gate_args(run_dir, enforce=True))
    assert exc.value.code == 1


def test_a_missed_expectation_fails_only_when_enforced(tmp_path, monkeypatch):
    from gaia.cli import _handle_eval_tasks

    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    run_dir = _judged_run_dir(tmp_path)
    expect = tmp_path / "expect.json"
    expect.write_text(json.dumps({**EXPECTED, "max_steps": 1}))
    _handle_eval_tasks(_gate_args(run_dir, expect=expect))
    with pytest.raises(SystemExit):
        _handle_eval_tasks(_gate_args(run_dir, expect=expect, enforce=True))
    assert "Agent steps" in summary.read_text(encoding="utf-8")


def test_propose_writes_expectations_the_same_run_meets(tmp_path):
    from gaia.cli import _handle_eval_tasks

    run_dir = _judged_run_dir(tmp_path)
    proposal = tmp_path / "proposal.json"
    _handle_eval_tasks(
        _gate_args(run_dir, expect=proposal, enforce=True, propose=proposal)
    )
    assert json.loads(proposal.read_text())["model"] == "m"


def test_propose_on_an_unjudged_run_warns_and_the_gate_still_decides(tmp_path):
    from gaia.cli import _handle_eval_tasks

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ft.write_scorecard(run_dir, _card(asdict_task("a"), asdict_task("b")))
    expect = tmp_path / "expect.json"
    expect.write_text(json.dumps(EXPECTED))
    proposal = tmp_path / "proposal.json"
    with pytest.raises(SystemExit):
        _handle_eval_tasks(
            _gate_args(run_dir, expect=expect, enforce=True, propose=proposal)
        )
    assert not proposal.exists()


def test_expectations_for_another_judge_fail_even_in_report_mode(tmp_path):
    from gaia.cli import _handle_eval_tasks

    run_dir = _judged_run_dir(tmp_path)
    expect = tmp_path / "expect.json"
    expect.write_text(json.dumps({**EXPECTED, "judge_model": "another-judge"}))
    with pytest.raises(SystemExit) as exc:
        _handle_eval_tasks(_gate_args(run_dir, expect=expect))
    assert exc.value.code == 2


def test_the_project_snapshot_fits_and_skips_caches(tmp_path):
    workdir = _copy_fixture(tmp_path)
    (workdir / "__pycache__").mkdir()
    (workdir / "__pycache__" / "x.pyc").write_text("junk")
    snapshot = ft.project_snapshot(workdir)
    assert "--- build_times.csv ---" in snapshot and "__pycache__" not in snapshot
    assert len(ft.project_snapshot()) <= ft.PROJECT_CAP
