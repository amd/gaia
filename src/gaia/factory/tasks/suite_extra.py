# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The rest of the task suite, widening coverage to the real use-case mix.

The first eight tasks proved the harness works but sat almost entirely in
``build``, ``verify`` and ``decide``. The corpus taxonomy has 24 use cases
across 7 tracks, and the tracks this omitted — ``operate`` most of all — are not
niche: moving work through a system and keeping it running is a large share of
what the agent is actually asked to do.

These live in their own module for readability only; :mod:`.suite` concatenates
them and remains the single entry point. Every task here obeys the same
contract, and every one is held to the same two-sided proof in
``tests/unit/factory/test_task_suite.py``: its verifier must reject the
untouched setup and accept a hand-written correct solution.

**Selection rule.** A use case earns a task only if its outcome can be checked
by a command in a sealed workspace. ``web_research`` needs the network,
``pr_triage`` needs a live forge, and ``slide_deck`` has no mechanical notion of
correct — including them would mean grading on a judge's opinion alone, which is
precisely what this suite exists to stop doing. They are listed as excluded in
the catalogue rather than quietly missing.
"""

from __future__ import annotations

from typing import Sequence

from .suite import Task

# --------------------------------------------------------------------- build

_SECURITY_APP = '''\
import sqlite3


def find_user(conn, name):
    """Look up a user by name."""
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM users WHERE name = '%s'" % name)
    return cur.fetchone()


def read_note(base_dir, filename):
    """Read a note from the notes directory."""
    import os

    return open(os.path.join(base_dir, filename)).read()
'''

_SECURITY_TEST = """\
import os
import sqlite3

import pytest

from app import find_user, read_note


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    c.execute("INSERT INTO users (name) VALUES ('alice')")
    c.execute("INSERT INTO users (name) VALUES ('bob')")
    c.commit()
    return c


def test_lookup_still_works(conn):
    assert find_user(conn, "alice")[1] == "alice"


def test_name_with_quote_is_not_a_syntax_error(conn):
    # A legitimate name containing an apostrophe must not blow up the query.
    assert find_user(conn, "O'Brien") is None


def test_injection_cannot_return_another_row(conn):
    # The classic payload. Parameterised, it matches no user at all.
    assert find_user(conn, "' OR '1'='1") is None


def test_note_read_still_works(tmp_path):
    (tmp_path / "note.txt").write_text("hello")
    assert read_note(str(tmp_path), "note.txt") == "hello"


def test_traversal_is_refused(tmp_path):
    (tmp_path / "note.txt").write_text("hello")
    secret = tmp_path.parent / "secret.txt"
    secret.write_text("TOPSECRET")
    with pytest.raises(Exception):
        read_note(str(tmp_path), "../" + secret.name)
"""

BUILD: Sequence[Task] = (
    Task(
        key="security_fix_injection",
        use_case="security_fix",
        track="build",
        prompt=(
            "test_app.py is failing on two security tests. Fix app.py so all "
            "tests pass, without weakening what already works or editing the "
            "tests."
        ),
        setup={"app.py": _SECURITY_APP, "test_app.py": _SECURITY_TEST},
        verify="python -m pytest test_app.py -q",
        rubric=(
            "Is the SQL parameterised rather than escaped by hand? Is the path "
            "check a real containment check rather than a substring ban on '..'?"
        ),
        expect_touched=("app.py",),
    ),
    Task(
        key="dependency_upgrade_api",
        use_case="dependency_upgrade",
        track="build",
        prompt=(
            "We upgraded the vendored `tinyhttp` library and the app no longer "
            "works. Read its CHANGELOG, update app.py to the new API, and make "
            "the tests pass. Do not edit the library or the tests."
        ),
        setup={
            "tinyhttp.py": '''\
"""tinyhttp 2.0 — minimal request helper."""


class Response:
    def __init__(self, status, body):
        self.status = status
        self.body = body


def request(method, url, *, headers=None, body=None, timeout=10):
    """Perform a request. Returns a Response.

    Added in 2.0. Replaces the 1.x get()/post() pair.
    """
    return Response(200, f"{method} {url}")
''',
            "CHANGELOG.md": """\
# tinyhttp changelog

## 2.0.0

**Breaking.** `get(url)` and `post(url, data)` are removed. Use
`request(method, url, body=...)` instead. The response attribute `code` is
renamed to `status`, and `text` is renamed to `body`.

## 1.4.0

Added `post()`.
""",
            "app.py": """\
import tinyhttp


def fetch_title(url):
    resp = tinyhttp.get(url)
    if resp.code != 200:
        raise RuntimeError(f"bad status {resp.code}")
    return resp.text


def submit(url, payload):
    resp = tinyhttp.post(url, payload)
    return resp.code
""",
            "test_app.py": """\
from app import fetch_title, submit


def test_fetch_uses_the_new_api():
    assert fetch_title("http://x/y") == "GET http://x/y"


def test_submit_returns_status():
    assert submit("http://x/y", "data") == 200
""",
        },
        verify="python -m pytest test_app.py -q",
        rubric=(
            "Did it use the documented replacement API, or reimplement the old "
            "one as a shim? Are both call sites updated consistently?"
        ),
        expect_touched=("app.py",),
    ),
)

# -------------------------------------------------------------------- verify

VERIFY: Sequence[Task] = (
    Task(
        key="doc_audit_drift",
        use_case="doc_audit",
        track="verify",
        prompt=(
            "README.md describes this tool's command-line flags. Some of it is "
            "wrong. Check it against cli.py and correct the README so it matches "
            "the code. Do not change cli.py."
        ),
        setup={
            "cli.py": """\
import argparse


def build_parser():
    p = argparse.ArgumentParser(prog="squish", description="Compress a folder.")
    p.add_argument("source", help="folder to compress")
    p.add_argument("--output", "-o", help="destination file")
    p.add_argument("--level", type=int, default=6, help="compression level 1-9")
    p.add_argument("--quiet", action="store_true", help="suppress progress")
    return p
""",
            "README.md": """\
# squish

Compress a folder.

## Usage

    squish SOURCE --output FILE

## Flags

- `--output`, `-o` — destination file
- `--level` — compression level 1-9 (default: 9)
- `--verbose` — print detailed progress
- `--threads` — number of worker threads
""",
        },
        # Three specific drifts: a wrong default, a flag that does not exist,
        # and a second flag that does not exist. The real one it omits
        # (`--quiet`) must be added. Checked by content, not by diff, so any
        # correct rewrite passes.
        verify=(
            'python -c "'
            "t=open('README.md',encoding='utf-8').read();"
            "low=t.lower();"
            "assert '--threads' not in low, 'still documents the nonexistent --threads';"
            "assert '--verbose' not in low, 'still documents the nonexistent --verbose';"
            "assert '--quiet' in low, 'never documents the real --quiet flag';"
            "import re;"
            "m=re.search(r'--level[^\\\\n]*', t);"
            "assert m and '9)' not in m.group(0), 'still claims the default level is 9'"
            '"'
        ),
        rubric=(
            "Did it correct every drift and invent nothing new? Is the surviving "
            "prose still readable, or stitched together?"
        ),
        expect_touched=("README.md",),
    ),
    Task(
        key="test_repair_suite",
        use_case="test_authoring",
        track="verify",
        prompt=(
            "The tests in test_money.py are broken — they were written against "
            "an older API. Update the tests so they pass against money.py as it "
            "is now. Do not change money.py; it is correct."
        ),
        setup={
            "money.py": '''\
from dataclasses import dataclass


@dataclass(frozen=True)
class Money:
    """An amount in minor units (cents), with a currency."""

    cents: int
    currency: str = "USD"

    def add(self, other):
        if other.currency != self.currency:
            raise ValueError("currency mismatch")
        return Money(self.cents + other.cents, self.currency)

    def format(self):
        return f"{self.cents // 100}.{self.cents % 100:02d} {self.currency}"
''',
            "test_money.py": """\
from money import Money


def test_add():
    # Old API took floats and a positional currency.
    assert Money(1.50, "USD").add(Money(2.25, "USD")).amount == 3.75


def test_format():
    assert Money(1.50, "USD").to_string() == "$1.50"
""",
        },
        verify=(
            "python -m pytest test_money.py -q && "
            'python -c "'
            "import hashlib;"
            "h=hashlib.sha256(open('money.py','rb').read()).hexdigest();"
            "assert h=='"
            "5dd37de2933f3ff51c1edc5cf1f78bbea0bec59622f001dd38c511216aad84e6"
            "', 'money.py was modified; the task was to fix the tests'"
            '"'
        ),
        rubric=(
            "Do the updated tests still assert the same behaviour, or were they "
            "weakened until they passed? Is anything now untested?"
        ),
        expect_touched=("test_money.py",),
    ),
)

# ------------------------------------------------------------------- operate

OPERATE: Sequence[Task] = (
    Task(
        key="ci_failure_triage",
        use_case="ci_failure_triage",
        track="operate",
        prompt=(
            "The CI job in ci.log failed. Work out which test failed and why, "
            "then write the failing test's name and the one-line root cause to "
            "diagnosis.txt."
        ),
        setup={
            "ci.log": """\
Run pytest -q
============================= test session starts ==============================
collected 42 items

tests/test_auth.py ......                                                [ 14%]
tests/test_cache.py .........                                            [ 35%]
tests/test_parser.py ....F...                                            [ 54%]
tests/test_render.py ..................                                  [100%]

=================================== FAILURES ===================================
______________________ test_parses_iso_timestamp_with_tz _______________________

    def test_parses_iso_timestamp_with_tz():
        got = parse_stamp("2026-03-07T12:00:00+02:00")
>       assert got.tzinfo is not None
E       AssertionError: assert None is not None
E        +  where None = datetime.datetime(2026, 3, 7, 12, 0).tzinfo

tests/test_parser.py:88: AssertionError
=========================== short test summary info ============================
FAILED tests/test_parser.py::test_parses_iso_timestamp_with_tz - AssertionError
1 failed, 41 passed in 3.21s
""",
        },
        verify=(
            'python -c "'
            "t=open('diagnosis.txt',encoding='utf-8').read().lower();"
            "assert 'test_parses_iso_timestamp_with_tz' in t, 'did not name the failing test';"
            "assert 'tzinfo' in t or 'timezone' in t or 'tz' in t, 'did not identify the cause'"
            '"'
        ),
        rubric=(
            "Is the root cause the actual one (the offset is dropped, so the "
            "parsed datetime is naive) rather than a restatement of the "
            "assertion? Is it one line, as asked?"
        ),
        expect_touched=("diagnosis.txt",),
    ),
    Task(
        key="release_cut_version",
        use_case="release_cut",
        track="operate",
        prompt=(
            "Cut a 1.3.0 release: bump the version wherever it is declared and "
            "add a CHANGELOG entry for it covering what landed since 1.2.0. The "
            "merged changes are listed in merged.txt."
        ),
        setup={
            "pyproject.toml": (
                '[project]\nname = "widget"\nversion = "1.2.0"\n'
                'requires-python = ">=3.11"\n'
            ),
            "widget/__init__.py": '__version__ = "1.2.0"\n',
            "CHANGELOG.md": """\
# Changelog

## 1.2.0

- Added `--quiet` flag.
- Fixed a crash on empty input.
""",
            "merged.txt": (
                "- Support for gzip-compressed inputs\n"
                "- Fix: timestamps lost their timezone offset\n"
                "- Drop Python 3.10 support\n"
            ),
        },
        # Both declaration sites must move, and the changelog must actually
        # mention the changes rather than just carrying a heading.
        verify=(
            'python -c "'
            "p=open('pyproject.toml',encoding='utf-8').read();"
            "i=open('widget/__init__.py',encoding='utf-8').read();"
            "c=open('CHANGELOG.md',encoding='utf-8').read();"
            "assert '1.3.0' in p and '1.2.0' not in p, 'pyproject not bumped';"
            "assert '1.3.0' in i and '1.2.0' not in i, '__init__ not bumped';"
            "assert '1.3.0' in c, 'no changelog entry for 1.3.0';"
            "low=c.lower();"
            "assert 'gzip' in low, 'changelog omits the gzip change';"
            "assert 'timezone' in low or 'timestamp' in low, 'changelog omits the fix';"
            "assert '3.10' in c, 'changelog omits the dropped Python version'"
            '"'
        ),
        rubric=(
            "Is the changelog entry written for a reader, in the file's existing "
            "style, or is it the input list pasted in? Was anything else "
            "touched?"
        ),
        expect_touched=("pyproject.toml", "widget/__init__.py", "CHANGELOG.md"),
    ),
    Task(
        key="env_setup_broken_imports",
        use_case="env_setup",
        track="operate",
        prompt=(
            "`python -m toybox` is supposed to print a report but it crashes. "
            "Work out why and fix it so the command runs and prints the report."
        ),
        setup={
            # Missing __main__.py is the real defect: the package exists but
            # cannot be run as a module.
            "toybox/__init__.py": "",
            "toybox/report.py": '''\
def build_report(rows):
    """Return a one-line summary of *rows*."""
    total = sum(r["amount"] for r in rows)
    return f"{len(rows)} rows, total {total}"


ROWS = [{"amount": 10}, {"amount": 32}]
''',
            "README.md": ("# toybox\n\nRun `python -m toybox` to print the report.\n"),
        },
        verify=(
            "python -m toybox > out.txt 2>&1 && "
            'python -c "'
            "t=open('out.txt',encoding='utf-8').read();"
            "assert '2 rows' in t and '42' in t, f'unexpected output: {t!r}'"
            '"'
        ),
        rubric=(
            "Did it add the missing entry point rather than work around it by "
            "changing what the command does? Is the entry point minimal?"
        ),
        expect_touched=("toybox/__main__.py",),
    ),
    Task(
        key="repo_ops_ignore_artifacts",
        use_case="repo_ops",
        track="operate",
        prompt=(
            "This repository has build artefacts and a secrets file committed by "
            "mistake. Stop tracking them and make sure they cannot be committed "
            "again. Do not delete the working copies."
        ),
        setup={
            ".gitignore": "*.pyc\n",
            "app.py": "print('hi')\n",
            "build/output.bin": "binary-ish",
            ".env": "API_KEY=abc123\n",
            "notes.txt": "keep me\n",
        },
        # A real git repo is created by the verifier's sibling: the setup writes
        # files, and this checks tracking state. `git add -A` at the start
        # establishes the mistaken commit the task describes.
        verify=(
            "git ls-files --error-unmatch .env >/dev/null 2>&1 && "
            "{ echo 'still tracking .env'; exit 1; }; "
            "git ls-files --error-unmatch build/output.bin >/dev/null 2>&1 && "
            "{ echo 'still tracking build artefact'; exit 1; }; "
            "git check-ignore -q .env || { echo '.env not ignored'; exit 1; }; "
            "git check-ignore -q build/output.bin || "
            "{ echo 'build/ not ignored'; exit 1; }; "
            "test -f .env || { echo 'working copy of .env was deleted'; exit 1; }; "
            "git ls-files --error-unmatch app.py >/dev/null 2>&1 || "
            "{ echo 'app.py was untracked by mistake'; exit 1; }; "
            "exit 0"
        ),
        rubric=(
            "Were the files untracked without being deleted? Is .gitignore "
            "specific, or did it ignore far more than asked?"
        ),
        expect_touched=(".gitignore",),
        git_init=True,
    ),
)

# ------------------------------------------------------------------- content

CONTENT: Sequence[Task] = (
    Task(
        key="meeting_notes_actions",
        use_case="meeting_transcript",
        track="content",
        prompt=(
            "Summarise the meeting in transcript.txt into notes.md. It must "
            "record the decisions that were made and who owns each action."
        ),
        setup={"transcript.txt": """\
[10:02] Priya: The release is blocked on the timezone bug. I think we hold 1.3.
[10:03] Sam: Agreed, holding. I'll take the timezone fix, should be a day.
[10:05] Priya: Do we still want gzip support in this release?
[10:06] Dana: It's done and tested. I'd ship it.
[10:07] Priya: OK, gzip goes in 1.3. Decision made.
[10:09] Sam: One more — we're dropping Python 3.10. Dana, can you update the docs?
[10:10] Dana: Yes, I'll do the docs for the 3.10 drop.
[10:12] Priya: Last thing, we are NOT doing the cache rewrite this quarter.
[10:13] Sam: Noted.
"""},
        verify=(
            'python -c "'
            "t=open('notes.md',encoding='utf-8').read();"
            "low=t.lower();"
            "missing=[k for k in ('sam','dana','gzip','timezone') if k not in low];"
            "assert not missing, f'notes omit: {missing}';"
            "assert '3.10' in t, 'notes omit the dropped Python version';"
            "assert 'cache' in low, 'notes omit the cache-rewrite decision'"
            '"'
        ),
        rubric=(
            "Are decisions separated from actions, and is every action attributed "
            "to the right person? Is the 'not doing the cache rewrite' recorded "
            "as a decision rather than dropped? Anything invented?"
        ),
        expect_touched=("notes.md",),
    ),
    Task(
        key="api_docs_public_surface",
        use_case="doc_authoring",
        track="content",
        prompt=(
            "Write API.md documenting every public function in geometry.py — "
            "what each one takes, returns, and raises. Private helpers do not "
            "belong in it."
        ),
        setup={"geometry.py": '''\
import math


def area_circle(radius):
    """Area of a circle. Raises ValueError for a negative radius."""
    if radius < 0:
        raise ValueError("radius must be non-negative")
    return math.pi * radius**2


def area_rectangle(width, height):
    """Area of a rectangle."""
    return width * height


def distance(a, b):
    """Euclidean distance between two (x, y) points."""
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _validate(value):
    """Internal. Not part of the public surface."""
    return value is not None
'''},
        verify=(
            'python -c "'
            "t=open('API.md',encoding='utf-8').read();"
            "missing=[n for n in ('area_circle','area_rectangle','distance') "
            "if n not in t];"
            "assert not missing, f'undocumented public functions: {missing}';"
            "assert '_validate' not in t, 'documents the private helper';"
            "assert 'ValueError' in t, 'does not mention the raised exception'"
            '"'
        ),
        rubric=(
            "Is each signature described accurately? Is the ValueError attached "
            "to the right function? Any invented parameters or behaviour?"
        ),
        expect_touched=("API.md",),
    ),
)

# ------------------------------------------------------------------- analyse

ANALYSE: Sequence[Task] = (
    Task(
        key="data_extraction_records",
        use_case="data_extraction",
        track="analyse",
        prompt=(
            "orders.txt contains order records in messy free text. Extract them "
            "into orders.json as a JSON array, one object per order, with keys "
            "`id`, `customer` and `total` (a number)."
        ),
        setup={"orders.txt": """\
Order #1001 -- customer: Acme Corp -- total $1,250.00
order 1002 for Globex; total: $89.99
#1003 | Initech | $17,500.50
Order #1004 — customer Umbrella Ltd — total $0.00
"""},
        verify=(
            'python -c "'
            "import json;"
            "d=json.load(open('orders.json',encoding='utf-8'));"
            "assert isinstance(d,list) and len(d)==4, f'expected 4 orders, got {len(d)}';"
            "by={str(o['id']).lstrip('#'):o for o in d};"
            "assert set(by)=={'1001','1002','1003','1004'}, f'ids wrong: {sorted(by)}';"
            "assert abs(float(by['1003']['total'])-17500.50)<0.01, 'wrong total for 1003';"
            "assert abs(float(by['1004']['total'])-0.0)<0.01, 'wrong total for 1004';"
            "assert 'globex' in str(by['1002']['customer']).lower(), 'wrong customer for 1002'"
            '"'
        ),
        rubric=(
            "Are totals real numbers rather than strings with currency symbols? "
            "Was the zero-total order kept rather than dropped as falsy?"
        ),
        expect_touched=("orders.json",),
    ),
    Task(
        key="data_join_regions",
        use_case="data_analysis",
        track="analyse",
        prompt=(
            "sales.csv has one row per sale with a store id. stores.csv maps "
            "store ids to regions. Work out which region has the highest total "
            "sales, and write just that region name to answer.txt."
        ),
        setup={
            "sales.csv": (
                "sale_id,store_id,amount\n"
                "1,S1,100\n2,S2,250\n3,S1,150\n4,S3,300\n"
                "5,S2,100\n6,S4,50\n7,S3,120\n8,S4,25\n"
            ),
            "stores.csv": (
                "store_id,region\n" "S1,North\nS2,South\nS3,South\nS4,North\n"
            ),
        },
        # North = 100+150+50+25 = 325; South = 250+100+300+120 = 770.
        verify=(
            'python -c "'
            "t=open('answer.txt',encoding='utf-8').read().strip().lower();"
            "assert t.startswith('south') or t=='south', f'expected South, got {t!r}'"
            '"'
        ),
        rubric=(
            "Did it actually join the two files, or guess from the larger "
            "individual sale? Did it verify the totals?"
        ),
        expect_touched=("answer.txt",),
    ),
)

# -------------------------------------------------------------------- decide

DECIDE: Sequence[Task] = (
    Task(
        key="issue_triage_severity",
        use_case="issue_triage",
        track="decide",
        prompt=(
            "issues.md lists four open issues. Assign each one a severity of "
            "p0, p1 or p2 and write the result to triage.md as lines of the form "
            "`<issue id>: <severity>`. p0 means someone is losing data or is "
            "locked out right now."
        ),
        setup={"issues.md": """\
## GH-101
Typo in the README: "recieve" should be "receive".

## GH-102
Every user is logged out after 30 seconds and cannot sign back in. Started
after this morning's deploy. Support queue is filling up.

## GH-103
Exporting a project silently drops the last row of every table. Users have
been shipping incomplete exports for at least a week.

## GH-104
Feature request: allow sorting the dashboard by column.
"""},
        # Parsed by scanning forward from each issue id to the first severity
        # token on the same line. Deliberately not a regex: this string passes
        # through Python, a shell, and `python -c`, and a pattern that survives
        # all three escaping layers is unreadable and easy to get silently wrong.
        verify=(
            'python -c "'
            "lines=open('triage.md',encoding='utf-8').read().lower().splitlines();"
            "sev={};"
            "[sev.setdefault(i, s) "
            "for ln in lines "
            "for i in ('gh-101','gh-102','gh-103','gh-104') if i in ln "
            "for s in ('p0','p1','p2') if s in ln.split(i,1)[1][:24]];"
            "missing=[k for k in ('gh-101','gh-102','gh-103','gh-104') if k not in sev];"
            "assert not missing, 'no severity found for '+str(missing);"
            "assert sev['gh-102']=='p0', 'lockout rated '+sev['gh-102'];"
            "assert sev['gh-103']=='p0', 'data loss rated '+sev['gh-103'];"
            "assert sev['gh-101']=='p2', 'typo rated '+sev['gh-101'];"
            "assert sev['gh-104']=='p2', 'request rated '+sev['gh-104']"
            '"'
        ),
        rubric=(
            "Is the reasoning stated and does it match the stated p0 rule? Did it "
            "resist rating the noisy lockout above the quiet data loss?"
        ),
        expect_touched=("triage.md",),
    ),
    Task(
        key="qa_multi_hop_owner",
        use_case="qa_conversational",
        track="decide",
        prompt=(
            "Who should review a change to the billing module, and what is their "
            "email? The answer is not in any single file. Write it to answer.txt."
        ),
        setup={
            "CODEOWNERS": "/src/billing/  @finance-team\n/src/ui/  @design-team\n",
            "TEAMS.md": """\
# Teams

| handle | lead | email |
|---|---|---|
| @design-team | Dana Reyes | dana@example.com |
| @finance-team | Priya Nair | priya@example.com |
| @infra-team | Sam Okafor | sam@example.com |
""",
            "README.md": "# app\n\nSee CODEOWNERS for review routing.\n",
        },
        # Checks exactly what was asked: the right person and their email. It
        # also demanded the owning team's name, which the prompt never asks
        # for — an agent that answered "Priya Nair, priya@example.com" was
        # marked wrong for not volunteering a fact nobody requested. Whether
        # the route is shown is a quality question, and it stays in the rubric.
        verify=(
            'python -c "'
            "t=open('answer.txt',encoding='utf-8').read().lower();"
            "assert 'priya@example.com' in t, 'wrong or missing email';"
            "assert 'priya' in t, 'did not name the reviewer'"
            '"'
        ),
        rubric=(
            "Did it show the two-step route (path → team → lead), or assert the "
            "answer? Is anything about the other teams included needlessly?"
        ),
        expect_touched=("answer.txt",),
    ),
    Task(
        key="planning_migration_steps",
        use_case="planning",
        track="decide",
        prompt=(
            "We need to move from the `legacy_store` module to `new_store` "
            "without downtime. Write plan.md with the ordered steps, what could "
            "go wrong at each, and how we would roll back."
        ),
        setup={
            "legacy_store.py": '''\
"""The current store. Synchronous, writes straight to disk."""


def put(key, value):
    with open(f"data/{key}", "w") as fh:
        fh.write(value)


def get(key):
    return open(f"data/{key}").read()
''',
            "new_store.py": '''\
"""The replacement. Writes through a cache and supports batching."""


def put(key, value, *, batch=None):
    raise NotImplementedError


def get(key):
    raise NotImplementedError
''',
        },
        # Structure only. Whether the plan is *good* is the rubric's job — no
        # regex can judge that, and pretending otherwise would be worse than
        # admitting the split.
        verify=(
            'python -c "'
            "t=open('plan.md',encoding='utf-8').read();"
            "low=t.lower();"
            "assert len(t)>400, 'plan is too short to be actionable';"
            "assert 'roll back' in low or 'rollback' in low, 'no rollback described';"
            "import re;"
            "steps=re.findall(r'^\\\\s*(?:[-*]|\\\\d+[.)])\\\\s+', t, re.M);"
            "assert len(steps)>=4, f'only {len(steps)} discrete steps'"
            '"'
        ),
        rubric=(
            "Is this a real migration plan — dual-write, backfill, verify, cut "
            "over — or a generic checklist that would fit any project? Is the "
            "rollback specific to the step it follows?"
        ),
        expect_touched=("plan.md",),
    ),
)

# --------------------------------------------------------------------- agent

AGENT: Sequence[Task] = (
    Task(
        key="agent_config_manifest",
        use_case="agent_config",
        track="agent",
        prompt=(
            "agent.yaml fails to load. Fix it so it parses and declares the "
            "fields the loader requires, which are listed in loader.py."
        ),
        setup={
            "loader.py": '''\
import yaml

REQUIRED = ("name", "version", "entrypoint", "tools")


def load(path="agent.yaml"):
    """Load and validate an agent manifest."""
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    missing = [k for k in REQUIRED if k not in data]
    if missing:
        raise ValueError(f"manifest missing required fields: {missing}")
    if not isinstance(data["tools"], list):
        raise TypeError("tools must be a list")
    return data
''',
            # Broken two ways: a tab-indented line (invalid YAML) and a missing
            # required field.
            "agent.yaml": 'name: sample-agent\nversion: "0.1.0"\ntools:\n\t- read_file\n',
        },
        verify=(
            'python -c "'
            "import loader;"
            "d=loader.load();"
            "assert d['name'] and d['version'] and d['entrypoint'];"
            "assert isinstance(d['tools'], list) and d['tools']"
            '"'
        ),
        rubric=(
            "Did it fix the YAML indentation properly rather than collapsing the "
            "list inline? Is the added entrypoint plausible for this manifest?"
        ),
        expect_touched=("agent.yaml",),
    ),
)

EXTRA: Sequence[Task] = BUILD + VERIFY + OPERATE + CONTENT + ANALYSE + DECIDE + AGENT

#: Use cases from the corpus taxonomy with no task here, and why. Kept beside
#: the suite so the catalogue reports real coverage instead of implying that
#: what is absent simply was not thought of.
NOT_COVERED = {
    "eval_benchmark": "running an eval inside an eval; the outer result would be "
    "dominated by the inner harness rather than the model, so it measures the "
    "wrong thing rather than merely being hard to measure",
}
