# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tasks the agent actually performs, with outcomes a machine can check.

The step-level benchmark asks "was that a good next move?" — one inference
against a frozen snapshot, judged by opinion. It cannot answer the question that
matters: **did the work get done?**

A task here is different in kind. It creates a real workspace on disk, hands the
agent a real request, lets the agent loop run to completion, and then **runs a
command** to decide whether the result is correct. A test passes or it does not.
The file compiles or it does not. No judge is consulted for that verdict.

Quality is separate and stays a judgement — "is this code something you would
accept" cannot be reduced to an exit code — so the two are scored and reported
apart, never blended into one number.

Each task states:

* ``setup`` — files written into a fresh temporary workspace before the run.
* ``prompt`` — what the agent is asked, in the user's words.
* ``verify`` — a shell command run afterwards **in that workspace**. Exit code 0
  means the task was accomplished. This is the correctness signal.
* ``rubric`` — what a reviewer should judge the output on. The quality signal.

Tasks are deliberately small. A benchmark that takes an hour per task cannot be
run often enough to catch a regression, and the failure modes worth measuring —
wrong tool, guessed path, declaring success without checking — show up in the
first few steps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence


@dataclass(frozen=True)
class Task:
    """One unit of real work with a machine-checkable outcome."""

    key: str
    use_case: str
    track: str
    prompt: str
    #: path -> contents, written before the agent starts.
    setup: Dict[str, str]
    #: Shell command run in the workspace afterwards. Exit 0 == accomplished.
    verify: str
    #: What a human (or judge) should assess beyond mere correctness.
    rubric: str
    #: Files the task is expected to produce or change, for reporting.
    expect_touched: Sequence[str] = field(default_factory=tuple)
    #: Initialise the workspace as a git repo with the setup files committed.
    #: Required by tasks about repository mechanics — untracking a file is not
    #: a meaningful request outside a repo where it is tracked.
    git_init: bool = False
    #: Further turns delivered after the first, in order, on the same agent with
    #: the conversation carried over. Real work is rarely one turn: several of
    #: the longest tasks in the corpus begin "continue" or change the
    #: requirement halfway through, and an agent that cannot resume its own
    #: thread fails them for a reason a single-turn benchmark never sees.
    follow_ups: Sequence[str] = field(default_factory=tuple)
    #: Seconds before this task is killed. ``None`` uses the runner default.
    #: Long-horizon tasks need minutes, and a timeout tuned for a 5-step task
    #: would score them as failures of speed rather than of capability.
    timeout_s: Optional[int] = None
    #: ``verified`` — the verifier decides, and its verdict is the result.
    #: ``judged``  — no command can decide this one, so ``verify`` is only a
    #: floor (the artefact exists and is substantial) and the real assessment is
    #: the judge's. Kept explicit and reported in its own column, because a
    #: judged pass and a verified pass are different claims and averaging them
    #: would quietly weaken every verified number in the table.
    scoring: str = "verified"

    @property
    def judged_only(self) -> bool:
        return self.scoring == "judged"


_PARSER_BUG = '''\
def parse_config(text):
    """Parse KEY=VALUE lines into a dict. Blank lines and # comments are skipped."""
    out = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip()
    return out
'''

_PARSER_TEST = """\
from config import parse_config


def test_basic():
    assert parse_config("A=1\\nB=2") == {"A": "1", "B": "2"}


def test_comments_and_blanks():
    assert parse_config("# c\\n\\nA=1") == {"A": "1"}


def test_value_containing_equals():
    # A connection string legitimately contains '='. This must not crash or
    # truncate — the value keeps everything after the FIRST separator.
    assert parse_config("DSN=host=db;port=5432") == {"DSN": "host=db;port=5432"}


def test_empty_input_returns_empty_dict():
    assert parse_config("") == {}


def test_line_without_separator_is_ignored():
    # A stray word on its own line is malformed, not a crash.
    assert parse_config("JUSTAWORD\\nA=1") == {"A": "1"}
"""

TASKS: Sequence[Task] = (
    Task(
        key="bug_fix_parser",
        use_case="bug_fix",
        track="build",
        prompt=(
            "The tests in test_config.py are failing. Run them, work out why, "
            "and fix config.py so they all pass. Do not edit the tests."
        ),
        setup={"config.py": _PARSER_BUG, "test_config.py": _PARSER_TEST},
        verify="python -m pytest test_config.py -q",
        rubric=(
            "Is the fix minimal and correct, or did it special-case the failing "
            "test? Does it still handle the cases that already worked?"
        ),
        expect_touched=("config.py",),
    ),
    Task(
        key="feature_dry_run",
        use_case="feature_impl",
        track="build",
        prompt=(
            "Add a --dry-run flag to cleanup.py. With the flag, it should print "
            "exactly what it would delete and delete nothing. Without it, "
            "behaviour must be unchanged."
        ),
        setup={
            "cleanup.py": '''\
import os
import sys


def cleanup(directory, dry_run=False):
    """Delete .tmp files in *directory*."""
    removed = []
    for name in sorted(os.listdir(directory)):
        if name.endswith(".tmp"):
            path = os.path.join(directory, name)
            os.remove(path)
            removed.append(name)
    return removed


if __name__ == "__main__":
    for name in cleanup(sys.argv[1] if len(sys.argv) > 1 else "."):
        print(f"removed {name}")
''',
            "test_cleanup.py": """\
import os
from cleanup import cleanup


def _fixture(tmp_path):
    (tmp_path / "a.tmp").write_text("x")
    (tmp_path / "b.txt").write_text("y")
    return tmp_path


def test_dry_run_deletes_nothing(tmp_path):
    d = _fixture(tmp_path)
    cleanup(str(d), dry_run=True)
    assert (d / "a.tmp").exists(), "--dry-run must not delete"


def test_dry_run_reports_what_it_would_delete(tmp_path):
    d = _fixture(tmp_path)
    assert cleanup(str(d), dry_run=True) == ["a.tmp"]


def test_normal_mode_unchanged(tmp_path):
    d = _fixture(tmp_path)
    assert cleanup(str(d)) == ["a.tmp"]
    assert not (d / "a.tmp").exists()
    assert (d / "b.txt").exists()
""",
        },
        verify="python -m pytest test_cleanup.py -q",
        rubric=(
            "Does the flag follow the file's existing style? Is the no-flag path "
            "genuinely untouched? Any needless refactoring?"
        ),
        expect_touched=("cleanup.py",),
    ),
    Task(
        key="test_authoring_slug",
        use_case="test_authoring",
        track="verify",
        prompt=(
            "slugify.py has no tests. Write tests for it in test_slugify.py. "
            "They must actually exercise the function — a test that passes "
            "against a broken implementation is worthless."
        ),
        setup={
            "slugify.py": '''\
import re


def slugify(text):
    """Lowercase, strip punctuation, collapse whitespace to single hyphens."""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\\s-]", "", text)
    text = re.sub(r"[\\s-]+", "-", text)
    return text.strip("-")
''',
        },
        # Two-sided on purpose. The tests must pass against the real
        # implementation *and* fail once it is replaced by a stub returning its
        # input unchanged. Tests that only manage the first are the classic
        # failure of this use case: they assert nothing and can never go red.
        # Clobbering slugify.py needs no restore — the workspace is discarded
        # after verification, and artefacts were captured before it ran.
        verify=(
            "python -m pytest test_slugify.py -q && "
            "printf 'def slugify(text):\\n    return text\\n' > slugify.py && "
            "! python -m pytest test_slugify.py -q"
        ),
        rubric=(
            "Do the tests cover real behaviour (punctuation, spacing, casing, "
            "edges) or only the happy path? Are they readable?"
        ),
        expect_touched=("test_slugify.py",),
    ),
    Task(
        key="data_analysis_builds",
        use_case="data_analysis",
        track="analyse",
        prompt=(
            "builds.csv has one row per CI build. Work out the mean duration in "
            "seconds for builds that FAILED, and write just that number — "
            "nothing else — to answer.txt."
        ),
        setup={
            "builds.csv": (
                "id,status,duration_s\n"
                "1,passed,120\n2,failed,300\n3,passed,110\n"
                "4,failed,500\n5,passed,130\n6,failed,400\n"
            ),
        },
        # (300+500+400)/3 = 400. Exact, reproducible from the input — which is
        # the pass criterion this use case was written with.
        verify=(
            'python -c "'
            "v=open('answer.txt').read().strip().replace(',','');"
            "assert abs(float(v)-400.0)<0.01, f'expected 400.0, got {v!r}'"
            '"'
        ),
        rubric=(
            "Is the number right, and is it derived from the data rather than "
            "guessed? Did the agent verify its own arithmetic?"
        ),
        expect_touched=("answer.txt",),
    ),
    Task(
        key="refactor_duplication",
        use_case="refactor",
        track="build",
        prompt=(
            "report.py has three functions that each format a date the same way. "
            "Remove the duplication without changing behaviour. The tests must "
            "still pass."
        ),
        setup={
            "report.py": """\
def daily_header(d):
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d} daily"


def weekly_header(d):
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d} weekly"


def monthly_header(d):
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d} monthly"
""",
            "test_report.py": """\
import datetime
from report import daily_header, weekly_header, monthly_header

D = datetime.date(2026, 3, 7)


def test_headers():
    assert daily_header(D) == "2026-03-07 daily"
    assert weekly_header(D) == "2026-03-07 weekly"
    assert monthly_header(D) == "2026-03-07 monthly"
""",
        },
        # "Tests pass" alone cannot verify a refactor — they passed beforehand
        # too, so it would hand every arm a free point. Correctness here is two
        # conditions: behaviour preserved AND the duplication gone. The second
        # is checked structurally, by counting how many places still build the
        # date. Any genuine fix satisfies it, including ones that sidestep the
        # helper entirely via strftime or isoformat.
        verify=(
            "python -m pytest test_report.py -q && "
            'python -c "'
            "src=open('report.py',encoding='utf-8').read();"
            "n=src.count('.year');"
            "assert n<=1, f'the date is still built in {n} places — not deduplicated'"
            '"'
        ),
        rubric=(
            "Was the duplication actually removed, or just moved? Is the result "
            "simpler than what it replaced?"
        ),
        expect_touched=("report.py",),
    ),
    Task(
        key="doc_authoring_readme",
        use_case="doc_authoring",
        track="content",
        prompt=(
            "Write a README.md for this tool. It must state what the tool does, "
            "show how to install it, and show a usage example."
        ),
        setup={
            "pyproject.toml": (
                '[project]\nname = "tickle"\nversion = "0.3.0"\n'
                'description = "Watch files and run a command when they change."\n'
            ),
            "tickle.py": '''\
"""Watch a directory and run a command whenever a file changes."""


def watch(path, command, interval=1.0):
    """Run *command* whenever any file under *path* changes."""
''',
        },
        # Mechanical floor only: the file exists and covers the three required
        # subjects. Whether it is *good* is the rubric's job, not grep's.
        verify=(
            'python -c "'
            "t=open('README.md',encoding='utf-8').read().lower();"
            "missing=[k for k in ('install','tickle') if k not in t];"
            "assert not missing, f'README missing: {missing}';"
            "assert len(t)>200, 'README is too short to be useful'"
            '"'
        ),
        rubric=(
            "Is it accurate about what the tool does? Is the usage example one a "
            "reader could actually run? Anything invented?"
        ),
        expect_touched=("README.md",),
    ),
    Task(
        key="code_review_planted",
        use_case="code_review",
        track="verify",
        prompt=(
            "Review auth.py and write your findings to review.md. Report the real "
            "defects you find and nothing you are not confident about."
        ),
        setup={
            "auth.py": """\
import hashlib


def hash_password(password, salt):
    # Defect 1: MD5 is not suitable for password hashing.
    return hashlib.md5((salt + password).encode()).hexdigest()


def verify(stored, password, salt):
    # Defect 2: == on digests is not constant time.
    return stored == hash_password(password, salt)


def get_user(db, name):
    # Defect 3: string-formatted SQL.
    return db.execute("SELECT * FROM users WHERE name = '%s'" % name)
""",
        },
        # Recall on the planted defects. Precision is the rubric's job — this
        # cannot mechanically tell an invented finding from a real one.
        verify=(
            'python -c "'
            "t=open('review.md',encoding='utf-8').read().lower();"
            "hits=sum(k in t for k in ('md5','constant','inject'));"
            "assert hits>=2, f'found {hits} of 3 planted defects'"
            '"'
        ),
        rubric=(
            "Did it find all three defects? Did it invent any? Are the fixes it "
            "suggests correct and proportionate?"
        ),
        expect_touched=("review.md",),
    ),
    Task(
        key="qa_repo_purpose",
        use_case="qa_conversational",
        track="decide",
        prompt=(
            "What Python version does this project require, and where did you "
            "find it? Write the answer to answer.txt."
        ),
        setup={
            "pyproject.toml": (
                '[project]\nname = "widget"\nversion = "1.0.0"\n'
                'requires-python = ">=3.11"\n'
            ),
            "README.md": "# widget\n\nA small widget library.\n",
        },
        verify=(
            'python -c "'
            "t=open('answer.txt',encoding='utf-8').read().lower();"
            "assert '3.11' in t, 'did not state the version';"
            "assert 'pyproject' in t, 'did not say where it found it'"
            '"'
        ),
        rubric=(
            "Is it correct and brief? Does it cite where the answer came from "
            "rather than asserting it?"
        ),
        expect_touched=("answer.txt",),
    ),
)


def _all_tasks() -> Sequence[Task]:
    """The full suite. Imported late so ``suite_extra`` can import ``Task``."""
    from .suite_extra import EXTRA
    from .suite_judged import JUDGED

    return TASKS + tuple(EXTRA) + tuple(JUDGED)


TASKS = _all_tasks()
BY_KEY: Dict[str, Task] = {t.key: t for t in TASKS}


def tasks_for(tracks: Sequence[str] = (), use_cases: Sequence[str] = ()) -> List[Task]:
    """Filter the suite. Empty filters mean everything."""
    out = list(TASKS)
    if tracks:
        out = [t for t in out if t.track in tracks]
    if use_cases:
        out = [t for t in out if t.use_case in use_cases]
    return out
