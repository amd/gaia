# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for reconciling a hand-written label file against the corpus.

``labels.txt`` is assembled by hand from 8-character session prefixes, so a
truncated prefix or a file generated against an older corpus is the expected
failure. Before #3934 those sessions silently vanished and the use-case tables
renormalised to whatever survived — a table built from 1 of 15 sessions still
printed "100%", and a file matching nothing rendered an empty table labelled
"0 sessions — 100%".
"""

import pytest

from gaia.factory.harvest.report import coverage_note, reconcile_labels


def traces(*prefixes):
    """Traces whose ids start with the given 8-char prefixes."""
    return [{"session_id": f"{p}-rest-of-uuid"} for p in prefixes]


def test_full_coverage_is_silent():
    """A complete label file should not editorialise about itself."""
    corpus = traces("aaaaaaaa", "bbbbbbbb")
    cov = reconcile_labels({"aaaaaaaa": "x", "bbbbbbbb": "y"}, corpus)
    assert cov["matched"] == 2 and cov["unlabelled"] == 0 and cov["unknown"] == []
    assert coverage_note(cov) == ""


def test_zero_matches_fails_loudly():
    """The regression: an all-wrong file rendered an empty 100% table, exit 0."""
    with pytest.raises(SystemExit) as err:
        reconcile_labels({"deadbeef": "x"}, traces("aaaaaaaa"))
    msg = str(err.value)
    assert "match a session" in msg
    # Name both populations so the reader can tell which side is wrong.
    assert "1 label" in msg and "1 sessions" in msg


def test_partial_coverage_states_its_denominator():
    corpus = traces("aaaaaaaa", "bbbbbbbb", "cccccccc")
    cov = reconcile_labels({"aaaaaaaa": "x"}, corpus)
    assert (cov["matched"], cov["unlabelled"]) == (1, 2)
    note = coverage_note(cov)
    assert "1 of 3 sessions" in note and "not the corpus" in note
    assert note.startswith("_") and note.endswith("_")


def test_unknown_prefixes_are_named():
    """An unmatched prefix is usually a typo — printing it is the whole fix."""
    cov = reconcile_labels(
        {"aaaaaaaa": "x", "deadbeef": "y"}, traces("aaaaaaaa", "bbbbbbbb")
    )
    assert cov["unknown"] == ["deadbeef"]
    assert "`deadbeef`" in coverage_note(cov)


def test_unknown_prefix_list_is_capped():
    corpus = traces("aaaaaaaa")
    labels = {"aaaaaaaa": "x", **{f"bad{i:05d}": "y" for i in range(9)}}
    note = coverage_note(reconcile_labels(labels, corpus))
    assert "9 label prefixes matched no session" in note and "+4 more" in note


@pytest.mark.parametrize(
    "labels,corpus,expect",
    [({}, traces("aaaaaaaa"), ""), ({}, [], "")],
)
def test_no_labels_is_not_an_error(labels, corpus, expect):
    """--labels is optional; its absence is not a mismatch."""
    assert coverage_note(reconcile_labels(labels, corpus)) == expect


def test_singular_grammar():
    cov = reconcile_labels({"aaaaaaaa": "x"}, traces("aaaaaaaa", "bbbbbbbb"))
    note = coverage_note(cov)
    assert "1 session carries no label" in note
