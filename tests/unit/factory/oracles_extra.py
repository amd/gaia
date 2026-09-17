# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Hand-written correct solutions for the widened task suite.

One per task in :mod:`gaia.factory.tasks.suite_extra`. These are the files a
competent engineer would leave behind, and they exist to prove the verifiers
accept real work: if a verifier rejects one of these, the verifier is wrong,
not the solution.

Kept beside the tests rather than beside the tasks on purpose. A solution
shipped next to its problem is a solution the agent could read.
"""

ORACLES_EXTRA = {
    # ----------------------------------------------------------------- build
    "security_fix_injection": {
        "app.py": '''\
import os
import sqlite3


def find_user(conn, name):
    """Look up a user by name."""
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM users WHERE name = ?", (name,))
    return cur.fetchone()


def read_note(base_dir, filename):
    """Read a note from the notes directory."""
    base = os.path.realpath(base_dir)
    target = os.path.realpath(os.path.join(base, filename))
    if os.path.commonpath([base, target]) != base:
        raise ValueError("path escapes the notes directory")
    return open(target).read()
''',
    },
    "dependency_upgrade_api": {
        "app.py": """\
import tinyhttp


def fetch_title(url):
    resp = tinyhttp.request("GET", url)
    if resp.status != 200:
        raise RuntimeError(f"bad status {resp.status}")
    return resp.body


def submit(url, payload):
    resp = tinyhttp.request("POST", url, body=payload)
    return resp.status
""",
    },
    # ---------------------------------------------------------------- verify
    "doc_audit_drift": {
        "README.md": """\
# squish

Compress a folder.

## Usage

    squish SOURCE --output FILE

## Flags

- `--output`, `-o` — destination file
- `--level` — compression level 1-9 (default: 6)
- `--quiet` — suppress progress
""",
    },
    "test_repair_suite": {
        "test_money.py": """\
from money import Money


def test_add():
    assert Money(150).add(Money(225)) == Money(375)


def test_add_rejects_mismatched_currency():
    import pytest

    with pytest.raises(ValueError):
        Money(150, "USD").add(Money(100, "EUR"))


def test_format():
    assert Money(150).format() == "1.50 USD"
""",
    },
    # --------------------------------------------------------------- operate
    "ci_failure_triage": {
        "diagnosis.txt": (
            "test_parses_iso_timestamp_with_tz: the parser drops the +02:00 "
            "offset, so it returns a naive datetime with tzinfo set to None.\n"
        ),
    },
    "release_cut_version": {
        "pyproject.toml": (
            '[project]\nname = "widget"\nversion = "1.3.0"\n'
            'requires-python = ">=3.11"\n'
        ),
        "widget/__init__.py": '__version__ = "1.3.0"\n',
        "CHANGELOG.md": """\
# Changelog

## 1.3.0

- Added support for gzip-compressed inputs.
- Fixed timestamps losing their timezone offset.
- Dropped support for Python 3.10.

## 1.2.0

- Added `--quiet` flag.
- Fixed a crash on empty input.
""",
    },
    "env_setup_broken_imports": {
        "toybox/__main__.py": """\
from toybox.report import ROWS, build_report

print(build_report(ROWS))
""",
    },
    "repo_ops_ignore_artifacts": {
        ".gitignore": "*.pyc\nbuild/\n.env\n",
    },
    # --------------------------------------------------------------- content
    "meeting_notes_actions": {
        "notes.md": """\
# Release planning — meeting notes

## Decisions

- Hold the 1.3 release until the timezone bug is fixed.
- gzip support ships in 1.3; it is done and tested.
- Python 3.10 support is dropped.
- The cache rewrite will **not** happen this quarter.

## Actions

| owner | action |
|---|---|
| Sam | Fix the timezone bug (about a day) |
| Dana | Update the docs for the Python 3.10 drop |
""",
    },
    "api_docs_public_surface": {
        "API.md": """\
# geometry API

## `area_circle(radius)`

Returns the area of a circle with the given radius.

- **radius** — a non-negative number.
- **Raises** `ValueError` if the radius is negative.

## `area_rectangle(width, height)`

Returns the area of a rectangle.

- **width**, **height** — the side lengths.

## `distance(a, b)`

Returns the Euclidean distance between two points, each an `(x, y)` pair.
""",
    },
    # --------------------------------------------------------------- analyse
    "data_extraction_records": {
        "orders.json": """\
[
  {"id": "1001", "customer": "Acme Corp", "total": 1250.00},
  {"id": "1002", "customer": "Globex", "total": 89.99},
  {"id": "1003", "customer": "Initech", "total": 17500.50},
  {"id": "1004", "customer": "Umbrella Ltd", "total": 0.00}
]
""",
    },
    "data_join_regions": {"answer.txt": "South\n"},
    # ---------------------------------------------------------------- decide
    "issue_triage_severity": {
        "triage.md": """\
# Triage

- GH-101: p2 — cosmetic typo, no user impact.
- GH-102: p0 — every user is locked out right now.
- GH-103: p0 — silent data loss in exports, ongoing for a week.
- GH-104: p2 — feature request, no one is blocked.
""",
    },
    "qa_multi_hop_owner": {
        "answer.txt": (
            "Changes to the billing module are owned by @finance-team "
            "(CODEOWNERS maps /src/billing/ to it), whose lead is Priya Nair — "
            "priya@example.com.\n"
        ),
    },
    "planning_migration_steps": {
        "plan.md": """\
# Migrating from legacy_store to new_store

## Steps

1. Implement `new_store.put`/`get` behind the same interface, with the cache
   write-through disabled at first.
   - *Risk*: the new backend diverges from legacy semantics on missing keys.
   - *Rollback*: nothing is wired up yet; delete the branch.
2. Dual-write: every `put` goes to both stores, reads still come from legacy.
   - *Risk*: a failing new-store write fails the request.
   - *Rollback*: feature-flag the second write off; legacy is untouched.
3. Backfill existing keys into new_store, then run a comparison job that reads
   every key from both and reports mismatches.
   - *Risk*: backfill overwrites newer dual-written values.
   - *Rollback*: backfill is idempotent and writes only missing keys.
4. Flip reads to new_store for a small percentage of traffic, watch the
   mismatch rate and latency.
   - *Risk*: cache staleness shows up only under real read load.
   - *Rollback*: move the read flag back to legacy; dual-write means legacy is
     still current.
5. Ramp reads to 100%, leave dual-write on for one full backup cycle, then stop
   writing to legacy and remove it.
   - *Risk*: removing legacy too early leaves no fallback.
   - *Rollback*: until the final step, legacy still has every write.
""",
    },
    # ----------------------------------------------------------------- agent
    "agent_config_manifest": {
        "agent.yaml": """\
name: sample-agent
version: "0.1.0"
entrypoint: agent:main
tools:
  - read_file
""",
    },
}


#: Solutions for the judged/hard-to-verify use cases. For the two judged-only
#: tasks these satisfy the *floor* the verifier checks; the rubric is what
#: actually assesses them, and no oracle can stand in for that.
ORACLES_JUDGED = {
    "pr_triage_blocking": {
        "triage.md": """\
# PR triage: billing invoice tax change

**Do not merge — changes requested.** Two blocking issues.

1. **`TAX_RATE` is undefined.** `total()` now references it but the diff never
   defines or imports it, and CI confirms the consequence:
   `test_total` fails with `NameError: name 'TAX_RATE' is not defined`. The
   author replied that this was fixed, but the diff under review still does not
   define it, and dana re-requested changes on that basis.
2. **Invoice formatting regression.** `format_invoice` dropped the `:.2f`, so
   invoices will print full float precision instead of two decimal places.
   This is user-visible and was raised by priya but never addressed.

Neither is a nit; the first breaks the build and the second changes output
customers see.
""",
    },
    "slide_deck_release": {
        "deck.md": """\
# widget 1.3.0

## Action required: re-export anything from 1.1.0-1.2.3

Timestamps in those exports lost their timezone offset and are silently wrong
by your local UTC offset. Nothing warned you. Re-exporting corrects them.

## Compressed imports now work directly

gzip inputs up to 2GB import without decompressing by hand first, in about a
third of the time. This was our most common support request.

## Python 3.11 or later is now required

3.10 reached end of life and support is dropped. Upgrade before taking 1.3.0.

## Also in this release

Internal cleanups only: a shared retry helper and six dev-dependency bumps.
No behaviour change.
""",
    },
    "web_research_compare": {
        "findings.md": """\
# Choosing a TOML parser for Python 3.11+

**Recommendation: use `tomllib` from the standard library for reading, and add
`tomli-w` only if you need to write.**

## Why

Python 3.11 added `tomllib` to the standard library, so on our stated floor of
3.11+ reading TOML needs no third-party dependency at all. It is the reference
implementation and is maintained as part of CPython.

## Write support

`tomllib` is read-only by design. If we need to emit TOML, `tomli-w` is the
companion writer from the same authors and is the usual pairing. We parse in
three places and write in none today, so this is optional.

## Alternatives considered

- `tomli` — what `tomllib` was derived from. Only needed for Python 3.10 and
  below, which we no longer support.
- `toml` — older, unmaintained, and does not follow the 1.0 specification.

## Conclusion

Drop the vendored parser, import `tomllib`, add no dependency.
""",
    },
}
