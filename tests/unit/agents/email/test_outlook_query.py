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


def test_from_operator_passes_through_unquoted():
    result = translate_query("from:netflix", now=_NOW)
    assert result.search == "from:netflix"
    assert result.filter is None


def test_subject_operator_passes_through_unquoted():
    result = translate_query("subject:invoice", now=_NOW)
    assert result.search == "subject:invoice"


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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
