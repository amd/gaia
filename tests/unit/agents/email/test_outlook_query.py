# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for ``outlook_query.translate_query`` (#2996), independent of
any HTTP mocking. See ``test_outlook_backend.py`` for the request-shape
assertions against ``LiveOutlookBackend.list_messages``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.outlook_query import translate_query

_NOW = datetime(2026, 8, 22, 12, 0, 0, tzinfo=timezone.utc)


def test_bare_phrase_is_quoted_as_exact_phrase():
    result = translate_query("quarterly report", now=_NOW)
    assert result.search == '"quarterly report"'
    assert result.filter is None


def test_from_operator_is_quoted_like_a_bare_phrase():
    # Graph's own KQL parser reads from: inside the wrapping quotes
    # (learn.microsoft.com/en-us/graph/search-query-parameter shows
    # $search="from:randiw"), so quoting from:/subject: does not defeat them.
    result = translate_query("from:netflix", now=_NOW)
    assert result.search == '"from:netflix"'
    assert result.filter is None


def test_subject_operator_is_quoted_like_a_bare_phrase():
    result = translate_query("subject:invoice", now=_NOW)
    assert result.search == '"subject:invoice"'


def test_from_operator_escapes_inner_quotes():
    result = translate_query('from:"Acme Corp"', now=_NOW)
    assert result.search == '"from:\\"Acme Corp\\""'


def test_is_unread_becomes_isread_filter():
    result = translate_query("is:unread", now=_NOW)
    assert result.filter == "isRead eq false"
    assert result.search is None


def test_is_read_becomes_isread_filter():
    result = translate_query("is:read", now=_NOW)
    assert result.filter == "isRead eq true"


def test_newer_than_days_becomes_ge_cutoff():
    result = translate_query("newer_than:7d", now=_NOW)
    assert result.filter == "receivedDateTime ge 2026-08-15T12:00:00Z"


def test_grouped_newer_than_days_does_not_capture_closing_parenthesis():
    result = translate_query("(newer_than:7d)", now=_NOW)
    assert result.filter == "receivedDateTime ge 2026-08-15T12:00:00Z"
    assert result.search is None


def test_older_than_days_becomes_le_cutoff():
    result = translate_query("older_than:14d", now=_NOW)
    assert result.filter == "receivedDateTime le 2026-08-08T12:00:00Z"


def test_newer_than_hours_is_exact():
    result = translate_query("newer_than:12h", now=_NOW)
    assert result.filter == "receivedDateTime ge 2026-08-22T00:00:00Z"


def test_newer_than_weeks_converts_to_days_like_gmail_path():
    # Gmail itself has no "w" unit either; parse_gmail_duration_value
    # (#2830) already normalizes weeks to the equivalent day count.
    result = translate_query("newer_than:2w", now=_NOW)
    assert result.filter == "receivedDateTime ge 2026-08-08T12:00:00Z"


def test_unparseable_duration_raises_same_as_gmail_path():
    with pytest.raises(ValueError, match="cannot parse duration value"):
        translate_query("newer_than:3q", now=_NOW)


def test_is_and_newer_than_combine_with_and():
    result = translate_query("is:unread newer_than:7d", now=_NOW)
    assert result.filter == (
        "isRead eq false and receivedDateTime ge 2026-08-15T12:00:00Z"
    )


def test_filter_and_search_operators_together_raises():
    with pytest.raises(ValueError, match="cannot be combined"):
        translate_query("is:unread from:alice", now=_NOW)


def test_filter_and_bare_text_together_raises():
    with pytest.raises(ValueError, match="cannot be combined"):
        translate_query("newer_than:7d budget report", now=_NOW)


def test_filter_and_grouped_bare_text_still_raises():
    with pytest.raises(ValueError, match="cannot be combined"):
        translate_query("(newer_than:7d) budget report", now=_NOW)


@pytest.mark.parametrize(
    "query",
    [
        "is:starred",
        "after:2026/07/01",
        "before:2026/07/08",
        "newer:2026/07/01",
        "older:2026/07/01",
        "label:promotions",
        "has:attachment",
        "in:inbox",
        "label:",
    ],
)
def test_operator_with_no_graph_equivalent_raises_instead_of_matching_nothing(query):
    # Before this fix each of these fell through to _graph_search_param and
    # reached Graph as literal text, matching nothing with no error (#2996
    # finding I62; newer:/older:/a bare "label:" with no value added after a
    # review on this PR caught the same silent fall-through for them).
    with pytest.raises(ValueError, match="not supported by this backend"):
        translate_query(query, now=_NOW)


def test_is_unsupported_value_raises_before_the_mixed_family_check():
    # is:starred is not consumed by _IS_RE (only unread/read are). The
    # unsupported-operator check now runs before the mixed-family check (a
    # review on this PR), so this raises naming is:starred directly rather
    # than telling the caller to re-run it as a separate search that would
    # fail the same way.
    with pytest.raises(ValueError, match="not supported by this backend"):
        translate_query("is:unread is:starred", now=_NOW)


@pytest.mark.parametrize(
    "query",
    [
        'subject:"check in: monday"',
        'subject:"Q3 has: numbers"',
    ],
)
def test_colon_word_inside_quoted_phrase_is_not_an_operator(query):
    # #3592: the colon word is part of the quoted phrase's text, not an
    # operator, so the guard above must not fire on it.
    result = translate_query(query, now=_NOW)
    assert result.filter is None


def test_unsupported_operator_after_a_quoted_phrase_still_raises():
    # A quoted colon word must not blind the guard to a real one that
    # follows outside the quotes.
    with pytest.raises(ValueError, match="'has:attachment' is not supported"):
        translate_query('subject:"check in: monday" has:attachment', now=_NOW)


def test_unsupported_operator_with_a_quoted_value_names_the_original_text():
    # The guard matches against a masked copy, so the error has to slice the
    # unmasked remainder or it reports the mask characters instead of the
    # operator's real quoted value.
    with pytest.raises(ValueError, match=r"'has:\"my file\"' is not supported"):
        translate_query('has:"my file"', now=_NOW)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
