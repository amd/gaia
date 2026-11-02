# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A task whose tools reached the answer key fails as contaminated (#4017)."""

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

import pytest

from gaia.eval import flagship_tasks as ft
from gaia.eval.bench import harness

#: This checkout's answer key, and a workdir two levels under the repo root.
KEY = ft.TASKS_DIR / "tasks.json"
NESTED_WORKDIR = ft.REPO_ROOT / "work" / "t"

TASK_FILE_TEXT = json.dumps(
    {
        "tasks": [
            {
                "id": "q",
                "genuine_answer": "the cache key ignores the locale",
                "must_establish": ["x"],
                "wrong_answers": ["y"],
            }
        ]
    }
)


def _gaia(*entries):
    return {"conversation": list(entries)}


def _tool(name, args, content):
    return {"role": "tool", "name": name, "tool_args": args, "content": content}


# --- what counts as contact ---------------------------------------------------


@pytest.mark.parametrize(
    "tasks_dir,path",
    [
        (
            "/home/runner/work/gaia/gaia/eval/tasks",
            "/home/runner/work/gaia/gaia/eval/tasks/tasks.json",
        ),
        (
            r"C:\actions-runner\_work\gaia\gaia\eval\tasks",
            r"C:\actions-runner\_work\gaia\gaia\eval\tasks\tasks.json",
        ),
        (
            r"C:\actions-runner\_work\gaia\gaia\eval\tasks",
            "c:/ACTIONS-RUNNER/_work/gaia/gaia/EVAL/TASKS/github/issues.json",
        ),
        ("/src/gaia/eval/tasks", "/src/gaia/src/../eval/tasks"),
    ],
)
def test_a_call_that_names_the_task_folder(monkeypatch, tasks_dir, path):
    monkeypatch.setattr(ft, "TASKS_DIR", Path(tasks_dir))
    why = ft.answer_key_contact(_gaia(_tool("read_file", {"path": path}, "...")))
    assert why.startswith("read_file named ")


def test_a_relative_path_is_resolved_against_the_workdir():
    entries = _gaia(_tool("read_file", {"path": "../../eval/tasks/tasks.json"}, ""))
    assert ft.answer_key_contact(entries, NESTED_WORKDIR).startswith("read_file")
    # From any other workdir the same path is some project's own folder.
    assert ft.answer_key_contact(entries, ft.REPO_ROOT / "a" / "b" / "c") == ""
    assert ft.answer_key_contact(entries) == ""


def test_a_path_inside_run_python_code():
    code = "import json; print(open('../../eval/tasks/tasks.json').read())"
    entries = _gaia(_tool("run_python", {"code": code}, ""))
    assert "run_python" in ft.answer_key_contact(entries, NESTED_WORKDIR)


def test_a_read_through_a_variable_is_caught_by_what_came_back():
    # The path never appears in the call; the answer key's text does in the result.
    code = "import os\nfor r, _d, f in os.walk('/'):\n    ..."
    result = {"stdout": TASK_FILE_TEXT, "stderr": ""}
    why = ft.answer_key_contact(_gaia(_tool("run_python", {"code": code}, result)))
    assert why == (
        "run_python returned the task file's "
        "'genuine_answer', 'must_establish', 'wrong_answers' fields"
    )


def test_native_tool_call_arguments_on_the_assistant_turn():
    # The native path keeps arguments only on the assistant message.
    conversation = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"path": str(KEY)}),
                    },
                }
            ],
        },
        {"role": "tool", "name": "read_file", "tool_call_id": "c1", "content": "{}"},
    ]
    assert "read_file" in ft.answer_key_contact({"conversation": conversation})


def test_claude_code_transcript():
    transcript = {
        "events": [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "Bash",
                            "input": {"command": f'cat "{KEY.as_posix()}"'},
                        }
                    ]
                },
            }
        ]
    }
    assert ft.answer_key_contact(transcript).startswith("Bash named ")


@pytest.mark.parametrize(
    "entries",
    [
        [],
        [_tool("read_file", {"path": "toybox/dates.py"}, "def parse(): ...")],
        # A project's own tasks.json is not the suite's answer key.
        [_tool("read_file", {"path": "repo/tasks.json"}, '{"tasks": []}')],
        [_tool("read_file", {"path": "src/eval/tasks_old.py"}, "")],
        [_tool("run_python", {"code": "print(1)"}, None)],
        # The model saying the word is not a tool reaching the file.
        [{"role": "assistant", "content": "I won't read eval/tasks."}],
    ],
)
def test_clean_runs_are_not_flagged(entries):
    assert ft.answer_key_contact(_gaia(*entries)) == ""


#: Everyday tool calls a coding or question task makes. None reaches the
#: suite's answer key, so none may fail a task as contaminated.
EVERYDAY_CALLS = [
    ("read_file", {"path": "src/app/main.py"}, "def main():\n    return 0\n"),
    ("read_file", {"path": "README.md"}, "# Project\nRun `make test`."),
    ("read_file", {"path": "tests/test_dates.py"}, "def test_parse(): ..."),
    ("read_file", {"path": r"C:\work\proj\src\tasks.py"}, "TASKS = []"),
    ("read_file", {"path": "celery/app/task.py"}, "class Task: ..."),
    ("read_file", {"path": "lm_eval/tasks/__init__.py"}, "TASK_REGISTRY = {}"),
    ("read_file", {"path": "src/retrieval/tasks/rank.py"}, "def rank(): ..."),
    ("read_file", {"path": "medieval/tasks.md"}, "Knights"),
    ("read_file", {"path": "docs/eval-tasks.md"}, "How we evaluate"),
    ("read_file", {"path": "evaluation/tasks/a.yaml"}, "name: a"),
    ("read_file", {"path": "eval/tasks_old.py"}, ""),
    ("read_file", {"path": "eval/taskset.json"}, "{}"),
    ("list_directory", {"path": "."}, "src/\ntests/\nsetup.py"),
    ("list_directory", {"path": "eval"}, "metrics.py\nrunner.py"),
    ("search_files", {"pattern": "*.py"}, "src/a.py\nsrc/b.py"),
    ("search_file_content", {"pattern": "def evaluate"}, "src/a.py:3"),
    ("search_file_content", {"pattern": "tasks"}, "src/queue.py:1: tasks = []"),
    ("run_shell_command", {"command": "python -m pytest -q"}, "3 passed"),
    ("run_shell_command", {"command": "git diff"}, "+    return x\n"),
    ("run_shell_command", {"command": "git status --short"}, " M src/a.py"),
    ("run_shell_command", {"command": "pip install -e ."}, "Successfully"),
    ("run_shell_command", {"command": "ls tests"}, "test_a.py"),
    ("run_shell_command", {"command": "python manage.py test tasks"}, "OK"),
    ("run_python", {"code": "import json; print(json.load(open('tasks.json')))"}, "[]"),
    ("run_python", {"code": "print(sum(range(10)))"}, "45"),
    ("run_python", {"code": "from eval import tasks"}, ""),
    ("write_file", {"path": "src/a.py", "content": "x = 1\n"}, "ok"),
    ("edit_file", {"path": "src/dates.py", "old": "z", "new": "Z"}, "ok"),
    ("Bash", {"command": "pytest tests/test_eval.py"}, "1 passed"),
    ("Read", {"file_path": "/repo/src/evaluator/tasks.py"}, "def run(): ..."),
    ("Grep", {"pattern": "answer", "path": "src"}, "src/qa.py:4: answer = 42"),
    ("web_fetch", {"url": "https://docs.python.org/3/library/re.html"}, "re"),
    ("read_file", {"path": "data/answers.csv"}, "id,answer\n1,yes"),
]


@pytest.mark.parametrize(
    "name,args,result", EVERYDAY_CALLS, ids=[str(i) for i in range(len(EVERYDAY_CALLS))]
)
def test_everyday_tool_calls_are_not_contamination(name, args, result):
    assert ft.answer_key_contact(_gaia(_tool(name, args, result))) == ""


def test_the_everyday_corpus_is_large_enough():
    assert len(EVERYDAY_CALLS) >= 30


# --- false positives: answer-key words and folders that are not the key ------


def _git_grep(pattern):
    """What a ``git grep`` over this repo's own eval code and docs prints."""
    git = shutil.which("git")
    if not git or not (ft.REPO_ROOT / ".git").exists():
        pytest.skip("needs a git checkout")
    return subprocess.run(
        [git, "grep", "-n", pattern, "--", "src/gaia/eval", "docs"],
        cwd=ft.REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    ).stdout[:4000]


#: Results a real task may see. Each holds a field name or an ``eval/tasks``
#: folder, and none is this checkout's answer key. Calls run from NESTED_WORKDIR,
#: the worst case: a workdir inside the repo.
LEGITIMATE = [
    ("read_file", {"path": "quiz/scoring.py"}, "wrong_answers = [a for a in q]\n"),
    ("read_file", {"path": "src/grader.py"}, "# must_establish: checked first\n"),
    ("read_file", {"path": "exams/models.py"}, "wrong_answers = IntegerField()\n"),
    ("read_file", {"path": "README.md"}, "Set `genuine_answer` in the fixture."),
    (
        "web_fetch",
        {"url": "https://opentdb.com/api.php?amount=1"},
        json.dumps({"results": [{"correct_answer": "4", "wrong_answers": ["3"]}]}),
    ),
    ("run_shell_command", {"command": "pytest -q"}, "FAILED ::test_wrong_answers"),
    ("search_file_content", {"pattern": "answers"}, "q.py:2: wrong_answers = []"),
    ("Grep", {"pattern": "must_establish"}, "src/check.py:10: must_establish = 1"),
    ("run_shell_command", {"command": "git log -3"}, "abc rename wrong_answers"),
    ("read_file", {"path": "web/data.json"}, '{"wrong_answers_count": 3}'),
    ("run_shell_command", {"command": "git grep"}, lambda: _git_grep("genuine_answer")),
    ("run_shell_command", {"command": "git grep"}, lambda: _git_grep("must_establish")),
    ("run_shell_command", {"command": "git grep"}, lambda: _git_grep("wrong_answers")),
    ("read_file", {"path": "docs/review.md"}, "A reviewer must establish the cause."),
    ("web_fetch", {"url": "https://example.com/faq"}, "Wrong answers are red."),
    ("read_file", {"path": "NOTES.md"}, "That was a genuine answer."),
    ("read_file", {"path": "web/quiz.ts"}, "const wrongAnswers: string[] = [];"),
    ("read_file", {"path": "lib/x.js"}, "let mustEstablish = true;"),
    ("read_file", {"path": "lib/y.js"}, "const genuineAnswer = await ask();"),
    ("read_file", {"path": "styles.css"}, ".wrong-answers { color: red }"),
    ("read_file", {"path": "settings.py"}, "MAX_WRONG_ANSWERS = 3"),
    ("read_file", {"path": "consts.py"}, "MUST_ESTABLISH_TIMEOUT = 5"),
    ("read_file", {"path": "a.py"}, "class Genuine_Answer: pass"),
    ("read_file", {"path": "quiz.py"}, "wrong_answer = pick()"),
    ("read_file", {"path": "net.py"}, "if must_establishment: pass"),
    ("read_file", {"path": "data/answers.csv"}, "id,answer,wrong\n1,yes,no"),
    ("run_shell_command", {"command": "pytest -k answers"}, "2 passed"),
    # A project's own eval/tasks folder, nested or relative to the workdir.
    ("read_file", {"path": "tests/eval/tasks/test_queue.py"}, "def test(): ..."),
    ("list_directory", {"path": "src/x/eval/tasks/"}, "a.py"),
    ("list_directory", {"path": "eval/tasks"}, "tasks.json"),
    ("read_file", {"path": "./eval/tasks/fixtures.yaml"}, "a: 1"),
    ("read_file", {"path": str(NESTED_WORKDIR / "eval" / "tasks" / "x.json")}, "{}"),
    (
        "read_file",
        {"path": str(ft.REPO_ROOT.parent / "other-gaia" / "eval" / "tasks")},
        "",
    ),
    ("read_file", {"path": "C:/other/proj/eval/tasks/a.json"}, "{}"),
    ("Bash", {"command": "pytest tests/eval/tasks -q"}, "4 passed"),
    ("Grep", {"pattern": "def", "path": "src/x/eval/tasks/"}, "a.py:1: def"),
    ("web_fetch", {"url": "https://github.com/o/r/tree/main/eval/tasks"}, "tree"),
]


@pytest.mark.parametrize(
    "name,args,result", LEGITIMATE, ids=[str(i) for i in range(len(LEGITIMATE))]
)
def test_legitimate_results_are_not_contamination(name, args, result):
    result = result() if callable(result) else result
    entries = _gaia(_tool(name, args, result))
    assert ft.answer_key_contact(entries, NESTED_WORKDIR) == ""


def test_the_legitimate_corpus_is_large_enough():
    assert len(LEGITIMATE) >= 37


# --- true positives: the answer key, however it was reached -------------------


def _real_key_reads():
    text = KEY.read_text(encoding="utf-8")
    lines = text.splitlines()
    first = next(i for i, line in enumerate(lines) if '"must_establish"' in line)
    last = next(i for i, line in enumerate(lines) if '"genuine_answer"' in line)
    question = next(t for t in json.loads(text)["tasks"] if "genuine_answer" in t)
    return {
        "the file as read": text,
        "a sed slice with two fields": "\n".join(lines[first : last + 1]),
        "the file in a stdout dict": {"stdout": text, "stderr": ""},
        "one line of JSON": json.dumps(json.loads(text)),
        "a Python repr of one task": repr(question),
        "JSON inside a JSON string": json.dumps({"r": json.dumps({"out": text})}),
    }


@pytest.mark.parametrize("label", list(_real_key_reads()))
def test_a_read_of_the_real_task_file_is_caught(label):
    entries = _gaia(_tool("run_python", {"code": "walk()"}, _real_key_reads()[label]))
    assert "returned the task file's" in ft.answer_key_contact(entries)


def _key_spellings():
    home = Path(os.path.expanduser("~"))
    found = {
        "native": str(KEY),
        "forward slashes": KEY.as_posix(),
        "upper case": str(KEY).upper(),
        "with ..": str(ft.REPO_ROOT / "src" / ".." / "eval" / "tasks" / "tasks.json"),
        "relative": "../../eval/tasks/tasks.json",
        "relative backslashes": r"..\..\eval\tasks",
        "file url": "file:///" + KEY.as_posix().lstrip("/"),
    }
    if KEY.is_relative_to(home):
        found["home"] = "~/" + KEY.relative_to(home).as_posix()
    drive = PureWindowsPath(str(KEY)).drive
    if len(drive) == 2:
        found["msys"] = f"/{drive[0].lower()}" + KEY.as_posix()[2:]
    return found


@pytest.mark.parametrize("label", list(_key_spellings()))
def test_every_spelling_of_the_real_path_is_caught(label):
    path = _key_spellings()[label]
    for name, args in (
        ("read_file", {"path": path}),
        ("run_shell_command", {"command": f'cat "{path}" | head'}),
        ("run_python", {"code": f"print(open(r'{path}').read())"}),
    ):
        why = ft.answer_key_contact(_gaia(_tool(name, args, "")), NESTED_WORKDIR)
        assert why.startswith(f"{name} named "), (name, path)


def test_one_field_alone_passes_the_field_check():
    # The accepted trade-off: a grep that shows only one field is not caught by
    # what came back, only by naming the path.
    lines = KEY.read_text(encoding="utf-8").splitlines()
    shown = "\n".join(line for line in lines if "genuine_answer" in line)
    entries = _gaia(_tool("Grep", {"pattern": "genuine_answer"}, shown))
    assert ft.answer_key_contact(entries) == ""


# --- the task fails, and the gate counts it -----------------------------------


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(
        ft, "prepare_workdir", lambda task, root, *_a: (project, project)
    )
    monkeypatch.setattr(ft, "_record", lambda result, ran, workdir: None)
    monkeypatch.setattr(ft, "_task_cost", lambda result, model: None)
    monkeypatch.setattr(ft, "workspace_diff", lambda *_a: "")
    monkeypatch.setattr(ft, "calls_after_answer", lambda *_a: [])
    monkeypatch.setattr(ft, "score", lambda *_a: (True, "probes passed"))
    written = {}
    scrubber = SimpleNamespace(
        write_json=lambda path, data: written.update({Path(path).name: data}),
        write_text=lambda path, text: written.update({Path(path).name: text}),
    )
    config = SimpleNamespace(
        harness=harness.GAIA, work_root=tmp_path / "work", therock_url=""
    )
    return SimpleNamespace(
        run=SimpleNamespace(config=config, gateway=None, scrubber=scrubber),
        tmp=tmp_path,
        written=written,
    )


def _task():
    return ft.Task(id="t", check="mechanical", prompt="fix it", max_steps=5)


def _run(ctx, monkeypatch, transcript, error=""):
    ran = harness.AgentRun(
        transcript=transcript,
        conversation=transcript.get("conversation", []),
        error=error,
    )
    monkeypatch.setattr(ft, "_agent_step", lambda *_a: ran)
    return ft.run_task(_task(), "m", ctx.tmp / "out" / "t", ctx.run)


def test_contaminated_task_fails_even_when_the_probes_pass(ctx, monkeypatch):
    transcript = _gaia(_tool("read_file", {"path": str(KEY)}, "{}"))
    result = _run(ctx, monkeypatch, transcript)
    assert result.passed is False
    assert result.contamination.startswith("read_file named ")
    assert result.why.startswith("read the answer key: ")
    # The transcript is still kept as evidence.
    assert "transcript.json" in ctx.written


def test_run_task_resolves_a_relative_path_against_its_workdir(ctx, monkeypatch):
    # The ctx workdir is tmp/project; put the "checkout" beside it.
    monkeypatch.setattr(ft, "TASKS_DIR", ctx.tmp / "gaia" / "eval" / "tasks")
    near = _gaia(_tool("read_file", {"path": "../gaia/eval/tasks/tasks.json"}, ""))
    assert _run(ctx, monkeypatch, near).contamination
    own = _gaia(_tool("read_file", {"path": "eval/tasks/tasks.json"}, ""))
    assert _run(ctx, monkeypatch, own).contamination == ""


def test_contamination_is_reported_alongside_an_agent_error(ctx, monkeypatch):
    transcript = _gaia(_tool("read_file", {"path": str(ft.TASKS_DIR)}, "{}"))
    result = _run(ctx, monkeypatch, transcript, error="crashed")
    assert result.passed is False and result.error == "crashed"
    assert result.contamination


def test_clean_task_is_scored_as_before(ctx, monkeypatch):
    result = _run(ctx, monkeypatch, _gaia(_tool("read_file", {"path": "a.py"}, "")))
    assert result.passed is True and result.contamination == ""


def _entry(task_id, contamination=""):
    return {
        "id": task_id,
        "check": "stated",
        "passed": False if contamination else True,
        "why": "",
        "error": "",
        "error_kind": "",
        "wall_seconds": 1.0,
        "steps": 1,
        "tool_calls": 1,
        "input_tokens": 10,
        "output_tokens": 10,
        "judge": {a: 5 for a in ft.AXES},
        "contamination": contamination,
    }


EXPECTED = {
    "suite": "core",
    "model": "m",
    "judge_model": "j",
    "min_passed": 0,  # slack that would otherwise absorb one failed task
    "min_quality": 1.0,
    "max_misreported": 9,
    "max_total_tokens": 10**9,
    "max_steps": 10**9,
    "max_wall_seconds": 10**9,
}


def _card(*tasks):
    return {"suite": "core", "model": "m", "judge_model": "j", "tasks": list(tasks)}


def test_gate_fails_on_any_contamination_regardless_of_slack():
    card = _card(_entry("a"), _entry("b", contamination="read_file named 'eval/tasks'"))
    checks = {c.metric: c for c in ft.gate(card, EXPECTED)}
    assert ft.summarize(card)["contaminated"] == 1
    assert checks["Tasks that read the answer key"].ok is False
    assert checks["Tasks passed"].ok is True


def test_gate_passes_a_clean_run():
    card = _card(_entry("a"), _entry("b"))
    checks = {c.metric: c for c in ft.gate(card, EXPECTED)}
    assert checks["Tasks that read the answer key"].ok is True


def test_the_judge_cannot_pass_a_contaminated_question():
    entry = _entry("q", contamination="read_file named 'eval/tasks'")
    entry["judge"] = {**entry["judge"], "answers_correctly": True}
    task = ft.Task(id="q", check="stated", prompt="?", max_steps=1)
    ft._apply_verdict(entry, task)
    assert entry["passed"] is False


def test_report_row_says_contaminated():
    row = ft._task_row(_entry("b", contamination="x"))
    assert row["result"] == "CONTAMINATED"


def test_no_baseline_from_a_contaminated_run():
    card = _card(_entry("a"), _entry("b", contamination="x"))
    with pytest.raises(ValueError, match="read the answer key"):
        ft.propose_expectations(card)


def test_old_scorecards_without_the_field_still_summarize():
    entry = _entry("a")
    del entry["contamination"]
    assert ft.summarize(_card(entry))["contaminated"] == 0


def test_the_gate_message_names_an_outage_and_contamination_together(tmp_path, capsys):
    from gaia.cli import _handle_eval_tasks

    down = {**_entry("c"), "error": "unreachable", "error_kind": "unavailable"}
    card = _card(_entry("a"), _entry("b", contamination="x"), down)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ft.write_scorecard(run_dir, card)
    expect = tmp_path / "expect.json"
    expect.write_text(json.dumps(EXPECTED), encoding="utf-8")
    args = argparse.Namespace(
        tasks_action="gate",
        run_dir=str(run_dir),
        expect=str(expect),
        enforce=True,
        propose=None,
    )
    with pytest.raises(SystemExit):
        _handle_eval_tasks(args)
    out = capsys.readouterr().out
    assert "1 task(s) not measured" in out
    assert "1 task(s) also read the answer key" in out
