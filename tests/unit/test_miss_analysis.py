# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The audits learn from what they missed: a refreshable, tracked miss-analysis."""

from __future__ import annotations

import importlib.util
import re
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "audit" / "miss_analysis.py"
DOC = ROOT / "scripts" / "audit" / "missed_bug_classes.md"
WORKFLOWS = ROOT / ".github" / "workflows"


@pytest.fixture(scope="module")
def ma():
    spec = importlib.util.spec_from_file_location("miss_analysis", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def pr(number, title, closes=(), body="", merged="2026-10-18T00:00:00Z"):
    return {
        "number": number,
        "title": title,
        "body": body,
        "mergedAt": merged,
        "closingIssuesReferences": [{"number": n} for n in closes],
    }


def test_a_fix_that_closes_an_audit_issue_counts_as_caught(ma):
    result = ma.analyse(
        [pr(1, "fix(x): a", closes=[900]), pr(2, "fix(x): b", body="Fixes #901")],
        [{"number": 900}, {"number": 901}],
    )
    assert result["caught"] == 2


def test_a_fix_with_no_audit_issue_is_a_miss_in_its_class(ma):
    result = ma.analyse(
        [pr(5, "fix(mcp): stop a hung MCP stdio server freezing the turn")], []
    )
    assert result["caught"] == 0
    assert result["classes"]["No deadline"] == 1


def test_classes_read_the_title_not_incidental_body_prose(ma):
    """Bodies say "tested on Windows" constantly; that is not a Windows bug."""
    hits = ma.classify(
        pr(6, "fix(llm): retry a dropped stream", body="Tested on Windows.")
    )
    assert "Windows behaviour" not in hits


def test_an_unmatched_fix_is_counted_as_other_not_dropped(ma):
    assert ma.classify(pr(7, "fix(cli): rename a flag")) == [ma.UNCLASSIFIED]


def test_noise_is_grouped_by_the_umbrella_it_was_closed_into(ma):
    issues = [
        {
            "number": 1,
            "stateReason": "DUPLICATE",
            "comments": [{"body": "Duplicate of #1077."}],
        },
        {
            "number": 2,
            "stateReason": "DUPLICATE",
            "comments": [{"body": "Duplicate of #1077."}],
        },
        {"number": 3, "stateReason": "NOT_PLANNED", "comments": []},
        {"number": 4, "stateReason": "COMPLETED", "comments": []},
    ]
    noise = ma.analyse([], issues)["noise"]
    assert noise == {"duplicate of #1077": 2, "not planned": 1}


def test_render_and_splice_replace_only_the_generated_block(ma):
    doc = f"# Title\nkeep me\n{ma.START}\nold\n{ma.END}\ntail\n"
    block = ma.render(
        ma.analyse([pr(1, "fix(x): hang")], []), date(2026, 6, 1), date(2026, 10, 1)
    )
    out = ma.splice(doc, block)
    assert out.startswith("# Title\nkeep me\n")
    assert out.endswith("\ntail\n")
    assert "old" not in out
    assert "**0 of 1 fix PRs (0%)" in out


def test_a_doc_without_exactly_one_block_is_refused(ma):
    with pytest.raises(SystemExit):
        ma.splice("no markers here", "x")


def test_a_chunk_that_hits_the_gh_cap_is_refused(ma, monkeypatch):
    monkeypatch.setattr(ma, "_gh", lambda args: [{"number": i} for i in range(1000)])
    with pytest.raises(SystemExit) as excinfo:
        ma.fetch_fix_prs("amd/gaia", date(2026, 10, 1), date(2026, 10, 5))
    assert "lower --chunk-days" in str(excinfo.value)


def test_every_class_the_script_counts_has_a_heading_in_the_doc(ma):
    """The script and the hand-written checklists must name the same classes."""
    headings = set(re.findall(r"^### (.+)$", DOC.read_text(encoding="utf-8"), re.M))
    static = {name for name, _ in ma.CLASSES} - {
        "Agent UI and TUI behaviour",
        "Agent answers and tool use",
    }
    assert static <= headings, static - headings


@pytest.mark.parametrize(
    "workflow",
    [
        "claude-nightly-audit.yml",
        "claude-security-audit.yml",
        "claude-weekly-doc-walkthrough.yml",
    ],
)
def test_every_audit_prompt_reads_the_classes_doc(workflow):
    assert "scripts/audit/missed_bug_classes.md" in (WORKFLOWS / workflow).read_text(
        encoding="utf-8"
    )
