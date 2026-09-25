# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for how the Code-work profile handles an empty search population.

A developer who greps through Bash never calls the Grep/Glob tools. Before
#3936 that rendered as two findings drawn over nothing — "Median / p90 search
result | 0 / 0 chars | searches return little; they are probes" and
"Searches returning nothing | 0 | — of searches were a miss", the second a
broken sentence where the percentage placeholder landed mid-clause.
"""

from gaia.factory.harvest.report import codework_table


def step(family, tool, chars=100, digest="x"):
    return {
        "family": family,
        "tool": tool,
        "arg_digest": digest,
        "result_chars": chars,
        "error": None,
        "resolved": True,
    }


def corpus(steps):
    return [
        {
            "session_id": "aaaaaaaa-1111",
            "steps": steps,
            "subagents": [],
            "total_calls": len(steps),
        }
    ]


def test_no_search_calls_says_so():
    """The regression: zero search calls must not render as measured findings."""
    table = codework_table(corpus([step("read", "Read"), step("edit", "Edit")]))
    assert "no Grep/Glob calls in this corpus" in table
    assert "0 / 0 chars" not in table
    assert "were a miss" not in table


def test_search_calls_still_render_both_rows():
    """The guard must not swallow the rows when there is a real population."""
    steps = [
        step("read", "Read"),
        step("search", "Grep", chars=500, digest="a"),
        step("search", "Grep", chars=0, digest="b"),
    ]
    table = codework_table(corpus(steps))
    assert "Median / p90 search result" in table
    assert "Searches returning nothing" in table
    assert "no Grep/Glob calls" not in table


def test_empty_search_result_is_counted_as_a_miss():
    steps = [step("search", "Grep", chars=0, digest=str(i)) for i in range(4)]
    table = codework_table(corpus(steps))
    assert "| Searches returning nothing | 4 |" in table
    assert "100.0% of searches were a miss" in table
