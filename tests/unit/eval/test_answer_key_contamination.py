# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A task whose tools reached the answer key fails as contaminated (#4017)."""

import argparse
import json
import os
import shutil
import subprocess
import time
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


# --- a checkout path with spaces, or spelled from the home folder ------------

SPACED_HOME = "C:/Users/John Smith"
SPACED_POSIX_HOME = "/home/j/my work"
#: A read that shows values only, so the field check cannot see it.
VALUES_ONLY = "the cache key ignores the locale\nthe cache key is stale"


def _spaced(monkeypatch, home, checkout):
    monkeypatch.setattr(ft, "TASKS_DIR", Path(home, checkout, "eval", "tasks"))
    monkeypatch.setattr(os.path, "expanduser", lambda p: home if p == "~" else p)


def _spaced_paths(home, checkout):
    key = f"{home}/{checkout}/eval/tasks/tasks.json"
    rel = f"{checkout}/eval/tasks/tasks.json"
    return {
        "forward": key,
        "backslash": key.replace("/", "\\"),
        "home ~": f"~/{rel}",
        "$HOME": f"$HOME/{rel}",
        "${HOME}": "${HOME}/" + rel,
        "%USERPROFILE%": "%USERPROFILE%\\" + rel.replace("/", "\\"),
        "$env:USERPROFILE": "$env:USERPROFILE\\" + rel.replace("/", "\\"),
        "$USERPROFILE": f"$USERPROFILE/{rel}",
        "%HOMEDRIVE%%HOMEPATH%": "%HOMEDRIVE%%HOMEPATH%\\" + rel.replace("/", "\\"),
        "$env:HOMEDRIVE$env:HOMEPATH": "$env:HOMEDRIVE$env:HOMEPATH\\"
        + rel.replace("/", "\\"),
    }


def _values_only_reads(path):
    return {
        "read_file": ("read_file", {"path": path}),
        "jq quoted": (
            "run_shell_command",
            {"command": f"jq -r '.tasks[].genuine_answer' \"{path}\""},
        ),
        "jq single-quoted": (
            "Bash",
            {"command": f"jq -r '.tasks[].genuine_answer' '{path}'"},
        ),
        "cat unquoted": ("run_shell_command", {"command": f"cat {path} | head"}),
        "python": ("run_python", {"code": f"print(open(r'{path}').read())"}),
        "powershell": (
            "run_shell_command",
            {"command": f'(Get-Content "{path}" | ConvertFrom-Json).tasks'},
        ),
    }


@pytest.mark.parametrize(
    "home,checkout",
    [(SPACED_HOME, "OneDrive - AMD/gaia"), (SPACED_POSIX_HOME, "src (x)/gaia")],
    ids=["windows", "posix"],
)
@pytest.mark.parametrize("spelling", list(_spaced_paths("h", "c")))
def test_a_checkout_path_with_spaces_is_caught(monkeypatch, home, checkout, spelling):
    _spaced(monkeypatch, home, checkout)
    path = _spaced_paths(home, checkout)[spelling]
    for label, (name, args) in _values_only_reads(path).items():
        transcript = _gaia(_tool(name, args, VALUES_ONLY))
        why = ft.answer_key_contact(transcript, Path("/tmp/gaia-bench/run/toybox"))
        assert why.startswith(f"{name} named "), (label, path)


def test_a_quoted_relative_path_with_spaces_is_resolved(monkeypatch):
    _spaced(monkeypatch, SPACED_POSIX_HOME, "gaia")
    workdir = Path(SPACED_POSIX_HOME, "runs", "t")
    for path in ("../../gaia/eval/tasks", "../../../my work/gaia/eval/tasks"):
        call = {"command": f"jq -r '.tasks[].genuine_answer' \"{path}/tasks.json\""}
        transcript = _gaia(_tool("Bash", call, VALUES_ONLY))
        assert ft.answer_key_contact(transcript, workdir).startswith("Bash named ")


@pytest.mark.parametrize(
    "call",
    [
        # Another checkout under the same spaced home.
        {"path": "C:/Users/John Smith/OneDrive - AMD/gaia2/eval/tasks/tasks.json"},
        {"path": "C:\\Users\\John Smith\\OneDrive - AMD\\proj\\eval\\tasks"},
        {"command": 'cat "$HOME/OneDrive - AMD/other/eval/tasks/a.json"'},
        {"command": "type %USERPROFILE%\\gaia\\eval\\tasks\\tasks.json"},
        {"command": "ls $env:USERPROFILE\\OneDrive - AMD\\gaia\\x\\eval\\tasks"},
        # ``..`` from inside the checkout lands outside it.
        {
            "command": "cd 'C:/Users/John Smith/OneDrive - AMD/gaia/x' && cat ../../eval/tasks"
        },
        {"command": 'cd "C:/Users/John Smith/OneDrive - AMD/gaia" && ls x/eval/tasks'},
        # The workdir's own folder, relative, with a space in front.
        {"command": "pytest -q tests/eval/tasks"},
        {"command": "ls  eval/tasks"},
        {"path": "My Docs/eval/tasks/a.json"},
    ],
)
def test_spaced_paths_that_are_not_the_key(monkeypatch, call):
    _spaced(monkeypatch, SPACED_HOME, "OneDrive - AMD/gaia")
    transcript = _gaia(_tool("run_shell_command", call, VALUES_ONLY))
    workdir = Path(SPACED_HOME, "AppData", "Local", "Temp", "gaia-bench", "t")
    assert ft.answer_key_contact(transcript, workdir) == ""


#: A checkout under a folder with spaces and parentheses, and a workdir in TEMP.
PAREN_CHECKOUT = "OneDrive - AMD (Work)/gaia"
PAREN_WORKDIR = Path(SPACED_HOME, "AppData", "Local", "Temp", "gaia-bench", "t")


@pytest.mark.parametrize(
    "path",
    [
        # Shell-escaped spaces and parentheses.
        r"~/OneDrive\ -\ AMD\ \(Work\)/gaia/eval/tasks/tasks.json",
        r"/c/Users/John\ Smith/OneDrive\ -\ AMD\ \(Work\)/gaia/eval/tasks/tasks.json",
        # Quoted in pieces.
        '"$HOME"/"OneDrive - AMD (Work)"/gaia/eval/tasks/tasks.json',
        '~/"OneDrive - AMD (Work)"/gaia/eval/tasks/tasks.json',
        # Relative, through a folder with parentheses.
        f"../../../../../{PAREN_CHECKOUT}/eval/tasks/tasks.json",
        f"./../../../../../{PAREN_CHECKOUT}/eval/tasks/tasks.json",
        r"..\..\..\..\..\OneDrive - AMD (Work)\gaia\eval\tasks\tasks.json",
        # More ``..`` than the workdir is deep: Windows stops at the drive root.
        "../" * 12 + f"Users/John Smith/{PAREN_CHECKOUT}/eval/tasks/tasks.json",
        "..\\" * 12 + r"Users\John Smith\OneDrive - AMD (Work)\gaia\eval\tasks",
        # Relative to the current folder on drive C.
        f"C:../../../../../{PAREN_CHECKOUT}/eval/tasks/tasks.json",
    ],
)
def test_shell_spellings_of_a_spaced_checkout_are_caught(monkeypatch, path):
    _spaced(monkeypatch, SPACED_HOME, PAREN_CHECKOUT)
    call = {"command": f"jq -r '.tasks[].genuine_answer' {path}"}
    transcript = _gaia(_tool("Bash", call, VALUES_ONLY))
    assert ft.answer_key_contact(transcript, PAREN_WORKDIR).startswith("Bash named ")


@pytest.mark.parametrize(
    "path",
    [
        r"~/OneDrive\ -\ AMD\ \(Work\)/other/eval/tasks/tasks.json",
        '"$HOME"/"OneDrive - AMD (Work)"/gaia2/eval/tasks/tasks.json',
        "../../../../" + f"{PAREN_CHECKOUT}/eval/tasks/tasks.json",
        "../" * 12 + f"Users/Jane/{PAREN_CHECKOUT}/eval/tasks/tasks.json",
        f"D:../../../../../{PAREN_CHECKOUT}/eval/tasks/tasks.json",
    ],
)
def test_shell_spellings_of_another_folder_are_not_the_key(monkeypatch, path):
    _spaced(monkeypatch, SPACED_HOME, PAREN_CHECKOUT)
    call = {"command": f"jq -r '.tasks[].genuine_answer' {path}"}
    transcript = _gaia(_tool("Bash", call, VALUES_ONLY))
    assert ft.answer_key_contact(transcript, PAREN_WORKDIR) == ""


# --- a checkout path holding shell punctuation --------------------------------

PUNCT_HOME = "C:/Users/kovtchar"
PUNCT_WORKDIR = Path(PUNCT_HOME, "AppData", "Local", "Temp", "gaia-bench", "t")


def _punct_reads(folder):
    key = f"{PUNCT_HOME}/{folder}/gaia/eval/tasks/tasks.json"
    rel = os.path.relpath(key, PUNCT_WORKDIR).replace("\\", "/")
    return {
        "read_file": ("read_file", {"path": key}),
        "backslashes": ("read_file", {"path": key.replace("/", "\\")}),
        "quoted": ("Bash", {"command": f'cat "{key}"'}),
        "python": ("run_python", {"code": f"print(open(r'{key}').read())"}),
        "home": ("Bash", {"command": f'cat "~/{folder}/gaia/eval/tasks/tasks.json"'}),
        "relative": ("read_file", {"path": rel}),
        "relative quoted": ("Bash", {"command": f"jq . '{rel}'"}),
        "dotted": ("read_file", {"path": key.replace("/gaia/", "/gaia/src/../")}),
    }


#: Folder names holding each character that once ended a path.
PUNCT_FOLDERS = [
    "OneDrive - Acme, Inc",
    "R&D",
    "gaia [old]",
    "a=b",
    "x;y",
    "back`tick",
    "(x)",
]


@pytest.mark.parametrize("folder", PUNCT_FOLDERS)
@pytest.mark.parametrize("spelling", list(_punct_reads("f")))
def test_a_checkout_path_with_shell_punctuation_is_caught(
    monkeypatch, folder, spelling
):
    _spaced(monkeypatch, PUNCT_HOME, f"{folder}/gaia")
    name, args = _punct_reads(folder)[spelling]
    why = ft.answer_key_contact(_gaia(_tool(name, args, VALUES_ONLY)), PUNCT_WORKDIR)
    assert why.startswith(f"{name} named "), (folder, args)


def test_a_folder_name_with_a_trailing_dot_matches_without_it(monkeypatch):
    # Windows drops it: ``Inc.`` and ``Inc`` are the same folder.
    _spaced(monkeypatch, PUNCT_HOME, "OneDrive - Acme, Inc./gaia")
    rel = "../../../../../OneDrive - Acme, Inc/gaia/eval/tasks/tasks.json"
    why = ft.answer_key_contact(
        _gaia(_tool("read_file", {"path": rel}, "")), PUNCT_WORKDIR
    )
    assert why.startswith("read_file named ")


@pytest.mark.parametrize(
    "call",
    [
        {"path": f"{PUNCT_HOME}/R&D/gaia2/eval/tasks/tasks.json"},
        {"path": f"{PUNCT_HOME}/R/gaia/eval/tasks/tasks.json"},
        {"path": "D:/R&D/gaia/eval/tasks/tasks.json"},
        {"command": f'cat "{PUNCT_HOME}/R&D/gaia/x/eval/tasks/tasks.json"'},
        {"command": "cat ~/D/gaia/eval/tasks/tasks.json"},
        # A command after ``&&`` is not a folder ``..`` can step out of.
        {"command": f"cd '{PUNCT_HOME}/R&D/gaia/x' && cat ../../eval/tasks"},
        {"command": f"cd {PUNCT_HOME}/R&D/gaia/x&&cat ../eval/tasks"},
        {"command": "ls eval/tasks; ls ../eval/tasks, ./eval/tasks"},
    ],
)
def test_punctuated_paths_that_are_not_the_key(monkeypatch, call):
    _spaced(monkeypatch, PUNCT_HOME, "R&D/gaia")
    transcript = _gaia(_tool("Bash", call, VALUES_ONLY))
    assert ft.answer_key_contact(transcript, PUNCT_WORKDIR) == ""


# --- many spaces, quotes split at a separator, escaped parentheses ------------


@pytest.mark.parametrize("folders", [15, 16, 17, 30])
def test_any_number_of_spaces_before_the_key(monkeypatch, folders):
    spaced = "/".join(f"s{i} x" for i in range(folders))
    _spaced(monkeypatch, "C:/r", f"{spaced}/gaia")
    key = f"C:/r/{spaced}/gaia/eval/tasks/tasks.json"
    workdir = Path("C:/r/tmp/run")
    rel = os.path.relpath(key, workdir).replace("\\", "/")
    for name, args in (
        ("read_file", {"path": rel}),
        ("Bash", {"command": f'cat "{rel}"'}),
        ("Bash", {"command": "cat " + rel.replace(" ", "\\ ")}),
        ("read_file", {"path": key.replace("/gaia/", "/gaia/src/../")}),
        ("read_file", {"path": "../" * 25 + key[3:]}),
        ("read_file", {"path": "C:" + "../" * 25 + key[3:]}),
    ):
        why = ft.answer_key_contact(_gaia(_tool(name, args, "")), workdir)
        assert why.startswith(f"{name} named "), args


@pytest.mark.parametrize(
    "command",
    [
        'cat "{co}"/"eval/tasks/tasks.json"',
        "cat '{co}/''eval/tasks/tasks.json'",
        'cat "{co}/"eval/tasks/tasks.json',
        'cat "$HOME/src/gaia"/eval/tasks/tasks.json',
        'cat "$HOME/src/gaia"/"eval/tasks/tasks.json"',
        "cat '{co}'/'eval'/'tasks'/'tasks.json'",
    ],
)
def test_quoted_pieces_split_at_a_separator(monkeypatch, command):
    _spaced(monkeypatch, PUNCT_HOME, "src/gaia")
    call = {"command": command.format(co=f"{PUNCT_HOME}/src/gaia")}
    why = ft.answer_key_contact(_gaia(_tool("Bash", call, "")), PUNCT_WORKDIR)
    assert why.startswith("Bash named ")


@pytest.mark.parametrize(
    "path",
    [
        r"C:/Users/kovtchar/src/gaia\(copy\)/eval/tasks/tasks.json",
        r"~/src/gaia\(copy\)/eval/tasks/tasks.json",
        r"/c/Users/kovtchar/src/gaia\(copy\)/eval/tasks/tasks.json",
    ],
)
def test_shell_escaped_parentheses_without_a_space(monkeypatch, path):
    _spaced(monkeypatch, PUNCT_HOME, "src/gaia(copy)")
    call = {"command": f"jq . {path}"}
    why = ft.answer_key_contact(_gaia(_tool("Bash", call, "")), PUNCT_WORKDIR)
    assert why.startswith("Bash named ")


@pytest.mark.parametrize(
    "code",
    [
        "open(r'/c/Users/kovtchar/src/gaia/eval/tasks/tasks.json')",
        "open(r'/mnt/c/Users/kovtchar/src/gaia/eval/tasks/tasks.json')",
        "open(b'C:\\\\Users\\\\kovtchar\\\\src\\\\gaia\\\\eval\\\\tasks')",
        "for p in ['eval/tasks', r'C:/Users/kovtchar/src/gaia/eval/tasks']: ...",
    ],
)
def test_a_python_string_prefix_does_not_hide_the_path(monkeypatch, code):
    _spaced(monkeypatch, PUNCT_HOME, "src/gaia")
    why = ft.answer_key_contact(
        _gaia(_tool("run_python", {"code": code}, "")), PUNCT_WORKDIR
    )
    assert why.startswith("run_python named ")


def test_a_path_through_another_eval_tasks_and_back_out(monkeypatch):
    _spaced(monkeypatch, PUNCT_HOME, "src/gaia")
    rel = os.path.relpath(f"{PUNCT_HOME}/src/gaia/eval/tasks", PUNCT_WORKDIR)
    for path in (
        "$HOME/eval/tasks/../../src/gaia/eval/tasks/tasks.json",
        f"./eval/tasks/../../{rel}/tasks.json".replace("\\", "/"),
    ):
        call = {"command": f"cat {path}"}
        why = ft.answer_key_contact(_gaia(_tool("Bash", call, "")), PUNCT_WORKDIR)
        assert why.startswith("Bash named "), path
    # One ``..`` short of stepping back out.
    call = {"command": "cat $HOME/eval/tasks/../src/gaia/eval/tasks/tasks.json"}
    assert ft.answer_key_contact(_gaia(_tool("Bash", call, "")), PUNCT_WORKDIR) == ""


@pytest.mark.parametrize(
    "path",
    ["{co}/eval/./tasks/tasks.json", "{co}/./eval/././tasks", "{co}/eval/.//tasks"],
)
def test_a_dot_folder_inside_the_key_path(monkeypatch, path):
    _spaced(monkeypatch, PUNCT_HOME, "src/gaia")
    call = {"command": "cat " + path.format(co=f"{PUNCT_HOME}/src/gaia")}
    why = ft.answer_key_contact(_gaia(_tool("Bash", call, "")), PUNCT_WORKDIR)
    assert why.startswith("Bash named ")


#: Checkout roots of the CI runners: short, so a walk-back cap sized from the
#: path length would stop early there and nowhere on a developer machine.
CI_ROOTS = {
    "windows": ("D:/a", "D:/a/gaia/gaia"),
    "linux": ("/home/runner", "/home/runner/work/gaia/gaia"),
    "macos": ("/Users/runner", "/Users/runner/work/gaia/gaia"),
    "this checkout": (os.path.expanduser("~"), str(ft.REPO_ROOT)),
}


def _ci_root(monkeypatch, root):
    home, checkout = CI_ROOTS[root]
    monkeypatch.setattr(ft, "TASKS_DIR", Path(checkout, "eval", "tasks"))
    monkeypatch.setattr(os.path, "expanduser", lambda p: home if p == "~" else p)
    return checkout.replace("\\", "/"), Path(checkout, "work")


def _ci_names(command, workdir):
    return ft.answer_key_contact(
        _gaia(_tool("Bash", {"command": command}, "")), workdir
    )


@pytest.mark.parametrize("root", list(CI_ROOTS))
def test_a_long_folder_name_that_dot_dot_removes(monkeypatch, root):
    _co, workdir = _ci_root(monkeypatch, root)
    long_name = "x" * 300
    assert _ci_names(f"cd a/b && cat {long_name}/../../eval/tasks/tasks.json", workdir)
    assert (
        _ci_names(f"cd a/b && cat {long_name}/../eval/tasks/tasks.json", workdir) == ""
    )


@pytest.mark.parametrize(
    "path",
    [
        "{co}/" + "z" * 255 + "/" + "y" * 255 + "/../../eval/tasks/tasks.json",
        "{co}/" + "x/../" * 63 + "eval/tasks/tasks.json",
        '"{co}/' + "a " * 155 + 'b/../eval/tasks/tasks.json"',
        "../" * 106 + "{tail}/eval/tasks/tasks.json",
    ],
    ids=["two long folders", "63 x/..", "155 spaces", "106 .."],
)
@pytest.mark.parametrize("root", list(CI_ROOTS))
def test_long_detours_on_a_short_checkout_root(monkeypatch, root, path):
    co, workdir = _ci_root(monkeypatch, root)
    tail = co.split(":", 1)[-1].lstrip("/")
    assert _ci_names(f"cat {path.format(co=co, tail=tail)}", workdir)


@pytest.mark.parametrize("folder", ["x (", "a ", "a,", "a&&", "a ="])
@pytest.mark.parametrize("root", list(CI_ROOTS))
def test_a_folder_name_ending_in_punctuation_that_dot_dot_removes(
    monkeypatch, root, folder
):
    co, workdir = _ci_root(monkeypatch, root)
    assert _ci_names(f'cat "{co}/{folder}/../eval/tasks/tasks.json"', workdir)


@pytest.mark.parametrize(
    "command",
    [
        # ``gaia `` stands for ``gaia``, but ``u /..`` then lands in ``users``.
        "qqqqqqqqqqqq /tasks/c:/users/u /../gaia /eval/tasks",
        "ls c:/users/k/x (/../eval/tasks",
        "ls c:/users/k/src/gaia/x (/../../eval/tasks",
    ],
)
def test_a_trailing_punctuation_folder_is_not_a_path_start(monkeypatch, command):
    _spaced(monkeypatch, "C:/Users/k", "src/gaia")
    assert _ci_names(command, Path("C:/Users/k/src")) == ""


@pytest.mark.parametrize(
    "command",
    [
        "ls /backup/home/runner/work/gaia/gaia/eval/tasks",
        "ls /var/lib/docker/overlay2/abc/merged/home/runner/work/gaia/gaia/eval/tasks",
        "curl https://ci.example.com/artifacts/home/runner/work/gaia/gaia/eval/tasks/",
        "rsync -a src/ host:/srv/home/runner/work/gaia/gaia/eval/tasks/",
    ],
)
def test_the_checkout_path_inside_a_longer_path_is_not_the_key(monkeypatch, command):
    _ci_root(monkeypatch, "linux")
    assert _ci_names(command, Path("/tmp/gaia-bench/run/toybox")) == ""


@pytest.mark.parametrize(
    "command",
    [
        "cat /home/runner/work/gaia/gaia/eval/tasks/tasks.json",
        "jq . </home/runner/work/gaia/gaia/eval/tasks/tasks.json",
        "jq --from-file=/home/runner/work/gaia/gaia/eval/tasks/tasks.json",
        "curl -d @/home/runner/work/gaia/gaia/eval/tasks/tasks.json http://x",
        "curl file:///home/runner/work/gaia/gaia/eval/tasks/tasks.json",
        "python -c \"open('/home/runner/work/gaia/gaia/eval/tasks/tasks.json')\"",
    ],
)
def test_the_rooted_checkout_path_after_punctuation_is_the_key(monkeypatch, command):
    _ci_root(monkeypatch, "linux")
    assert _ci_names(command, Path("/tmp/gaia-bench/run/toybox"))


@pytest.mark.parametrize(
    "command",
    [
        "tar -C{co}/eval/tasks -cf - . | tar -xOf -",
        "tar -cf - -C{co}/eval/tasks .",
        "docker run -v{co}/eval/tasks:/t alpine cat /t/tasks.json",
        "jq -r .tasks[].genuine_answer -f{co}/eval/tasks/tasks.json",
        "gcc -I{co}/eval/tasks x.c",
    ],
)
@pytest.mark.parametrize("root", ["linux", "macos"])
def test_a_path_glued_to_a_short_option_is_the_key(monkeypatch, root, command):
    co, _workdir = _ci_root(monkeypatch, root)
    assert _ci_names(command.format(co=co), Path("/tmp/gaia-bench/run/toybox"))


@pytest.mark.parametrize(
    "command",
    [
        "tar -C/d/a/gaia/gaia/eval/tasks -cf - .",
        "tar -C/mnt/d/a/gaia/gaia/eval/tasks -cf - .",
        "docker run -v/d/a/gaia/gaia/eval/tasks:/t a",
        "jq -f/d/a/gaia/gaia/eval/tasks/tasks.json",
    ],
)
def test_a_posix_drive_path_glued_to_a_short_option_is_the_key(monkeypatch, command):
    _co, workdir = _ci_root(monkeypatch, "windows")
    assert _ci_names(command, workdir)


@pytest.mark.parametrize(
    "command",
    [
        "tar -C/backup/home/runner/work/gaia/gaia/eval/tasks -cf - .",
        "gcc -I/srv/home/runner/work/gaia/gaia/eval/tasks x.c",
    ],
)
def test_a_short_option_does_not_make_a_longer_path_the_key(monkeypatch, command):
    _ci_root(monkeypatch, "linux")
    assert _ci_names(command, Path("/tmp/gaia-bench/run/toybox")) == ""


@pytest.mark.parametrize(
    "content,names_key",
    [
        ("[" * 5000 + "1" + "]" * 5000, False),
        ('{"a":' * 5000 + "1" + "}" * 5000, False),
        ("[" * 5000 + json.dumps(str(KEY)) + "]" * 5000, True),
    ],
    ids=["list", "object", "key inside"],
)
def test_deeply_nested_json_in_a_tool_call_does_not_crash(content, names_key):
    transcript = _gaia(_tool("write_file", {"path": "t.json", "content": content}, ""))
    assert bool(ft.answer_key_contact(transcript, NESTED_WORKDIR)) == names_key


def test_a_deeply_nested_call_is_still_read():
    args = {"path": str(KEY)}
    for _ in range(5000):
        args = {"a": [args]}
    assert ft.answer_key_contact(_gaia(_tool("write_file", args, "")), NESTED_WORKDIR)


# --- large tool calls stay fast ----------------------------------------------

#: Far above a linear scan even on a loaded runner (each case takes tenths of a
#: second locally); a scan quadratic in the text's length took 40-60 s.
FAST_ENOUGH = 5.0


def _timed(transcript, workdir=NESTED_WORKDIR):
    """The verdict and the best of three timings, so one stall on a busy runner is ignored."""
    best = float("inf")
    for _ in range(3):
        started = time.perf_counter()
        why = ft.answer_key_contact(transcript, workdir)
        best = min(best, time.perf_counter() - started)
    return why, best


def _long_texts():
    line = "- The suite lives in eval/tasks, and the runner reads it when set.\n"
    checkout = ft.TASKS_DIR.parent.parent.name
    return {
        "one mention after 20k words": "word " * 20000 + "eval/tasks",
        "1 MB of markdown, 12k mentions": (line * 16000)[:1_000_000],
        "checkout-named mentions with dots": (
            (". ../ ./ " * 40 + f"{checkout}/eval/tasks ") * 600
        )[:200_000],
    }


@pytest.mark.parametrize("label", list(_long_texts()))
def test_a_long_write_is_scanned_in_linear_time(label):
    call = {"path": "notes.md", "content": _long_texts()[label]}
    why, seconds = _timed(_gaia(_tool("write_file", call, "ok")))
    assert why == ""
    assert seconds < FAST_ENOUGH, f"{label}: {seconds:.2f}s"


def test_a_large_native_call_with_escaped_arguments_is_fast():
    line = "- The suite lives in eval/tasks and the runner reads it when set\n"
    arguments = json.dumps({"content": (line * 1600)[:100_000]})
    call = {"function": {"name": "write_file", "arguments": arguments}}
    entry = {"role": "assistant", "content": "", "tool_calls": [call]}
    why, seconds = _timed({"conversation": [entry]})
    assert why == ""
    assert seconds < FAST_ENOUGH, f"{seconds:.2f}s"


def test_a_long_write_still_names_the_key_at_its_end():
    content = "word " * 20000 + f"cat {KEY}"
    why, seconds = _timed(_gaia(_tool("write_file", {"content": content}, "ok")))
    assert why.startswith("write_file named ")
    assert seconds < FAST_ENOUGH, f"{seconds:.2f}s"


def _adversarial_units(checkout):
    """Repeated units that make every ``eval/tasks`` worth a closer look."""
    sp16 = " a" * 16 + " "
    return {
        "16 spaces, checkout": f"{sp16}{checkout}/eval/tasks ",
        "16 spaces, dot": f"{sp16}../eval/tasks ",
        "16 spaces, many ..": sp16 + "../" * 30 + f"{checkout}/eval/tasks ",
        "quotes and parens": f" '\"( ./{checkout}/./eval/tasks",
        "quotes at separators": f' "./{checkout}"/"eval/tasks" ',
        "escaped spaces": "cd\\ a\\ b\\ c\\ d\\ e\\ f\\ g\\ h\\ i\\ j\\ k\\ l\\ m\\ n\\ o\\ p"
        f"/{checkout}/../eval/tasks ",
        "commas": f", ./{checkout}/./eval/tasks",
        "home and msys tokens": " ~/eval/tasks /c/eval/tasks /mnt/c/x/../eval/tasks",
        "long folder full of spaces": " a" * 2000 + f"/{checkout}/eval/tasks ",
        "escapes, no checkout": "a\\ b/../a b/../a b/../eval/tasks ",
        "slashes after eval": "eval" + "/" * 5000,
    }


def _scan_seconds(text, workdir):
    return _timed(_gaia(_tool("write_file", {"content": text}, "ok")), workdir)


def _assert_linear(unit, workdir):
    """1 MB takes ~4x the time of 256 KB, not the 16x of a quadratic scan.

    The slack is scaled by a same-size text with a single mention, which costs
    what any 1 MB call does, so a slow runner widens it in proportion.
    """
    small, big = ((unit * (n // len(unit) + 1))[:n] for n in (256_000, 1_000_000))
    _w, t_trivial = _scan_seconds("w" * (len(big) - 10) + "eval/tasks", workdir)
    why_small, t_small = _scan_seconds(small, workdir)
    why_big, t_big = _scan_seconds(big, workdir)
    assert why_small == why_big == ""
    assert t_big < FAST_ENOUGH, f"{t_big:.2f}s for 1 MB"
    slack = 0.25 + 10 * t_trivial
    assert t_big < 8 * t_small + slack, f"{t_small:.3f}s -> {t_big:.3f}s"


@pytest.mark.parametrize("label", list(_adversarial_units("c")))
def test_adversarial_mentions_scan_in_linear_time(label):
    checkout = ft.TASKS_DIR.parent.parent
    unit = _adversarial_units(checkout.name)[label]
    _assert_linear(unit, checkout.parent / "tmp-run" / "toybox")


@pytest.mark.parametrize(
    "unit",
    [
        " a" * 6 + " ./gaia/eval/tasks",
        " '\"( ./gaia/./eval/tasks",
        "../../../../../gaia/eval/tasks",
    ],
)
def test_a_checkout_under_another_eval_tasks_still_scans_in_linear_time(
    monkeypatch, unit
):
    # A path holding ``eval/tasks`` twice, so a walk must cross a mention.
    deep = "/".join(f"deep_folder_name_{i:02d}" for i in range(10))
    monkeypatch.setattr(
        ft, "TASKS_DIR", Path(f"C:/Users/x/{deep}/eval/tasks/gaia/eval/tasks")
    )
    monkeypatch.setattr(
        os.path, "expanduser", lambda p: "C:/Users/x" if p == "~" else p
    )
    _assert_linear(
        unit, Path("C:/Users/x/" + "/".join(f"wd_{i:02d}" for i in range(10)))
    )
    key = f"C:/Users/x/{deep}/eval/tasks/gaia/eval/tasks/tasks.json"
    why, _s = _scan_seconds(f"cat {key.replace('/gaia/', '/gaia/x/../')}", None)
    assert why.startswith("write_file named ")


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
