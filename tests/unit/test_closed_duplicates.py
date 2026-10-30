# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Closed duplicates are read in small pages, fail loudly at a cap, and name their
canonical however a human closed them.

The audits' dedup and miss analysis used one `gh issue list --limit 5000` query
that pulled every comment of every closed issue, and read the canonical only from
"Duplicate of #N" text. GitHub's "Close as duplicate" button writes no comment.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "audit" / "closed_duplicates.py"
)


@pytest.fixture(scope="module")
def cd():
    spec = importlib.util.spec_from_file_location("closed_duplicates", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def marked(dup, canonical):
    return {"type": "MarkedAsDuplicateEvent", "duplicate": dup, "canonical": canonical}


def unmarked(dup, canonical):
    return {
        "type": "UnmarkedAsDuplicateEvent",
        "duplicate": dup,
        "canonical": canonical,
    }


# ----------------------------------------------------------------------
# canonical_of
# ----------------------------------------------------------------------


def test_the_close_as_duplicate_event_names_the_canonical_with_no_comment(cd):
    issue = {
        "number": 9,
        "body": "",
        "comments": [],
        "duplicate_events": [marked(9, 12)],
    }
    assert cd.canonical_of(issue) == 12


def test_the_event_wins_over_older_comment_text(cd):
    issue = {
        "number": 9,
        "body": "",
        "comments": [{"body": "Duplicate of #3"}],
        "duplicate_events": [marked(9, 12)],
    }
    assert cd.canonical_of(issue) == 12


def test_the_newest_event_wins_when_re_pointed(cd):
    issue = {"number": 9, "duplicate_events": [marked(9, 12), marked(9, 40)]}
    assert cd.canonical_of(issue) == 40


def test_an_unmarked_duplicate_falls_back_to_the_text(cd):
    issue = {
        "number": 9,
        "comments": [{"body": "Duplicate of #5"}],
        "duplicate_events": [marked(9, 12), unmarked(9, 12)],
    }
    assert cd.canonical_of(issue) == 5


def test_an_event_about_another_issue_is_ignored(cd):
    """The canonical's own timeline also lists the issues marked against it."""
    issue = {"number": 12, "duplicate_events": [marked(9, 12)]}
    assert cd.canonical_of(issue) is None


def test_the_newest_comment_wins_then_the_body(cd):
    issue = {
        "number": 9,
        "body": "duplicate of #1",
        "comments": [{"body": "Duplicate of #2"}, {"body": "Actually duplicate of #3"}],
    }
    assert cd.canonical_of(issue) == 3
    assert cd.canonical_of({"number": 9, "body": "DUPLICATE OF #1"}) == 1


def test_an_issue_never_names_itself(cd):
    issue = {"number": 9, "comments": [{"body": "Duplicate of #9"}]}
    assert cd.canonical_of(issue) is None


# ----------------------------------------------------------------------
# Paging and the hard cap
# ----------------------------------------------------------------------


def _pages(cd, monkeypatch, pages, total):
    calls = []

    def graphql(query, variables):
        calls.append(variables)
        index = len(calls) - 1
        return {
            "repository": {
                "issues": {
                    "totalCount": total,
                    "pageInfo": {
                        "hasNextPage": index + 1 < len(pages),
                        "endCursor": f"c{index}",
                    },
                    "nodes": pages[index],
                }
            }
        }

    monkeypatch.setattr(cd, "graphql", graphql)
    return calls


def _issues(start, count):
    return [
        {"id": f"I{n}", "number": n, "state": "CLOSED", "stateReason": "COMPLETED"}
        for n in range(start, start + count)
    ]


def test_every_page_is_read_with_its_cursor(cd, monkeypatch):
    calls = _pages(cd, monkeypatch, [_issues(0, 100), _issues(100, 7)], 107)
    issues = cd.list_labelled("amd/gaia", "weekly-audit", ("CLOSED",), cap=1000)
    assert len(issues) == 107
    assert [c["cursor"] for c in calls] == [None, "c0"]
    assert all(c["first"] == cd.LIST_PAGE <= 100 for c in calls)
    assert calls[0]["owner"] == "amd" and calls[0]["name"] == "gaia"


def test_hitting_the_cap_with_more_pages_fails_loudly(cd, monkeypatch):
    _pages(cd, monkeypatch, [_issues(0, 100), _issues(100, 100), _issues(200, 1)], 201)
    with pytest.raises(SystemExit) as excinfo:
        cd.list_labelled("amd/gaia", "weekly-audit", ("CLOSED",), cap=200)
    assert "201 issues" in str(excinfo.value)
    assert "over the cap of 200" in str(excinfo.value)


def test_exactly_the_cap_with_no_more_pages_is_fine(cd, monkeypatch):
    _pages(cd, monkeypatch, [_issues(0, 100), _issues(100, 100)], 200)
    assert len(cd.list_labelled("amd/gaia", "x", ("CLOSED",), cap=200)) == 200


def test_details_are_read_in_small_batches_for_duplicates_only(cd, monkeypatch):
    listing = [
        {"id": f"I{n}", "number": n, "state": "CLOSED", "stateReason": "DUPLICATE"}
        for n in range(60)
    ] + _issues(100, 5)
    detail_batches = []

    def graphql(query, variables):
        if "nodes(ids:" in query:
            detail_batches.append(variables["ids"])
            return {
                "nodes": [
                    {"number": int(i[1:]), "body": "", "comments": {"nodes": []}}
                    for i in variables["ids"]
                ]
            }
        return {
            "repository": {
                "issues": {
                    "totalCount": len(listing),
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": listing,
                }
            }
        }

    monkeypatch.setattr(cd, "graphql", graphql)
    dups = cd.fetch_closed_duplicates("amd/gaia", "weekly-audit")
    assert len(dups) == 60
    assert [len(b) for b in detail_batches] == [25, 25, 10]
    assert all(d["stateReason"] == "DUPLICATE" for d in dups)


def test_graphql_errors_fail_loudly(cd, monkeypatch):
    class Done:
        returncode = 0
        stdout = json.dumps({"errors": [{"message": "Field 'x' doesn't exist"}]})
        stderr = ""

    sent = {}

    def run(args, **kwargs):
        sent["args"], sent["input"] = args, kwargs.get("input")
        return Done()

    monkeypatch.setattr(cd.subprocess, "run", run)
    with pytest.raises(SystemExit) as excinfo:
        cd.graphql("query { x }", {"a": 1})
    assert "doesn't exist" in str(excinfo.value)
    assert sent["args"] == ["gh", "api", "graphql", "--input", "-"]
    assert json.loads(sent["input"]) == {"query": "query { x }", "variables": {"a": 1}}


def test_a_gh_failure_fails_loudly(cd, monkeypatch):
    class Failed:
        returncode = 1
        stdout = ""
        stderr = "HTTP 502"

    monkeypatch.setattr(cd.subprocess, "run", lambda *a, **k: Failed())
    with pytest.raises(SystemExit) as excinfo:
        cd.graphql("query { x }", {})
    assert "HTTP 502" in str(excinfo.value)


def test_the_queries_ask_only_for_the_fields_they_use(cd):
    """The listing must stay cheap: no bodies or comments for every closed issue."""
    assert "body" not in cd._LIST_QUERY and "comments" not in cd._LIST_QUERY
    assert "MARKED_AS_DUPLICATE_EVENT" in cd._DETAIL_QUERY
    assert "comments(last: $comments)" in cd._DETAIL_QUERY
